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
        event = PlatformEvent.objects.create(
            event_type=event_type,
            user=user if getattr(user, "pk", None) else None,
            business=business if getattr(business, "pk", None) else None,
            session_key=session_key[:64],
            module=(module or "")[:40],
            route_name=(route_name or "")[:100],
            path=(path or "")[:255],
            metadata=metadata or {},
        )
        return event
    except (DatabaseError, Exception):
        # Analytics must never make an operational flow fail.  This also keeps
        # migrations/startup safe while the new table is not yet available.
        return None



def record_founder_signup_contact(*, business, user=None, email="", name="", signed_up_at=None):
    """Persist the Founder signup-list snapshot independently of the tenant row."""
    try:
        from .models import FounderSignupContactState

        signup_email = (email or getattr(user, "email", "") or "").strip()
        email_key = signup_email.casefold()
        if not email_key:
            return None
        signup_name = (name or getattr(user, "fullname", "") or getattr(user, "username", "") or "").strip()
        vertical = (getattr(business, "vertical", "") or "").strip()
        service = business.get_vertical_display() if business is not None else vertical
        state, _ = FounderSignupContactState.objects.update_or_create(
            email_key=email_key,
            defaults={
                "signup_email": signup_email,
                "signup_name": signup_name,
                "business_name": (getattr(business, "name", "") or "").strip(),
                "business_id_snapshot": getattr(business, "pk", None),
                "vertical": vertical,
                "service": service,
                "signed_up_at": signed_up_at or timezone.now(),
                "deleted_at": None,
                "permanently_hidden": False,
                "updated_by": None,
            },
        )
        return state
    except (DatabaseError, Exception):
        # Signup must remain operational while a new migration is rolling out.
        return None


def mark_founder_signup_business_deleted(*, business, updated_by=None):
    """Mark the mailing-list signup associated with a Founder hard-deleted business."""
    try:
        from .models import FounderSignupContactState, PlatformEvent

        deleted_at = timezone.now()
        business_id = business.pk
        FounderSignupContactState.objects.filter(
            business_id_snapshot=business_id, permanently_hidden=False
        ).update(deleted_at=deleted_at, updated_by=updated_by, updated_at=deleted_at)

        events = (
            PlatformEvent.objects
            .filter(event_type=PlatformEvent.EVENT_REGISTRATION, business=business)
            .select_related("user")
            .order_by("-occurred_at", "-id")
        )
        seen = set()
        for event in events:
            metadata = event.metadata or {}
            signup_email = (metadata.get("signup_email") or getattr(event.user, "email", "") or "").strip()
            email_key = signup_email.casefold()
            if not email_key or email_key in seen:
                continue
            seen.add(email_key)
            current = FounderSignupContactState.objects.filter(email_key=email_key).first()
            if current and current.business_id_snapshot not in (None, business_id):
                continue
            signup_name = (metadata.get("signup_name") or getattr(event.user, "fullname", "") or getattr(event.user, "username", "") or "").strip()
            vertical = (metadata.get("vertical") or getattr(business, "vertical", "") or "").strip()
            FounderSignupContactState.objects.update_or_create(
                email_key=email_key,
                defaults={
                    "signup_email": signup_email,
                    "signup_name": signup_name,
                    "business_name": (metadata.get("business_name") or business.name or "").strip(),
                    "business_id_snapshot": business_id,
                    "vertical": vertical,
                    "service": business.get_vertical_display() if business else vertical,
                    "signed_up_at": event.occurred_at,
                    "deleted_at": deleted_at,
                    "permanently_hidden": False,
                    "updated_by": updated_by,
                },
            )
    except (DatabaseError, Exception):
        # The existing hard-delete flow must not be blocked by Founder-list bookkeeping.
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



def founder_signup_contacts(*, limit=None, include_deleted=False):
    """Return one durable Founder-visible contact per normalized signup email."""
    from .models import FounderSignupContactState, PlatformEvent

    states = {state.email_key: state for state in FounderSignupContactState.objects.all()}
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
        state = states.get(key)
        if state and state.permanently_hidden:
            continue
        if state and state.deleted_at and not include_deleted:
            continue
        vertical = (getattr(state, "vertical", "") if state else "") or metadata.get("vertical") or getattr(event.business, "vertical", "") or ""
        service = (getattr(state, "service", "") if state else "") or (event.business.get_vertical_display() if event.business else vertical)
        contacts.append({
            "email": (getattr(state, "signup_email", "") if state else "") or email,
            "name": (getattr(state, "signup_name", "") if state else "") or (metadata.get("signup_name") or getattr(event.user, "fullname", "") or getattr(event.user, "username", "") or "").strip(),
            "business": (getattr(state, "business_name", "") if state else "") or (metadata.get("business_name") or getattr(event.business, "name", "") or "").strip(),
            "business_id": getattr(state, "business_id_snapshot", None) if state else getattr(event, "business_id", None),
            "vertical": vertical,
            "service": service,
            "signed_up_at": (getattr(state, "signed_up_at", None) if state else None) or event.occurred_at,
            "deleted": bool(state and state.deleted_at),
            "deleted_at": state.deleted_at if state else None,
        })

    # Snapshot-only rows remain visible even if their historical event is absent.
    for key, state in states.items():
        if key in seen or state.permanently_hidden or not state.signup_email:
            continue
        if state.deleted_at and not include_deleted:
            continue
        contacts.append({
            "email": state.signup_email,
            "name": state.signup_name,
            "business": state.business_name,
            "business_id": state.business_id_snapshot,
            "vertical": state.vertical,
            "service": state.service or state.vertical,
            "signed_up_at": state.signed_up_at or state.updated_at,
            "deleted": bool(state.deleted_at),
            "deleted_at": state.deleted_at,
        })

    contacts.sort(key=lambda row: row["signed_up_at"] or timezone.now(), reverse=True)
    return contacts[:limit] if limit is not None else contacts

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
    all_signup_contacts = founder_signup_contacts(include_deleted=True)
    active_signup_contacts = [contact for contact in all_signup_contacts if not contact["deleted"]]
    return {
        "lead_sessions_30d": lead_sessions,
        "registrations_7d": PlatformEvent.objects.filter(event_type=PlatformEvent.EVENT_REGISTRATION, occurred_at__gte=since_7).count(),
        "registrations_30d": registrations,
        "latest_registration_id": PlatformEvent.objects.filter(event_type=PlatformEvent.EVENT_REGISTRATION).order_by("-id").values_list("id", flat=True).first() or 0,
        "signup_conversion_30d": conversion,
        "logins_7d": PlatformEvent.objects.filter(event_type=PlatformEvent.EVENT_LOGIN, occurred_at__gte=since_7).count(),
        "active_businesses_7d": PlatformEvent.objects.filter(
            event_type=PlatformEvent.EVENT_MODULE_VIEW, occurred_at__gte=since_7, business__isnull=False
        ).values("business_id").distinct().count(),
        "subscription_events_30d": recent_30.filter(event_type__in=PlatformEvent.SUBSCRIPTION_EVENTS).count(),
        "signup_contacts_count": len(active_signup_contacts),
        "signup_contacts_total_count": len(all_signup_contacts),
        "signup_contacts": all_signup_contacts[:50],
        "top_modules": top_modules,
        "recent_events": PlatformEvent.objects.select_related("business", "user").order_by("-occurred_at", "-id")[:30],
    }
