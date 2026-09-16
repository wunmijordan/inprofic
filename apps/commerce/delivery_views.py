import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from accounts.models import CustomUser, UserBusiness
from accounts.services import business_has_module, is_business_admin, user_has_permission
from core.models import Business
from core.verticals import vertical_config
from core.services import audit

from .delivery_forms import DeliveryAreaForm, DeliveryDriverForm, DeliveryOriginForm, DeliveryProviderAccountForm, DeliveryRateBandForm, DeliverySettingsForm
from .delivery_services import (
    create_delivery_quote_options, delivery_available, raise_delivery_issue,
    select_delivery_quote, serialize_delivery_quote, switch_delivery_method, update_delivery_status,
)
from .notification_services import queue_commerce_notification
from .models import (
    CommerceIntegration, CommerceNotification, CommerceSettings,
    DeliveryArea,
    DeliveryAssignment,
    DeliveryDriver,
    DeliveryEvent,
    DeliveryIssue,
    DeliveryOrigin,
    DeliveryProviderAccount,
    DeliveryRateBand,
    DeliverySettings,
)


def _detail(exc):
    return "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


def _can_approve_delivery_switch(user, business):
    if getattr(user, "is_superuser", False) or is_business_admin(user, business):
        return True
    membership = UserBusiness.objects.filter(user=user, business=business, active=True).select_related("role").first()
    return bool(membership and membership.role.key in {
        CustomUser.ROLE_MANAGER, CustomUser.ROLE_MD_DIRECTOR, CustomUser.ROLE_BUSINESS_ADMIN,
    })


def _settings(business):
    # Provider-neutral built-ins must also exist for businesses created after
    # the data migration that seeded existing tenants.
    from .delivery_providers import ensure_builtin_provider_accounts
    ensure_builtin_provider_accounts(business)
    obj, _ = DeliverySettings.objects.get_or_create(business=business, defaults={"created_by": None})
    return obj


@login_required
def delivery_dashboard(request):
    settings = _settings(request.business)
    assignments = DeliveryAssignment.objects.select_related(
        "intake", "driver__user", "origin", "quote", "provider_account"
    ).prefetch_related("events", "issues")[:100]
    origins = DeliveryOrigin.objects.filter(business=request.business)
    rate_bands = DeliveryRateBand.objects.filter(business=request.business)
    areas = DeliveryArea.objects.filter(business=request.business).select_related("rate_band")
    drivers = DeliveryDriver.objects.filter(business=request.business).select_related("user")
    provider_accounts = DeliveryProviderAccount.objects.filter(business=request.business)
    base_ready = origins.filter(
        active=True, latitude__isnull=False, longitude__isnull=False
    ).exists()
    pricing_ready = rate_bands.filter(active=True).exists()
    provider_ready = True
    if settings.default_provider == DeliverySettings.PROVIDER_THIRD_PARTY:
        account = settings.default_provider_account
        provider_ready = bool(
            account and account.active and (
                account.provider_code != DeliveryProviderAccount.PROVIDER_GLOVO
                or account.is_configured_for_quote
            )
        )
    public_ready = bool(settings.enabled and base_ready and pricing_ready and provider_ready)
    return render(request, "commerce/delivery/dashboard.html", {
        "delivery_settings": settings,
        "assignments": assignments,
        "origins": origins,
        "rate_bands": rate_bands,
        "areas": areas,
        "drivers": drivers,
        "provider_accounts": provider_accounts,
        "delivery_setup": {
            "base_ready": base_ready,
            "pricing_ready": pricing_ready,
            "destinations_ready": areas.filter(active=True).exists(),
            "dispatch_ready": drivers.filter(active=True).exists() or provider_accounts.filter(active=True).exists(),
            "provider_ready": provider_ready,
            "public_ready": public_ready,
        },
        "can_manage_delivery": is_business_admin(request.user, request.business),
        "can_update_delivery": user_has_permission(request.user, request.business, "delivery", "edit"),
        "glovo_webhook_path": f"/api/v1/delivery/providers/glovo/{request.business.slug}/webhook",
        "status_choices": DeliveryAssignment.STATUS_CHOICES,
        "issue_status_choices": DeliveryIssue.STATUS_CHOICES,
        "can_approve_delivery_switch": _can_approve_delivery_switch(request.user, request.business),
    })


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
    intro="", setup_tip="",
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
    }
    if include_user_directory:
        context["delivery_user_directory"] = list(
            form.fields["user"].queryset.values("id", "fullname", "username", "phone", "email")
        )
    return render(request, "commerce/delivery/object_form.html", context)


@login_required
def delivery_provider_account_form(request, pk=None):
    return _model_form_view(
        request, model=DeliveryProviderAccount, form_class=DeliveryProviderAccountForm,
        title="Delivery provider account", success="Delivery provider account saved.", pk=pk,
        intro="Connect a courier plug-in or keep a manual provider record. Credentials stay tenant-scoped and are never exposed to storefront customers.",
        setup_tip="For Glovo, save the issued LaaS credentials and Address Book pickup ID, then return to Delivery and register the tenant webhook. Keep Sandbox on until a complete test quote and dispatch succeeds.",
    )


@login_required
@require_POST
def delivery_provider_register_glovo_webhooks(request, pk):
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
        intro="Define what customers pay and the ETA they see for a distance range. INPROFIC calculates distance from the selected delivery base.",
        setup_tip="Create non-overlapping bands from nearest to farthest. A fee is calculated as base fee plus the per-kilometre fee; use zero per-kilometre for a flat rate.",
    )


@login_required
def delivery_area_form(request, pk=None):
    return _model_form_view(
        request, model=DeliveryArea, form_class=DeliveryAreaForm,
        title="Delivery destination / zone", success="Delivery destination saved.",
        pk=pk, business_kw=True, location_label="delivery destination centre",
        intro="Give customers and counter staff a familiar destination choice, then link it to the price band that governs its fee and ETA.",
        setup_tip="The zone pin is a convenient default. Customers can place a more precise destination pin during checkout, while the selected zone still controls its configured pricing band.",
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


@login_required
@require_POST
def delivery_assignment_update(request, public_id):
    if not user_has_permission(request.user, request.business, "delivery", "edit"):
        return render(request, "403.html", status=403)
    assignment = get_object_or_404(DeliveryAssignment, public_id=public_id, business=request.business)
    driver = None
    driver_id = request.POST.get("driver_id")
    if driver_id:
        driver = get_object_or_404(DeliveryDriver, pk=driver_id, business=request.business, active=True)
    try:
        requested_status = request.POST.get("status")
        if (
            requested_status == DeliveryAssignment.STATUS_CANCELLED
            and assignment.provider_account_id
            and assignment.provider_account.provider_code == DeliveryProviderAccount.PROVIDER_GLOVO
            and assignment.provider_order_id
        ):
            from .delivery_providers import cancel_assignment_with_provider
            cancel_assignment_with_provider(assignment, actor=request.user)
        update_delivery_status(
            assignment=assignment,
            status=requested_status,
            actor=request.user,
            note=request.POST.get("note", ""),
            driver=driver,
            clear_driver=("driver_id" in request.POST and not driver_id),
            proof_note=request.POST.get("proof_note", ""),
            proof_reference=request.POST.get("proof_reference", ""),
            external_reference=request.POST.get("external_reference", ""),
            external_tracking_url=request.POST.get("external_tracking_url", ""),
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


@login_required
def delivery_rider_dashboard(request):
    driver = _rider_profile(request)
    assignments = []
    if driver:
        assignments = list(
            DeliveryAssignment.raw_objects.filter(business=request.business, driver=driver)
            .select_related("intake", "quote", "origin", "business")
            .prefetch_related("events", "issues", "intake__items__finished_good")
            .order_by("status", "-created_at")[:60]
        )
    active_statuses = {
        DeliveryAssignment.STATUS_ASSIGNED, DeliveryAssignment.STATUS_READY,
        DeliveryAssignment.STATUS_PICKED_UP, DeliveryAssignment.STATUS_OUT_FOR_DELIVERY,
        DeliveryAssignment.STATUS_FAILED,
    }
    rider_transitions = {
        DeliveryAssignment.STATUS_ASSIGNED: [DeliveryAssignment.STATUS_PICKED_UP],
        DeliveryAssignment.STATUS_READY: [DeliveryAssignment.STATUS_PICKED_UP],
        DeliveryAssignment.STATUS_PICKED_UP: [
            DeliveryAssignment.STATUS_OUT_FOR_DELIVERY, DeliveryAssignment.STATUS_FAILED, DeliveryAssignment.STATUS_RETURNED,
        ],
        DeliveryAssignment.STATUS_OUT_FOR_DELIVERY: [
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
        DeliveryAssignment.STATUS_PICKED_UP, DeliveryAssignment.STATUS_OUT_FOR_DELIVERY,
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


@require_http_methods(["GET"])
def storefront_delivery_tracking(request, business_slug, public_id):
    business = get_object_or_404(Business, slug=business_slug)
    settings = DeliverySettings.raw_objects.filter(business=business, enabled=True, customer_tracking_enabled=True).first()
    if not settings or not business_has_module(business, "delivery"):
        return render(request, "404.html", status=404)
    assignment = get_object_or_404(
        DeliveryAssignment.raw_objects.select_related("intake", "driver", "origin").prefetch_related("events"),
        business=business, public_id=public_id,
    )
    commerce_settings = getattr(business, "commerce_settings", None) or CommerceSettings.raw_objects.filter(business=business).first()
    return render(request, "commerce/delivery/tracking.html", {
        "store_business": business,
        "assignment": assignment,
        "commerce_settings": commerce_settings,
        "storefront_copy": vertical_config(business)["storefront"],
    })


@csrf_exempt
@require_http_methods(["POST"])
def glovo_delivery_webhook(request, business_slug):
    business = get_object_or_404(Business, slug=business_slug)
    if not business_has_module(business, "delivery"):
        return JsonResponse({"detail": "Delivery is not available."}, status=404)
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
