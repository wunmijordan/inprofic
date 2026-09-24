from django.db import transaction
from django.db.models import Q
from .models import (
    BusinessModuleAccess,
    BusinessSubscription,
    CustomUser,
    Role,
    RoleModulePermission,
    SubscriptionService,
    UserBusiness,
    UserModulePermission,
)

ROLE_DEFAULTS = {
    CustomUser.ROLE_STOCK_KEEPER: {
        "dashboard": (True, False), "inventory": (True, True), "procurement": (True, True),
        "production": (True, True), "sales": (True, False), "expenses": (False, False),
        "finance": (False, False), "reports": (True, False), "users": (False, False), "commerce": (False, False),
    },
    CustomUser.ROLE_MANAGER: {
        "dashboard": (True, False), "inventory": (True, True), "procurement": (True, True),
        "production": (True, True), "sales": (True, True), "expenses": (True, True),
        "finance": (True, True), "reports": (True, True), "users": (False, False), "commerce": (False, False),
    },
    CustomUser.ROLE_ACCOUNTANT: {
        "dashboard": (True, False), "inventory": (True, False), "procurement": (True, False),
        "production": (True, False), "sales": (True, False), "expenses": (True, True),
        "finance": (True, True), "reports": (True, True), "users": (False, False), "commerce": (False, False),
    },
    CustomUser.ROLE_MD_DIRECTOR: {
        "dashboard": (True, False), "inventory": (True, False), "procurement": (True, False),
        "production": (True, False), "sales": (True, False), "expenses": (True, False),
        "finance": (True, True), "reports": (True, True), "users": (True, False), "commerce": (False, False),
    },
    CustomUser.ROLE_POS_OPERATOR: {
        # POS-only staff should not need general Dashboard access. When this is
        # their only effective permission, login lands them directly in the
        # in-premise storefront/POS and the POS screen exposes its own logout.
        "pos": (True, True),
    },
    # External auditors are deliberately isolated from the operating app.
    # Their workspace reads tenant-scoped evidence internally without granting
    # ordinary Inventory/Finance/Procurement/etc. navigation.
    CustomUser.ROLE_AUDITOR: {
        "audit": (True, False),
    },
    CustomUser.ROLE_DELIVERY_COORDINATOR: {
        "delivery": (True, True),
    },
    CustomUser.ROLE_DELIVERY_RIDER: {
        "delivery_rider": (True, True),
    },
    CustomUser.ROLE_BUSINESS_ADMIN: {m: (True, True) for m, _ in RoleModulePermission.MODULE_CHOICES},
    # Demo can open normal operational add/edit workflows so the live UI can be
    # exercised, but mutation is blocked centrally by middleware. The historical
    # internal key remains ``live_tester`` so existing assignments stay valid.
    # User administration remains out of scope to avoid exposing access-control data.
    CustomUser.ROLE_LIVE_TESTER: {
        m: ((False, False) if m == "users" else (True, True))
        for m, _ in RoleModulePermission.MODULE_CHOICES
    },
    CustomUser.ROLE_SUPERUSER: {m: (True, True) for m, _ in RoleModulePermission.MODULE_CHOICES},
}


def _request_cache():
    """Return this request's cache, or None outside request handling."""
    from core.context import get_request_cache

    return get_request_cache()


def invalidate_business_access_cache(business):
    """Discard request-local entitlement data after an in-request mutation."""
    cache = _request_cache()
    if cache is None or not business:
        return
    for namespace in (
        "business_subscription",
        "business_module_access",
        "business_feature_access",
    ):
        cache.pop((namespace, business.pk), None)


def business_subscription_for(business):
    """Resolve a primary or additional service's subscription once per request."""
    if not business:
        return None
    cache = _request_cache()
    key = ("business_subscription", business.pk)
    if cache is not None and key in cache:
        return cache[key]

    service = (
        SubscriptionService.objects.filter(business=business)
        .select_related("subscription__plan")
        .first()
    )
    subscription = service.subscription if service else (
        BusinessSubscription.objects.filter(primary_business=business)
        .select_related("plan")
        .first()
    )
    if cache is not None:
        cache[key] = subscription
    return subscription


def _permission_snapshot(user, business):
    """Load one membership and both permission layers in three bounded queries."""
    cache = _request_cache()
    key = ("permission_snapshot", user.pk, business.pk)
    if cache is not None and key in cache:
        return cache[key]

    membership = (
        UserBusiness.objects.filter(user=user, business=business, active=True)
        .select_related("role")
        .prefetch_related("module_permissions", "role__module_permissions")
        .first()
    )
    if membership:
        user_permissions = {permission.module: permission for permission in membership.module_permissions.all()}
        role_permissions = {permission.module: permission for permission in membership.role.module_permissions.all()}
    else:
        user_permissions = {}
        role_permissions = {}
    snapshot = membership, user_permissions, role_permissions
    if cache is not None:
        cache[key] = snapshot
    return snapshot


def role_key(role):
    return role.key if role else CustomUser.ROLE_STOCK_KEEPER


def seed_business_roles(business):
    """Ensure system roles/permissions exist using bounded bulk operations.

    This helper is intentionally safe to call from request-time forms/views: a
    healthy tenant performs only two reads (roles + permission keys). Missing
    seed rows are repaired in bulk, while existing business-customized role
    labels and permission values are left untouched just as before.
    """
    definitions = dict(CustomUser.SYSTEM_ROLE_DEFINITIONS)
    system_keys = tuple(definitions)
    hidden_system_keys = {CustomUser.ROLE_SUPERUSER, CustomUser.ROLE_LIVE_TESTER}

    # Demo used to be described generically as a hidden custom review role. Adopt
    # any existing ``Demo``/``demo`` role instead of creating a second role, and
    # keep ``live_tester`` as the canonical key so already-assigned tester users
    # are not broken by the user-facing rename. This stays in the same bounded
    # role query used by the normal seeding path.
    role_rows = list(
        Role.objects.filter(business=business)
        .filter(Q(key__in=system_keys) | Q(key="demo") | Q(name__iexact="Demo"))
        .order_by("pk")
    )
    roles = {role.key: role for role in role_rows if role.key in system_keys}
    canonical_demo = roles.get(CustomUser.ROLE_LIVE_TESTER)
    duplicate_demo_roles = [
        role for role in role_rows
        if role.pk != getattr(canonical_demo, "pk", None)
        and (role.key == "demo" or role.name.strip().casefold() == "demo")
    ]
    if not canonical_demo and duplicate_demo_roles:
        # Prefer the row already named Demo so adopting it cannot collide with
        # another role's unique business/name constraint during the rename.
        duplicate_demo_roles.sort(key=lambda role: (role.name.strip().casefold() != "demo", role.pk))
        canonical_demo = duplicate_demo_roles.pop(0)
        with transaction.atomic():
            canonical_demo.key = CustomUser.ROLE_LIVE_TESTER
            canonical_demo.name = definitions[CustomUser.ROLE_LIVE_TESTER]
            canonical_demo.is_system = True
            canonical_demo.visible_to_admin = False
            canonical_demo.active = True
            canonical_demo.save(update_fields=["key", "name", "is_system", "visible_to_admin", "active"])
        roles[CustomUser.ROLE_LIVE_TESTER] = canonical_demo

    if canonical_demo and duplicate_demo_roles:
        # If both names already exist (for example after an earlier Live Tester
        # deploy), merge old Demo memberships into the canonical fixed policy,
        # then remove the duplicate role before claiming the Demo display name.
        with transaction.atomic():
            for duplicate_role in duplicate_demo_roles:
                UserBusiness.objects.filter(role=duplicate_role).update(role=canonical_demo)
                duplicate_role.delete()

    missing_keys = [key for key in system_keys if key not in roles]
    if missing_keys:
        # Missing system roles are exceptional. Use the original get-or-create
        # semantics here so uniqueness/name conflicts remain visible instead of
        # being silently ignored; the healthy request path never enters this loop.
        with transaction.atomic():
            for key in missing_keys:
                role, _created = Role.objects.get_or_create(
                    business=business, key=key,
                    defaults={
                        "name": definitions[key],
                        "is_system": True,
                        "visible_to_admin": key not in hidden_system_keys,
                    },
                )
                roles[key] = role

    # Preserve business-renamed ordinary system-role labels. Demo is different:
    # it is a fixed global testing policy, so its display name and hidden status
    # are invariants just like the Superuser role's hidden status.
    roles_to_update = []
    for key, role in roles.items():
        changed = False
        if not role.is_system:
            role.is_system = True
            changed = True
        if key in hidden_system_keys and role.visible_to_admin:
            role.visible_to_admin = False
            changed = True
        if key == CustomUser.ROLE_LIVE_TESTER:
            if role.name != definitions[key]:
                role.name = definitions[key]
                changed = True
            if not role.active:
                role.active = True
                changed = True
        if changed:
            roles_to_update.append(role)
    role_ids = [role.pk for role in roles.values()]
    existing_permissions = set(
        RoleModulePermission.objects
        .filter(role_id__in=role_ids)
        .values_list("role_id", "module")
    )
    missing_permissions = []
    for key in system_keys:
        role = roles.get(key)
        if not role:
            continue
        defaults = ROLE_DEFAULTS.get(key, {})
        for module, _label in RoleModulePermission.MODULE_CHOICES:
            marker = (role.pk, module)
            if marker in existing_permissions:
                continue
            can_view, can_edit = defaults.get(module, (False, False))
            missing_permissions.append(
                RoleModulePermission(
                    role=role, module=module,
                    can_view=can_view, can_edit=can_edit,
                )
            )
    if roles_to_update or missing_permissions:
        with transaction.atomic():
            if roles_to_update:
                Role.objects.bulk_update(roles_to_update, ["name", "is_system", "visible_to_admin", "active"])
            if missing_permissions:
                RoleModulePermission.objects.bulk_create(missing_permissions, ignore_conflicts=True)

    return roles


def ensure_permissions(membership):
    """Create user overrides only when they don't exist; role defaults remain authoritative otherwise."""
    seed_business_roles(membership.business)
    for module, _ in RoleModulePermission.MODULE_CHOICES:
        UserModulePermission.objects.get_or_create(membership=membership, module=module)


def seed_business_modules(business, source=BusinessModuleAccess.SOURCE_DEFAULT):
    """Provision today's full module set behind the future plan boundary."""
    for module, _label in RoleModulePermission.MODULE_CHOICES:
        if module in {"pos", "delivery_rider"}:
            continue
        BusinessModuleAccess.objects.get_or_create(
            business=business,
            module=module,
            defaults={"enabled": module not in {"commerce", "audit", "delivery"}, "source": source},
        )
    invalidate_business_access_cache(business)


def business_has_module(business, module):
    """Return the business entitlement used to gate user-facing access.

    This function is an authorization/UI ceiling, not a domain-persistence switch.
    Cross-module services must keep authoritative stock, sales, finance, payment,
    audit and related records synchronized even when this returns False, so a later
    plan upgrade reveals complete history rather than starting sync at upgrade time.

    Existing businesses with no subscription keep the current missing-row=enabled
    rule. Once a subscription exists, expiry is enforced dynamically even before
    a scheduled entitlement refresh runs. Dashboard remains available; the
    dedicated Plans/Billing URLs are separately whitelisted as the recovery surface.
    """
    if not business:
        return False
    # Rider access is a purpose-specific surface inside the plan-gated Delivery
    # module, not a separate commercial entitlement.
    entitlement_module = "delivery" if module == "delivery_rider" else module
    subscription = business_subscription_for(business)
    if subscription and not subscription.is_effectively_active and entitlement_module != "dashboard":
        return False
    # Production is a vertical capability as well as a plan entitlement.
    # Wholesale and retail keep any historical production data intact, but do
    # not expose or authorize the production workflow while using a stock-first
    # vertical.
    if entitlement_module == "production" and not business.uses_production:
        return False
    cache = _request_cache()
    access_key = ("business_module_access", business.pk)
    if cache is not None and access_key in cache:
        module_access = cache[access_key]
    else:
        module_access = dict(
            BusinessModuleAccess.objects.filter(business=business).values_list("module", "enabled")
        )
        if cache is not None:
            cache[access_key] = module_access
    enabled = module_access.get(entitlement_module)
    return enabled is not False


def user_has_permission(user, business, module, action="view"):
    if not getattr(user, "is_authenticated", False) or not business:
        return False
    if not business_has_module(business, module):
        return False
    if getattr(user, "is_superuser", False):
        return True
    membership, user_permissions, role_permissions = _permission_snapshot(user, business)
    if not membership:
        return False
    # This role intentionally gets edit-level *navigation* permission so Add/Edit
    # links and GET forms remain testable. Unsafe HTTP methods are independently
    # blocked before views execute; do not turn this into ordinary write access.
    if membership.role.key == CustomUser.ROLE_LIVE_TESTER:
        return module != "users"
    perm = user_permissions.get(module)
    if not perm:
        role_perm = role_permissions.get(module)
        if not role_perm:
            return False
        return role_perm.can_edit if action == "edit" else role_perm.can_view
    if action == "edit":
        role_perm = role_permissions.get(module)
        return perm.can_edit if perm.can_edit is not None else bool(role_perm and role_perm.can_edit)
    role_perm = role_permissions.get(module)
    return perm.can_view if perm.can_view is not None else bool(role_perm and role_perm.can_view)




def is_live_tester(user, business):
    """Return whether the current tenant membership is the server-enforced read-only Demo role."""
    if not getattr(user, "is_authenticated", False) or not business or getattr(user, "is_superuser", False):
        return False
    membership, _user_permissions, _role_permissions = _permission_snapshot(user, business)
    return bool(membership and membership.role.key == CustomUser.ROLE_LIVE_TESTER)


def can_use_commerce_storefront(user, business, action="view"):
    """Return whether the in-premise POS is entitled, enabled, and permitted.

    POS keeps its dedicated role/user permission, but it is a Commerce surface:
    the active plan must include Commerce and the tenant must explicitly enable
    Commerce before the POS appears or can be opened.
    """
    if not business_has_module(business, "commerce"):
        return False
    from commerce.models import CommerceSettings
    commerce_enabled = CommerceSettings.raw_objects.filter(
        business=business, enabled=True
    ).exists()
    return bool(commerce_enabled and user_has_permission(user, business, "pos", action))

def is_business_admin(user, business):
    if getattr(user, "is_superuser", False):
        return True
    if not getattr(user, "is_authenticated", False) or not business:
        return False
    membership, _user_permissions, _role_permissions = _permission_snapshot(user, business)
    return bool(membership and membership.role.key == CustomUser.ROLE_BUSINESS_ADMIN)
