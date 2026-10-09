import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Prefetch
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import CustomUser, UserBusiness
from accounts.platform_integrations import glovo_platform_enabled
from accounts.services import business_has_module, is_business_admin, user_has_permission
from core.models import Business
from core.context import get_request_cache
from core.performance import performance_section
from core.verticals import vertical_config
from core.services import audit

from .delivery_forms import DeliveryAreaForm, DeliveryDriverForm, DeliveryOriginForm, DeliveryProviderAccountForm, DeliveryRateBandForm, DeliverySettingsForm
from .delivery_services import (
    add_delivery_message, assign_delivery_batch_rider, create_delivery_batch, create_delivery_quote_options, delivery_area_distance_summary,
    delivery_available, delivery_status_choices_for, organise_delivery_batch, pickup_delivery_batch,
    raise_delivery_issue, resolve_delivery_location, select_delivery_quote, serialize_delivery_quote, serialize_delivery_tracking,
    switch_delivery_method, update_delivery_status,
)
from .notification_services import queue_commerce_notification
from .models import (
    CommerceIntegration, CommerceNotification, CommerceSettings,
    DeliveryArea,
    DeliveryAssignment,
    DeliveryBatch,
    DeliveryDriver,
    DeliveryEvent,
    DeliveryIssue,
    DeliveryOrigin,
    DeliveryProviderAccount,
    DeliveryRateBand,
    DeliverySettings,
)




def _annotate_rider_readiness(assignments):
    """Attach compact fulfilment context used by the rider workspace only."""
    for assignment in assignments:
        items = list(assignment.intake.items.all())
        made_to_order = [
            item for item in items
            if item.fulfilment_source == item.FULFILMENT_MADE_TO_ORDER
        ]
        production_order = assignment.intake.accepted_order or assignment.intake.split_order
        assignment.has_made_to_order = bool(made_to_order)
        assignment.made_to_order_pending = bool(
            made_to_order
            and assignment.intake.fulfilment_state != assignment.intake.FULFIL_COMPLETE
        )
        assignment.production_order = production_order
        assignment.production_status_label = (
            production_order.get_status_display() if production_order else "Awaiting production"
        )
    return assignments


def _detail(exc):
    return "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


def _can_approve_delivery_switch(user, business):
    if getattr(user, "is_superuser", False) or is_business_admin(user, business):
        return True
    request_cache = get_request_cache()
    snapshot = (
        request_cache.get(("permission_snapshot", user.pk, business.pk))
        if request_cache is not None else None
    )
    membership = snapshot[0] if snapshot else None
    if membership is None:
        membership = UserBusiness.objects.filter(
            user=user, business=business, active=True
        ).select_related("role").first()
    return bool(membership and membership.role.key in {
        CustomUser.ROLE_MANAGER, CustomUser.ROLE_MD_DIRECTOR, CustomUser.ROLE_BUSINESS_ADMIN,
    })


def _settings(business, *, ensure_provider_accounts=True):
    # Provider-neutral built-ins must also exist for businesses created after
    # the data migration that seeded existing tenants.
    if ensure_provider_accounts:
        from .delivery_providers import ensure_builtin_provider_accounts
        ensure_builtin_provider_accounts(business)
    obj, _ = DeliverySettings.objects.get_or_create(business=business, defaults={"created_by": None})
    return obj


@login_required
def delivery_dashboard(request):
    with performance_section(request, "delivery.settings"):
        settings = _settings(request.business, ensure_provider_accounts=False)
    terminal_statuses = {
        DeliveryAssignment.STATUS_DELIVERED, DeliveryAssignment.STATUS_RETURNED,
        DeliveryAssignment.STATUS_CANCELLED,
    }
    with performance_section(request, "delivery.assignments"):
        active_assignments = list(
            DeliveryAssignment.objects.exclude(status__in=terminal_statuses)
            .select_related("intake", "driver__user", "origin", "quote", "provider_account", "batch")
            .prefetch_related("events", "issues", "messages")[:160]
        )
        completed_assignments = list(
            DeliveryAssignment.objects.filter(status__in=terminal_statuses)
            .select_related("intake", "driver__user")[:60]
        )

    with performance_section(request, "delivery.batches"):
        active_batches = list(
            DeliveryBatch.objects.select_related("driver__user")
            .prefetch_related(Prefetch(
                "assignments",
                queryset=DeliveryAssignment.objects.select_related("intake", "quote").order_by("batch_stop_sequence", "id"),
            ))
            .annotate(order_count=Count("assignments"))
            .exclude(status__in=[DeliveryBatch.STATUS_COMPLETED, DeliveryBatch.STATUS_CANCELLED])[:40]
        )
    # Materialize setup collections once: the dashboard renders each list and also
    # derives readiness from it, so Python checks avoid duplicate EXISTS queries.
    with performance_section(request, "delivery.setup_data"):
        origins = list(DeliveryOrigin.objects.filter(business=request.business))
        rate_bands = list(DeliveryRateBand.objects.filter(business=request.business))
        areas = list(DeliveryArea.objects.filter(business=request.business).select_related("rate_band"))
        drivers = list(DeliveryDriver.objects.filter(business=request.business).select_related("user"))
        integration_enabled = glovo_platform_enabled()
        provider_accounts_qs = DeliveryProviderAccount.objects.filter(business=request.business)
        if not integration_enabled:
            provider_accounts_qs = provider_accounts_qs.exclude(provider_code=DeliveryProviderAccount.PROVIDER_GLOVO)
        provider_accounts = list(provider_accounts_qs)
        if integration_enabled and not any(
            row.provider_code == DeliveryProviderAccount.PROVIDER_GLOVO for row in provider_accounts
        ):
            # Missing built-ins are an exceptional repair path. Healthy
            # dashboards reuse the provider-account query above instead of
            # issuing a duplicate existence lookup on every request.
            from .delivery_providers import ensure_builtin_provider_accounts
            account, created = ensure_builtin_provider_accounts(request.business)
            if created and account:
                provider_accounts.append(account)
    for provider in provider_accounts:
        provider.webhook_path = (
            f"/api/v1/delivery/providers/custom/{request.business.slug}/{provider.pk}/webhook"
            if provider.provider_code == DeliveryProviderAccount.PROVIDER_GENERIC else ""
        )
    mapped_active_origins = [
        row for row in origins
        if row.active and row.latitude is not None and row.longitude is not None
    ]
    active_origin = next((row for row in mapped_active_origins if row.is_default), None) or (mapped_active_origins[0] if mapped_active_origins else None)
    with performance_section(request, "delivery.distance"):
        for area in areas:
            summary = delivery_area_distance_summary(area, origin=active_origin)
            area.centre_distance_from_base_km = summary["centre_distance_km"]
            area.maximum_distance_from_base_km = summary["maximum_base_distance_km"]
    base_ready = bool(mapped_active_origins)
    pricing_ready = any(row.active for row in rate_bands)
    destinations_ready = any(row.active for row in areas)
    dispatch_ready = any(row.active for row in drivers) or any(row.active for row in provider_accounts)
    provider_ready = True
    if settings.default_provider in {DeliverySettings.PROVIDER_THIRD_PARTY, DeliverySettings.PROVIDER_HYBRID}:
        account = settings.default_provider_account
        account_required = settings.default_provider == DeliverySettings.PROVIDER_THIRD_PARTY or settings.hybrid_routing_policy in {
            DeliverySettings.HYBRID_ROUTE_PROVIDER_FIRST, DeliverySettings.HYBRID_ROUTE_CUSTOMER,
            DeliverySettings.HYBRID_ROUTE_LOWEST, DeliverySettings.HYBRID_ROUTE_FASTEST,
        }
        if account_required:
            provider_ready = bool(
                account and account.active
                and not (account.provider_code == DeliveryProviderAccount.PROVIDER_GLOVO and not integration_enabled)
                and account.is_configured_for_dispatch
            )
    public_ready = bool(settings.enabled and base_ready and pricing_ready and provider_ready)
    with performance_section(request, "delivery.permissions"):
        can_manage_delivery = is_business_admin(request.user, request.business)
        can_update_delivery = user_has_permission(request.user, request.business, "delivery", "edit")
        can_approve_delivery_switch = _can_approve_delivery_switch(request.user, request.business)
    has_inhouse_riders = any(row.active and row.provider == DeliveryDriver.PROVIDER_INHOUSE for row in drivers)
    for batch in active_batches:
        batch.rider_source_default = (
            "independent" if batch.has_independent_rider or (not batch.driver_id and not has_inhouse_riders)
            else "inhouse"
        )
    for assignment in active_assignments:
        # Offer dispatch only the steps the delivery can actually take next, and
        # open the rider form on the kind of rider this delivery really has. With
        # no in-house riders at all, independent rider entry is the default.
        assignment.status_options = delivery_status_choices_for(assignment)
        assignment.rider_source_default = (
            "independent" if assignment.has_independent_rider
            or (not assignment.driver_id and not has_inhouse_riders)
            else "inhouse"
        )
        assignment.batch_label = str(assignment.batch.public_id)[:8].upper() if assignment.batch_id else ""
    context = {
        "delivery_settings": settings,
        "assignments": active_assignments,
        "completed_assignments": completed_assignments,
        "active_batches": active_batches,
        "origins": origins,
        "rate_bands": rate_bands,
        "areas": areas,
        "drivers": drivers,
        "provider_accounts": provider_accounts,
        "delivery_setup": {
            "base_ready": base_ready,
            "pricing_ready": pricing_ready,
            "destinations_ready": destinations_ready,
            "dispatch_ready": dispatch_ready,
            "provider_ready": provider_ready,
            "public_ready": public_ready,
        },
        "can_manage_delivery": can_manage_delivery,
        "can_update_delivery": can_update_delivery,
        "glovo_webhook_path": (f"/api/v1/delivery/providers/glovo/{request.business.slug}/webhook" if integration_enabled else ""),
        "has_inhouse_riders": has_inhouse_riders,
        "issue_status_choices": DeliveryIssue.STATUS_CHOICES,
        "can_approve_delivery_switch": can_approve_delivery_switch,
    }
    with performance_section(request, "delivery.render"):
        return render(request, "commerce/delivery/dashboard.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def delivery_settings(request):
    if not is_business_admin(request.user, request.business):
        return render(request, "403.html", status=403)
    obj = _settings(request.business)
    form = DeliverySettingsForm(request.POST or None, instance=obj, business=request.business)
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.business = request.business
        saved.created_by = saved.created_by or request.user
        saved.save()
        audit(request.business, request.user, "delivery_settings", saved, "Delivery settings updated")
        messages.success(request, "Delivery settings saved.")
        return redirect("delivery_dashboard")
    return render(request, "commerce/delivery/settings.html", {"form": form})


def _model_form_view(
    request, *, model, form_class, title, success, pk=None,
    business_kw=False, location_label="", include_user_directory=False,
    intro="", setup_tip="", radius_selector=False, form_notice="", form_notice_scope="",
):
    if not is_business_admin(request.user, request.business):
        return render(request, "403.html", status=403)
    obj = get_object_or_404(model, pk=pk, business=request.business) if pk else None
    kwargs = {"instance": obj}
    if business_kw:
        kwargs["business"] = request.business
    form = form_class(request.POST or None, **kwargs)
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.business = request.business
        saved.created_by = saved.created_by or request.user
        if isinstance(saved, DeliveryOrigin) and saved.is_default:
            DeliveryOrigin.objects.filter(business=request.business, is_default=True).exclude(pk=saved.pk).update(is_default=False)
        saved.save()
        audit(request.business, request.user, "delivery_setup", saved, f"{title} saved")
        messages.success(request, success)
        return redirect("delivery_dashboard")
    context = {
        "form": form,
        "title": title,
        "obj": obj,
        "location_label": location_label,
        "form_intro": intro,
        "setup_tip": setup_tip,
        "form_notice": form_notice,
        "form_notice_scope": form_notice_scope,
        "radius_selector": radius_selector,
    }
    if location_label:
        context["delivery_areas"] = list(
            DeliveryArea.raw_objects.filter(business=request.business, active=True).order_by("name", "id")
        )
    if include_user_directory:
        context["delivery_user_directory"] = list(
            form.fields["user"].queryset.values("id", "fullname", "username", "phone", "email")
        )
    return render(request, "commerce/delivery/object_form.html", context)


@login_required
def delivery_provider_account_form(request, pk=None):
    glovo_enabled = glovo_platform_enabled()
    if pk and not glovo_enabled and DeliveryProviderAccount.objects.filter(
        pk=pk, business=request.business, provider_code=DeliveryProviderAccount.PROVIDER_GLOVO
    ).exists():
        raise Http404("Delivery provider account is unavailable.")
    return _model_form_view(
        request, model=DeliveryProviderAccount, form_class=DeliveryProviderAccountForm,
        title="Delivery provider account", success="Delivery provider account saved.", pk=pk,
        intro="Add any delivery partner while INPROFIC remains the control engine for checkout quotes, routing, assignments, staff actions and the customer timeline. A partner may stay fully manual or connect through its own API/adapter.",
        setup_tip=(
            "Custom partners can use INPROFIC price bands and manual dispatch immediately after activation. Optional automatic dispatch uses the INPROFIC Delivery Adapter v1 endpoint and account-specific status webhook. For Glovo, use only credentials and API access issued or enabled for that business, keep the connection inactive until setup is complete, then test a full quote and dispatch in Sandbox before production."
            if glovo_enabled else
            "Custom partners can use INPROFIC price bands and manual dispatch after activation. If the courier or your integration service supports automation, configure its endpoint and credentials; the account-specific status webhook appears on the Delivery dashboard after saving."
        ),
        form_notice=(
            "Glovo account required: this connection requires an active Glovo business account and API access issued or enabled by Glovo. INPROFIC does not provide or resell Glovo accounts. Glovo is a third-party service; availability, onboarding, pricing and API access are governed by Glovo and may vary by market."
            if glovo_enabled else ""
        ),
        form_notice_scope="glovo" if glovo_enabled else "",
    )


@login_required
@require_POST
def delivery_provider_test_custom_connection(request, pk):
    if not is_business_admin(request.user, request.business):
        return render(request, "403.html", status=403)
    account = get_object_or_404(
        DeliveryProviderAccount, pk=pk, business=request.business,
        provider_code=DeliveryProviderAccount.PROVIDER_GENERIC,
    )
    try:
        from .delivery_providers import test_generic_provider_connection
        test_generic_provider_connection(account)
        audit(
            request.business, request.user, "delivery_provider_connection_test", account,
            "Custom delivery partner connection test succeeded", {"provider_account": account.pk},
        )
        messages.success(request, f"{account.name} connection test succeeded.")
    except ValidationError as exc:
        messages.error(request, _detail(exc))
    return redirect("delivery_dashboard")


@login_required
@require_POST
def delivery_provider_register_glovo_webhooks(request, pk):
    if not glovo_platform_enabled():
        raise Http404("Delivery provider integration is unavailable.")
    if not is_business_admin(request.user, request.business):
        return render(request, "403.html", status=403)
    account = get_object_or_404(
        DeliveryProviderAccount, pk=pk, business=request.business,
        provider_code=DeliveryProviderAccount.PROVIDER_GLOVO,
    )
    try:
        from .delivery_providers import ensure_glovo_webhooks
        callback_url = request.build_absolute_uri(reverse("glovo_delivery_webhook", args=[request.business.slug]))
        result = ensure_glovo_webhooks(account, callback_url=callback_url)
        created = ", ".join(result["created"]) or "none"
        existing = ", ".join(result["existing"]) or "none"
        audit(request.business, request.user, "delivery_provider_webhooks", account, "Glovo webhooks registered/verified", {"created": result["created"], "existing": result["existing"]})
        messages.success(request, f"Glovo webhooks verified. Created: {created}; already active: {existing}.")
    except ValidationError as exc:
        messages.error(request, _detail(exc))
    return redirect("delivery_dashboard")


@login_required
def delivery_origin_form(request, pk=None):
    return _model_form_view(
        request, model=DeliveryOrigin, form_class=DeliveryOriginForm,
        title="Delivery / office base", success="Delivery base saved.", pk=pk,
        location_label="dispatch base",
        intro="This is where riders or delivery partners collect paid orders. Distance and delivery fees are calculated from the mapped base.",
        setup_tip="Place the map pin accurately and mark the normal pickup location as default. You can keep additional bases inactive until they are ready.",
    )


@login_required
def delivery_rate_band_form(request, pk=None):
    return _model_form_view(
        request, model=DeliveryRateBand, form_class=DeliveryRateBandForm,
        title="Delivery price band", success="Delivery price band saved.", pk=pk,
        intro="Define the fee, per-kilometre rate, minimum basket and ETA used by one or more destination areas. INPROFIC prices the real distance from the delivery base to the validated customer destination.",
        setup_tip="For named destination areas, the area's mapped radius is the coverage boundary; the min/max distance fields remain only as a fallback for map-only quotes with no selected area.",
    )


@login_required
def delivery_area_form(request, pk=None):
    return _model_form_view(
        request, model=DeliveryArea, form_class=DeliveryAreaForm,
        title="Delivery destination / zone", success="Delivery destination saved.",
        pk=pk, business_kw=True, location_label="delivery destination centre", radius_selector=True,
        intro="Map the destination centre and its maximum coverage radius, then link the area to the pricing band that supplies its fee, minimum basket and ETA rules.",
        setup_tip="Drag the radius handle to set the normal coverage boundary. Optional NE/SE/SW/NW handles can extend awkward corridors beyond the circle. Checkout validates the customer's exact address or pin inside that shape, then prices the real distance from your delivery base.",
    )


@login_required
def delivery_driver_form(request, pk=None):
    return _model_form_view(
        request, model=DeliveryDriver, form_class=DeliveryDriverForm,
        title="Delivery rider / courier", success="Delivery rider saved.", pk=pk,
        business_kw=True, include_user_directory=True,
        intro="Create an assignable rider or manual courier contact. Linking an in-house rider to a staff user unlocks their focused My Deliveries workspace.",
        setup_tip="Select a staff user first to fill their contact details automatically. External API providers belong under Provider plug-ins, not Rider login.",
    )


def _rider_selection(request, assignment):
    """Translate the dispatch form's rider fields into update_delivery_status arguments.

    ``rider_source`` says which kind of rider dispatch is naming, so a form can
    never submit two conflicting riders at once:

    * ``inhouse``     - the chosen in-house/courier profile (blank = unassigned);
    * ``independent`` - a rider the business booked outside INPROFIC, typed in.

    Older forms that send no ``rider_source`` keep their original behaviour: the
    rider select decides the assigned profile, and independent-rider details are
    changed only when the form actually submitted them. A plain status update
    therefore never wipes a rider it did not mention.
    """
    post = request.POST
    source = post.get("rider_source", "")
    driver_id = post.get("driver_id", "")
    if source not in {"", "inhouse", "independent"}:
        raise ValidationError("Choose a valid rider source.")
    if source == "independent" or (not source and not driver_id and post.get("manual_rider_name")):
        name = post.get("manual_rider_name", "").strip()
        if not name:
            raise ValidationError("Enter the independent rider's name, or switch to an in-house rider.")
        return {
            "driver": None,
            # Release an in-house profile only if one is actually attached, so
            # re-saving the same independent rider keeps its assignment time.
            "clear_driver": bool(assignment.driver_id),
            "manual_rider_name": name,
            "manual_rider_phone": post.get("manual_rider_phone", ""),
            "manual_rider_vehicle": post.get("manual_rider_vehicle", ""),
        }
    selection = {"driver": None, "clear_driver": False}
    if driver_id:
        selection["driver"] = get_object_or_404(
            DeliveryDriver, pk=driver_id, business=request.business, active=True
        )
        # Naming an in-house rider replaces any independent rider.
        selection.update(manual_rider_name="", manual_rider_phone="", manual_rider_vehicle="")
    elif "driver_id" in post:
        selection["clear_driver"] = bool(assignment.driver_id)
        if source == "inhouse":
            selection.update(manual_rider_name="", manual_rider_phone="", manual_rider_vehicle="")
    return selection


@login_required
@require_POST
def delivery_assignment_update(request, public_id):
    if not user_has_permission(request.user, request.business, "delivery", "edit"):
        return render(request, "403.html", status=403)
    assignment = get_object_or_404(DeliveryAssignment, public_id=public_id, business=request.business)
    try:
        rider = _rider_selection(request, assignment)
        requested_status = request.POST.get("status")
        if requested_status == DeliveryAssignment.STATUS_CANCELLED and assignment.provider_account_id and assignment.provider_order_id:
            from .delivery_providers import cancel_assignment_with_provider
            is_disabled_glovo = (
                assignment.provider_account.provider_code == DeliveryProviderAccount.PROVIDER_GLOVO
                and not glovo_platform_enabled()
            )
            if not is_disabled_glovo:
                cancel_assignment_with_provider(assignment, actor=request.user)
        update_delivery_status(
            assignment=assignment,
            status=requested_status,
            actor=request.user,
            note=request.POST.get("note", ""),
            proof_note=request.POST.get("proof_note", ""),
            proof_reference=request.POST.get("proof_reference", ""),
            external_reference=request.POST.get("external_reference", ""),
            external_tracking_url=request.POST.get("external_tracking_url", ""),
            **rider,
        )
        messages.success(request, "Delivery updated and added to the timeline.")
    except ValidationError as exc:
        messages.error(request, _detail(exc))
    return redirect("delivery_dashboard")


@login_required
@require_POST
def delivery_assignment_switch(request, public_id):
    if not user_has_permission(request.user, request.business, "delivery", "edit"):
        return render(request, "403.html", status=403)
    assignment = get_object_or_404(DeliveryAssignment, public_id=public_id, business=request.business)
    try:
        manager_approved = bool(request.POST.get("manager_approved")) and _can_approve_delivery_switch(request.user, request.business)
        switch_delivery_method(
            assignment=assignment,
            target_provider=request.POST.get("target_provider", ""),
            actor=request.user,
            manager_approved=manager_approved,
        )
        messages.success(request, "Delivery method switched. The customer's paid delivery fee was not increased.")
    except ValidationError as exc:
        messages.error(request, _detail(exc))
    return redirect("delivery_dashboard")


def _batch_rider_selection(request):
    """Rider arguments for a batch from the dispatch form: in-house profile or independent rider."""
    post = request.POST
    if post.get("rider_source") == "independent":
        return {
            "driver": None,
            "manual_rider_name": post.get("manual_rider_name", ""),
            "manual_rider_phone": post.get("manual_rider_phone", ""),
            "manual_rider_vehicle": post.get("manual_rider_vehicle", ""),
        }
    driver_id = post.get("driver_id", "")
    if not driver_id:
        raise ValidationError("Choose an in-house rider or enter an independent rider.")
    return {"driver": get_object_or_404(
        DeliveryDriver, pk=driver_id, business=request.business,
        active=True, provider=DeliveryDriver.PROVIDER_INHOUSE,
    )}


def _parse_route_stops(post):
    """Ordered ``{delivery_id, stop_minutes}`` rows from a route form (rider portal and dispatch share it)."""
    delivery_ids = post.getlist("delivery_id")
    stop_minutes = post.getlist("stop_minutes")
    sequences = post.getlist("sequence")
    if len(delivery_ids) != len(stop_minutes) or len(delivery_ids) != len(sequences):
        raise ValidationError("Every route stop needs a route position and stop-gap timing value.")
    try:
        rows = [
            {"delivery_id": delivery_id, "stop_minutes": minutes, "sequence": int(sequence)}
            for delivery_id, minutes, sequence in zip(delivery_ids, stop_minutes, sequences)
        ]
    except ValueError as exc:
        raise ValidationError("Route positions must be whole numbers.") from exc
    rows.sort(key=lambda row: row["sequence"])
    if len({row["sequence"] for row in rows}) != len(rows):
        raise ValidationError("Use a different route position for every stop.")
    return [{"delivery_id": row["delivery_id"], "stop_minutes": row["stop_minutes"]} for row in rows]


@login_required
@require_POST
def delivery_batch_create_view(request):
    if not user_has_permission(request.user, request.business, "delivery", "edit"):
        return render(request, "403.html", status=403)
    assignments = list(
        DeliveryAssignment.objects.filter(
            business=request.business, public_id__in=request.POST.getlist("delivery_id")
        ).select_related("intake")
    )
    try:
        batch = create_delivery_batch(
            business=request.business, assignments=assignments, actor=request.user,
            **_batch_rider_selection(request),
        )
        who = "Dispatch arranges its route" if batch.has_independent_rider else "The rider must organise its route"
        messages.success(request, f"Batch {str(batch.public_id)[:8].upper()} created. {who} before pickup.")
    except ValidationError as exc:
        messages.error(request, _detail(exc))
    return redirect("delivery_dashboard")


def _dispatch_batch(request, public_id):
    return get_object_or_404(DeliveryBatch.raw_objects, business=request.business, public_id=public_id)


@login_required
@require_POST
def delivery_batch_rider_update(request, public_id):
    """Dispatch override: hand a whole batch to an in-house or independent rider."""
    if not user_has_permission(request.user, request.business, "delivery", "edit"):
        return render(request, "403.html", status=403)
    batch = _dispatch_batch(request, public_id)
    try:
        assign_delivery_batch_rider(batch=batch, actor=request.user, **_batch_rider_selection(request))
        messages.success(request, "Batch rider updated for every delivery in the batch.")
    except ValidationError as exc:
        messages.error(request, _detail(exc))
    return redirect("delivery_dashboard")


@login_required
@require_POST
def delivery_batch_route_update(request, public_id):
    """Dispatch saves a batch's route for a rider who has no rider-app login."""
    if not user_has_permission(request.user, request.business, "delivery", "edit"):
        return render(request, "403.html", status=403)
    batch = _dispatch_batch(request, public_id)
    try:
        organise_delivery_batch(batch=batch, stops=_parse_route_stops(request.POST), actor=request.user)
        messages.success(request, "Batch route saved. Marking the batch picked up now starts the ETA countdowns.")
    except ValidationError as exc:
        messages.error(request, _detail(exc))
    return redirect("delivery_dashboard")


@login_required
@require_POST
def delivery_batch_pickup_update(request, public_id):
    """Dispatch marks a routed batch as picked up on the rider's behalf."""
    if not user_has_permission(request.user, request.business, "delivery", "edit"):
        return render(request, "403.html", status=403)
    batch = _dispatch_batch(request, public_id)
    try:
        pickup_delivery_batch(batch=batch, actor=request.user)
        messages.success(request, "Batch picked up. Every customer ETA is now counting down from pickup.")
    except ValidationError as exc:
        messages.error(request, _detail(exc))
    return redirect("delivery_dashboard")


@login_required
@require_POST
def delivery_staff_message(request, public_id):
    if not user_has_permission(request.user, request.business, "delivery", "edit"):
        return render(request, "403.html", status=403)
    assignment = get_object_or_404(DeliveryAssignment, public_id=public_id, business=request.business)
    try:
        add_delivery_message(
            assignment=assignment, sender_type="staff", body=request.POST.get("body", ""), actor=request.user
        )
        messages.success(request, "Delivery message sent.")
    except ValidationError as exc:
        messages.error(request, _detail(exc))
    return redirect("delivery_dashboard")


@login_required
@require_POST
def delivery_issue_update(request, issue_id):
    if not user_has_permission(request.user, request.business, "delivery", "edit"):
        return render(request, "403.html", status=403)
    issue = get_object_or_404(
        DeliveryIssue.raw_objects.select_related("assignment__intake", "reporter_driver__user"),
        pk=issue_id, business=request.business,
    )
    status = request.POST.get("status") or issue.status
    if status not in {value for value, _ in DeliveryIssue.STATUS_CHOICES}:
        messages.error(request, "Choose a valid issue status.")
        return redirect("delivery_dashboard")
    issue.status = status
    issue.resolution_note = (request.POST.get("resolution_note") or "").strip()
    if status == DeliveryIssue.STATUS_RESOLVED:
        from django.utils import timezone
        issue.resolved_at = timezone.now()
    else:
        issue.resolved_at = None
    issue.save(update_fields=["status", "resolution_note", "resolved_at", "updated_at"])
    audit(
        request.business, request.user, "delivery_issue_update", issue,
        f"Delivery issue {issue.pk} changed to {status}",
        {"delivery_id": str(issue.assignment.public_id), "resolution_note": issue.resolution_note},
    )
    if issue.reporter_driver_id and issue.reporter_driver.user_id:
        queue_commerce_notification(
            business=request.business,
            recipient_user=issue.reporter_driver.user,
            event_type=CommerceNotification.EVENT_DELIVERY_ISSUE,
            title=f"Delivery issue {issue.get_status_display().lower()} · {issue.assignment.intake.public_number}",
            message=issue.resolution_note or issue.get_status_display(),
            target_url="/delivery/rider/",
            dedupe_key=f"delivery-issue:{issue.pk}:{status}",
        )
    messages.success(request, "Rider issue updated.")
    return redirect("delivery_dashboard")


def _rider_profile(request):
    if not user_has_permission(request.user, request.business, "delivery_rider", "view"):
        return None
    return DeliveryDriver.raw_objects.filter(
        business=request.business, user=request.user, active=True, provider=DeliveryDriver.PROVIDER_INHOUSE
    ).first()




def _rider_active_batches(business, driver):
    if not driver:
        return []
    assignment_qs = (
        DeliveryAssignment.raw_objects.filter(business=business)
        .select_related("intake__accepted_order", "intake__split_order", "quote")
        .prefetch_related("intake__items__finished_good")
        .order_by("batch_stop_sequence", "created_at", "id")
    )
    batches = list(
        DeliveryBatch.raw_objects.filter(business=business, driver=driver)
        .exclude(status__in=[DeliveryBatch.STATUS_COMPLETED, DeliveryBatch.STATUS_CANCELLED])
        .prefetch_related(Prefetch("assignments", queryset=assignment_qs))
    )
    for batch in batches:
        batch_assignments = list(batch.assignments.all())
        _annotate_rider_readiness(batch_assignments)
        batch.waiting_made_to_order_count = sum(
            1 for assignment in batch_assignments if assignment.made_to_order_pending
        )
    return batches


@login_required
def delivery_rider_dashboard(request):
    driver = _rider_profile(request)
    assignments = []
    if driver:
        assignments = list(
            DeliveryAssignment.raw_objects.filter(business=request.business, driver=driver)
            .select_related(
                "intake", "intake__accepted_order", "intake__split_order",
                "quote", "origin", "business",
            )
            .prefetch_related("events", "issues", "messages", "intake__items__finished_good")
            .order_by("status", "-created_at")[:60]
        )
        _annotate_rider_readiness(assignments)
    active_statuses = {
        DeliveryAssignment.STATUS_ASSIGNED, DeliveryAssignment.STATUS_READY,
        DeliveryAssignment.STATUS_PICKED_UP,
        DeliveryAssignment.STATUS_FAILED,
    }
    rider_transitions = {
        DeliveryAssignment.STATUS_ASSIGNED: [DeliveryAssignment.STATUS_PICKED_UP],
        DeliveryAssignment.STATUS_READY: [DeliveryAssignment.STATUS_PICKED_UP],
        DeliveryAssignment.STATUS_PICKED_UP: [
            DeliveryAssignment.STATUS_DELIVERED, DeliveryAssignment.STATUS_FAILED, DeliveryAssignment.STATUS_RETURNED,
        ],
        DeliveryAssignment.STATUS_FAILED: [DeliveryAssignment.STATUS_RETURNED],
    }
    status_labels = dict(DeliveryAssignment.STATUS_CHOICES)
    for row in assignments:
        row.rider_actions = [(value, status_labels[value]) for value in rider_transitions.get(row.status, [])]
    return render(request, "commerce/delivery/rider_dashboard.html", {
        "driver": driver,
        "active_assignments": [row for row in assignments if row.status in active_statuses],
        "recent_assignments": [row for row in assignments if row.status not in active_statuses][:20],
        "issue_categories": DeliveryIssue.CATEGORY_CHOICES,
        "delivery_settings": DeliverySettings.raw_objects.filter(business=request.business).first(),
        "active_batches": _rider_active_batches(request.business, driver),
    })


@login_required
@require_POST
def delivery_rider_update(request, public_id):
    if not user_has_permission(request.user, request.business, "delivery_rider", "edit"):
        return render(request, "403.html", status=403)
    driver = _rider_profile(request)
    if not driver:
        return render(request, "403.html", status=403)
    assignment = get_object_or_404(
        DeliveryAssignment.raw_objects, business=request.business, public_id=public_id, driver=driver
    )
    requested_status = request.POST.get("status")
    allowed = {
        DeliveryAssignment.STATUS_PICKED_UP,
        DeliveryAssignment.STATUS_DELIVERED, DeliveryAssignment.STATUS_FAILED, DeliveryAssignment.STATUS_RETURNED,
    }
    if requested_status not in allowed:
        messages.error(request, "That delivery action is not available from the rider workspace.")
        return redirect("delivery_rider_dashboard")
    try:
        update_delivery_status(
            assignment=assignment, status=requested_status, actor=request.user,
            note=request.POST.get("note", ""), driver=driver,
            proof_note=request.POST.get("proof_note", ""),
            proof_reference=request.POST.get("proof_reference", ""),
        )
        messages.success(request, "Delivery status updated.")
    except ValidationError as exc:
        messages.error(request, _detail(exc))
    return redirect("delivery_rider_dashboard")


@login_required
@require_POST
def delivery_rider_issue(request, public_id):
    if not user_has_permission(request.user, request.business, "delivery_rider", "edit"):
        return render(request, "403.html", status=403)
    driver = _rider_profile(request)
    if not driver:
        return render(request, "403.html", status=403)
    assignment = get_object_or_404(
        DeliveryAssignment.raw_objects, business=request.business, public_id=public_id, driver=driver
    )
    try:
        raise_delivery_issue(
            assignment=assignment, driver=driver, category=request.POST.get("category", ""),
            details=request.POST.get("details", ""), actor=request.user,
        )
        messages.success(request, "Issue sent to dispatch staff.")
    except ValidationError as exc:
        messages.error(request, _detail(exc))
    return redirect("delivery_rider_dashboard")


@login_required
@require_POST
def delivery_rider_message(request, public_id):
    if not user_has_permission(request.user, request.business, "delivery_rider", "edit"):
        return render(request, "403.html", status=403)
    driver = _rider_profile(request)
    if not driver:
        return render(request, "403.html", status=403)
    assignment = get_object_or_404(
        DeliveryAssignment.raw_objects, business=request.business, public_id=public_id, driver=driver
    )
    try:
        add_delivery_message(
            assignment=assignment, sender_type="rider", body=request.POST.get("body", ""), actor=request.user
        )
        messages.success(request, "Message sent to the customer and dispatch.")
    except ValidationError as exc:
        messages.error(request, _detail(exc))
    return redirect("delivery_rider_dashboard")


@login_required
@require_POST
def delivery_rider_batch_route(request, public_id):
    driver = _rider_profile(request)
    if not driver:
        return render(request, "403.html", status=403)
    batch = get_object_or_404(DeliveryBatch.raw_objects, business=request.business, public_id=public_id, driver=driver)
    try:
        organise_delivery_batch(
            batch=batch,
            stops=_parse_route_stops(request.POST),
            actor=request.user,
            rider=driver,
        )
        messages.success(request, "Batch route saved. Pickup can now start the ETA countdowns.")
    except ValidationError as exc:
        messages.error(request, _detail(exc))
    return redirect("delivery_rider_dashboard")


@login_required
@require_POST
def delivery_rider_batch_pickup(request, public_id):
    driver = _rider_profile(request)
    if not driver:
        return render(request, "403.html", status=403)
    batch = get_object_or_404(DeliveryBatch.raw_objects, business=request.business, public_id=public_id, driver=driver)
    try:
        pickup_delivery_batch(batch=batch, actor=request.user, rider=driver)
        messages.success(request, "Batch picked up. Every customer ETA is now counting down from pickup.")
    except ValidationError as exc:
        messages.error(request, _detail(exc))
    return redirect("delivery_rider_dashboard")


def _location_payload(result):
    return {
        "address": result["address"],
        "latitude": str(result["latitude"]),
        "longitude": str(result["longitude"]),
        "validated_by": result["validated_by"],
        "location_source": result["validated_by"],
        "address_resolved": result["address_resolved"],
        "area_id": result["area_id"],
        "area_name": result["area_name"],
    }


@require_http_methods(["POST"])
def storefront_delivery_location(request, business_slug):
    business = get_object_or_404(Business, slug=business_slug)
    commerce_settings = CommerceSettings.raw_objects.filter(business=business).first()
    if not business_has_module(business, "commerce") or not commerce_settings or not commerce_settings.enabled or not delivery_available(business):
        return JsonResponse({"detail": "Delivery location lookup is unavailable."}, status=404)
    try:
        result = resolve_delivery_location(
            business=business,
            address=request.POST.get("address", ""),
            area_id=request.POST.get("area_id") or None,
            latitude=request.POST.get("latitude") or None,
            longitude=request.POST.get("longitude") or None,
        )
        return JsonResponse(_location_payload(result))
    except (ValidationError, InvalidOperation, TypeError, ValueError) as exc:
        return JsonResponse({"detail": _detail(exc)}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def api_delivery_location(request, business_slug):
    business = get_object_or_404(Business, slug=business_slug)
    commerce_settings = CommerceSettings.raw_objects.filter(business=business).first()
    if not business_has_module(business, "commerce") or not commerce_settings or not commerce_settings.enabled or not commerce_settings.api_enabled or not delivery_available(business):
        return JsonResponse({"detail": "Delivery location API unavailable."}, status=404)
    key = request.headers.get("X-INPROFIC-Key", "")
    if not CommerceIntegration.raw_objects.filter(
        business=business, active=True, integration_type=CommerceIntegration.TYPE_API, api_key=key
    ).exists():
        return JsonResponse({"detail": "Invalid commerce API credential."}, status=403)
    try:
        data = json.loads(request.body or b"{}")
        result = resolve_delivery_location(
            business=business,
            address=data.get("address", ""),
            area_id=data.get("area_id"),
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
        )
        return JsonResponse(_location_payload(result))
    except (json.JSONDecodeError, ValidationError, InvalidOperation, TypeError, ValueError) as exc:
        return JsonResponse({"detail": _detail(exc)}, status=400)


@require_http_methods(["POST"])
def storefront_delivery_quote(request, business_slug):
    business = get_object_or_404(Business, slug=business_slug)
    commerce_settings = CommerceSettings.raw_objects.filter(business=business).first()
    if not business_has_module(business, "commerce") or not commerce_settings or not commerce_settings.enabled or not delivery_available(business):
        return JsonResponse({"detail": "Delivery quoting is unavailable."}, status=404)
    try:
        settings, options = create_delivery_quote_options(
            business=business,
            subtotal=request.POST.get("subtotal"),
            destination_address=request.POST.get("address", ""),
            area_id=request.POST.get("area_id") or None,
            latitude=request.POST.get("latitude") or None,
            longitude=request.POST.get("longitude") or None,
            location_source=request.POST.get("location_source") or None,
        )
        selected = select_delivery_quote(settings, options)
        return JsonResponse({
            "quote_id": str(selected.public_id) if selected else None,
            "selection_required": selected is None,
            "routing_policy": settings.hybrid_routing_policy if settings.default_provider == DeliverySettings.PROVIDER_HYBRID else None,
            "switch_policy": settings.hybrid_switch_policy if settings.default_provider == DeliverySettings.PROVIDER_HYBRID else None,
            "switch_policy_text": settings.customer_switch_policy_text if settings.default_provider == DeliverySettings.PROVIDER_HYBRID else "",
            "options": [serialize_delivery_quote(row) for row in options],
            **(serialize_delivery_quote(selected) if selected else {}),
        })
    except (ValidationError, InvalidOperation, TypeError, ValueError) as exc:
        return JsonResponse({"detail": _detail(exc)}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def api_delivery_quote(request, business_slug):
    business = get_object_or_404(Business, slug=business_slug)
    commerce_settings = CommerceSettings.raw_objects.filter(business=business).first()
    if not business_has_module(business, "commerce") or not commerce_settings or not commerce_settings.enabled or not commerce_settings.api_enabled or not delivery_available(business):
        return JsonResponse({"detail": "Delivery API unavailable."}, status=404)
    key = request.headers.get("X-INPROFIC-Key", "")
    if not CommerceIntegration.raw_objects.filter(
        business=business, active=True, integration_type=CommerceIntegration.TYPE_API, api_key=key
    ).exists():
        return JsonResponse({"detail": "Invalid commerce API credential."}, status=403)
    try:
        data = json.loads(request.body or b"{}")
        settings, options = create_delivery_quote_options(
            business=business, subtotal=data.get("subtotal"), destination_address=data.get("address", ""),
            area_id=data.get("area_id"), latitude=data.get("latitude"), longitude=data.get("longitude"),
            location_source=data.get("location_source"),
        )
        selected = select_delivery_quote(settings, options)
        return JsonResponse({
            "quote_id": str(selected.public_id) if selected else None,
            "selection_required": selected is None,
            "routing_policy": settings.hybrid_routing_policy if settings.default_provider == DeliverySettings.PROVIDER_HYBRID else None,
            "switch_policy": settings.hybrid_switch_policy if settings.default_provider == DeliverySettings.PROVIDER_HYBRID else None,
            "switch_policy_text": settings.customer_switch_policy_text if settings.default_provider == DeliverySettings.PROVIDER_HYBRID else "",
            "options": [serialize_delivery_quote(row) for row in options],
            **(serialize_delivery_quote(selected) if selected else {}),
        }, status=201)
    except (json.JSONDecodeError, ValidationError, InvalidOperation, TypeError, ValueError) as exc:
        return JsonResponse({"detail": _detail(exc)}, status=400)


def _tracking_assignment(business, public_id):
    return get_object_or_404(
        DeliveryAssignment.raw_objects.select_related("intake", "driver", "origin", "quote", "provider_account").prefetch_related("events"),
        business=business, public_id=public_id,
    )


def _delivery_assignment_ref(business, public_id):
    """Lean assignment load for customer mutations that do not render the timeline."""
    return get_object_or_404(
        DeliveryAssignment.raw_objects.select_related("intake", "driver"),
        business=business, public_id=public_id,
    )


@require_POST
def storefront_delivery_message(request, business_slug, public_id):
    business = get_object_or_404(Business, slug=business_slug)
    assignment = _delivery_assignment_ref(business, public_id)
    try:
        add_delivery_message(
            assignment=assignment, sender_type="customer", body=request.POST.get("body", ""), actor=None
        )
        return JsonResponse(_tracking_payload(business, _tracking_assignment(business, public_id)))
    except ValidationError as exc:
        return JsonResponse({"detail": _detail(exc)}, status=400)


@require_POST
def storefront_delivery_report(request, business_slug, public_id):
    business = get_object_or_404(Business, slug=business_slug)
    assignment = _delivery_assignment_ref(business, public_id)
    active = {DeliveryAssignment.STATUS_ASSIGNED, DeliveryAssignment.STATUS_READY, DeliveryAssignment.STATUS_PICKED_UP, DeliveryAssignment.STATUS_FAILED}
    if assignment.status not in active:
        return JsonResponse({"detail": "Delivery reporting is available only while the delivery is active."}, status=400)
    details = (request.POST.get("details") or "").strip()
    if len(details) < 5:
        return JsonResponse({"detail": "Describe the delivery problem so staff can act on it."}, status=400)
    issue = DeliveryIssue.raw_objects.create(
        business=business, assignment=assignment, reporter_driver=None,
        category=request.POST.get("category") if request.POST.get("category") in {v for v, _ in DeliveryIssue.CATEGORY_CHOICES} else DeliveryIssue.CATEGORY_OTHER,
        details=details,
    )
    queue_commerce_notification(
        business=business, event_type=CommerceNotification.EVENT_DELIVERY_ISSUE,
        title=f"Customer delivery report · {assignment.intake.public_number}",
        message=details[:220], target_url="/delivery/", dedupe_key=f"customer-delivery-issue:{issue.pk}",
    )
    audit(business, None, "delivery_customer_report", issue, f"Customer reported active delivery {assignment.intake.public_number}")
    return JsonResponse({"ok": True, "issue_id": issue.pk})


def _tracking_payload(business, assignment):
    payload = serialize_delivery_tracking(assignment)
    payload["realtime"] = {
        "websocket_path": f"/ws/storefront/{business.slug}/deliveries/{assignment.public_id}/",
        "public_status_path": f"/shop/{business.slug}/deliveries/{assignment.public_id}/status/",
        "event_type": "delivery.changed",
        "fallback_poll_seconds": 10,
    }
    return payload


@require_http_methods(["GET"])
def storefront_delivery_tracking(request, business_slug, public_id):
    business = get_object_or_404(Business, slug=business_slug)
    settings = DeliverySettings.raw_objects.filter(business=business, enabled=True, customer_tracking_enabled=True).first()
    if not settings or not business_has_module(business, "delivery"):
        return render(request, "404.html", status=404)
    assignment = _tracking_assignment(business, public_id)
    commerce_settings = getattr(business, "commerce_settings", None) or CommerceSettings.raw_objects.filter(business=business).first()
    return render(request, "commerce/delivery/tracking.html", {
        "store_business": business,
        "assignment": assignment,
        "tracking_payload": _tracking_payload(business, assignment),
        "commerce_settings": commerce_settings,
        "storefront_copy": vertical_config(business)["storefront"],
    })


@require_http_methods(["GET"])
def storefront_delivery_status(request, business_slug, public_id):
    """Customer-safe status snapshot for the unguessable hosted tracking URL."""
    business = get_object_or_404(Business, slug=business_slug)
    settings = DeliverySettings.raw_objects.filter(business=business, enabled=True, customer_tracking_enabled=True).first()
    if not settings or not business_has_module(business, "delivery"):
        return JsonResponse({"detail": "Delivery tracking is unavailable."}, status=404)
    assignment = _tracking_assignment(business, public_id)
    return JsonResponse(_tracking_payload(business, assignment))


@csrf_exempt
@require_http_methods(["GET"])
def api_delivery_tracking(request, business_slug, public_id):
    """Headless delivery tracking snapshot; API key stays on the website server."""
    business = get_object_or_404(Business, slug=business_slug)
    commerce_settings = CommerceSettings.raw_objects.filter(business=business).first()
    delivery_settings = DeliverySettings.raw_objects.filter(
        business=business, enabled=True, customer_tracking_enabled=True
    ).first()
    if (
        not business_has_module(business, "commerce")
        or not business_has_module(business, "delivery")
        or not commerce_settings
        or not commerce_settings.enabled
        or not commerce_settings.api_enabled
        or not delivery_settings
    ):
        return JsonResponse({"detail": "Delivery tracking API unavailable."}, status=404)
    key = request.headers.get("X-INPROFIC-Key", "")
    if not CommerceIntegration.raw_objects.filter(
        business=business, active=True, integration_type=CommerceIntegration.TYPE_API, api_key=key
    ).exists():
        return JsonResponse({"detail": "Invalid commerce API credential."}, status=403)
    assignment = _tracking_assignment(business, public_id)
    payload = _tracking_payload(business, assignment)
    payload["realtime"]["api_status_path"] = f"/api/v1/storefronts/{business.slug}/deliveries/{assignment.public_id}/tracking"
    return JsonResponse(payload)


@csrf_exempt
@require_http_methods(["POST"])
def api_delivery_message(request, business_slug, public_id):
    business = get_object_or_404(Business, slug=business_slug)
    commerce_settings = CommerceSettings.raw_objects.filter(business=business).first()
    key = request.headers.get("X-INPROFIC-Key", "")
    if not commerce_settings or not commerce_settings.enabled or not commerce_settings.api_enabled or not CommerceIntegration.raw_objects.filter(
        business=business, active=True, integration_type=CommerceIntegration.TYPE_API, api_key=key
    ).exists():
        return JsonResponse({"detail": "Invalid commerce API credential."}, status=403)
    assignment = _delivery_assignment_ref(business, public_id)
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
        add_delivery_message(assignment=assignment, sender_type="customer", body=data.get("body", ""), actor=None)
        return JsonResponse(_tracking_payload(business, _tracking_assignment(business, public_id)))
    except (json.JSONDecodeError, ValidationError) as exc:
        return JsonResponse({"detail": _detail(exc) if isinstance(exc, ValidationError) else "Invalid JSON."}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def api_delivery_report(request, business_slug, public_id):
    business = get_object_or_404(Business, slug=business_slug)
    commerce_settings = CommerceSettings.raw_objects.filter(business=business).first()
    key = request.headers.get("X-INPROFIC-Key", "")
    if not commerce_settings or not commerce_settings.enabled or not commerce_settings.api_enabled or not CommerceIntegration.raw_objects.filter(
        business=business, active=True, integration_type=CommerceIntegration.TYPE_API, api_key=key
    ).exists():
        return JsonResponse({"detail": "Invalid commerce API credential."}, status=403)
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON."}, status=400)
    assignment = _delivery_assignment_ref(business, public_id)
    active = {DeliveryAssignment.STATUS_ASSIGNED, DeliveryAssignment.STATUS_READY, DeliveryAssignment.STATUS_PICKED_UP, DeliveryAssignment.STATUS_FAILED}
    if assignment.status not in active:
        return JsonResponse({"detail": "Delivery reporting is available only while the delivery is active."}, status=400)
    details = (data.get("details") or "").strip()
    if len(details) < 5:
        return JsonResponse({"detail": "Describe the delivery problem so staff can act on it."}, status=400)
    category = data.get("category") if data.get("category") in {v for v, _ in DeliveryIssue.CATEGORY_CHOICES} else DeliveryIssue.CATEGORY_OTHER
    issue = DeliveryIssue.raw_objects.create(business=business, assignment=assignment, reporter_driver=None, category=category, details=details)
    queue_commerce_notification(business=business, event_type=CommerceNotification.EVENT_DELIVERY_ISSUE, title=f"Customer delivery report · {assignment.intake.public_number}", message=details[:220], target_url="/delivery/", dedupe_key=f"customer-delivery-issue:{issue.pk}")
    return JsonResponse({"ok": True, "issue_id": issue.pk})


@csrf_exempt
@require_http_methods(["POST"])
def generic_delivery_provider_webhook(request, business_slug, provider_id):
    business = get_object_or_404(Business, slug=business_slug)
    # Provider callbacks remain data-continuity writes for deliveries that
    # already exist. A plan may hide Delivery surfaces, but it must not leave
    # historical assignments stale underneath if their external partner is
    # still reporting status.
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"detail": "Invalid JSON."}, status=400)
    secret = request.headers.get("Authorization") or request.headers.get("X-INPROFIC-Delivery-Webhook-Secret") or ""
    try:
        from .delivery_providers import consume_generic_provider_webhook
        assignment = consume_generic_provider_webhook(
            business=business, account_id=provider_id, payload=data, header_secret=secret
        )
    except ValidationError as exc:
        return JsonResponse({"detail": _detail(exc)}, status=403)
    return JsonResponse({"ok": True, "delivery_id": str(assignment.public_id) if assignment else None})


@csrf_exempt
@require_http_methods(["POST"])
def glovo_delivery_webhook(request, business_slug):
    if not glovo_platform_enabled():
        return JsonResponse({"detail": "Delivery provider integration is unavailable."}, status=404)
    business = get_object_or_404(Business, slug=business_slug)
    # Founder availability is the platform kill switch. Plan visibility is not:
    # keep already-created delivery records synchronized underneath so a later
    # plan upgrade reveals complete history rather than a status gap.
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON payload."}, status=400)
    secret = request.headers.get("Authorization") or request.headers.get("X-INPROFIC-Delivery-Webhook-Secret") or request.headers.get("X-Glovo-Webhook-Secret") or ""
    try:
        from .delivery_providers import consume_glovo_webhook
        assignment = consume_glovo_webhook(business=business, payload=data, header_secret=secret)
    except ValidationError as exc:
        return JsonResponse({"detail": _detail(exc)}, status=403)
    if not assignment:
        return JsonResponse({"detail": "No matching delivery assignment."}, status=202)
    return JsonResponse({"ok": True, "delivery_id": str(assignment.public_id), "status": assignment.status})
