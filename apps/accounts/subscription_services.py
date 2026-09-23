import hashlib
import re
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Business
from .models import (
    BusinessFeatureAccess,
    BusinessModuleAccess,
    BusinessSubscription,
    FounderTrialGrant,
    PaidPlanTrialClaim,
    RoleModulePermission,
    SubscriptionPayment,
    SubscriptionPlan,
    SubscriptionPolicySettings,
    SubscriptionPlanModule,
    SubscriptionPromotion,
    SubscriptionService,
    UserBusiness,
)
from .services import ensure_permissions, invalidate_business_access_cache, seed_business_roles


def _track_subscription_event(event_type, subscription, *, user=None, metadata=None):
    try:
        from .analytics import record_platform_event
        record_platform_event(
            event_type, user=user, business=subscription.primary_business,
            metadata={"plan": subscription.plan.code, **(metadata or {})},
        )
    except Exception:
        pass


# POS is an operational role permission, not a commercial plan entitlement.
# This prevents a cashier from needing broad Commerce access simply to operate
# the in-premise counter.
PLAN_ENTITLEMENT_MODULES = tuple(
    (module, label) for module, label in RoleModulePermission.MODULE_CHOICES if module not in {"pos", "delivery_rider"}
)


PLAN_MATRIX = {
    SubscriptionPlan.CODE_STARTER: {
        "dashboard": "full", "inventory": "full", "sales": "full", "expenses": "full",
        "procurement": "none", "production": "none", "finance": "none", "reports": "basic",
        "users": "full", "commerce": "none", "audit": "none", "delivery": "none",
    },
    SubscriptionPlan.CODE_PRODUCTION: {
        "dashboard": "full", "inventory": "full", "procurement": "full", "production": "full",
        "sales": "full", "expenses": "full", "finance": "none", "reports": "full",
        "users": "full", "commerce": "none", "audit": "none", "delivery": "none",
    },
    SubscriptionPlan.CODE_BUSINESS_PRO: {
        module: ("none" if module in {"audit", "delivery"} else "full")
        for module, _ in PLAN_ENTITLEMENT_MODULES
    },
}


def build_plan_feature_matrix(plans):
    """Return one comparison matrix shared by public and in-app plan views.

    The table is intentionally derived from the persisted plan entitlements so
    Founder Console changes are reflected everywhere without duplicating plan
    claims in templates. POS and the rider workspace remain role permissions;
    their commercial availability follows Commerce and Delivery respectively.
    """
    plans = list(plans)
    entitlement_maps = {
        plan.pk: {row.module: row for row in plan.module_entitlements.all()}
        for plan in plans
    }

    def capacity_value(text):
        # Capacity rows carry meaningful text rather than a binary entitlement.
        return {"text": text, "state": "text"}

    rows = [
        {
            "label": "Users",
            "detail": "Maximum active users across the subscription.",
            "values": [
                capacity_value(
                    f"{plan.user_limit} user{'s' if plan.user_limit != 1 else ''}"
                    if plan.user_limit is not None else "Unlimited"
                )
                for plan in plans
            ],
        },
        {
            "label": "Additional service profiles",
            "detail": "Extra business/service profiles beyond the primary workspace.",
            "values": [
                capacity_value(
                    "Unlimited"
                    if plan.additional_service_limit is None
                    else (
                        f"{plan.additional_service_limit} add-on{'s' if plan.additional_service_limit != 1 else ''}"
                        if plan.additional_service_limit
                        else "None"
                    )
                )
                for plan in plans
            ],
        },
    ]

    feature_modules = list(PLAN_ENTITLEMENT_MODULES) + [
        ("pos", "In-Premise POS"),
        ("delivery_rider", "Delivery Rider Workspace"),
    ]
    details = {
        "dashboard": "Workspace overview and operational starting point.",
        "inventory": "Stock, materials, finished goods and movement history.",
        "procurement": "Purchase orders, receiving and supplier activity.",
        "production": "Production orders, batches, recipes/formulas and yield.",
        "sales": "Sales records, customers, credit and order history.",
        "expenses": "Operational expense capture and tracking.",
        "finance": "Cash accounts, movements, receivables and payables.",
        "reports": "Operational and financial reporting depth for the plan.",
        "users": "Business users, roles and permissions.",
        "commerce": "Hosted storefront, website API and commerce operations.",
        "delivery": "Delivery quoting, dispatch, tracking and provider workflows.",
        "audit": "Read-only external audit evidence workspace and audit packs.",
        "pos": "Dedicated cashier workspace; availability follows Commerce access.",
        "delivery_rider": "Purpose-specific rider workspace; availability follows Delivery access.",
    }

    for module, label in feature_modules:
        entitlement_module = "commerce" if module == "pos" else "delivery" if module == "delivery_rider" else module
        values = []
        for plan in plans:
            entitlement = entitlement_maps.get(plan.pk, {}).get(entitlement_module)
            if not entitlement or not entitlement.enabled or entitlement.level == SubscriptionPlanModule.LEVEL_NONE:
                values.append({"text": "Not included", "state": "off"})
            elif entitlement_module == "reports" and entitlement.level == SubscriptionPlanModule.LEVEL_BASIC:
                values.append({"text": "Basic", "state": "partial"})
            elif entitlement_module == "reports" and entitlement.level == SubscriptionPlanModule.LEVEL_FULL:
                values.append({"text": "Full", "state": "included"})
            else:
                values.append({"text": "Included", "state": "included"})
        rows.append({"label": label, "detail": details.get(module, ""), "values": values})
    return rows


def ensure_default_plans():
    """Ensure built-in plans and entitlement rows exist with bounded queries.

    Built-in defaults are seeds, not a policy reset. Founder-configured plan
    module rows remain authoritative; this routine only creates missing rows.
    """
    names = {
        SubscriptionPlan.CODE_STARTER: "STARTER",
        SubscriptionPlan.CODE_PRODUCTION: "PRODUCTION",
        SubscriptionPlan.CODE_BUSINESS_PRO: "BUSINESS PRO",
    }
    codes = tuple(names)
    general_trial_days = max(1, int(SubscriptionPolicySettings.load().general_trial_days or 30))

    plan_rows = list(SubscriptionPlan.objects.filter(code__in=codes))
    plans = {plan.code: plan for plan in plan_rows}
    missing_codes = [code for code in codes if code not in plans]
    if missing_codes:
        # Creation is exceptional, so retain get-or-create's strict conflict
        # behavior without paying its per-plan cost on ordinary page loads.
        with transaction.atomic():
            for code in missing_codes:
                plan, _created = SubscriptionPlan.objects.get_or_create(
                    code=code,
                    defaults={
                        "name": names[code], "trial_days": 0 if code == SubscriptionPlan.CODE_STARTER else general_trial_days,
                        "monthly_price": Decimal("0.00"),
                        "user_limit": 1 if code == SubscriptionPlan.CODE_STARTER else (5 if code == SubscriptionPlan.CODE_PRODUCTION else None),
                        "additional_service_limit": 0 if code == SubscriptionPlan.CODE_STARTER else (1 if code == SubscriptionPlan.CODE_PRODUCTION else None),
                    },
                )
                plans[code] = plan

    # Starter keeps its intentionally small capacity, while the Founder may
    # switch its commercial mode between free-forever (price 0) and paid. A
    # paid Starter receives the Founder-configured general trial window used by
    # paid plans; a free Starter has no expiry.
    plan_updates = []
    for plan in plans.values():
        changed = False
        expected_trial_days = 0 if plan.is_free_forever else general_trial_days
        if plan.trial_days != expected_trial_days:
            plan.trial_days = expected_trial_days
            changed = True
        if plan.code == SubscriptionPlan.CODE_STARTER:
            if plan.user_limit != 1:
                plan.user_limit = 1
                changed = True
            if plan.additional_service_limit != 0:
                plan.additional_service_limit = 0
                changed = True
        if changed:
            plan_updates.append(plan)
    entitlement_rows = list(
        SubscriptionPlanModule.objects.filter(plan_id__in=[plan.pk for plan in plans.values()])
    )
    entitlements = {(row.plan_id, row.module): row for row in entitlement_rows}
    missing_entitlements = []
    for code in codes:
        plan = plans.get(code)
        if not plan:
            continue
        for module, _label in PLAN_ENTITLEMENT_MODULES:
            level = PLAN_MATRIX[code].get(module, "none")
            enabled = level != "none"
            row = entitlements.get((plan.pk, module))
            if row is None:
                missing_entitlements.append(
                    SubscriptionPlanModule(
                        plan=plan, module=module, enabled=enabled, level=level,
                    )
                )
                continue
            # Existing rows may have been edited from the Founder Console.
            # Never re-impose PLAN_MATRIX on an existing entitlement.

    if plan_updates or missing_entitlements:
        with transaction.atomic():
            if plan_updates:
                SubscriptionPlan.objects.bulk_update(
                    plan_updates,
                    ["trial_days", "user_limit", "additional_service_limit"],
                )
            if missing_entitlements:
                SubscriptionPlanModule.objects.bulk_create(missing_entitlements, ignore_conflicts=True)

    return plans


def business_has_feature(business, feature):
    if not business:
        return False
    from core.context import get_request_cache

    cache = get_request_cache()
    key = ("business_feature_access", business.pk)
    if cache is not None and key in cache:
        feature_access = cache[key]
    else:
        feature_access = dict(
            BusinessFeatureAccess.objects.filter(business=business).values_list("feature", "enabled")
        )
        if cache is not None:
            cache[key] = feature_access
    row = feature_access.get(feature)
    # Missing feature rows retain full access until a subscription is explicitly applied.
    return row is not False


@transaction.atomic
def apply_subscriptions_entitlements(subscriptions):
    """Apply several subscription policies with a bounded set of bulk queries."""
    subscription_ids = [
        item.pk if isinstance(item, BusinessSubscription) else int(item)
        for item in subscriptions
        if item is not None
    ]
    if not subscription_ids:
        return []
    rows = list(
        BusinessSubscription.objects.filter(pk__in=subscription_ids)
        .select_related("plan")
        .prefetch_related("plan__module_entitlements", "services__business")
    )
    policies = {}
    businesses = {}
    for subscription in rows:
        plan_rows = {item.module: item for item in subscription.plan.module_entitlements.all()}
        source = (
            BusinessModuleAccess.SOURCE_FOUNDER
            if subscription.founder_lifetime
            else BusinessModuleAccess.SOURCE_PLAN
        )
        active = subscription.is_effectively_active
        reports = plan_rows.get("reports")
        for service in subscription.services.all():
            business = service.business
            businesses[business.pk] = business
            policies[business.pk] = {
                "source": source,
                "modules": {
                    module: bool(
                        (active and plan_rows.get(module) and plan_rows[module].enabled)
                        or (not active and module == "dashboard")
                    )
                    for module, _label in PLAN_ENTITLEMENT_MODULES
                },
                "reports_full": bool(
                    active and reports and reports.enabled and reports.level == "full"
                ),
            }

    business_ids = list(policies)
    access_by_key = {
        (item.business_id, item.module): item
        for item in BusinessModuleAccess.objects.filter(business_id__in=business_ids)
    }
    access_to_create = []
    access_to_update = []
    for business_id, policy in policies.items():
        for module, enabled in policy["modules"].items():
            item = access_by_key.get((business_id, module))
            if item is None:
                access_to_create.append(BusinessModuleAccess(
                    business_id=business_id, module=module,
                    enabled=enabled, source=policy["source"],
                ))
            elif item.enabled != enabled or item.source != policy["source"]:
                item.enabled = enabled
                item.source = policy["source"]
                access_to_update.append(item)
    if access_to_create:
        BusinessModuleAccess.objects.bulk_create(access_to_create, ignore_conflicts=True)
    if access_to_update:
        BusinessModuleAccess.objects.bulk_update(access_to_update, ["enabled", "source"])

    features = {
        item.business_id: item
        for item in BusinessFeatureAccess.objects.filter(
            business_id__in=business_ids, feature="reports_full"
        )
    }
    features_to_create = []
    features_to_update = []
    for business_id, policy in policies.items():
        item = features.get(business_id)
        enabled = policy["reports_full"]
        if item is None:
            features_to_create.append(BusinessFeatureAccess(
                business_id=business_id, feature="reports_full",
                enabled=enabled, source=policy["source"],
            ))
        elif item.enabled != enabled or item.source != policy["source"]:
            item.enabled = enabled
            item.source = policy["source"]
            features_to_update.append(item)
    if features_to_create:
        BusinessFeatureAccess.objects.bulk_create(features_to_create, ignore_conflicts=True)
    if features_to_update:
        BusinessFeatureAccess.objects.bulk_update(features_to_update, ["enabled", "source"])
    for business in businesses.values():
        invalidate_business_access_cache(business)
    return rows


def apply_subscription_entitlements(subscription):
    """Make BusinessModuleAccess the hard entitlement boundary for one subscription."""
    rows = apply_subscriptions_entitlements([subscription])
    return next((item for item in rows if item.pk == subscription.pk), subscription)


@transaction.atomic
def start_trial_for_business(business, plan=None, *, trial_days_override=None):
    plans = ensure_default_plans()
    plan = plan or plans[SubscriptionPlan.CODE_STARTER]
    now = timezone.now()
    is_free_plan = plan.is_free_forever
    policy_days = max(1, int(SubscriptionPolicySettings.load().general_trial_days or 30))
    trial_days = max(1, int(trial_days_override or policy_days))
    subscription, created = BusinessSubscription.objects.get_or_create(
        primary_business=business,
        defaults={
            "plan": plan,
            "status": BusinessSubscription.STATUS_ACTIVE if is_free_plan else BusinessSubscription.STATUS_TRIAL,
            "trial_ends_at": None if is_free_plan else now + timezone.timedelta(days=trial_days),
        },
    )
    if created:
        SubscriptionService.objects.create(subscription=subscription, business=business, is_primary=True)
        apply_subscription_entitlements(subscription)
        from .models import PlatformEvent
        _track_subscription_event(PlatformEvent.EVENT_SUBSCRIPTION_STARTED, subscription, metadata={"status": subscription.status})
    return subscription


def _trial_credentials(user):
    values = [
        (PaidPlanTrialClaim.KIND_USERNAME, (user.username or "").strip().casefold()),
        (PaidPlanTrialClaim.KIND_EMAIL, (user.email or "").strip().casefold()),
        (PaidPlanTrialClaim.KIND_PHONE, re.sub(r"\D", "", user.phone or "")),
    ]
    return [(kind, value) for kind, value in values if value]


def _trial_fingerprint(kind, value):
    raw = f"{settings.SECRET_KEY}|paid-plan-trial|{kind}|{value}".encode()
    return hashlib.sha256(raw).hexdigest()


def paid_trial_available(user):
    fingerprints = [_trial_fingerprint(kind, value) for kind, value in _trial_credentials(user)]
    if not fingerprints:
        return False
    return not PaidPlanTrialClaim.objects.filter(credential_fingerprint__in=fingerprints).exists()


@transaction.atomic
def start_paid_plan_trial(subscription, plan, user):
    if not plan.active:
        raise ValidationError("That paid plan is not currently available.")
    if plan.code == SubscriptionPlan.CODE_STARTER or Decimal(plan.monthly_price or 0) <= 0:
        raise ValidationError("The free Starter plan does not need a trial.")
    subscription = BusinessSubscription.objects.select_for_update().select_related("plan").get(pk=subscription.pk)
    if subscription.status == BusinessSubscription.STATUS_TRIAL and subscription.is_effectively_active:
        subscription.plan = plan
        subscription.save(update_fields=["plan"])
        return apply_subscription_entitlements(subscription)
    if (
        subscription.founder_lifetime
        or subscription.status != BusinessSubscription.STATUS_ACTIVE
        or subscription.plan.code != SubscriptionPlan.CODE_STARTER
    ):
        raise ValidationError("This subscription is not eligible for a new paid-plan trial.")
    credentials = _trial_credentials(user)
    fingerprints = [(kind, _trial_fingerprint(kind, value)) for kind, value in credentials]
    if not fingerprints or PaidPlanTrialClaim.objects.filter(
        credential_fingerprint__in=[fingerprint for _kind, fingerprint in fingerprints]
    ).exists():
        raise ValidationError("A paid-plan free trial has already been used with these account credentials.")
    try:
        with transaction.atomic():
            PaidPlanTrialClaim.objects.bulk_create([
                PaidPlanTrialClaim(
                    credential_kind=kind,
                    credential_fingerprint=fingerprint,
                    user=user,
                    subscription=subscription,
                    plan=plan,
                )
                for kind, fingerprint in fingerprints
            ])
    except IntegrityError as exc:
        raise ValidationError("A paid-plan free trial has already been used with these account credentials.") from exc
    subscription.plan = plan
    subscription.status = BusinessSubscription.STATUS_TRIAL
    trial_days = max(1, int(SubscriptionPolicySettings.load().general_trial_days or 30))
    subscription.trial_ends_at = timezone.now() + timezone.timedelta(days=trial_days)
    subscription.paid_until = None
    subscription.save(update_fields=["plan", "status", "trial_ends_at", "paid_until"])
    subscription = apply_subscription_entitlements(subscription)
    from .models import PlatformEvent
    _track_subscription_event(PlatformEvent.EVENT_SUBSCRIPTION_TRIAL, subscription, user=user)
    return subscription


@transaction.atomic
def cancel_paid_plan_trial(subscription):
    plans = ensure_default_plans()
    subscription = BusinessSubscription.objects.select_for_update().get(pk=subscription.pk)
    if subscription.status != BusinessSubscription.STATUS_TRIAL:
        raise ValidationError("Only an active paid-plan trial can be cancelled.")
    starter = plans[SubscriptionPlan.CODE_STARTER]
    subscription.plan = starter
    subscription.status = (
        BusinessSubscription.STATUS_ACTIVE
        if starter.is_free_forever
        else BusinessSubscription.STATUS_EXPIRED
    )
    subscription.trial_ends_at = None
    subscription.paid_until = None
    subscription.save(update_fields=["plan", "status", "trial_ends_at", "paid_until"])
    return apply_subscription_entitlements(subscription)


def subscription_for_business(business):
    service = getattr(business, "subscription_service", None)
    if service:
        return service.subscription
    return BusinessSubscription.objects.filter(primary_business=business).select_related("plan").first()


def assert_user_capacity(business, user=None):
    subscription = subscription_for_business(business)
    if not subscription or subscription.plan.user_limit is None:
        return
    business_ids = subscription.services.values_list("business_id", flat=True)
    active_user_ids = UserBusiness.objects.filter(
        business_id__in=business_ids, active=True, user__is_active=True
    ).values_list("user_id", flat=True).distinct()
    if user and UserBusiness.objects.filter(
        business_id__in=business_ids, active=True, user=user, user__is_active=True
    ).exists():
        return
    if active_user_ids.count() >= subscription.plan.user_limit:
        raise ValidationError(
            f"{subscription.plan.name} allows {subscription.plan.user_limit} active user(s). Change plan or deactivate a user before adding another."
        )


@transaction.atomic
def switch_subscription_plan(subscription, plan, *, keep_expiry=True):
    subscription.plan = plan
    if not keep_expiry and not subscription.founder_lifetime:
        subscription.status = BusinessSubscription.STATUS_ACTIVE
        subscription.paid_until = timezone.now() + timezone.timedelta(days=30)
    subscription.save()
    subscription = apply_subscription_entitlements(subscription)
    from .models import PlatformEvent
    _track_subscription_event(PlatformEvent.EVENT_SUBSCRIPTION_CHANGED, subscription)
    return subscription


@transaction.atomic
def grant_founder_trial_extension(subscription, plan, days, actor, note=""):
    """Grant or extend a trial without disturbing a live paid or lifetime term."""
    days = max(1, min(3650, int(days)))
    subscription = (
        BusinessSubscription.objects.select_for_update()
        .select_related("plan", "primary_business")
        .get(pk=subscription.pk)
    )
    now = timezone.now()
    if subscription.founder_lifetime:
        raise ValidationError("Revoke founder lifetime access before granting a trial window.")
    if (
        subscription.status == BusinessSubscription.STATUS_ACTIVE
        and subscription.paid_until
        and subscription.paid_until >= now
    ):
        raise ValidationError("This business already has an active paid term; a founder trial would overlap it.")

    previous_end = subscription.trial_ends_at if subscription.status == BusinessSubscription.STATUS_TRIAL else None
    base = previous_end if previous_end and previous_end > now else now
    granted_end = base + timezone.timedelta(days=days)
    subscription.plan = plan
    subscription.status = BusinessSubscription.STATUS_TRIAL
    subscription.trial_ends_at = granted_end
    subscription.paid_until = None
    subscription.save(update_fields=["plan", "status", "trial_ends_at", "paid_until"])
    FounderTrialGrant.objects.create(
        subscription=subscription,
        plan=plan,
        days=days,
        previous_ends_at=previous_end,
        granted_ends_at=granted_end,
        granted_by=actor,
        note=note or "",
    )
    subscription = apply_subscription_entitlements(subscription)
    from .models import PlatformEvent
    _track_subscription_event(
        PlatformEvent.EVENT_SUBSCRIPTION_TRIAL, subscription, user=actor,
        metadata={"founder_extension_days": days, "trial_ends_at": granted_end.isoformat()},
    )
    return subscription


@transaction.atomic
def grant_founder_lifetime(subscription, plan, actor, note=""):
    subscription.plan = plan
    subscription.status = BusinessSubscription.STATUS_FOUNDER
    subscription.founder_lifetime = True
    subscription.founder_granted_by = actor
    subscription.founder_granted_at = timezone.now()
    subscription.founder_note = note
    subscription.trial_ends_at = None
    subscription.paid_until = None
    subscription.save()
    subscription = apply_subscription_entitlements(subscription)
    from .models import PlatformEvent
    _track_subscription_event(PlatformEvent.EVENT_SUBSCRIPTION_FOUNDER, subscription, user=actor)
    return subscription


@transaction.atomic
def revoke_founder_lifetime(subscription):
    subscription.founder_lifetime = False
    subscription.founder_granted_by = None
    subscription.founder_granted_at = None
    subscription.status = BusinessSubscription.STATUS_EXPIRED
    subscription.save()
    return apply_subscription_entitlements(subscription)


@transaction.atomic
def add_service_business(subscription, *, name, service_type, actor):
    """Provision a separate Business profile under the same commercial subscription."""
    from django.utils.text import slugify
    additional_count = subscription.services.filter(is_primary=False).count()
    limit = subscription.plan.additional_service_limit
    if limit is not None and additional_count >= limit:
        raise ValidationError(
            f"{subscription.plan.name} allows {limit} additional service profile(s). Change plan before adding another."
        )
    base = (slugify(name) or "business")[:52]
    slug = base
    suffix = 2
    while Business.objects.filter(slug=slug).exists():
        token = f"-{suffix}"
        slug = f"{base[:60-len(token)]}{token}"
        suffix += 1
    business = Business.objects.create(
        name=name.strip(), slug=slug, vertical=service_type,
        accent_color=subscription.primary_business.accent_color,
        background_color=subscription.primary_business.background_color,
        currency_symbol=subscription.primary_business.currency_symbol,
    )
    roles = seed_business_roles(business)
    SubscriptionService.objects.create(subscription=subscription, business=business, is_primary=False)
    # Give every active member of the primary profile the same system-role key where possible.
    for membership in UserBusiness.objects.filter(business=subscription.primary_business, active=True).select_related("user", "role"):
        role = roles.get(membership.role.key) or roles.get("stock_keeper")
        new_membership, _ = UserBusiness.objects.get_or_create(
            user=membership.user, business=business, defaults={"role": role, "active": True}
        )
        ensure_permissions(new_membership)
    apply_subscription_entitlements(subscription)
    return business


def active_promotion_for_plan(plan, *, moment=None, billing_cycle=None):
    """Resolve one effective plan-specific or all-plan promotion for a billing cycle."""
    moment = moment or timezone.now()
    prefetched = getattr(plan, "_active_promotions", None)
    if prefetched is not None:
        candidates = [
            promo for promo in prefetched
            if (
                promo.applies_to_plan(plan)
                and promo.active
                and promo.starts_at <= moment < promo.ends_at
                and (billing_cycle is None or promo.applies_to_billing_cycle(billing_cycle))
            )
        ]
        # A plan-specific promo wins over an all-plan promo if historical data
        # contains an overlap; form validation prevents creating new overlaps.
        return sorted(
            candidates,
            key=lambda promo: (not promo.applies_to_all_plans, promo.starts_at, promo.pk or 0),
            reverse=True,
        )[0] if candidates else None
    return (
        SubscriptionPromotion.objects.filter(
            Q(plan=plan) | Q(applies_to_all_plans=True),
            active=True, starts_at__lte=moment, ends_at__gt=moment,
        )
        .filter(
            Q(billing_cycle=SubscriptionPromotion.CYCLE_BOTH) | Q(billing_cycle=billing_cycle)
            if billing_cycle else Q()
        )
        .order_by("applies_to_all_plans", "-starts_at", "-id")
        .first()
    )


def attach_active_promotions(plans, *, moment=None):
    """Attach active promotions and per-plan effective promo prices in one query."""
    moment = moment or timezone.now()
    plans = list(plans)
    plan_ids = [plan.pk for plan in plans]
    rows = list(SubscriptionPromotion.objects.filter(
        Q(plan_id__in=plan_ids) | Q(applies_to_all_plans=True),
        active=True, starts_at__lte=moment, ends_at__gt=moment,
    ).select_related("plan").order_by("applies_to_all_plans", "-starts_at", "-id"))
    for plan in plans:
        plan._active_promotions = [promo for promo in rows if promo.applies_to_plan(plan)]
        plan.current_monthly_promotion = active_promotion_for_plan(
            plan, moment=moment, billing_cycle=SubscriptionPayment.CYCLE_MONTHLY
        )
        plan.current_yearly_promotion = active_promotion_for_plan(
            plan, moment=moment, billing_cycle=SubscriptionPayment.CYCLE_YEARLY
        )
        # Backward-compatible display hook: prefer a monthly offer, otherwise
        # expose the yearly-only offer so marketing can still announce it.
        plan.current_promotion = plan.current_monthly_promotion or plan.current_yearly_promotion
        if plan.current_monthly_promotion:
            plan.promo_monthly_price = plan.current_monthly_promotion.discounted_monthly_price_for(plan)
            plan.promo_additional_service_monthly_price = plan.current_monthly_promotion.discounted_additional_service_monthly_price_for(plan)
        if plan.current_yearly_promotion:
            plan.promo_yearly_price = plan.current_yearly_promotion.discounted_yearly_price_for(plan)
    return plans


def effective_monthly_price(plan, promotion=None, *, billing_cycle=SubscriptionPayment.CYCLE_MONTHLY):
    if promotion is False:
        return Decimal(plan.monthly_price or 0).quantize(Decimal("0.01"))
    promotion = promotion if promotion is not None else active_promotion_for_plan(plan, billing_cycle=billing_cycle)
    return promotion.discounted_monthly_price_for(plan) if promotion else Decimal(plan.monthly_price or 0).quantize(Decimal("0.01"))


def effective_additional_service_monthly_price(plan, promotion=None, *, billing_cycle=SubscriptionPayment.CYCLE_MONTHLY):
    if promotion is False:
        return plan.additional_service_monthly_price
    promotion = promotion if promotion is not None else active_promotion_for_plan(plan, billing_cycle=billing_cycle)
    if promotion:
        return promotion.discounted_additional_service_monthly_price_for(plan)
    return plan.additional_service_monthly_price


def payment_amount(plan, service_count, months=1, billing_cycle="monthly", promotion=None):
    service_count = max(1, int(service_count or 1))
    if promotion is None:
        promotion = active_promotion_for_plan(plan, billing_cycle=billing_cycle)
    base_monthly = effective_monthly_price(plan, promotion, billing_cycle=billing_cycle)
    addon_monthly = effective_additional_service_monthly_price(plan, promotion, billing_cycle=billing_cycle)
    monthly_total = base_monthly + Decimal(service_count - 1) * addon_monthly
    if billing_cycle == SubscriptionPayment.CYCLE_YEARLY:
        discount = min(max(plan.yearly_discount_percent, Decimal("0")), Decimal("100"))
        return (monthly_total * Decimal("12") * (Decimal("1") - discount / Decimal("100"))).quantize(Decimal("0.01"))
    months = max(1, int(months or 1))
    return (monthly_total * Decimal(months)).quantize(Decimal("0.01"))


def payment_is_locked(subscription, plan):
    """Prevent premature renewal of the plan already providing active access."""
    if not subscription or not subscription.is_effectively_active or subscription.plan_id != plan.pk:
        return False
    return subscription.founder_lifetime or not subscription.is_expiring_soon


@transaction.atomic
def create_payment_request(subscription, plan, *, months=1, billing_cycle="monthly", provider="manual"):
    if payment_is_locked(subscription, plan):
        if subscription.founder_lifetime:
            raise ValidationError("Your current plan has founder lifetime access and does not require payment.")
        raise ValidationError("Renewal for your current plan opens within 7 days of its expiry date.")
    if billing_cycle == SubscriptionPayment.CYCLE_YEARLY:
        months = 12
    service_count = max(1, subscription.services.count())
    promotion = active_promotion_for_plan(plan, billing_cycle=billing_cycle)
    base_amount = payment_amount(plan, service_count, months, billing_cycle=billing_cycle, promotion=False)
    amount = payment_amount(plan, service_count, months, billing_cycle=billing_cycle, promotion=promotion)
    if amount <= 0:
        raise ValidationError("This plan does not yet have a payable price configured. Contact the INPROFIC founder/superuser.")
    return SubscriptionPayment.objects.create(
        subscription=subscription,
        plan=plan,
        amount=amount,
        base_amount=base_amount,
        promotion=promotion,
        promotion_reason=(promotion.reason if promotion else ""),
        promotion_discount_amount=max(Decimal("0"), base_amount - amount),
        service_count=service_count,
        months=months,
        billing_cycle=billing_cycle,
        provider=provider,
        reference=f"SUB-{subscription.pk}-{uuid4().hex[:12].upper()}",
    )


@transaction.atomic
def mark_payment_paid(payment):
    # Re-read both rows under locks. Payment callbacks can arrive after a
    # founder has changed the subscription, so the object originally loaded
    # by the request may no longer represent the current entitlement state.
    payment = (
        SubscriptionPayment.objects.select_for_update()
        .select_related("subscription", "plan")
        .get(pk=payment.pk)
    )
    if payment.status == SubscriptionPayment.STATUS_PAID:
        return payment
    subscription = (
        BusinessSubscription.objects.select_for_update()
        .select_related("plan")
        .get(pk=payment.subscription_id)
    )
    now = timezone.now()
    payment.status = SubscriptionPayment.STATUS_PAID
    payment.paid_at = now
    payment.save(update_fields=["status", "paid_at"])

    # A stale payment request must not revoke a newer founder lifetime grant.
    # A payment created after that grant is still allowed to represent an
    # intentional, explicitly confirmed switch to another paid plan.
    if subscription.founder_lifetime and (
        subscription.founder_granted_at is None
        or payment.created_at <= subscription.founder_granted_at
    ):
        return payment

    subscription.plan = payment.plan
    subscription.status = BusinessSubscription.STATUS_ACTIVE
    subscription.founder_lifetime = False
    base = max([x for x in (subscription.paid_until, subscription.trial_ends_at, now) if x is not None])
    duration_days = 365 if payment.billing_cycle == SubscriptionPayment.CYCLE_YEARLY else 30 * payment.months
    subscription.paid_until = base + timezone.timedelta(days=duration_days)
    subscription.save()
    apply_subscription_entitlements(subscription)
    from .models import PlatformEvent
    _track_subscription_event(
        PlatformEvent.EVENT_SUBSCRIPTION_PAID, subscription,
        metadata={"amount": str(payment.amount), "billing_cycle": payment.billing_cycle, "provider": payment.provider},
    )
    return payment
