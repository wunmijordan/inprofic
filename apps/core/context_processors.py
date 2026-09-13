from accounts.models import RoleModulePermission, UserBusiness
from accounts.services import business_subscription_for, can_use_commerce_storefront, is_business_admin, is_live_tester, user_has_permission
from accounts.subscription_services import business_has_feature
from django.utils.functional import SimpleLazyObject
from .context import get_request_cache
from .models import Business
from .verticals import vertical_config

def business(request):
    biz = getattr(request, "business", None)
    permissions = {}
    if getattr(request.user, "is_authenticated", False) and biz:
        for module, _ in RoleModulePermission.MODULE_CHOICES:
            permissions[module] = {
                "view": user_has_permission(request.user, biz, module, "view"),
                "edit": user_has_permission(request.user, biz, module, "edit"),
            }
    available_businesses = []
    can_manage_business = False
    if getattr(request.user, "is_authenticated", False):
        request_cache = get_request_cache()
        businesses_key = ("available_businesses", request.user.pk)
        if request_cache is not None and businesses_key in request_cache:
            available_businesses = request_cache[businesses_key]
        elif request.user.is_superuser:
            available_businesses = list(Business.objects.order_by("name", "id"))
        else:
            available_businesses = list(Business.objects.filter(
                user_memberships__user=request.user,
                user_memberships__active=True,
            ).distinct().order_by("name", "id"))
        if request_cache is not None:
            request_cache[businesses_key] = available_businesses
        can_manage_business = bool(biz and is_business_admin(request.user, biz))
    subscription = None
    if biz and getattr(request.user, "is_authenticated", False):
        subscription = business_subscription_for(biz)
    return {
        "biz": biz,
        "module_permissions": permissions,
        "available_businesses": available_businesses,
        "can_manage_business": can_manage_business,
        "can_use_storefront_pos": bool(biz and getattr(request.user, "is_authenticated", False) and can_use_commerce_storefront(request.user, biz)),
        "is_live_tester": bool(biz and getattr(request.user, "is_authenticated", False) and is_live_tester(request.user, biz)),
        "vertical_ui": vertical_config(biz),
        "subscription": subscription,
        # Only the reports page consumes this flag. Keep it lazy so ordinary
        # navigation doesn't query feature entitlements that won't be rendered.
        "reports_full": (
            SimpleLazyObject(lambda: business_has_feature(biz, "reports_full"))
            if biz else False
        ),
    }
