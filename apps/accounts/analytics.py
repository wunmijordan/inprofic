from __future__ import annotations

from datetime import timedelta

from django.db import DatabaseError
from django.db.models import Count
from django.utils import timezone


def _default_business_for_user(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None
    try:
        membership = user.business_memberships.filter(active=True).select_related("business").order_by("id").first()
        return membership.business if membership else None
    except Exception:
        return None


def record_platform_event(event_type, *, request=None, user=None, business=None, module="", route_name="", path="", metadata=None):
    """Best-effort, first-party product analytics.

    Analytics is deliberately unable to block the user workflow.  The event
    contains operational context only; passwords, payment secrets and request
    bodies are never captured here.
    """
    try:
        from .models import PlatformEvent

        if request is not None:
            user = user or (request.user if getattr(request, "user", None) and request.user.is_authenticated else None)
            business = business or getattr(request, "business", None)
            path = path or request.path
            try:
                route_name = route_name or (request.resolver_match.url_name if request.resolver_match else "")
            except Exception:
                pass
            if not request.session.session_key:
                request.session.save()
            session_key = request.session.session_key or ""
        else:
            session_key = ""
        business = business or _default_business_for_user(user)
        PlatformEvent.objects.create(
            event_type=event_type,
            user=user if getattr(user, "pk", None) else None,
            business=business if getattr(business, "pk", None) else None,
            session_key=session_key[:64],
            module=(module or "")[:40],
            route_name=(route_name or "")[:100],
            path=(path or "")[:255],
            metadata=metadata or {},
        )
    except (DatabaseError, Exception):
        # Analytics must never make an operational flow fail.  This also keeps
        # migrations/startup safe while the new table is not yet available.
        return None


def module_from_path(path):
    path = (path or "").strip("/")
    if not path:
        return "dashboard"
    first = path.split("/", 1)[0]
    aliases = {
        "users": "accounts",
        "accounts": "accounts",
        "business": "settings",
        "shop": "storefront",
        "api": "api",
    }
    return aliases.get(first, first)[:40]



def founder_signup_contacts(*, limit=None):
    """Return one founder-visible contact per signup email.

    Registration-event metadata preserves the address used at signup, while
    the related user is a fallback for registrations recorded before the
    mailing-list fields were introduced.
    """
    from .models import PlatformEvent

    events = (
        PlatformEvent.objects
        .filter(event_type=PlatformEvent.EVENT_REGISTRATION)
        .select_related("business", "user")
        .order_by("-occurred_at", "-id")
    )
    contacts = []
    seen = set()
    for event in events.iterator(chunk_size=500):
        metadata = event.metadata or {}
        email = (metadata.get("signup_email") or getattr(event.user, "email", "") or "").strip()
        key = email.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        vertical = metadata.get("vertical") or getattr(event.business, "vertical", "") or ""
        service = event.business.get_vertical_display() if event.business else vertical
        contacts.append({
            "email": email,
            "name": (metadata.get("signup_name") or getattr(event.user, "fullname", "") or getattr(event.user, "username", "") or "").strip(),
            "business": (metadata.get("business_name") or getattr(event.business, "name", "") or "").strip(),
            "vertical": vertical,
            "service": service,
            "signed_up_at": event.occurred_at,
        })
        if limit is not None and len(contacts) >= limit:
            break
    return contacts

def founder_analytics_summary(*, now=None):
    from .models import PlatformEvent

    now = now or timezone.now()
    since_7 = now - timedelta(days=7)
    since_30 = now - timedelta(days=30)
    recent_30 = PlatformEvent.objects.filter(occurred_at__gte=since_30)
    lead_sessions = recent_30.filter(event_type=PlatformEvent.EVENT_SIGNUP_VIEW).exclude(session_key="").values("session_key").distinct().count()
    registrations = recent_30.filter(event_type=PlatformEvent.EVENT_REGISTRATION).count()
    conversion = round((registrations / lead_sessions * 100), 1) if lead_sessions else 0
    top_modules = list(
        recent_30.filter(event_type=PlatformEvent.EVENT_MODULE_VIEW, module__gt="")
        .values("module").annotate(total=Count("id"))
        .order_by("-total", "module")[:8]
    )
    all_signup_contacts = founder_signup_contacts()
    return {
        "lead_sessions_30d": lead_sessions,
        "registrations_7d": PlatformEvent.objects.filter(event_type=PlatformEvent.EVENT_REGISTRATION, occurred_at__gte=since_7).count(),
        "registrations_30d": registrations,
        "signup_conversion_30d": conversion,
        "logins_7d": PlatformEvent.objects.filter(event_type=PlatformEvent.EVENT_LOGIN, occurred_at__gte=since_7).count(),
        "active_businesses_7d": PlatformEvent.objects.filter(
            event_type=PlatformEvent.EVENT_MODULE_VIEW, occurred_at__gte=since_7, business__isnull=False
        ).values("business_id").distinct().count(),
        "subscription_events_30d": recent_30.filter(event_type__in=PlatformEvent.SUBSCRIPTION_EVENTS).count(),
        "signup_contacts_count": len(all_signup_contacts),
        "signup_contacts": all_signup_contacts[:50],
        "top_modules": top_modules,
        "recent_events": PlatformEvent.objects.select_related("business", "user").order_by("-occurred_at", "-id")[:30],
    }
