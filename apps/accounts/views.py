from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.text import slugify
from core.models import Business
from .forms import BusinessSignupForm, UserForm, PermissionMatrixForm, RoleForm, RolePermissionForm
from .models import BusinessModuleAccess, CustomUser, Role, RoleModulePermission, UserBusiness
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
                from .subscription_services import start_trial_for_business
                start_trial_for_business(business)
            auth_login(request, user)
            request.session["active_business_id"] = business.pk
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
    obj = membership.user if membership else None
    if request.method == "POST":
        form = UserForm(request.POST, instance=obj, business=request.business, actor=request.user, membership=membership)
        if form.is_valid():
            with transaction.atomic():
                user = form.save()
                role = form.cleaned_data["role"]
                membership, _ = UserBusiness.objects.get_or_create(user=user, business=request.business, defaults={"role": role, "active": user.is_active})
                membership.role = role
                membership.active = user.is_active
                membership.commerce_storefront_access = bool(form.cleaned_data.get("commerce_storefront_access"))
                membership.save(update_fields=["role", "active", "commerce_storefront_access"])
                ensure_permissions(membership)
            messages.success(request, "User updated." if obj else "User created.")
            return redirect("users_list")
    else:
        form = UserForm(instance=obj, business=request.business, actor=request.user, membership=membership)
    return render(request, "accounts/user_form.html", {"form": form, "obj": obj})


@login_required
def user_permissions(request, pk):
    if not can_manage(request):
        return render(request, "403.html", status=403)
    membership = get_object_or_404(UserBusiness.objects.select_related("user", "role"), pk=pk, business=request.business)
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
    from .subscription_services import attach_active_promotions, ensure_default_plans, payment_is_locked
    ensure_default_plans()
    service = getattr(request.business, "subscription_service", None)
    subscription = service.subscription if service else BusinessSubscription.objects.filter(primary_business=request.business).select_related("plan").first()
    if not subscription:
        # Legacy live businesses are not silently downgraded; they may opt into a plan from this page.
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
    return render(request, "accounts/subscription_plans.html", {
        "subscription": subscription,
        "plan_cards": plan_cards,
    })


@login_required
def subscription_payment(request, plan_code=None):
    from .models import BusinessSubscription, SubscriptionPayment, SubscriptionPaymentSettings, SubscriptionPlan
    from .payment_gateways import GatewayError, initialize_gateway
    from .subscription_services import (
        active_promotion_for_plan,
        create_payment_request,
        ensure_default_plans,
        payment_amount,
        payment_is_locked,
        start_trial_for_business,
    )
    if not is_business_admin(request.user, request.business):
        return render(request, "403.html", status=403)
    plans = ensure_default_plans()
    selected = get_object_or_404(SubscriptionPlan, code=plan_code, active=True) if plan_code else None
    service = getattr(request.business, "subscription_service", None)
    subscription = service.subscription if service else BusinessSubscription.objects.filter(primary_business=request.business).select_related("plan").first()
    if not subscription:
        subscription = start_trial_for_business(request.business, selected or plans[SubscriptionPlan.CODE_STARTER])
    selected = selected or subscription.plan
    selected.current_promotion = active_promotion_for_plan(selected)
    payment_locked = payment_is_locked(subscription, selected)
    requires_change_warning = bool(
        subscription.is_effectively_active and subscription.plan_id != selected.pk
    )
    payment_settings = SubscriptionPaymentSettings.load()
    available_payment_providers = [
        (code, label)
        for code, label in (
            (SubscriptionPayment.PROVIDER_PAYSTACK, "Paystack"),
            (SubscriptionPayment.PROVIDER_MONNIFY, "Monnify"),
        )
        if payment_settings.provider_enabled(code)
    ]
    if request.method == "POST":
        if payment_locked:
            if subscription.founder_lifetime:
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
            messages.success(request, f"Trial switched to {selected.name}; the original 30-day trial end date is unchanged.")
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
    return render(request, "accounts/subscription_payment_result.html", {"success": False, "payment": payment, "message": "Payment is not yet confirmed. If you completed payment, the webhook may still confirm it shortly."}, status=400)


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
            business = add_service_business(
                subscription,
                name=form.cleaned_data["business_name"],
                service_type=form.cleaned_data["service_type"],
                actor=request.user,
            )
            messages.success(request, f"{business.name} added as an additional service profile. Your next plan payment includes the discounted service add-on.")
            return redirect("subscription_plans")
    else:
        form = AddSubscriptionServiceForm()
    return render(request, "accounts/subscription_service_form.html", {"form": form, "subscription": subscription})


@login_required
def founder_subscriptions(request):
    from .forms import FounderGrantForm, LegacyTenantImportForm, SubscriptionPromotionForm
    from .models import BusinessSubscription, SubscriptionPlan, SubscriptionPayment, SubscriptionPaymentSettings, SubscriptionPromotion
    from .subscription_services import ensure_default_plans, grant_founder_lifetime, mark_payment_paid, start_trial_for_business
    from .legacy_import import LegacyImportError, analyze_legacy_backup, import_legacy_backup
    if not request.user.is_superuser:
        return render(request, "403.html", status=403)
    ensure_default_plans()
    payment_settings = SubscriptionPaymentSettings.load()
    action = request.POST.get("action") if request.method == "POST" else ""
    form = FounderGrantForm(request.POST if action == "grant" else None)
    legacy_form = LegacyTenantImportForm(
        request.POST if action in {"legacy_dry_run", "legacy_import"} else None,
        request.FILES if action in {"legacy_dry_run", "legacy_import"} else None,
    )
    promotion_form = SubscriptionPromotionForm(request.POST if action == "create_promotion" else None)
    legacy_import_report = None
    if request.method == "POST":
        if action in {"legacy_dry_run", "legacy_import"} and legacy_form.is_valid():
            target_business = legacy_form.cleaned_data["target_business"]
            source_business_id = legacy_form.cleaned_data.get("source_business_id")
            uploaded = legacy_form.cleaned_data["database"]
            try:
                if action == "legacy_dry_run":
                    legacy_import_report = analyze_legacy_backup(
                        uploaded, target_business, source_business_id
                    )
                else:
                    if not source_business_id:
                        uploaded.seek(0)
                        auto_report = analyze_legacy_backup(uploaded, target_business, None)
                        source_business_id = auto_report.get("source_business_id")
                        if not source_business_id:
                            legacy_form.add_error("source_business_id", "This backup contains more than one legacy tenant. Run Dry run and enter the tenant ID you want to import.")
                        uploaded.seek(0)
                    expected = f"IMPORT {target_business.slug}"
                    if legacy_form.cleaned_data.get("confirmation", "").strip() != expected:
                        legacy_form.add_error("confirmation", f"Type {expected} exactly to authorize this tenant import.")
                    if not legacy_form.errors:
                        result = import_legacy_backup(uploaded, target_business, source_business_id, actor=request.user)
                        messages.success(
                            request,
                            f"Legacy tenant import completed for {target_business.name}: "
                            f"{result['total_rows']} operational rows, "
                            f"{result['identity']['users_created']} new user(s), and "
                            f"{result['identity']['users_matched']} existing user match(es).",
                        )
                        return redirect("founder_subscriptions")
            except LegacyImportError as exc:
                legacy_form.add_error(None, str(exc))
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
        if action == "create_promotion" and promotion_form.is_valid():
            promotion = promotion_form.save(commit=False)
            promotion.created_by = request.user
            promotion.active = True
            promotion.save()
            messages.success(request, f"Promotion scheduled for {promotion.plan.name}: {promotion.reason}.")
            return redirect("founder_subscriptions")
        if action == "deactivate_promotion":
            promotion = get_object_or_404(SubscriptionPromotion, pk=request.POST.get("promotion_id"))
            promotion.active = False
            promotion.save(update_fields=["active", "updated_at"])
            messages.success(request, f"Promotion ended for {promotion.plan.name}. Base pricing remains unchanged.")
            return redirect("founder_subscriptions")
        if action == "save_plan_pricing":
            from decimal import Decimal, InvalidOperation
            plans_to_update = list(SubscriptionPlan.objects.all().order_by("id"))
            parsed = []
            try:
                for plan in plans_to_update:
                    monthly = max(Decimal("0"), Decimal(request.POST.get(f"monthly_price_{plan.pk}") or "0"))
                    yearly_discount = min(Decimal("100"), max(Decimal("0"), Decimal(request.POST.get(f"yearly_discount_{plan.pk}") or "0")))
                    addon_discount = min(Decimal("100"), max(Decimal("0"), Decimal(request.POST.get(f"addon_discount_{plan.pk}") or "0")))
                    parsed.append((plan, monthly, yearly_discount, addon_discount))
            except (InvalidOperation, TypeError, ValueError):
                messages.error(request, "Enter valid numeric pricing and discount values for every plan.")
                return redirect("founder_subscriptions")
            # Keep configurable base pricing from making an already scheduled
            # fixed-amount promotion impossible to pay. One bounded query checks
            # all still-relevant promotions before the atomic price update.
            fixed_promos = {}
            for promo in SubscriptionPromotion.objects.filter(
                plan_id__in=[plan.pk for plan, *_ in parsed],
                active=True,
                discount_type=SubscriptionPromotion.DISCOUNT_AMOUNT,
                ends_at__gt=timezone.now(),
            ).order_by("plan_id", "starts_at", "id"):
                current = fixed_promos.get(promo.plan_id)
                if current is None or promo.discount_value > current.discount_value:
                    fixed_promos[promo.plan_id] = promo
            invalid = []
            for plan, monthly, _yearly_discount, _addon_discount in parsed:
                promo = fixed_promos.get(plan.pk)
                if promo and monthly <= promo.discount_value:
                    invalid.append(f"{plan.name} ({promo.reason})")
            if invalid:
                messages.error(
                    request,
                    "End or reduce the fixed promotion before lowering its base price: " + ", ".join(invalid),
                )
                return redirect("founder_subscriptions")
            with transaction.atomic():
                for plan, monthly, yearly_discount, addon_discount in parsed:
                    plan.monthly_price = monthly
                    plan.yearly_discount_percent = yearly_discount
                    plan.additional_service_discount_percent = addon_discount
                    plan.save(update_fields=["monthly_price", "yearly_discount_percent", "additional_service_discount_percent"])
            messages.success(request, "All plan pricing settings were saved together.")
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
        if action == "revoke_founder":
            from .subscription_services import revoke_founder_lifetime
            subscription = get_object_or_404(BusinessSubscription, pk=request.POST.get("subscription_id"))
            revoke_founder_lifetime(subscription)
            messages.success(request, f"Founder lifetime access revoked for {subscription.primary_business.name}.")
            return redirect("founder_subscriptions")
    subscriptions = BusinessSubscription.objects.select_related("primary_business", "plan", "founder_granted_by").prefetch_related("services__business")
    pending_payments = SubscriptionPayment.objects.filter(status=SubscriptionPayment.STATUS_PENDING).select_related("subscription__primary_business", "plan")[:50]
    return render(request, "accounts/founder_subscriptions.html", {
        "form": form,
        "subscriptions": subscriptions,
        "pending_payments": pending_payments,
        "plans": SubscriptionPlan.objects.all().order_by("monthly_price", "id"),
        "payment_settings": payment_settings,
        "legacy_form": legacy_form,
        "legacy_import_report": legacy_import_report,
        "promotion_form": promotion_form,
        "promotions": SubscriptionPromotion.objects.select_related("plan", "created_by").order_by("-active", "-starts_at", "-id")[:50],
        "now": timezone.now(),
    })
