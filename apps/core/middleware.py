import re
import time
from uuid import uuid4
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.contrib.auth import logout as auth_logout
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from .models import Business
from .context import begin_request_cache, end_request_cache, get_request_cache, set_current_business, set_ledger_context
from accounts.services import is_live_tester, user_has_permission


class BusinessMiddleware:
    """Resolve a tenant from authenticated membership and session state."""

    def __init__(self, get_response):
        self.get_response = get_response

    def process_exception(self, request, exception):
        from .ledger import LedgerDateError
        if not isinstance(exception, LedgerDateError):
            return None
        from . import ledger
        return render(request, "core/ledger_date_error.html", {
            "error": exception,
            "backlog_entry_allowed": bool(
                getattr(request, "business", None) and ledger.can_enter_backlog(request.user, request.business)
            ),
        }, status=400)

    def __call__(self, request):
        # Render probes this endpoint frequently. Keep it completely outside
        # tenant/session resolution so a health check can still succeed when
        # the database connection pool is under pressure.
        if (
            request.path == "/health/"
            or request.path == "/privacy-policy/"
            or request.path == "/manifest.webmanifest"
            or request.path == "/service-worker.js"
            or request.path.startswith("/pwa/")
            or request.path == "/users/founder/subscriptions/live-signups/"
        ):
            request.business = None
            return self.get_response(request)

        # Clear any previous request value and isolate repeated authorization
        # lookups before resolving this request's membership.
        cache_token = begin_request_cache()
        set_current_business(None)
        set_ledger_context(None, None, None)
        try:
            business = self._resolve_business(request)
            request.business = business
            set_current_business(business)
            request.ledger_scope = "all"
            request.ledger_cutover = None
            if business:
                self._apply_ledger_scope(request, business)
            return self.get_response(request)
        finally:
            set_current_business(None)
            set_ledger_context(None, None, None)
            end_request_cache(cache_token)

    @staticmethod
    def _apply_ledger_scope(request, business):
        """Live / Backlog / Combined scope for this request (see core.ledger)."""
        from . import ledger
        from .models import LedgerCutover
        cutover = ledger.latest_cutover(business)
        request.ledger_cutover = cutover
        if not cutover or cutover.status == LedgerCutover.STATUS_REVERTED:
            return
        status = cutover.status
        wanted = request.session.get("ledger_mode", "live")
        scope = "live"
        if status == LedgerCutover.STATUS_MERGED:
            scope = "all"
        elif wanted == "all" and ledger.can_view_backlog(request.user, business):
            scope = "all"
        elif wanted == "backlog":
            if status == LedgerCutover.STATUS_ARCHIVED:
                # An archived backlog is sealed: viewing only (writes are guarded).
                allowed = ledger.can_view_backlog(request.user, business)
            else:
                allowed = ledger.can_enter_backlog(request.user, business)
            if allowed:
                scope = "backlog"
        # Only touch the session when the stored mode is no longer valid so
        # ordinary requests never trigger a session write.
        if status != LedgerCutover.STATUS_MERGED and wanted != scope and wanted in {"all", "backlog"}:
            request.session["ledger_mode"] = scope
        request.ledger_scope = scope
        set_ledger_context(scope, cutover.cutover_date, status)

    @staticmethod
    def _resolve_business(request):
        if not getattr(request.user, "is_authenticated", False):
            return None

        from accounts.models import UserBusiness

        selected_id = request.session.get("active_business_id")
        request_cache = get_request_cache()
        if request.user.is_superuser:
            businesses = list(
                Business.objects.select_related(
                    "subscription__plan",
                    "subscription_service__subscription__plan",
                ).order_by("id")
            )
            selected = next((business for business in businesses if business.pk == selected_id), None)
            main = next((business for business in businesses if business.slug == "main"), None)
            business = selected or main or (businesses[0] if businesses else None)
            if request_cache is not None:
                request_cache[("available_businesses", request.user.pk)] = sorted(
                    businesses,
                    key=lambda item: (item.name.casefold(), item.pk),
                )
        else:
            memberships = list(
                UserBusiness.objects.filter(
                    user=request.user, active=True, business__isnull=False
                )
                .select_related(
                    "business",
                    "role",
                    "business__subscription__plan",
                    "business__subscription_service__subscription__plan",
                )
                .prefetch_related("module_permissions", "role__module_permissions")
                .order_by("business__name", "business_id")
            )
            selected = next(
                (membership for membership in memberships if membership.business_id == selected_id),
                None,
            )
            membership = selected or (memberships[0] if memberships else None)
            business = membership.business if membership else None
            if request_cache is not None:
                request_cache[("available_businesses", request.user.pk)] = [
                    item.business for item in memberships
                ]
                if membership:
                    request_cache[("permission_snapshot", request.user.pk, business.pk)] = (
                        membership,
                        {
                            permission.module: permission
                            for permission in membership.module_permissions.all()
                        },
                        {
                            permission.module: permission
                            for permission in membership.role.module_permissions.all()
                        },
                    )
        if business and request_cache is not None:
            # The tenant query above already joins both possible subscription
            # paths. Prime the request cache from those joined objects so the
            # permission middleware and the base template don't make a separate
            # subscription round trip on every authenticated page.
            service = business._state.fields_cache.get("subscription_service")
            primary = business._state.fields_cache.get("subscription")
            subscription = service.subscription if service else primary
            request_cache[("business_subscription", business.pk)] = subscription
        if business:
            if selected_id != business.pk:
                request.session["active_business_id"] = business.pk
        else:
            if selected_id is not None:
                request.session.pop("active_business_id", None)
        return business



LIVE_TESTER_ALLOWED_MUTATION_PATHS = (
    "/accounts/logout/",
    "/business/switch/",
)
LIVE_TESTER_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _live_tester_mutation_blocked(request):
    if request.method not in LIVE_TESTER_UNSAFE_METHODS:
        return False
    if any(request.path.startswith(prefix) for prefix in LIVE_TESTER_ALLOWED_MUTATION_PATHS):
        return False
    business = getattr(request, "business", None)
    return bool(business and is_live_tester(request.user, business))


EXEMPT_PREFIXES = (
    "/marketing/location-enrich/",
    "/accounts/login", "/accounts/logout", "/accounts/signup", "/privacy-policy", "/robots.txt", "/sitemap.xml", "/favicon.ico", "/llms.txt",
    "/business/settings", "/business/switch", "/admin", "/static", "/media/", "/shop/", "/shared/pack/",
    "/health/", "/ops/", "/manifest.webmanifest", "/service-worker.js", "/pwa/",
    "/api/v1/whatsapp/webhook/", "/api/v1/storefronts/", "/api/v1/connectors/", "/api/v1/delivery/providers/",
    "/users/plans/payment/callback/", "/users/plans/payment/webhook/",
)

# Billing/recovery surfaces must remain reachable after a subscription expires,
# without reopening the whole User Management module. Views still enforce Business
# Admin/superuser authorization themselves.
SUBSCRIPTION_RECOVERY_PREFIXES = (
    "/users/plans",
    "/users/founder/subscriptions",
    "/users/founder/platform/",
    "/users/mailing/",
)


class LoginRequiredMiddleware:
    """Requires login for every page except auth, admin, and static files."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Do not force Django's lazy authenticated user/session to resolve for
        # public infrastructure requests that do not need the application's idle
        # timeout policy. Protected tenant PWA endpoints continue through this
        # middleware and enforce tenant access again in their views.
        if (
            request.path == "/health/"
            or request.path == "/manifest.webmanifest"
            or request.path == "/service-worker.js"
            or request.path == "/pwa/offline/"
        ):
            return self.get_response(request)

        path_is_public = request.path == "/" or request.path.startswith(EXEMPT_PREFIXES)
        if not request.user.is_authenticated and not path_is_public:
            return redirect(f"{reverse('login')}?next={request.path}")
        if request.user.is_authenticated:
            now = int(time.time())
            last_activity = request.session.get("storetrack_last_activity")
            idle_limit = max(int(getattr(settings, "AUTHENTICATED_IDLE_TIMEOUT_SECONDS", 28800)), 60)
            try:
                last_activity_at = int(last_activity) if last_activity else None
                idle_seconds = now - last_activity_at if last_activity_at else 0
            except (TypeError, ValueError):
                last_activity_at = None
                idle_seconds = 0
            if idle_seconds > idle_limit:
                auth_logout(request)
                destination = reverse("dashboard") if request.path == "/" else request.get_full_path()
                return redirect(f"{reverse('login')}?{urlencode({'next': destination})}")
            write_interval = max(
                int(getattr(settings, "AUTHENTICATED_ACTIVITY_WRITE_INTERVAL_SECONDS", 60)),
                1,
            )
            if last_activity_at is None or now - last_activity_at >= min(write_interval, idle_limit):
                request.session["storetrack_last_activity"] = now
        if request.user.is_authenticated and _live_tester_mutation_blocked(request):
            detail = "Demo is read-only. This action was not saved."
            wants_json = (
                request.path.startswith("/api/")
                or "application/json" in (request.headers.get("Accept") or "")
                or (request.headers.get("Content-Type") or "").startswith("application/json")
            )
            if wants_json:
                return JsonResponse({"detail": detail, "read_only": True}, status=403)
            return render(request, "403.html", {"live_tester_blocked": True}, status=403)
        if request.user.is_authenticated and not path_is_public:
            if any(request.path.startswith(prefix) for prefix in SUBSCRIPTION_RECOVERY_PREFIXES):
                return self.get_response(request)
            if not getattr(request, "business", None):
                if getattr(request.user, "platform_mail_access", False):
                    return redirect("platform_mailing_workspace")
                return render(request, "accounts/no_business_access.html", status=403)
            # Purpose-specific users land directly in their isolated workspace
            # rather than receiving broad Dashboard access just to have a home.
            if request.path.rstrip("/") == "/dashboard":
                business = getattr(request, "business", None)
                if not user_has_permission(request.user, business, "dashboard", "view"):
                    if user_has_permission(request.user, business, "audit", "view"):
                        return redirect("audit_workspace")
                    if user_has_permission(request.user, business, "pos", "view"):
                        return redirect("commerce_storefront_pos")
                    if user_has_permission(request.user, business, "kitchen", "view"):
                        return redirect("orders_list")
                    if user_has_permission(request.user, business, "delivery_rider", "view"):
                        return redirect("delivery_rider_dashboard")
                    if user_has_permission(request.user, business, "delivery", "view"):
                        return redirect("delivery_dashboard")
            if request.path.startswith('/commerce/notifications/'):
                candidates = ('commerce', 'delivery', 'delivery_rider', 'inventory')
                if any(user_has_permission(request.user, request.business, candidate, 'view') for candidate in candidates):
                    # The notification view narrows event visibility and push
                    # permissions; access to this shared endpoint is not Commerce-only.
                    return self.get_response(request)
            module = _module_for_path(request.path)
            action = _action_for_request(request)
            if module == "production" and _KITCHEN_FLOOR_PATH.match(request.path):
                # Production-floor users reach only the normal order list, entry
                # form, detail and flexible-material save; everything else under
                # /orders/ (approve, complete, runs, invoices...) stays closed.
                business_ = getattr(request, "business", None)
                if not user_has_permission(request.user, business_, "production", "view") and user_has_permission(request.user, business_, "kitchen", action):
                    module = "kitchen"
            if not user_has_permission(request.user, getattr(request, "business", None), module, action):
                return render(request, "403.html", status=403)
        return self.get_response(request)


MODULE_RULES = [
    ("/audit", "audit"),
    ("/delivery/rider", "delivery_rider"),
    ("/delivery", "delivery"),
    ("/commerce/storefront-pos", "pos"),
    ("/inventory", "inventory"),
    ("/procurement", "procurement"),
    ("/orders", "production"),
    ("/sales", "sales"),
    ("/expenses", "expenses"),
    ("/finance", "finance"),
    ("/reports", "reports"),
    ("/users", "users"),
    ("/commerce", "commerce"),
    ("/business", "dashboard"),
]

_KITCHEN_FLOOR_PATH = re.compile(r"^/orders/(?:add/?|\d+/?|\d+/edit/?|\d+/flexible/?)?$")


def _module_for_path(path):
    for prefix, module in MODULE_RULES:
        if path == prefix or path.startswith(prefix + "/"):
            return module
    return "dashboard"

def _action_for_request(request):
    # A contained purchase preview/approval review is read-only on GET.
    # The views retain their own edit/admin checks for submissions.
    if request.method in {'GET', 'HEAD'} and (
        re.fullmatch(r'/procurement/\d+/approve/', request.path)
        or (re.fullmatch(r'/procurement/\d+/edit/', request.path) and request.GET.get('share') == '1')
    ):
        return 'view'
    # The assistant is read-only; asking is a POST only so the question travels in the body.
    # Each tool re-checks the member's own module permission.
    if request.path.startswith("/assistant/") and request.method == "POST":
        return "view"
    # Brief delivery choices belong to the signed-in member. Dashboard viewers
    # can save their own preferences without permission to edit business data.
    # Template configuration still needs edit access and the view's admin check.
    if request.path.rstrip("/") == "/brief" and request.method == "POST":
        if request.POST.get("action", "email") in {"email", "whatsapp", "test", "weekly", "email_now", "report_sections"}:
            return "view"
    # A read receipt only changes the current user's alert acknowledgement;
    # commerce viewers do not need broad commerce-edit permission for it.
    if request.path.startswith("/audit/query/") and request.path.rstrip("/") == "/audit/query":
        return "view"
    if request.path.startswith("/commerce/notifications/"):
        return "view"
    if request.path.startswith("/inventory/alerts/"):
        return "view"
    # "complete" in the Complete dashboard URL is a name, not the production
    # "complete order" action, so reading it needs only view access.
    if request.method != "POST" and (request.path == "/dashboard/complete" or request.path.startswith("/dashboard/complete/")):
        return "view"
    if request.method != "POST":
        path = request.path.rstrip("/")
        if any(token in path.split("/") for token in ("add", "edit", "delete", "approve", "reject", "complete", "receive", "dispense", "permissions", "split")):
            return "edit"
        return "view"
    return "edit"


class PlatformAnalyticsMiddleware:
    """Capture meaningful first-party usage without a per-request write storm."""

    THROTTLE_SECONDS = 300

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.method != "GET" or getattr(response, "status_code", 500) >= 400:
            return response
        path = request.path or ""
        if path.startswith(("/static/", "/media/", "/health/", "/ops/", "/api/", "/service-worker.js", "/manifest.webmanifest")):
            return response
        try:
            from accounts.analytics import module_from_path, record_platform_event
            from accounts.models import PlatformEvent
            if path.startswith("/accounts/signup"):
                event_type = PlatformEvent.EVENT_SIGNUP_VIEW
                module = "signup"
            elif getattr(request.user, "is_authenticated", False):
                event_type = PlatformEvent.EVENT_MODULE_VIEW
                module = module_from_path(path)
            else:
                return response
            now = int(time.time())
            route = request.resolver_match.url_name if request.resolver_match else path
            throttle_key = f"analytics:{event_type}:{getattr(getattr(request, 'business', None), 'pk', 'global')}:{route}"
            identity = request.session.session_key or (f"user-{request.user.pk}" if request.user.is_authenticated else uuid4().hex)
            if cache.add(f"{throttle_key}:{identity}", now, timeout=self.THROTTLE_SECONDS):
                record_platform_event(event_type, request=request, module=module, route_name=route)
        except Exception:
            pass
        return response
