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
from .models import BILLING_CYCLE_LABELS, BILLING_CYCLE_MONTHS
from .models import (
    BusinessPayrollAddon,
    BusinessFeatureAccess,
    BusinessModuleAccess,
    BusinessSubscription,
    FounderTrialGrant,
    PaidPlanTrialClaim,
    PayrollAddonTier,
    PayrollStaffBatch,
    PlatformEvent,
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
    (module, label) for module, label in RoleModulePermission.MODULE_CHOICES if module not in {"pos", "delivery_rider", "kitchen", "ledger"}
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


def attach_payroll_tiers(plans):
    """Attach each plan's active payroll packages and extra-staff batches.

    Sets ``plan.payroll_tiers`` (ordered by capacity, unlimited last),
    ``plan.payroll_batches`` (the shared, plan-independent extra-staff batches,
    smallest first) and
    ``plan.payroll_from`` (cheapest monthly package price, or None when the
    Founder has not priced the add-on for that plan). Shown to every tenant and
    visitor, including trial and Founder-lifetime tenants who currently get it
    free.
    """
    plans = list(plans)
    by_plan = {plan.pk: [] for plan in plans}
    ids = list(by_plan)
    for tier in PayrollAddonTier.objects.filter(plan_id__in=ids, active=True):
        by_plan[tier.plan_id].append(tier)
    # Batches are not tied to a plan: one query, shared by every plan.
    batches = list(PayrollStaffBatch.objects.filter(active=True).order_by("staff_count", "id"))
    for plan in plans:
        plan.payroll_tiers = by_plan[plan.pk]
        plan.payroll_batches = batches
        plan.payroll_from = min((t.monthly_price for t in plan.payroll_tiers), default=None)
    return plans


def build_plan_feature_matrix(plans, currency_symbol="\u20a6"):
    """Return one comparison matrix shared by public and in-app plan views.

    The table is intentionally derived from the persisted plan entitlements so
    Founder Console changes are reflected everywhere without duplicating plan
    claims in templates. POS and the rider workspace remain role permissions;
    their commercial availability follows Commerce and Delivery respectively.
    """
    plans = list(plans)
    from .platform_integrations import shipbubble_platform_enabled, whatsapp_storefront_platform_enabled
    shipbubble_features = []
    if shipbubble_platform_enabled():
        shipbubble_features.append((
            "Shipbubble delivery integration",
            "Connect the business's own Shipbubble account for configurable courier choice, parcel defaults, live quotes and dispatch when the platform adapter is enabled.",
            "delivery",
        ))
    whatsapp_features = []
    if whatsapp_storefront_platform_enabled():
        whatsapp_features.append((
            "WhatsApp storefront & AI sales assistant",
            "Answer from the live catalogue, take simple orders and payments in chat, and use hosted checkout for product options or payment proof; includes a staff inbox for handoff.",
            "commerce",
        ))
    for plan in plans:
        plan.matrix_display_name = re.sub(r"\bfounder\b", "Complimentary", plan.name, flags=re.IGNORECASE).strip()
    entitlement_maps = {
        plan.pk: {row.module: row for row in plan.module_entitlements.all()}
        for plan in plans
    }

    def capacity_value(text):
        # Capacity rows carry meaningful text rather than a binary entitlement.
        return {"text": text, "state": "text"}

    def payroll_value(plan):
        tiers = getattr(plan, "payroll_tiers", None)
        if not tiers:
            return capacity_value("Pricing coming soon") if tiers is not None else capacity_value("Available as add-on")
        limits = [t.staff_limit for t in tiers if not t.unlimited]
        has_unlimited = any(t.unlimited for t in tiers)
        value = capacity_value(f"From {currency_symbol}{plan.payroll_from:,.2f} / mo")
        if has_unlimited and not limits:
            sub = "Unlimited staff"
        elif has_unlimited:
            sub = f"{limits[0]}\u2013{limits[-1]} staff, or unlimited"
        else:
            sub = f"{limits[0]}\u2013{limits[-1]} staff" if len(limits) > 1 else f"Up to {limits[0]} staff"
        if getattr(plan, "payroll_batches", None) and not (has_unlimited and not limits):
            sub += " + extra batches"
        value["sub"] = sub
        return value

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
        {
            "label": "Staff payroll add-on",
            "detail": "Supplementary payroll is included during a free trial and complimentary access; after that, subsequent staff tiers are purchased separately.",
            "values": [payroll_value(plan) for plan in plans],
        },
    ]

    feature_modules = list(PLAN_ENTITLEMENT_MODULES) + [
        ("pos", "In-Premise POS"),
        ("delivery_rider", "Delivery Rider Workspace"),
        ("kitchen", "Production Entry Workspace"),
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
        "delivery": "Delivery quoting, dispatch, tracking and courier-partner workflows.",
        "audit": "Read-only external audit evidence workspace and audit packs.",
        "pos": "Dedicated cashier workspace; availability follows Commerce access.",
        "delivery_rider": "Purpose-specific rider workspace; availability follows Delivery access.",
        "kitchen": "Purpose-specific production-entry workspace for kitchen staff; availability follows Production access.",
    }
    entitlement_for = {"pos": "commerce", "delivery_rider": "delivery", "kitchen": "production"}

    def module_state(plan, entitlement_module, require_full=False):
        entitlement = entitlement_maps.get(plan.pk, {}).get(entitlement_module)
        if not entitlement or not entitlement.enabled or entitlement.level == SubscriptionPlanModule.LEVEL_NONE:
            return {"text": "Not included", "state": "off"}
        if entitlement_module == "reports" or require_full:
            if entitlement.level == SubscriptionPlanModule.LEVEL_BASIC:
                return {"text": "Basic", "state": "partial"} if not require_full else {"text": "Not included", "state": "off"}
            return {"text": "Full" if entitlement_module == "reports" else "Included", "state": "included"}
        return {"text": "Included", "state": "included"}

    def group(name):
        return {"group": name, "label": "", "detail": "", "values": []}

    def feature_row(label, detail, module, require_full=False):
        # Purpose-specific workspaces (POS, rider, production entry) have no plan
        # entitlement of their own; they follow the module they sit inside.
        entitlement_module = entitlement_for.get(module, module)
        return {"label": label, "detail": detail,
                "values": [module_state(plan, entitlement_module, require_full) for plan in plans]}

    def cycle_text(plan):
        parts = []
        for key, label in (("quarterly", "Quarterly"), ("biannual", "Bi-annual"), ("yearly", "Yearly")):
            pct = plan.cycle_discount_percent(key)
            if pct:
                parts.append(f"{label} {pct.normalize():f}% off")
        return capacity_value("Monthly" if plan.is_free_forever else "Monthly, quarterly, bi-annual, yearly") | (
            {"sub": " · ".join(parts)} if parts and not plan.is_free_forever else {}
        )

    def trial_text(plan):
        if plan.is_free_forever:
            return capacity_value("Free forever")
        return capacity_value(f"{plan.trial_days}-day free trial" if plan.trial_days else "Paid from day one")

    rows.insert(0, group("Capacity & billing"))
    rows.append({"label": "Free trial", "detail": "How a new workspace starts on this plan.", "values": [trial_text(p) for p in plans]})
    rows.append({"label": "Billing cycles", "detail": "Pay monthly, or commit to a longer cycle for a discount.", "values": [cycle_text(p) for p in plans]})

    rows.append(group("Workspace modules"))
    for module, label in feature_modules:
        rows.append({"label": label, "detail": details.get(module, ""),
                     "values": [module_state(plan, entitlement_for.get(module, module)) for plan in plans]})

    # Feature-level rows. Each follows the entitlement of the module that owns
    # it, so Founder Console plan changes flow through without a second source of truth.
    catalog = [
        ("Inventory & purchasing", [
            ("Raw materials & finished goods", "Track what you buy and what you sell, separately, with full movement history.", "inventory"),
            ("Buy-unit vs use-unit conversion", "Buy flour by the bag and use it by the kilogram; stock and recipe usage stay consistent, and live conversions change cost and quantities together.", "inventory"),
            ("Product categories & channel pricing", "Group products into categories and price them per channel (store, distribution, online).", "inventory"),
            ("Low-stock & warning alerts", "Persistent, per-user alerts for raw materials and finished goods.", "inventory"),
            ("Purchase orders & receiving", "Order from suppliers, receive stock and keep the cost history behind every price change.", "procurement"),
            ("Supplier balances & payments", "See what you owe each supplier and pay it down through Finance.", "procurement"),
        ]),
        ("Production", [
            ("Recipes & bills of materials", "Define what each product is made from, including packaging and other inputs.", "production"),
            ("Production orders & batches", "Release materials, record what was actually made, wastage, yield, batch number and expiry.", "production"),
            ("Flexible materials", "Mark a material as decided at the bench (yeast, seasoning) instead of fixed by the recipe.", "production"),
            ("Shared production runs", "Make several customer or store orders in one exercise while keeping each order separate.", "production"),
            ("Quality checks & batch costing", "Attach inspection results to a batch and freeze its material cost as evidence.", "production"),
            ("Production-floor entry roles", "The people who make the product (Baker for bakeries, Chef for restaurants, Production Staff for general production) log it through the normal order form, trimmed to what the floor needs. The order is saved with no channel and a manager assigns it later.", "kitchen"),
            ("Guided entry (no recipe access)", "A variant of the floor role with no access to recipes: it sees the order and sets only the flexible material amounts.", "kitchen"),
            ("Assign a production entry to channels", "A manager says how many of each product go to which channel, right from one page; the floor's recorded output is shared in proportion.", "kitchen"),
        ]),
        ("Selling & commerce", [
            ("Hosted storefront & Order Now links", "A branded online catalogue and shareable order links, with payment-first checkout.", "commerce"),
            ("Targeted QR codes for social selling", "Generate QR codes aimed at specific platforms or campaigns so each audience lands on the right order page.", "commerce"),
            ("Headless Commerce API", "Run checkout from your own website or app and let INPROFIC handle the order, payment and fulfilment.", "commerce"),
            *whatsapp_features,
            ("Paystack, Monnify & transfer payments", "Gateway payments or a no-gateway manual transfer mode with verification.", "commerce"),
            ("In-premise POS", "A focused counter-sales workspace for cashiers.", "pos"),
            ("Customer records & pricing", "Reusable customer profiles, customer-specific prices and receivable tracking.", "sales"),
        ]),
        ("Delivery", [
            ("In-house riders & rider workspace", "Register your own riders; each gets a lightweight My Deliveries screen instead of the full workspace.", "delivery"),
            ("Delivery zones with radius coverage", "Map zones on a graphical editor; fees come from real distance and your pricing bands.", "delivery"),
            ("Precise address validation", "Checkout locates the address and checks it against your coverage before payment.", "delivery"),
            ("Courier partner integrations", "Connect a custom delivery partner (or the optional built-in adapter) next to your own riders; INPROFIC keeps the quote, fee, tracking and audit trail.", "delivery"),
            *shipbubble_features,
            ("Hybrid routing & customer choice", "Use riders, couriers or both, with a clear switch policy shown before payment.", "delivery"),
            ("Proof of delivery & live tracking", "Status timeline, delivery proof and customer tracking on every order.", "delivery"),
        ]),
        ("Finance & reporting", [
            ("Cash accounts & movements", "Keep cash truth separate from sales: receipts, payments and transfers.", "finance"),
            ("Receivables, payables & profit", "Customer balances, supplier balances, gross profit and cash flow.", "finance"),
            ("Sales & stock CSV exports", "Dated sales and stock exports available on every plan with reports.", "reports"),
            ("Excel exports & full backup", "Procurement, production, finance and adjustment exports plus a full JSON backup.", "reports", True),
            ("Expense tracking", "Capture and categorise operating expenses.", "expenses"),
        ]),
        ("Team, trust & control", [
            ("Users, roles & per-user permissions", "Purpose-built roles plus optional per-user overrides, always inside the plan ceiling.", "users"),
            ("Audit trail", "A tamper-evident history of important actions across the workspace.", "users"),
            ("Audit Workspace for external auditors", "A read-only review environment where auditors can raise queries without changing records.", "audit"),
        ]),
    ]
    for group_name, items in catalog:
        rows.append(group(group_name))
        for item in items:
            label, detail, module = item[0], item[1], item[2]
            rows.append(feature_row(label, detail, module, require_full=(len(item) > 3 and item[3])))
    # Tag every row (group markers included) with the group it belongs to so the
    # template can render one accordion panel per group via ``{% regroup %}``.
    current_section = ""
    for row in rows:
        if row.get("group"):
            current_section = row["group"]
        row["section"] = current_section
    return rows


def ensure_default_plans(*, general_trial_days=None):
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
    if general_trial_days is None:
        general_trial_days = SubscriptionPolicySettings.load().general_trial_days
    general_trial_days = max(1, int(general_trial_days or 30))

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
        tagline=subscription.primary_business.tagline,
        contact_phone=subscription.primary_business.contact_phone,
        contact_email=subscription.primary_business.contact_email,
        contact_address=subscription.primary_business.contact_address,
        contact_website=subscription.primary_business.contact_website,
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
        # One ordered description of every billing cycle, shared by the public
        # pricing page and the in-app plan pages so both switch panels agree.
        options = []
        for cycle, months in BILLING_CYCLE_MONTHS.items():
            promo = active_promotion_for_plan(plan, moment=moment, billing_cycle=cycle) if cycle != "monthly" else plan.current_monthly_promotion
            total = plan.price_for_cycle(cycle)
            promo_total = promo.discounted_cycle_price_for(plan, cycle) if promo else None
            effective = promo_total if promo_total is not None else total
            options.append({
                "key": cycle, "label": BILLING_CYCLE_LABELS[cycle], "months": months,
                "period": {"monthly": "month", "quarterly": "quarter", "biannual": "6 months", "yearly": "year"}[cycle],
                "discount": plan.cycle_discount_percent(cycle),
                "total": total, "promo_total": promo_total,
                "per_month": (effective / months).quantize(Decimal("0.01")),
                "promotion": promo,
            })
        plan.cycle_options = options
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
    if billing_cycle in BILLING_CYCLE_MONTHS and billing_cycle != SubscriptionPayment.CYCLE_MONTHLY:
        discount = plan.cycle_discount_percent(billing_cycle)
        cycle_months = Decimal(BILLING_CYCLE_MONTHS[billing_cycle])
        return (monthly_total * cycle_months * (Decimal("1") - discount / Decimal("100"))).quantize(Decimal("0.01"))
    months = max(1, int(months or 1))
    return (monthly_total * Decimal(months)).quantize(Decimal("0.01"))


def payment_is_locked(subscription, plan):
    """Prevent premature renewal of the plan already providing active access."""
    if not subscription or not subscription.is_effectively_active or subscription.plan_id != plan.pk:
        return False
    return subscription.founder_lifetime or not subscription.is_expiring_soon


@transaction.atomic
def create_payment_request(subscription, plan, *, months=1, billing_cycle="monthly", provider="manual", purpose=None, payroll_tier=None, payroll_batch=None, batch_quantity=1):
    purpose = purpose or SubscriptionPayment.PURPOSE_SUBSCRIPTION
    if billing_cycle in BILLING_CYCLE_MONTHS and billing_cycle != SubscriptionPayment.CYCLE_MONTHLY:
        months = BILLING_CYCLE_MONTHS[billing_cycle]
    service_count = max(1, subscription.services.count())
    if purpose == SubscriptionPayment.PURPOSE_PAYROLL_BATCH:
        from .payroll import assert_can_buy_batch, batch_snapshot, prorated_batch_amount
        if not subscription.is_effectively_active:
            raise ValidationError("Renew the business plan before adding extra payroll staff.")
        if payroll_batch is None:
            raise ValidationError("Choose an extra-staff batch.")
        quantity = int(batch_quantity or 1)
        addon = assert_can_buy_batch(subscription.primary_business, payroll_batch, quantity)
        amount, days = prorated_batch_amount(payroll_batch, quantity, addon.paid_until)
        if amount <= 0:
            raise ValidationError("This extra-staff batch does not yet have a payable price.")
        from types import SimpleNamespace
        pending = SimpleNamespace(batch_id=payroll_batch.pk, batch=payroll_batch, quantity=quantity)
        snapshot = batch_snapshot([pending])
        snapshot[0]["prorated_days"] = days
        return SubscriptionPayment.objects.create(
            subscription=subscription,
            plan=subscription.plan,
            purpose=purpose,
            payroll_batch=payroll_batch,
            payroll_batch_quantity=quantity,
            payroll_batch_snapshot=snapshot,
            amount=amount,
            base_amount=amount,
            service_count=service_count,
            months=1,
            billing_cycle=SubscriptionPayment.CYCLE_MONTHLY,
            provider=provider,
            reference=f"PAYBATCH-{subscription.pk}-{uuid4().hex[:12].upper()}",
            notes=f"Pro-rata for {days} day(s) until the payroll add-on renews.",
        )
    if purpose == SubscriptionPayment.PURPOSE_PAYROLL_ADDON:
        from .payroll import batch_snapshot, held_batches_for_renewal, renewal_monthly_total
        if subscription.founder_lifetime or (subscription.status == BusinessSubscription.STATUS_TRIAL and subscription.is_effectively_active):
            raise ValidationError("Payroll is already included with the current Founder lifetime or free-trial access.")
        if not subscription.is_effectively_active:
            raise ValidationError("Renew the business plan before purchasing the payroll add-on.")
        if not payroll_tier or not payroll_tier.active or payroll_tier.plan_id != subscription.plan_id:
            raise ValidationError("Choose an active payroll add-on tier for the current plan.")
        period_count = Decimal("12") if billing_cycle == SubscriptionPayment.CYCLE_YEARLY else Decimal(max(1, int(months or 1)))
        # Held extra-staff batches renew automatically at today's batch prices.
        addon = BusinessPayrollAddon.objects.filter(business=subscription.primary_business).select_related("tier").first()
        holdings = [] if payroll_tier.unlimited else held_batches_for_renewal(addon)
        amount = (renewal_monthly_total(payroll_tier, addon) * period_count).quantize(Decimal("0.01"))
        if amount <= 0:
            raise ValidationError("This payroll add-on tier does not yet have a payable price.")
        return SubscriptionPayment.objects.create(
            subscription=subscription,
            plan=subscription.plan,
            purpose=purpose,
            payroll_tier=payroll_tier,
            payroll_batch_snapshot=batch_snapshot(holdings),
            amount=amount,
            base_amount=amount,
            service_count=service_count,
            months=months,
            billing_cycle=billing_cycle,
            provider=provider,
            reference=f"PAYROLL-{subscription.pk}-{uuid4().hex[:12].upper()}",
        )
    if payment_is_locked(subscription, plan):
        if subscription.founder_lifetime:
            raise ValidationError("Your current plan has founder lifetime access and does not require payment.")
        raise ValidationError("Renewal for your current plan opens within 7 days of its expiry date.")
    promotion = active_promotion_for_plan(plan, billing_cycle=billing_cycle)
    base_amount = payment_amount(plan, service_count, months, billing_cycle=billing_cycle, promotion=False)
    amount = payment_amount(plan, service_count, months, billing_cycle=billing_cycle, promotion=promotion)
    if amount <= 0:
        raise ValidationError("This plan does not yet have a payable price. Contact the INPROFIC.")
    return SubscriptionPayment.objects.create(
        subscription=subscription,
        plan=plan,
        purpose=purpose,
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

    if payment.purpose == SubscriptionPayment.PURPOSE_PAYROLL_ADDON:
        from .payroll import activate_paid_payroll_addon
        activate_paid_payroll_addon(payment)
        return payment
    if payment.purpose == SubscriptionPayment.PURPOSE_PAYROLL_BATCH:
        from .payroll import activate_paid_payroll_batch
        activate_paid_payroll_batch(payment)
        return payment

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
    if payment.billing_cycle == SubscriptionPayment.CYCLE_YEARLY:
        duration_days = 365
    elif payment.billing_cycle in (SubscriptionPayment.CYCLE_QUARTERLY, SubscriptionPayment.CYCLE_BIANNUAL):
        duration_days = round(365 * payment.months / 12)  # 91 / 182 days
    else:
        duration_days = 30 * payment.months
    subscription.paid_until = base + timezone.timedelta(days=duration_days)
    subscription.save()
    apply_subscription_entitlements(subscription)
    from .models import PlatformEvent
    _track_subscription_event(
        PlatformEvent.EVENT_SUBSCRIPTION_PAID, subscription,
        metadata={"amount": str(payment.amount), "billing_cycle": payment.billing_cycle, "provider": payment.provider},
    )
    return payment


def founder_subscription_mix(subscriptions, *, now=None):
    """Free vs paid subscription counts and trial-to-paid conversion for the Founder console.

    Every subscription lands in exactly one bucket:

    * ``founder``  - Founder lifetime (complimentary; neither free nor paid);
    * ``free``     - the free-forever Starter plan;
    * ``trial``    - a trial that is still running;
    * ``paid``     - an active term on a paid plan;
    * ``expired``  - anything lapsed or inactive.

    "Free" for the headline is ``free + trial``: workspaces not paying today.

    Conversion = trials that have reached a decision and paid. A trial counts as
    decided when it is no longer running, or when the subscription already paid
    early; a trial still running and unpaid is undecided and left out of the
    rate. "Paid" means at least one paid *plan* payment (payroll add-ons do not
    count). A lapsed trial loses ``trial_ends_at``, so the trial cohort is built
    from every durable trace instead: current trial state, a retained
    ``trial_ends_at``, Founder trial grants, paid-plan trial claims, and the
    signup / trial-started events. Founder-lifetime subscriptions are excluded.
    """
    now = now or timezone.now()
    subscriptions = list(subscriptions)
    mix = {"free": 0, "trial": 0, "paid": 0, "founder": 0, "expired": 0}
    by_id = {}
    for sub in subscriptions:
        if sub.founder_lifetime:
            bucket = "founder"
        elif sub.plan.is_free_forever and sub.status == BusinessSubscription.STATUS_ACTIVE:
            bucket = "free"
        elif not sub.is_effectively_active:
            bucket = "expired"
        elif sub.status == BusinessSubscription.STATUS_TRIAL:
            bucket = "trial"
        elif sub.status == BusinessSubscription.STATUS_ACTIVE:
            bucket = "paid"
        else:
            bucket = "expired"
        mix[bucket] += 1
        by_id[sub.pk] = (sub, bucket)

    # --- trial cohort: every subscription that ever ran a trial -------------
    cohort = {
        pk for pk, (sub, bucket) in by_id.items()
        if bucket != "founder"
        and (sub.status == BusinessSubscription.STATUS_TRIAL or sub.trial_ends_at is not None)
    }
    eligible = {pk for pk, (_, bucket) in by_id.items() if bucket != "founder"}
    cohort |= set(
        FounderTrialGrant.objects.filter(subscription_id__in=eligible).values_list("subscription_id", flat=True)
    )
    cohort |= set(
        PaidPlanTrialClaim.objects.filter(subscription_id__in=eligible).values_list("subscription_id", flat=True)
    )
    business_to_sub = {sub.primary_business_id: pk for pk, (sub, _) in by_id.items() if pk in eligible}
    trial_event_businesses = set(
        PlatformEvent.objects.filter(
            Q(event_type=PlatformEvent.EVENT_SUBSCRIPTION_TRIAL)
            | Q(event_type=PlatformEvent.EVENT_SUBSCRIPTION_STARTED, metadata__status=BusinessSubscription.STATUS_TRIAL),
            business_id__in=business_to_sub.keys(),
        ).values_list("business_id", flat=True)
    )
    cohort |= {business_to_sub[b] for b in trial_event_businesses}

    paid_ids = set(
        SubscriptionPayment.objects.filter(
            subscription_id__in=cohort,
            status=SubscriptionPayment.STATUS_PAID,
            purpose=SubscriptionPayment.PURPOSE_SUBSCRIPTION,
        ).values_list("subscription_id", flat=True)
    )
    converted = len(cohort & paid_ids)
    undecided = sum(1 for pk in cohort - paid_ids if by_id[pk][1] == "trial")
    lapsed = len(cohort) - converted - undecided
    decided = converted + lapsed
    return {
        **mix,
        "free_total": mix["free"] + mix["trial"],
        "total": len(subscriptions),
        "trials_started": len(cohort),
        "trials_converted": converted,
        "trials_lapsed": lapsed,
        "trials_undecided": undecided,
        "trials_decided": decided,
        "conversion_rate": round(converted * 100 / decided, 1) if decided else None,
    }
