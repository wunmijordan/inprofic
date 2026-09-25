from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.text import slugify
from core.models import Business
from .forms import BusinessSignupForm, UserForm, PermissionMatrixForm, RoleForm, RolePermissionForm
from .models import BusinessModuleAccess, CustomUser, Role, RoleModulePermission, UserBusiness, UserModulePermission
from .services import ensure_permissions, is_business_admin, seed_business_modules, seed_business_roles, user_has_permission


def _unique_business_slug(name):
    base = (slugify(name) or "business")[:52]
    candidate = base
    number = 2
    while Business.objects.filter(slug=candidate).exists():
        suffix = f"-{number}"
        candidate = f"{base[:60 - len(suffix)]}{suffix}"
        number += 1
    return candidate


def signup(request):
    """Create a tenant and its first Business Admin atomically."""
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        form = BusinessSignupForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                business = Business.objects.create(
                    name=form.cleaned_data["business_name"].strip(),
                    slug=_unique_business_slug(form.cleaned_data["business_name"]),
                    vertical=form.cleaned_data["vertical"],
                )
                roles = seed_business_roles(business)
                seed_business_modules(business, source=BusinessModuleAccess.SOURCE_DEFAULT)
                user = CustomUser.objects.create_user(
                    username=form.cleaned_data["username"],
                    password=form.cleaned_data["password1"],
                    fullname=form.cleaned_data["fullname"].strip(),
                    email=form.cleaned_data["email"],
                    phone=form.cleaned_data["phone"].strip(),
                )
                membership = UserBusiness.objects.create(
                    user=user,
                    business=business,
                    role=roles[CustomUser.ROLE_BUSINESS_ADMIN],
                )
                ensure_permissions(membership)
                from .models import BusinessTrialIdentity
                from .forms import _trial_name_key, _trial_phone_key
                BusinessTrialIdentity.objects.create(
                    business=business,
                    email_key=(user.email or "").strip().casefold(),
                    phone_key=_trial_phone_key(user.phone),
                    business_name_key=_trial_name_key(business.name),
                )
                from .subscription_services import start_trial_for_business
                subscription = start_trial_for_business(business)
            auth_login(request, user)
            request.session["active_business_id"] = business.pk
            from .analytics import record_founder_signup_contact, record_platform_event
            from .models import PlatformEvent
            registration_event = record_platform_event(
                PlatformEvent.EVENT_REGISTRATION, request=request, user=user, business=business,
                metadata={
                    "vertical": business.vertical,
                    "signup_email": user.email,
                    "signup_name": user.fullname,
                    "business_name": business.name,
                },
            )
            record_founder_signup_contact(
                business=business, user=user, email=user.email, name=user.fullname,
                signed_up_at=getattr(registration_event, "occurred_at", None),
            )
            if registration_event is not None:
                from .realtime import publish_founder_signup_changed
                publish_founder_signup_changed(registration_event.pk)
            from .emails import send_signup_welcome_email
            workspace_url = request.build_absolute_uri(reverse("dashboard"))
            transaction.on_commit(
                lambda: send_signup_welcome_email(
                    user=user, business=business, subscription=subscription, workspace_url=workspace_url,
                )
            )
            messages.success(request, f"Welcome to {business.name}. Your Business Admin account is ready.")
            return redirect("dashboard")
    else:
        form = BusinessSignupForm()
    return render(request, "accounts/signup.html", {"form": form})


def can_manage(request):
    # User administration is deliberately reserved for the global superuser or
    # the Business Admin of the current business. A custom role with Users/Edit
    # cannot escalate itself into user administration.
    return request.user.is_superuser or is_business_admin(request.user, request.business)


def can_manage_roles(request):
    return request.user.is_superuser or is_business_admin(request.user, request.business)


@login_required
def users_list(request):
    if not can_manage(request):
        return render(request, "403.html", status=403)
    seed_business_roles(request.business)
    memberships = (
        UserBusiness.objects
        .filter(business=request.business, active=True)
        .select_related("user", "role")
        .order_by("user__fullname")
    )
    roles = Role.objects.filter(business=request.business, active=True)

    # A global superuser can audit every membership and role in the selected
    # business. Business Admins are intentionally shielded from global
    # superusers and roles marked visible_to_admin=False.
    if not request.user.is_superuser:
        memberships = memberships.filter(
            role__visible_to_admin=True,
            user__is_superuser=False,
        )
        roles = roles.filter(visible_to_admin=True)

    roles = roles.order_by("is_system", "name")
    return render(request, "accounts/users_list.html", {"memberships": memberships, "roles": roles, "can_manage_roles": can_manage_roles(request)})


@login_required
def user_form(request, pk=None):
    if not can_manage(request):
        return render(request, "403.html", status=403)
    membership = (
        get_object_or_404(
            UserBusiness.objects.select_related("user", "role"),
            user_id=pk,
            business=request.business,
        )
        if pk else None
    )
    if membership and not membership.role.visible_to_admin and not request.user.is_superuser:
        return render(request, "403.html", status=403)
    obj = membership.user if membership else None
    if request.method == "POST":
        form = UserForm(request.POST, instance=obj, business=request.business, actor=request.user, membership=membership)
        if form.is_valid():
            try:
                was_active_member = bool(membership and membership.active and obj and obj.is_active)
                if form.cleaned_data.get("is_active") and not was_active_member:
                    from .subscription_services import assert_user_capacity
                    assert_user_capacity(request.business, user=obj)
                with transaction.atomic():
                    user = form.save()
                    role = form.cleaned_data["role"]
                    membership, _ = UserBusiness.objects.get_or_create(user=user, business=request.business, defaults={"role": role, "active": user.is_active})
                    membership.role = role
                    membership.active = user.is_active
                    membership.save(update_fields=["role", "active"])
                    ensure_permissions(membership)
                messages.success(request, "User updated." if obj else "User created.")
                return redirect("users_list")
            except ValidationError as exc:
                form.add_error(None, exc)
    else:
        form = UserForm(instance=obj, business=request.business, actor=request.user, membership=membership)
    return render(request, "accounts/user_form.html", {"form": form, "obj": obj})


@login_required
def user_permissions(request, pk):
    if not can_manage(request):
        return render(request, "403.html", status=403)
    membership = get_object_or_404(UserBusiness.objects.select_related("user", "role"), pk=pk, business=request.business)
    if membership.role.key == CustomUser.ROLE_LIVE_TESTER:
        return render(request, "403.html", {"live_tester_policy_fixed": True}, status=403)
    if request.method == "POST":
        form = PermissionMatrixForm(request.POST, membership=membership)
        if form.is_valid():
            form.save()
            messages.success(request, f"Permissions updated for {membership.user.fullname}.")
            return redirect("users_permissions", pk=membership.pk)
    else:
        form = PermissionMatrixForm(membership=membership)
    rows = [(m, label, form[f"{m}_view"], form[f"{m}_edit"]) for m, label in RoleModulePermission.MODULE_CHOICES]
    return render(request, "accounts/user_permissions.html", {"membership": membership, "form": form, "rows": rows})


@login_required
def roles_list(request):
    if not can_manage_roles(request):
        return render(request, "403.html", status=403)
    seed_business_roles(request.business)
    roles = Role.objects.filter(business=request.business)
    if not request.user.is_superuser:
        roles = roles.exclude(visible_to_admin=False)
    roles = roles.prefetch_related("module_permissions")
    return render(request, "accounts/roles_list.html", {"roles": roles})


@login_required
def role_form(request, pk=None):
    if not can_manage_roles(request):
        return render(request, "403.html", status=403)
    obj = get_object_or_404(Role, pk=pk, business=request.business) if pk else None
    if obj and obj.key == CustomUser.ROLE_LIVE_TESTER:
        return render(request, "403.html", {"live_tester_policy_fixed": True}, status=403)
    if obj and obj.is_system and request.user.is_superuser is False and obj.key in (CustomUser.ROLE_BUSINESS_ADMIN, CustomUser.ROLE_SUPERUSER):
        return render(request, "403.html", status=403)
    # A role the superuser hid from admins (e.g. a demo/review role) is off
    # limits to anyone else, even by guessing its edit URL directly.
    if obj and not obj.visible_to_admin and not request.user.is_superuser:
        return render(request, "403.html", status=403)
    if request.method == "POST":
        form = RoleForm(request.POST, instance=obj, actor=request.user)
        if form.is_valid():
            role = form.save(commit=False)
            if not role.pk:
                base = slugify(role.name) or "custom-role"
                key = base
                n = 2
                while Role.objects.filter(business=request.business, key=key).exists():
                    key = f"{base}-{n}"; n += 1
                role.key = key
                role.business = request.business
                role.is_system = False
            role.save()
            for module, _ in RoleModulePermission.MODULE_CHOICES:
                RoleModulePermission.objects.get_or_create(role=role, module=module)
            messages.success(request, "Role created." if not obj else "Role updated.")
            return redirect("roles_list")
    else:
        form = RoleForm(instance=obj, actor=request.user)
    return render(request, "accounts/role_form.html", {"form": form, "obj": obj})


@login_required
def role_permissions(request, pk):
    if not can_manage_roles(request):
        return render(request, "403.html", status=403)
    role = get_object_or_404(Role, pk=pk, business=request.business)
    if role.key == CustomUser.ROLE_LIVE_TESTER:
        return render(request, "403.html", {"live_tester_policy_fixed": True}, status=403)
    if not role.visible_to_admin and not request.user.is_superuser:
        return render(request, "403.html", status=403)
    if request.method == "POST":
        form = RolePermissionForm(request.POST, role=role)
        if form.is_valid():
            form.save()
            messages.success(request, f"Permissions updated for {role.name}.")
            return redirect("role_permissions", pk=role.pk)
    else:
        form = RolePermissionForm(role=role)
    rows = [(m, label, form[f"{m}_view"], form[f"{m}_edit"]) for m, label in RoleModulePermission.MODULE_CHOICES]
    return render(request, "accounts/role_permissions.html", {"role": role, "form": form, "rows": rows})


@login_required
def subscription_plans(request):
    if not is_business_admin(request.user, request.business):
        return render(request, "403.html", status=403)
    from .models import BusinessSubscription, SubscriptionPlan
    from .subscription_services import attach_active_promotions, build_plan_feature_matrix, ensure_default_plans, paid_trial_available, payment_is_locked
    ensure_default_plans()
    service = getattr(request.business, "subscription_service", None)
    subscription = service.subscription if service else BusinessSubscription.objects.filter(primary_business=request.business).select_related("plan").first()
    if not subscription:
        # Existing businesses are not silently downgraded; they may choose a plan from this page.
        subscription = None
    plans = attach_active_promotions(
        SubscriptionPlan.objects.filter(active=True).prefetch_related("module_entitlements").order_by("monthly_price", "id")
    )
    plan_cards = [
        {
            "plan": plan,
            "is_current": bool(subscription and subscription.is_effectively_active and subscription.plan_id == plan.pk),
            "payment_locked": payment_is_locked(subscription, plan),
            "requires_change_warning": bool(
                subscription and subscription.is_effectively_active and subscription.plan_id != plan.pk
            ),
        }
        for plan in plans
    ]
    trial_credentials_available = paid_trial_available(request.user)
    can_start_paid_trial = trial_credentials_available and bool(
        not subscription or (
            subscription.status == BusinessSubscription.STATUS_ACTIVE
            and subscription.plan.is_free_forever
        )
    )
    return render(request, "accounts/subscription_plans.html", {
        "subscription": subscription,
        "plans": plans,
        "plan_cards": plan_cards,
        "plan_feature_matrix": build_plan_feature_matrix(plans),
        "paid_trial_available": can_start_paid_trial,
        "can_add_service": bool(
            subscription and (
                subscription.plan.additional_service_limit is None
                or subscription.services.filter(is_primary=False).count() < subscription.plan.additional_service_limit
            )
        ),
    })


@login_required
def subscription_payment(request, plan_code=None):
    from .models import BusinessSubscription, SubscriptionPayment, SubscriptionPaymentSettings, SubscriptionPlan, SubscriptionPolicySettings
    from .payment_gateways import GatewayError, initialize_gateway
    from .subscription_services import (
        active_promotion_for_plan,
        create_payment_request,
        ensure_default_plans,
        payment_amount,
        payment_is_locked,
        cancel_paid_plan_trial,
        paid_trial_available,
        start_paid_plan_trial,
        start_trial_for_business,
    )
    if not is_business_admin(request.user, request.business):
        return render(request, "403.html", status=403)
    plans = ensure_default_plans()
    selected = get_object_or_404(SubscriptionPlan, code=plan_code, active=True) if plan_code else None
    service = getattr(request.business, "subscription_service", None)
    subscription = service.subscription if service else BusinessSubscription.objects.filter(primary_business=request.business).select_related("plan").first()
    if not subscription:
        subscription = start_trial_for_business(request.business, plans[SubscriptionPlan.CODE_STARTER])
    selected = selected or subscription.plan
    selected.current_monthly_promotion = active_promotion_for_plan(
        selected, billing_cycle=SubscriptionPayment.CYCLE_MONTHLY
    )
    selected.current_yearly_promotion = active_promotion_for_plan(
        selected, billing_cycle=SubscriptionPayment.CYCLE_YEARLY
    )
    selected.current_promotion = selected.current_monthly_promotion or selected.current_yearly_promotion
    payment_locked = payment_is_locked(subscription, selected)
    requires_change_warning = bool(
        subscription.is_effectively_active and subscription.plan_id != selected.pk
    )
    payment_settings = SubscriptionPaymentSettings.load()
    general_trial_days = max(1, int(SubscriptionPolicySettings.load().general_trial_days or 30))
    available_payment_providers = [
        (code, label)
        for code, label in (
            (SubscriptionPayment.PROVIDER_PAYSTACK, "Paystack"),
            (SubscriptionPayment.PROVIDER_MONNIFY, "Monnify"),
        )
        if payment_settings.provider_enabled(code)
    ]
    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "cancel_trial":
            try:
                restored = cancel_paid_plan_trial(subscription)
                if restored.plan.is_free_forever:
                    messages.success(request, "Paid-plan trial cancelled. Your workspace is back on free Starter; no payment was taken.")
                else:
                    messages.success(request, "Paid-plan trial cancelled. Your workspace is back on Starter; Starter currently requires payment before normal access resumes.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            return redirect("subscription_plans")
        if action == "start_paid_trial":
            try:
                start_paid_plan_trial(subscription, selected, request.user)
                messages.success(request, f"Your {general_trial_days}-day {selected.name} trial has started. You can cancel anytime and return to Starter.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            return redirect("subscription_plans")
        if payment_locked:
            if selected.is_free_forever:
                messages.info(request, "Starter is currently free forever and does not require renewal or payment.")
            elif subscription.founder_lifetime:
                messages.info(request, "Your current plan has founder lifetime access; payment is disabled for it.")
            else:
                messages.info(request, "Renewal for your current plan opens within 7 days of expiry.")
            return redirect("subscription_plans")
        if requires_change_warning and request.POST.get("confirm_plan_change") != "1":
            messages.warning(request, "Confirm that you understand this payment will change your active plan after verification.")
            return redirect("subscription_payment_plan", plan_code=selected.code)
        if request.POST.get("action") == "switch_trial" and subscription.status == BusinessSubscription.STATUS_TRIAL and subscription.is_effectively_active:
            from .subscription_services import switch_subscription_plan
            switch_subscription_plan(subscription, selected, keep_expiry=True)
            messages.success(request, f"Trial switched to {selected.name}; the current trial end date is unchanged.")
            return redirect("subscription_plans")
        billing_cycle = request.POST.get("billing_cycle") or SubscriptionPayment.CYCLE_MONTHLY
        if billing_cycle not in {SubscriptionPayment.CYCLE_MONTHLY, SubscriptionPayment.CYCLE_YEARLY}:
            billing_cycle = SubscriptionPayment.CYCLE_MONTHLY
        provider = request.POST.get("provider") or ""
        if not payment_settings.provider_enabled(provider):
            messages.error(request, "That subscription payment provider is currently unavailable. Choose an enabled provider.")
            return redirect("subscription_payment_plan", plan_code=selected.code)
        try:
            months = 12 if billing_cycle == SubscriptionPayment.CYCLE_YEARLY else max(1, min(12, int(request.POST.get("months") or 1)))
        except (TypeError, ValueError):
            months = 1
        try:
            payment = create_payment_request(subscription, selected, months=months, billing_cycle=billing_cycle, provider=provider)
            callback = request.build_absolute_uri(reverse("subscription_payment_callback", args=[provider]))
            separator = "&" if "?" in callback else "?"
            callback = f"{callback}{separator}reference={payment.reference}"
            result = initialize_gateway(
                payment,
                email=request.user.email,
                customer_name=request.user.fullname or request.user.username,
                callback_url=callback,
            )
            payment.checkout_url = result["checkout_url"]
            payment.provider_reference = result.get("provider_reference", "")
            payment.provider_payload = result.get("payload") or {}
            payment.save(update_fields=["checkout_url", "provider_reference", "provider_payload"])
            return redirect(payment.checkout_url)
        except Exception as exc:
            if 'payment' in locals():
                payment.status = SubscriptionPayment.STATUS_FAILED
                payment.notes = str(exc)[:255]
                payment.save(update_fields=["status", "notes"])
            messages.error(request, str(exc))
            return redirect("subscription_payment_plan", plan_code=selected.code)
    service_profiles = list(subscription.services.select_related("business").all())
    return render(request, "accounts/subscription_payment.html", {
        "subscription": subscription,
        "selected_plan": selected,
        "plans": SubscriptionPlan.objects.filter(active=True).order_by("monthly_price", "id"),
        "payments": subscription.subscription_payments.select_related("plan")[:20],
        "service_profiles": service_profiles,
        "selected_monthly_total": payment_amount(selected, len(service_profiles), 1),
        "selected_yearly_total": payment_amount(selected, len(service_profiles), 12, billing_cycle=SubscriptionPayment.CYCLE_YEARLY),
        "selected_base_monthly_total": payment_amount(selected, len(service_profiles), 1, promotion=False),
        "selected_base_yearly_total": payment_amount(selected, len(service_profiles), 12, billing_cycle=SubscriptionPayment.CYCLE_YEARLY, promotion=False),
        "available_payment_providers": available_payment_providers,
        "payment_locked": payment_locked,
        "requires_change_warning": requires_change_warning,
        "paid_trial_available": paid_trial_available(request.user),
        "general_trial_days": general_trial_days,
    })


def subscription_payment_callback(request, provider):
    from .models import SubscriptionPayment
    from .payment_gateways import verify_gateway
    from .subscription_services import mark_payment_paid
    reference = (request.GET.get("reference") or request.GET.get("trxref") or request.GET.get("paymentReference") or "").strip()
    payment = SubscriptionPayment.objects.filter(reference=reference, provider=provider).select_related("subscription", "plan").first()
    if not payment:
        return render(request, "accounts/subscription_payment_result.html", {"success": False, "message": "Payment reference was not found."}, status=404)
    if payment.status == SubscriptionPayment.STATUS_PAID:
        return render(request, "accounts/subscription_payment_result.html", {"success": True, "payment": payment, "message": "This subscription payment is already confirmed."})
    try:
        verified, payload = verify_gateway(payment)
        payment.provider_payload = payload or {}
        payment.save(update_fields=["provider_payload"])
        if verified:
            payment = mark_payment_paid(payment)
            return render(request, "accounts/subscription_payment_result.html", {"success": True, "payment": payment, "message": "Payment verified. Your subscription access has been updated."})
    except Exception as exc:
        return render(request, "accounts/subscription_payment_result.html", {"success": False, "payment": payment, "message": str(exc)}, status=400)
    return render(request, "accounts/subscription_payment_result.html", {"success": False, "payment": payment, "message": "Payment is not yet confirmed. If you completed payment, confirmation from the payment provider may still arrive shortly."}, status=400)


@csrf_exempt
@require_POST
def subscription_payment_webhook(request, provider):
    import json
    from .models import SubscriptionPayment
    from .payment_gateways import monnify_signature_valid, paystack_signature_valid, verify_gateway
    from .subscription_services import mark_payment_paid
    raw = request.body
    try:
        if provider == SubscriptionPayment.PROVIDER_PAYSTACK:
            if not paystack_signature_valid(raw, request.headers.get("x-paystack-signature", "")):
                return HttpResponse(status=403)
            payload = json.loads(raw or b"{}")
            if payload.get("event") != "charge.success":
                return HttpResponse(status=200)
            reference = str((payload.get("data") or {}).get("reference") or "")
        elif provider == SubscriptionPayment.PROVIDER_MONNIFY:
            if not monnify_signature_valid(raw, request.headers.get("monnify-signature", "")):
                return HttpResponse(status=403)
            payload = json.loads(raw or b"{}")
            if payload.get("eventType") != "SUCCESSFUL_TRANSACTION":
                return HttpResponse(status=200)
            reference = str((payload.get("eventData") or {}).get("paymentReference") or "")
        else:
            return HttpResponse(status=404)
        payment = SubscriptionPayment.objects.filter(reference=reference, provider=provider).select_related("subscription", "plan").first()
        if not payment or payment.status == SubscriptionPayment.STATUS_PAID:
            return HttpResponse(status=200)
        verified, verify_payload = verify_gateway(payment)
        payment.provider_payload = {"webhook": payload, "verification": verify_payload}
        payment.save(update_fields=["provider_payload"])
        if verified:
            payment = mark_payment_paid(payment)
        return HttpResponse(status=200)
    except Exception:
        # Invalid/unverifiable events are not used to grant access. Returning 400 allows provider retry.
        return HttpResponse(status=400)


@login_required
def subscription_add_service(request):
    from .forms import AddSubscriptionServiceForm
    from .models import BusinessSubscription
    from .subscription_services import add_service_business
    if not is_business_admin(request.user, request.business):
        return render(request, "403.html", status=403)
    service = getattr(request.business, "subscription_service", None)
    subscription = service.subscription if service else BusinessSubscription.objects.filter(primary_business=request.business).select_related("plan").first()
    if not subscription:
        messages.error(request, "Choose a subscription plan before adding another service.")
        return redirect("subscription_plans")
    if request.method == "POST":
        form = AddSubscriptionServiceForm(request.POST)
        if form.is_valid():
            try:
                business = add_service_business(
                    subscription,
                    name=form.cleaned_data["business_name"],
                    service_type=form.cleaned_data["service_type"],
                    actor=request.user,
                )
                messages.success(request, f"{business.name} added as an additional service profile. Your next plan payment includes the discounted service add-on.")
                return redirect("subscription_plans")
            except ValidationError as exc:
                form.add_error(None, exc)
    else:
        form = AddSubscriptionServiceForm()
    return render(request, "accounts/subscription_service_form.html", {"form": form, "subscription": subscription})


@login_required
def founder_platform_business(request, pk):
    """Founder-native business editor; Django admin remains an optional fallback."""
    from core.forms import BusinessForm
    from core.services import audit

    if not request.user.is_superuser:
        return render(request, "403.html", status=403)
    business = get_object_or_404(Business, pk=pk)
    previous_slug = business.slug
    if request.method == "POST":
        form = BusinessForm(request.POST, request.FILES, instance=business)
        if form.is_valid():
            business = form.save()
            audit(
                business, request.user, "update", business,
                "Founder updated business settings",
                {"previous_slug": previous_slug, "slug": business.slug, "vertical": business.vertical},
            )
            messages.success(request, f"{business.name} was updated.")
            return redirect(f"{reverse('founder_subscriptions')}?workspace=management#platform-management")
    else:
        form = BusinessForm(instance=business)
    memberships = business.user_memberships.select_related("user", "role").order_by("user__fullname", "user__username")
    return render(request, "accounts/founder_platform_form.html", {
        "form": form,
        "title": f"Manage {business.name}",
        "eyebrow": "Founder platform management · Business",
        "object_kind": "business",
        "managed_business": business,
        "memberships": memberships,
    })


@login_required
def founder_platform_user(request, pk):
    """Founder-native global account editor with self-lockout protection."""
    from .forms import FounderUserManagementForm

    if not request.user.is_superuser:
        return render(request, "403.html", status=403)
    account = get_object_or_404(CustomUser, pk=pk)
    if request.method == "POST":
        form = FounderUserManagementForm(request.POST, instance=account, actor=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, f"{account.fullname or account.username} was updated.")
            return redirect(f"{reverse('founder_subscriptions')}?workspace=management#platform-management")
    else:
        form = FounderUserManagementForm(instance=account, actor=request.user)
    memberships = account.business_memberships.select_related("business", "role").order_by("business__name")
    return render(request, "accounts/founder_platform_form.html", {
        "form": form,
        "title": account.fullname or account.username,
        "eyebrow": "Founder platform management · User",
        "object_kind": "user",
        "managed_user": account,
        "memberships": memberships,
    })


def _founder_deletion_context(obj, object_kind):
    """Build a plain-text cascade preview independent of model-admin policy."""
    from django.contrib.admin.utils import NestedObjects
    from django.db import router

    collector = NestedObjects(using=router.db_for_write(obj.__class__, instance=obj))
    collector.collect([obj])
    model_count = {
        model._meta.verbose_name_plural: len(objects)
        for model, objects in collector.model_objs.items()
    }

    return {
        "target": obj,
        "target_label": str(obj),
        "object_kind": object_kind,
        "deleted_objects": collector.nested(format_callback=str),
        "model_count": model_count,
        "protected": [str(item) for item in collector.protected],
    }


@login_required
def founder_platform_business_delete(request, pk):
    """Delete a tenant root only after showing its full cascade impact."""
    from django.db.models.deletion import ProtectedError, RestrictedError
    from .models import SubscriptionService

    if not request.user.is_superuser:
        return render(request, "403.html", status=403)
    business = get_object_or_404(Business, pk=pk)
    context = _founder_deletion_context(business, "business")
    additional_services = list(
        SubscriptionService.objects.filter(subscription__primary_business=business)
        .exclude(business=business)
        .select_related("business")
    )
    if additional_services:
        names = ", ".join(service.business.name for service in additional_services)
        context["protected"].append(
            f"Additional service workspace(s) on this subscription: {names}. Delete those businesses first."
        )
    context["blocked"] = bool(context["protected"])
    if request.method == "POST" and request.POST.get("confirm_delete") == "yes":
        if context["blocked"]:
            messages.error(request, "This business cannot be deleted while protected records remain.")
        else:
            business_name = business.name
            try:
                with transaction.atomic():
                    from .analytics import mark_founder_signup_business_deleted
                    mark_founder_signup_business_deleted(business=business, updated_by=request.user)
                    business.delete()
            except (ProtectedError, RestrictedError):
                messages.error(request, "This business could not be deleted because a protected record was added or changed.")
            else:
                if request.session.get("active_business_id") == pk:
                    request.session.pop("active_business_id", None)
                messages.success(request, f"{business_name} and its connected records were deleted.")
                return redirect(f"{reverse('founder_subscriptions')}?workspace=management#platform-management")
    return render(request, "accounts/founder_platform_confirm_delete.html", context)


@login_required
def founder_platform_user_delete(request, pk):
    """Delete a global account with cascade preview and self-delete protection."""
    from django.db.models.deletion import ProtectedError, RestrictedError

    if not request.user.is_superuser:
        return render(request, "403.html", status=403)
    account = get_object_or_404(CustomUser, pk=pk)
    context = _founder_deletion_context(account, "user")
    context["self_delete"] = account.pk == request.user.pk
    context["blocked"] = bool(context["self_delete"] or context["protected"])
    if request.method == "POST" and request.POST.get("confirm_delete") == "yes":
        if context["blocked"]:
            messages.error(request, "This account cannot be deleted from the current session.")
        else:
            account_name = account.fullname or account.username
            try:
                with transaction.atomic():
                    account.delete()
            except (ProtectedError, RestrictedError):
                messages.error(request, "This account could not be deleted because a protected record was added or changed.")
            else:
                messages.success(request, f"{account_name} and its connected access records were deleted.")
                return redirect(f"{reverse('founder_subscriptions')}?workspace=management#platform-management")
    return render(request, "accounts/founder_platform_confirm_delete.html", context)


@login_required
@require_POST
def founder_mailing_list_contact_action(request):
    """Permanently hide a signup row only after its business was hard-deleted."""
    if not request.user.is_superuser:
        return render(request, "403.html", status=403)
    from .models import FounderSignupContactState

    email = (request.POST.get("email") or "").strip()
    email_key = email.casefold()
    if not email_key:
        messages.error(request, "Choose a valid signup contact.")
        return redirect(f"{reverse('founder_subscriptions')}?workspace=management#signup-mailing-list")

    state = FounderSignupContactState.objects.filter(email_key=email_key).first()
    if not state or not state.deleted_at:
        messages.error(request, "Permanent removal is available only after the related business has been deleted.")
        return redirect(f"{reverse('founder_subscriptions')}?workspace=management#signup-mailing-list")

    if (request.POST.get("contact_action") or "").strip() != "permanent_delete":
        messages.error(request, "Choose a valid mailing-list action.")
        return redirect(f"{reverse('founder_subscriptions')}?workspace=management#signup-mailing-list")

    state.permanently_hidden = True
    state.updated_by = request.user
    state.save(update_fields=["permanently_hidden", "updated_by", "updated_at"])
    messages.success(request, f"{email} permanently removed from the signup mailing-list table.")
    return redirect(f"{reverse('founder_subscriptions')}?workspace=management#signup-mailing-list")


@login_required
def founder_mailing_list_csv(request):
    """Export signup contacts for founder-owned email communications."""
    import csv

    if not request.user.is_superuser:
        return render(request, "403.html", status=403)
    from .analytics import founder_signup_contacts

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="inprofic-signup-contacts.csv"'
    writer = csv.writer(response)
    writer.writerow(["Email", "Name", "Business", "Service", "Signed up at"])
    for contact in founder_signup_contacts():
        signed_up_at = contact["signed_up_at"]
        if timezone.is_aware(signed_up_at):
            signed_up_at = timezone.localtime(signed_up_at)
        writer.writerow([
            contact["email"],
            contact["name"],
            contact["business"],
            contact["service"],
            signed_up_at.isoformat(timespec="seconds"),
        ])
    return response


@login_required
def founder_signup_live_snapshot(request):
    """Return Founder-only live fragments used after signup realtime signals."""
    if not request.user.is_superuser:
        return render(request, "403.html", status=403)

    from datetime import timedelta
    from django.http import JsonResponse
    from django.template.loader import render_to_string
    from .analytics import founder_signup_contacts
    from .models import BusinessSubscription, PlatformEvent

    try:
        after_id = max(0, int(request.GET.get("after") or 0))
    except (TypeError, ValueError):
        after_id = 0
    platform_query = (request.GET.get("platform_q") or "").strip()[:100]

    businesses = Business.objects.select_related(
        "subscription__plan", "subscription_service__subscription__plan"
    ).annotate(member_count=Count("user_memberships", distinct=True)).order_by("-id")
    users = CustomUser.objects.annotate(
        business_count=Count("business_memberships", distinct=True)
    ).order_by("-date_joined")
    if platform_query:
        businesses = businesses.filter(Q(name__icontains=platform_query) | Q(slug__icontains=platform_query))
        users = users.filter(
            Q(fullname__icontains=platform_query)
            | Q(username__icontains=platform_query)
            | Q(email__icontains=platform_query)
            | Q(phone__icontains=platform_query)
        )

    contacts = founder_signup_contacts(include_deleted=True)
    subscriptions = BusinessSubscription.objects.select_related("primary_business", "plan", "founder_granted_by").prefetch_related("services__business")
    recent_events = PlatformEvent.objects.select_related("business", "user").order_by("-occurred_at", "-id")[:30]
    latest_registration_id = (
        PlatformEvent.objects.filter(event_type=PlatformEvent.EVENT_REGISTRATION)
        .order_by("-id").values_list("id", flat=True).first() or 0
    )
    new_events = list(
        PlatformEvent.objects.filter(
            event_type=PlatformEvent.EVENT_REGISTRATION, id__gt=after_id
        ).select_related("business", "user").order_by("id")[:20]
    )
    new_signups = []
    for event in new_events:
        metadata = event.metadata or {}
        business_name = (metadata.get("business_name") or getattr(event.business, "name", "") or "").strip()
        vertical = metadata.get("vertical") or getattr(event.business, "vertical", "") or ""
        new_signups.append({
            "id": event.pk,
            "business": business_name or "New business",
            "name": (metadata.get("signup_name") or getattr(event.user, "fullname", "") or getattr(event.user, "username", "") or "").strip(),
            "email": (metadata.get("signup_email") or getattr(event.user, "email", "") or "").strip(),
            "service": event.business.get_vertical_display() if event.business else vertical,
        })

    now = timezone.now()
    since_30 = now - timedelta(days=30)
    lead_sessions_30d = PlatformEvent.objects.filter(
        event_type=PlatformEvent.EVENT_SIGNUP_VIEW, occurred_at__gte=since_30
    ).exclude(session_key="").values("session_key").distinct().count()
    registrations_30d = PlatformEvent.objects.filter(
        event_type=PlatformEvent.EVENT_REGISTRATION, occurred_at__gte=since_30
    ).count()
    active_subscriptions = BusinessSubscription.objects.filter(
        Q(founder_lifetime=True)
        | Q(status__in=[BusinessSubscription.STATUS_ACTIVE, BusinessSubscription.STATUS_TRIAL])
    ).count()
    return JsonResponse({
        "latest_registration_id": latest_registration_id,
        "new_signups": new_signups,
        "businesses_html": render_to_string("accounts/_founder_business_rows.html", {"platform_businesses": businesses[:50]}, request=request),
        "users_html": render_to_string("accounts/_founder_user_rows.html", {"platform_users": users[:50]}, request=request),
        "contacts_html": render_to_string("accounts/_founder_signup_rows.html", {"signup_contacts": contacts[:50]}, request=request),
        "subscriptions_html": render_to_string("accounts/_founder_subscription_rows.html", {"subscriptions": subscriptions}, request=request),
        "recent_events_html": render_to_string("accounts/_founder_recent_event_rows.html", {"recent_events": recent_events}, request=request),
        "stats": {
            "businesses": Business.objects.count(),
            "users": CustomUser.objects.count(),
            "active_subscriptions": active_subscriptions,
            "signup_contacts": len([row for row in contacts if not row["deleted"]]),
            "registrations_7d": PlatformEvent.objects.filter(
                event_type=PlatformEvent.EVENT_REGISTRATION, occurred_at__gte=now - timedelta(days=7)
            ).count(),
            "registrations_30d": registrations_30d,
            "signup_conversion_30d": round((registrations_30d / lead_sessions_30d * 100), 1) if lead_sessions_30d else 0,
        },
    })


@login_required
def founder_subscriptions(request):
    from .forms import (
        FounderGrantForm, FounderTrialGrantForm, SubscriptionTrialPolicyForm,
        BusinessRestoreForm, SubscriptionPromotionForm, MarketingPromoCampaignForm,
        MarketingTrustSettingsForm, MarketingTrustLogoForm,
    )
    from .models import (
        BusinessSubscription, FounderTrialGrant, SubscriptionPlan, SubscriptionPayment,
        SubscriptionPaymentSettings, SubscriptionPolicySettings, PlatformIntegrationSettings,
        SubscriptionPromotion, MarketingPromoCampaign, MarketingTrustSettings, MarketingTrustLogo,
    )
    from .subscription_services import (
        ensure_default_plans, grant_founder_lifetime, grant_founder_trial_extension,
        mark_payment_paid, start_trial_for_business,
    )
    from .backup_restore import BackupRestoreError, analyze_backup, restore_backup
    if not request.user.is_superuser:
        return render(request, "403.html", status=403)
    ensure_default_plans()
    payment_settings = SubscriptionPaymentSettings.load()
    integration_settings = PlatformIntegrationSettings.load()
    trial_policy_settings = SubscriptionPolicySettings.load()
    trust_settings = MarketingTrustSettings.load()
    action = request.POST.get("action") if request.method == "POST" else ""
    form = FounderGrantForm(request.POST if action == "grant" else None)
    trial_policy_form = SubscriptionTrialPolicyForm(
        request.POST if action == "save_trial_policy" else None, instance=trial_policy_settings
    )
    trial_grant_form = FounderTrialGrantForm(
        request.POST if action == "grant_trial" else None,
        default_days=trial_policy_settings.general_trial_days,
    )
    restore_form = BusinessRestoreForm(
        request.POST if action in {"backup_preview", "backup_restore"} else None,
        request.FILES if action in {"backup_preview", "backup_restore"} else None,
    )
    promotion_form = SubscriptionPromotionForm(request.POST if action == "create_promotion" else None)
    trust_settings_form = MarketingTrustSettingsForm(
        request.POST if action == "save_trust_settings" else None, instance=trust_settings
    )
    trust_logo_form = MarketingTrustLogoForm(
        request.POST if action == "add_trust_logo" else None,
        request.FILES if action == "add_trust_logo" else None,
    )
    campaign_id = (request.POST.get("campaign_id") if action == "save_marketing_campaign" else None) or request.GET.get("campaign")
    try:
        campaign_pk = int(campaign_id) if campaign_id else None
    except (TypeError, ValueError):
        campaign_pk = None
    campaign_instance = MarketingPromoCampaign.objects.filter(pk=campaign_pk).select_related("promotion__plan").first() if campaign_pk else None
    campaign_form = MarketingPromoCampaignForm(
        request.POST if action == "save_marketing_campaign" else None,
        request.FILES if action == "save_marketing_campaign" else None,
        instance=campaign_instance,
    )
    from .marketing_campaigns import sanitize_campaign_html
    if action == "save_marketing_campaign":
        campaign_editor_html = sanitize_campaign_html(request.POST.get("content_html") or "")
    elif campaign_instance:
        campaign_editor_html = campaign_instance.content_html
    else:
        campaign_editor_html = ""
    backup_restore_report = None
    if request.method == "POST":
        if action == "open_business_users":
            try:
                business_id = int(request.POST.get("business_id") or "")
            except (TypeError, ValueError):
                messages.error(request, "Choose a valid business workspace.")
                return redirect(f"{reverse('founder_subscriptions')}?workspace=management#platform-management")
            business = get_object_or_404(Business, pk=business_id)
            request.session["active_business_id"] = business.pk
            return redirect("users_list")
        if action in {"backup_preview", "backup_restore"} and restore_form.is_valid():
            target_business = restore_form.cleaned_data["target_business"]
            source_business_id = restore_form.cleaned_data.get("source_business_id")
            uploaded = restore_form.cleaned_data["database"]
            try:
                if action == "backup_preview":
                    backup_restore_report = analyze_backup(
                        uploaded, target_business, source_business_id
                    )
                else:
                    if not source_business_id:
                        uploaded.seek(0)
                        auto_report = analyze_backup(uploaded, target_business, None)
                        source_business_id = auto_report.get("source_business_id")
                        if not source_business_id:
                            restore_form.add_error("source_business_id", "This backup contains more than one business. Preview it first, then enter the business number you want to restore.")
                        uploaded.seek(0)
                    expected = f"RESTORE {target_business.slug}"
                    if restore_form.cleaned_data.get("confirmation", "").strip() != expected:
                        restore_form.add_error("confirmation", f"Type {expected} exactly to confirm this business restore.")
                    if not restore_form.errors:
                        result = restore_backup(uploaded, target_business, source_business_id, actor=request.user)
                        messages.success(
                            request,
                            f"Business restore completed for {target_business.name}: "
                            f"{result['total_rows']} operational rows, "
                            f"{result['identity']['users_created']} new user(s), and "
                            f"{result['identity']['users_matched']} existing user match(es).",
                        )
                        return redirect("founder_subscriptions")
            except BackupRestoreError as exc:
                restore_form.add_error(None, str(exc))
        if action == "save_trial_policy" and trial_policy_form.is_valid():
            policy = trial_policy_form.save(commit=False)
            policy.pk = trial_policy_settings.pk
            policy.updated_by = request.user
            policy.save()
            # Keep the legacy per-plan trial_days field synchronized so admin,
            # exports and older code paths report the same Founder policy.
            plans_to_sync = list(SubscriptionPlan.objects.all())
            for plan in plans_to_sync:
                plan.trial_days = 0 if plan.is_free_forever else policy.general_trial_days
            if plans_to_sync:
                SubscriptionPlan.objects.bulk_update(plans_to_sync, ["trial_days"])
            messages.success(request, f"General free trial set to {policy.general_trial_days} days for new eligible trials.")
            return redirect(f"{reverse('founder_subscriptions')}#trial-policy")
        if action == "grant_trial" and trial_grant_form.is_valid():
            business = trial_grant_form.cleaned_data["business"]
            plan = trial_grant_form.cleaned_data["plan"]
            days = trial_grant_form.cleaned_data["days"]
            note = trial_grant_form.cleaned_data["note"]
            service = getattr(business, "subscription_service", None)
            subscription = service.subscription if service else BusinessSubscription.objects.filter(primary_business=business).first()
            try:
                if subscription:
                    subscription = grant_founder_trial_extension(subscription, plan, days, request.user, note)
                else:
                    subscription = start_trial_for_business(business, plan, trial_days_override=days)
                    FounderTrialGrant.objects.create(
                        subscription=subscription, plan=plan, days=days, previous_ends_at=None,
                        granted_ends_at=subscription.trial_ends_at, granted_by=request.user, note=note or "",
                    )
            except ValidationError as exc:
                trial_grant_form.add_error(None, exc.messages[0] if getattr(exc, "messages", None) else str(exc))
            else:
                messages.success(
                    request,
                    f"Founder trial granted to {business.name} on {plan.name} through {subscription.trial_ends_at:%d %b %Y}.",
                )
                return redirect(f"{reverse('founder_subscriptions')}#trial-policy")
        if action == "grant" and form.is_valid():
            business = form.cleaned_data["business"]
            service = getattr(business, "subscription_service", None)
            subscription = service.subscription if service else BusinessSubscription.objects.filter(primary_business=business).first()
            if not subscription:
                subscription = start_trial_for_business(business, form.cleaned_data["plan"])
            grant_founder_lifetime(subscription, form.cleaned_data["plan"], request.user, form.cleaned_data["note"])
            messages.success(request, f"Founder lifetime access granted to {business.name} on {form.cleaned_data['plan'].name}.")
            return redirect("founder_subscriptions")
        if action == "mark_paid":
            payment = get_object_or_404(SubscriptionPayment, pk=request.POST.get("payment_id"))
            payment = mark_payment_paid(payment)
            messages.success(request, f"Payment {payment.reference} marked paid and entitlements updated.")
            return redirect("founder_subscriptions")
        if action == "save_trust_settings" and trust_settings_form.is_valid():
            saved = trust_settings_form.save(commit=False)
            saved.pk = trust_settings.pk
            saved.updated_by = request.user
            saved.save()
            messages.success(request, "Marketing trust strip settings saved.")
            return redirect(f"{reverse('founder_subscriptions')}#marketing-trust-strip")
        if action == "add_trust_logo" and trust_logo_form.is_valid():
            logo = trust_logo_form.save(commit=False)
            logo.created_by = request.user
            logo.save()
            messages.success(request, f"{logo.name} was added to the trusted-business strip.")
            return redirect(f"{reverse('founder_subscriptions')}#marketing-trust-strip")
        if action == "toggle_trust_logo":
            logo = get_object_or_404(MarketingTrustLogo, pk=request.POST.get("trust_logo_id"))
            logo.active = not logo.active
            logo.save(update_fields=["active", "updated_at"])
            messages.success(request, f"{logo.name} is now {'visible' if logo.active else 'hidden'} on the marketing page.")
            return redirect(f"{reverse('founder_subscriptions')}#marketing-trust-strip")
        if action == "create_promotion" and promotion_form.is_valid():
            promotion = promotion_form.save(commit=False)
            promotion.created_by = request.user
            promotion.active = True
            promotion.save()
            messages.success(request, f"Promotion scheduled for {promotion.target_label}: {promotion.reason}.")
            return redirect("founder_subscriptions")
        if action == "deactivate_promotion":
            promotion = get_object_or_404(SubscriptionPromotion, pk=request.POST.get("promotion_id"))
            promotion.active = False
            promotion.save(update_fields=["active", "updated_at"])
            messages.success(request, f"Promotion ended for {promotion.target_label}. Base pricing remains unchanged.")
            return redirect("founder_subscriptions")
        if action == "save_marketing_campaign" and campaign_form.is_valid():
            campaign = campaign_form.save(commit=False)
            if not campaign.pk:
                campaign.created_by = request.user
            campaign.save()
            messages.success(request, f"Marketing promo creative saved for {campaign.promotion.target_label}: {campaign.name}.")
            return redirect(f"{reverse('founder_subscriptions')}#marketing-promo-campaigns")
        if action == "deactivate_marketing_campaign":
            campaign = get_object_or_404(MarketingPromoCampaign, pk=request.POST.get("campaign_id"))
            campaign.active = False
            campaign.save(update_fields=["active", "updated_at"])
            messages.success(request, f"Marketing promo creative '{campaign.name}' was taken off the marketing page.")
            return redirect(f"{reverse('founder_subscriptions')}#marketing-promo-campaigns")
        if action == "save_plan_entitlements":
            from .models import SubscriptionPlanModule
            from .subscription_services import PLAN_ENTITLEMENT_MODULES, apply_subscriptions_entitlements
            plans_to_update = list(SubscriptionPlan.objects.all().order_by("id"))
            with transaction.atomic():
                existing_rows = {
                    (item.plan_id, item.module): item
                    for item in SubscriptionPlanModule.objects.filter(plan__in=plans_to_update)
                }
                rows_to_create = []
                rows_to_update = []
                for plan in plans_to_update:
                    for module, _label in PLAN_ENTITLEMENT_MODULES:
                        field_name = f"module_{plan.pk}_{module}"
                        if module == "reports":
                            level = (request.POST.get(field_name) or "none").strip().lower()
                            if level not in {SubscriptionPlanModule.LEVEL_NONE, SubscriptionPlanModule.LEVEL_BASIC, SubscriptionPlanModule.LEVEL_FULL}:
                                level = SubscriptionPlanModule.LEVEL_NONE
                        else:
                            level = SubscriptionPlanModule.LEVEL_FULL if request.POST.get(field_name) == "on" else SubscriptionPlanModule.LEVEL_NONE
                        enabled = level != SubscriptionPlanModule.LEVEL_NONE
                        entitlement = existing_rows.get((plan.pk, module))
                        if entitlement is None:
                            rows_to_create.append(SubscriptionPlanModule(
                                plan=plan, module=module, enabled=enabled, level=level,
                            ))
                        elif entitlement.enabled != enabled or entitlement.level != level:
                            entitlement.enabled = enabled
                            entitlement.level = level
                            rows_to_update.append(entitlement)
                if rows_to_create:
                    SubscriptionPlanModule.objects.bulk_create(rows_to_create, ignore_conflicts=True)
                if rows_to_update:
                    SubscriptionPlanModule.objects.bulk_update(rows_to_update, ["enabled", "level"])
                subscription_ids = list(BusinessSubscription.objects.filter(plan__in=plans_to_update).values_list("pk", flat=True))
                apply_subscriptions_entitlements(subscription_ids)
            messages.success(request, "All plan module access settings were saved together and applied to current subscribers.")
            return redirect(f"{reverse('founder_subscriptions')}#plan-entitlements")
        if action == "save_plan_pricing":
            from decimal import Decimal, InvalidOperation
            plans_to_update = list(SubscriptionPlan.objects.all().order_by("id"))
            parsed = []
            try:
                for plan in plans_to_update:
                    starter_free = bool(
                        plan.code == SubscriptionPlan.CODE_STARTER
                        and request.POST.get(f"free_forever_{plan.pk}") == "on"
                    )
                    monthly = max(Decimal("0"), Decimal(request.POST.get(f"monthly_price_{plan.pk}") or "0"))
                    if starter_free:
                        monthly = Decimal("0")
                    elif plan.code == SubscriptionPlan.CODE_STARTER and monthly <= 0:
                        raise ValueError("Paid Starter needs a positive monthly price.")
                    yearly_discount = min(Decimal("100"), max(Decimal("0"), Decimal(request.POST.get(f"yearly_discount_{plan.pk}") or "0")))
                    addon_discount = min(Decimal("100"), max(Decimal("0"), Decimal(request.POST.get(f"addon_discount_{plan.pk}") or "0")))
                    if plan.code == SubscriptionPlan.CODE_STARTER:
                        user_limit, service_limit = 1, 0
                    else:
                        user_limit = None if request.POST.get(f"users_unlimited_{plan.pk}") == "on" else max(1, int(request.POST.get(f"user_limit_{plan.pk}") or 1))
                        service_limit = None if request.POST.get(f"services_unlimited_{plan.pk}") == "on" else max(0, int(request.POST.get(f"service_limit_{plan.pk}") or 0))
                    parsed.append((
                        plan, monthly, yearly_discount, addon_discount, user_limit, service_limit,
                        plan.is_free_forever, starter_free,
                    ))
            except (InvalidOperation, TypeError, ValueError):
                messages.error(request, "Enter valid numeric pricing and discount values for every plan. Paid Starter requires a price above zero.")
                return redirect("founder_subscriptions")
            # Keep configurable base pricing from making an already scheduled
            # fixed-amount promotion impossible to pay. One bounded query checks
            # all still-relevant promotions before the atomic price update.
            fixed_promos = list(SubscriptionPromotion.objects.filter(
                Q(plan_id__in=[plan.pk for plan, *_ in parsed]) | Q(applies_to_all_plans=True),
                active=True,
                discount_type=SubscriptionPromotion.DISCOUNT_AMOUNT,
                ends_at__gt=timezone.now(),
            ).select_related("plan"))
            invalid = []
            for plan, monthly, _yearly_discount, _addon_discount, _user_limit, _service_limit, _was_free, will_be_free in parsed:
                if will_be_free:
                    continue
                applicable = [promo for promo in fixed_promos if promo.applies_to_plan(plan)]
                promo = max(applicable, key=lambda row: row.discount_value, default=None)
                if promo and monthly <= promo.discount_value:
                    invalid.append(f"{plan.name} ({promo.reason})")
            if invalid:
                messages.error(
                    request,
                    "End or reduce the fixed promotion before lowering its base price: " + ", ".join(invalid),
                )
                return redirect("founder_subscriptions")
            now = timezone.now()
            transition_messages = []
            with transaction.atomic():
                plan_rows = []
                for plan, monthly, yearly_discount, addon_discount, user_limit, service_limit, was_free, will_be_free in parsed:
                    plan.monthly_price = monthly
                    plan.yearly_discount_percent = yearly_discount
                    plan.additional_service_discount_percent = addon_discount
                    plan.user_limit = user_limit
                    plan.additional_service_limit = service_limit
                    plan.trial_days = 0 if will_be_free else trial_policy_settings.general_trial_days
                    plan_rows.append(plan)
                    if plan.code != SubscriptionPlan.CODE_STARTER or was_free == will_be_free:
                        continue
                    subscriptions = BusinessSubscription.objects.filter(plan=plan, founder_lifetime=False)
                    if was_free and not will_be_free:
                        # Existing free Starter tenants receive the Founder-configured
                        # grace/trial instead of losing access the moment policy changes.
                        changed = subscriptions.filter(
                            status=BusinessSubscription.STATUS_ACTIVE,
                            trial_ends_at__isnull=True,
                            paid_until__isnull=True,
                        ).update(
                            status=BusinessSubscription.STATUS_TRIAL,
                            trial_ends_at=now + timezone.timedelta(days=trial_policy_settings.general_trial_days),
                            paid_until=None,
                        )
                        transition_messages.append(
                            f"Starter changed to paid; {changed} existing free subscriber(s) received "
                            f"{trial_policy_settings.general_trial_days} days of uninterrupted access."
                        )
                    elif not was_free and will_be_free:
                        changed = subscriptions.update(
                            status=BusinessSubscription.STATUS_ACTIVE,
                            trial_ends_at=None,
                            paid_until=None,
                        )
                        transition_messages.append(f"Starter changed back to free forever; {changed} Starter subscriber(s) now have non-expiring access.")
                SubscriptionPlan.objects.bulk_update(
                    plan_rows,
                    [
                        "monthly_price", "yearly_discount_percent",
                        "additional_service_discount_percent", "trial_days",
                        "user_limit", "additional_service_limit",
                    ],
                )
            messages.success(request, "All plan pricing and capacity settings were saved together." + (" " + " ".join(transition_messages) if transition_messages else ""))
            return redirect("founder_subscriptions")
        if action == "save_payment_channels":
            payment_settings.paystack_enabled = request.POST.get("paystack_enabled") == "on"
            payment_settings.monnify_enabled = request.POST.get("monnify_enabled") == "on"
            payment_settings.updated_by = request.user
            payment_settings.save(update_fields=[
                "paystack_enabled", "monnify_enabled", "updated_by", "updated_at",
            ])
            messages.success(request, "Subscription payment channels updated. Existing payment callbacks remain active.")
            return redirect("founder_subscriptions")
        if action == "save_platform_integrations":
            integration_settings.glovo_enabled = request.POST.get("glovo_enabled") == "on"
            integration_settings.updated_by = request.user
            integration_settings.save(update_fields=["glovo_enabled", "updated_by", "updated_at"])
            from .platform_integrations import set_glovo_platform_enabled
            set_glovo_platform_enabled(integration_settings.glovo_enabled)
            state = "available" if integration_settings.glovo_enabled else "hidden and unavailable"
            messages.success(request, f"External integration availability updated. Optional delivery provider is now {state} outside the Founder Console.")
            return redirect(f"{reverse('founder_subscriptions')}?workspace=management#platform-integrations")
        if action == "revoke_founder":
            from .subscription_services import revoke_founder_lifetime
            subscription = get_object_or_404(BusinessSubscription, pk=request.POST.get("subscription_id"))
            revoke_founder_lifetime(subscription)
            messages.success(request, f"Founder lifetime access revoked for {subscription.primary_business.name}.")
            return redirect("founder_subscriptions")
    subscriptions = BusinessSubscription.objects.select_related("primary_business", "plan", "founder_granted_by").prefetch_related("services__business")
    pending_payments = SubscriptionPayment.objects.filter(status=SubscriptionPayment.STATUS_PENDING).select_related("subscription__primary_business", "plan")[:50]
    platform_query = (request.GET.get("platform_q") or "").strip()[:100]
    businesses = Business.objects.select_related("subscription__plan", "subscription_service__subscription__plan").annotate(member_count=Count("user_memberships", distinct=True)).order_by("-id")
    users = CustomUser.objects.annotate(business_count=Count("business_memberships", distinct=True)).order_by("-date_joined")
    if platform_query:
        businesses = businesses.filter(Q(name__icontains=platform_query) | Q(slug__icontains=platform_query))
        users = users.filter(
            Q(fullname__icontains=platform_query)
            | Q(username__icontains=platform_query)
            | Q(email__icontains=platform_query)
            | Q(phone__icontains=platform_query)
        )
    from .analytics import founder_analytics_summary
    founder_analytics = founder_analytics_summary()
    return render(request, "accounts/founder_subscriptions.html", {
        "form": form,
        "subscriptions": subscriptions,
        "pending_payments": pending_payments,
        "plans": SubscriptionPlan.objects.prefetch_related("module_entitlements").all().order_by("monthly_price", "id"),
        "payment_settings": payment_settings,
        "integration_settings": integration_settings,
        "trial_policy_settings": trial_policy_settings,
        "trial_policy_form": trial_policy_form,
        "trial_grant_form": trial_grant_form,
        "recent_trial_grants": FounderTrialGrant.objects.select_related(
            "subscription__primary_business", "plan", "granted_by"
        )[:12],
        "restore_form": restore_form,
        "backup_restore_report": backup_restore_report,
        "promotion_form": promotion_form,
        "trust_settings_form": trust_settings_form,
        "trust_logo_form": trust_logo_form,
        "trust_logos": MarketingTrustLogo.objects.select_related("created_by").all(),
        "promotions": SubscriptionPromotion.objects.select_related("plan", "created_by").order_by("-active", "-starts_at", "-id")[:50],
        "campaign_form": campaign_form,
        "campaign_instance": campaign_instance,
        "campaign_editor_html": campaign_editor_html,
        "marketing_campaigns": MarketingPromoCampaign.objects.select_related("promotion__plan", "created_by").order_by("-active", "-priority", "id")[:50],
        "now": timezone.now(),
        "founder_analytics": founder_analytics,
        "platform_stats": {
            "businesses": Business.objects.count(),
            "users": CustomUser.objects.count(),
            "active_subscriptions": subscriptions.filter(
                Q(founder_lifetime=True) | Q(status__in=[BusinessSubscription.STATUS_ACTIVE, BusinessSubscription.STATUS_TRIAL])
            ).count(),
            "pending_payments": pending_payments.count(),
        },
        "platform_query": platform_query,
        "platform_businesses": businesses[:50],
        "platform_users": users[:50],
    })


def _can_platform_mail(user):
    return bool(user.is_authenticated and (user.is_superuser or getattr(user, "platform_mail_access", False)))


def _mailing_business_rows(*, service="", plan_id=None):
    from django.core.validators import validate_email
    from django.db.models import Prefetch

    admin_memberships = (
        UserBusiness.objects.filter(
            active=True,
            user__is_active=True,
            role__key=CustomUser.ROLE_BUSINESS_ADMIN,
        )
        .select_related("user", "role")
        .order_by("id")
    )
    businesses = Business.objects.all().order_by("name")
    if service:
        businesses = businesses.filter(vertical=service)
    businesses = businesses.select_related(
        "subscription",
        "subscription__plan",
        "subscription_service__subscription",
        "subscription_service__subscription__plan",
    ).prefetch_related(
        Prefetch(
            "user_memberships",
            queryset=admin_memberships,
            to_attr="mail_admin_memberships",
        )
    )
    rows = []
    for business in businesses:
        subscription = getattr(business, "subscription", None)
        service_link = getattr(business, "subscription_service", None)
        if not subscription and service_link:
            subscription = service_link.subscription
        if plan_id and (not subscription or subscription.plan_id != plan_id):
            continue
        membership = next(iter(getattr(business, "mail_admin_memberships", [])), None)
        email = (membership.user.email if membership else "").strip().lower()
        if not membership or not email:
            continue
        try:
            validate_email(email)
        except ValidationError:
            continue
        rows.append(
            {
                "business": business,
                "owner": membership.user,
                "email": email,
                "service": business.get_vertical_display(),
                "plan": subscription.plan.name if subscription else "No active plan",
                "plan_status": (
                    subscription.effective_status_label
                    if subscription
                    else "No subscription"
                ),
            }
        )
    return rows


@login_required
def platform_mailing_workspace(request):
    if not _can_platform_mail(request.user):
        return render(request, "403.html", status=403)

    from .forms import PlatformMailComposeForm
    from .models import (
        PlatformMailCampaign,
        PlatformMailRecipient,
        PlatformMailTemplate,
        SubscriptionPlan,
    )

    valid_services = {value for value, _label in Business.VERTICAL_CHOICES}
    requested_service = (request.GET.get("service") or "").strip()
    service = requested_service if requested_service in valid_services else ""
    requested_plan = (request.GET.get("plan") or "").strip()
    plan_id = int(requested_plan) if requested_plan.isdigit() else None
    rows = _mailing_business_rows(service=service, plan_id=plan_id)

    if request.method == "POST":
        form = PlatformMailComposeForm(request.POST)
        if form.is_valid():
            selected_ids = set(form.cleaned_data["business_ids"])
            selected = [
                row for row in rows if row["business"].pk in selected_ids
            ]
            if not selected:
                form.add_error("business_ids", "Choose at least one available business recipient.")
            else:
                with transaction.atomic():
                    campaign = PlatformMailCampaign.objects.create(
                        template=form.cleaned_data.get("template"),
                        subject=form.cleaned_data["subject"],
                        heading=form.cleaned_data["heading"],
                        body_html=form.cleaned_data["body_html"],
                        cta_label=form.cleaned_data.get("cta_label") or "",
                        cta_url=form.cleaned_data.get("cta_url") or "",
                        status=PlatformMailCampaign.STATUS_QUEUED,
                        total_recipients=len(selected),
                        created_by=request.user,
                        queued_at=timezone.now(),
                    )
                    PlatformMailRecipient.objects.bulk_create(
                        [
                            PlatformMailRecipient(
                                campaign=campaign,
                                business_id_snapshot=row["business"].pk,
                                business_name=row["business"].name,
                                service=row["service"],
                                plan_name=row["plan"],
                                recipient_name=(
                                    row["owner"].fullname or row["owner"].username
                                ),
                                email=row["email"],
                            )
                            for row in selected
                        ]
                    )
                from .mailing import dispatch_queued_platform_mail

                delivery = dispatch_queued_platform_mail(
                    campaign_id=campaign.pk,
                    limit=100,
                )
                remaining = campaign.recipients.exclude(
                    status=PlatformMailRecipient.STATUS_SENT
                ).count()
                if delivery["sent"]:
                    detail = f'{delivery["sent"]} message(s) sent.'
                    if remaining:
                        detail += f" {remaining} remain queued for automatic retry."
                    messages.success(request, detail)
                else:
                    messages.error(
                        request,
                        "The campaign was saved, but the mail server did not accept a message yet. "
                        "It remains queued for automatic retry; verify the SMTP settings if this continues.",
                    )
                return redirect("platform_mailing_workspace")
    else:
        initial = {}
        template_id = (request.GET.get("template") or "").strip()
        if template_id.isdigit():
            topic = PlatformMailTemplate.objects.filter(
                pk=template_id,
                active=True,
            ).first()
            if topic:
                initial = {
                    "template": topic,
                    "subject": topic.subject,
                    "heading": topic.heading,
                    "body_html": topic.body_html,
                    "cta_label": topic.cta_label,
                    "cta_url": topic.cta_url,
                }
        form = PlatformMailComposeForm(initial=initial)

    campaigns = (
        PlatformMailCampaign.objects.select_related("template", "created_by")
        .annotate(
            pending_count=Count(
                "recipients",
                filter=Q(
                    recipients__status__in=[
                        PlatformMailRecipient.STATUS_PENDING,
                        PlatformMailRecipient.STATUS_SENDING,
                    ]
                ),
            )
        )[:20]
    )
    return render(
        request,
        "accounts/platform_mailing_workspace.html",
        {
            "form": form,
            "business_rows": rows,
            "campaigns": campaigns,
            "topics": PlatformMailTemplate.objects.order_by("name"),
            "total_businesses": Business.objects.count(),
            "service_counts": Business.objects.values("vertical")
            .annotate(total=Count("id"))
            .order_by("vertical"),
            "filter_service": service,
            "filter_plan": str(plan_id or ""),
            "plans": SubscriptionPlan.objects.filter(active=True).order_by(
                "monthly_price",
                "name",
            ),
        },
    )


@login_required
@require_POST
def platform_mail_campaign_dispatch(request, pk):
    if not _can_platform_mail(request.user):
        return render(request, "403.html", status=403)

    from .mailing import dispatch_queued_platform_mail, retry_failed_platform_mail
    from .models import PlatformMailCampaign

    campaign = get_object_or_404(PlatformMailCampaign, pk=pk)
    if request.POST.get("retry_failed"):
        retry_failed_platform_mail(campaign)
    result = dispatch_queued_platform_mail(campaign_id=campaign.pk, limit=500)
    if result["sent"]:
        messages.success(
            request,
            f'{result["sent"]} queued message(s) were delivered.',
        )
    elif result.get("retrying"):
        messages.error(
            request,
            "Delivery was attempted but the mail server did not accept a message. "
            "The campaign remains queued for another retry.",
        )
    else:
        messages.success(request, "There are no queued recipients in this campaign.")
    return redirect("platform_mailing_workspace")


@login_required
def platform_mail_template_editor(request, pk=None):
    if not _can_platform_mail(request.user):
        return render(request, "403.html", status=403)

    from .forms import PlatformMailTemplateForm
    from .models import PlatformMailTemplate

    topic = get_object_or_404(PlatformMailTemplate, pk=pk) if pk else None
    if request.method == "POST":
        form = PlatformMailTemplateForm(request.POST, instance=topic)
        if form.is_valid():
            obj = form.save(commit=False)
            if not obj.created_by_id:
                obj.created_by = request.user
            obj.save()
            messages.success(request, "Mailing topic saved.")
            return redirect("platform_mailing_workspace")
    else:
        form = PlatformMailTemplateForm(instance=topic)
    return render(
        request,
        "accounts/platform_mail_template_form.html",
        {
            "form": form,
            "topic": topic,
            "title": "Edit mailing topic" if topic else "Create mailing topic",
            "eyebrow": "INPROFIC Project Mailing · Topic",
        },
    )





@login_required
def founder_platform_user_add(request):
    if not request.user.is_superuser:
        return render(request, "403.html", status=403)
    from .forms import FounderUserManagementForm
    if request.method == "POST":
        form = FounderUserManagementForm(request.POST, actor=request.user)
        if form.is_valid():
            account = form.save()
            messages.success(request, f"{account.fullname or account.username} was created.")
            return redirect(f"{reverse('founder_subscriptions')}?workspace=management#platform-management")
    else:
        form = FounderUserManagementForm(actor=request.user)
    return render(request, "accounts/founder_platform_form.html", {
        "form": form, "title": "Create project-level user", "eyebrow": "Founder platform management · User",
        "object_kind": "user", "managed_user": None, "memberships": [],
    })
