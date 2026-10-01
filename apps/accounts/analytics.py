from __future__ import annotations

from datetime import timedelta

from django.core.cache import cache
from django.db import DatabaseError
from django.db.models import Count, Max, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from urllib.parse import unquote

from .geo import country_metadata
from .timezone_geo import timezone_metadata


_FOUNDER_ANALYTICS_CACHE_KEY = "founder-analytics-summary:v4"


def invalidate_founder_analytics_cache():
    cache.delete(_FOUNDER_ANALYTICS_CACHE_KEY)


def _clean_location_header(value, *, limit=120):
    value = unquote(str(value or "")).strip()
    return value[:limit]


def _coordinate_header(headers, *names):
    for name in names:
        raw = (headers.get(name) or "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if name.lower().endswith("latitude") and not -90 <= value <= 90:
            continue
        if name.lower().endswith("longitude") and not -180 <= value <= 180:
            continue
        return round(value, 6)
    return None


def marketing_location_metadata(request):
    """Return coarse, first-party visit geography without storing visitor IPs.

    Prefer trusted edge-provider city/region/coordinate headers when present.
    If only an ISO country code is available, use a bundled country centroid so
    Founder analytics can still map the visit without any external lookup.
    """
    headers = request.headers
    country_code = _clean_location_header(
        headers.get("CF-IPCountry")
        or headers.get("X-Vercel-IP-Country")
        or headers.get("CloudFront-Viewer-Country")
        or headers.get("X-Country-Code"),
        limit=2,
    ).upper()
    country_info = country_metadata(country_code)
    country_name = _clean_location_header(
        headers.get("CloudFront-Viewer-Country-Name")
        or headers.get("X-Country-Name")
        or country_info.get("name")
        or country_code,
        limit=80,
    )
    region = _clean_location_header(
        headers.get("X-Vercel-IP-Country-Region")
        or headers.get("CloudFront-Viewer-Country-Region-Name")
        or headers.get("CloudFront-Viewer-Country-Region")
        or headers.get("X-Region"),
    )
    city = _clean_location_header(
        headers.get("X-Vercel-IP-City")
        or headers.get("CloudFront-Viewer-City")
        or headers.get("X-City"),
    )
    latitude = _coordinate_header(
        headers,
        "X-Vercel-IP-Latitude",
        "CloudFront-Viewer-Latitude",
        "X-Latitude",
    )
    longitude = _coordinate_header(
        headers,
        "X-Vercel-IP-Longitude",
        "CloudFront-Viewer-Longitude",
        "X-Longitude",
    )
    precision = "edge" if latitude is not None and longitude is not None else "country"
    if latitude is None or longitude is None:
        latitude = country_info.get("latitude")
        longitude = country_info.get("longitude")
    location = ", ".join(part for part in (city, region, country_name) if part) or "Unknown"
    return {
        "country": country_code,
        "country_code": country_code,
        "country_name": country_name,
        "region": region,
        "city": city,
        "latitude": latitude,
        "longitude": longitude,
        "location_precision": precision if latitude is not None and longitude is not None else "unknown",
        "location": location,
    }




def browser_marketing_location_metadata(*, timezone_name="", language="", current=None):
    """Improve coarse marketing geography using browser timezone metadata.

    This fallback is intentionally permissionless and privacy-conscious: it uses
    only the browser's IANA timezone and language, never precise device
    geolocation. Edge-provided coordinates remain authoritative when available.
    """
    current = dict(current or {})
    timezone_name = str(timezone_name or "").strip()[:80]
    language = str(language or "").strip()[:40]
    zone_info = timezone_metadata(timezone_name)
    if not zone_info:
        if timezone_name:
            current["browser_timezone"] = timezone_name
        if language:
            current["browser_language"] = language
        return current

    zone_country = str(zone_info.get("country_code") or "").upper()
    existing_country = str(
        current.get("country_code") or current.get("country") or ""
    ).strip().upper()
    existing_precision = str(current.get("location_precision") or "").strip()

    current["browser_timezone"] = timezone_name
    if language:
        current["browser_language"] = language

    # Never replace edge coordinates. If an edge country exists and disagrees
    # with the device timezone, retain the edge country centroid rather than
    # inventing a more precise-looking but contradictory location.
    if existing_precision == "edge":
        return current
    if existing_country and zone_country and existing_country != zone_country:
        return current

    country_code = existing_country or zone_country
    country_info = country_metadata(country_code)
    city = str(zone_info.get("city") or "").strip()
    country_name = str(current.get("country_name") or country_info.get("name") or country_code).strip()
    latitude = zone_info.get("latitude")
    longitude = zone_info.get("longitude")
    if latitude is None or longitude is None:
        return current

    current.update({
        "country": country_code,
        "country_code": country_code,
        "country_name": country_name,
        "city": current.get("city") or city,
        "latitude": latitude,
        "longitude": longitude,
        "location_precision": "timezone",
        "location": ", ".join(part for part in (current.get("city") or city, country_name) if part) or "Unknown",
    })
    return current


def enrich_latest_marketing_visit_from_browser(request, *, timezone_name="", language=""):
    """Enrich the current anonymous session's latest marketing visit in place."""
    try:
        from .models import PlatformEvent

        session_key = request.session.session_key or ""
        if not session_key:
            return None
        event = (
            PlatformEvent.objects.filter(
                event_type=PlatformEvent.EVENT_MARKETING_VISIT,
                session_key=session_key[:64],
            )
            .order_by("-occurred_at", "-id")
            .first()
        )
        if not event:
            return None
        updated = browser_marketing_location_metadata(
            timezone_name=timezone_name,
            language=language,
            current=event.metadata,
        )
        if updated != (event.metadata or {}):
            event.metadata = updated
            event.save(update_fields=["metadata"])
            invalidate_founder_analytics_cache()
        return event
    except Exception:
        # Browser enrichment is analytics-only and must never affect the public page.
        return None


def _marketing_visit_location(metadata):
    metadata = metadata or {}
    location_hint = str(metadata.get("location") or "").strip()
    raw_country = str(
        metadata.get("country_code")
        or metadata.get("country")
        or (location_hint if len(location_hint) == 2 else "")
    ).strip()
    country_code = raw_country.upper() if len(raw_country) == 2 else ""
    country_info = country_metadata(country_code)
    country_name = str(metadata.get("country_name") or "").strip()
    if not country_name:
        country_name = country_info.get("name") or (raw_country if len(raw_country) != 2 else country_code)
    city = str(metadata.get("city") or "").strip()
    region = str(metadata.get("region") or "").strip()
    label = ", ".join(part for part in (city, region, country_name) if part) or str(metadata.get("location") or "Unknown").strip() or "Unknown"
    try:
        latitude = float(metadata.get("latitude")) if metadata.get("latitude") not in (None, "") else None
        longitude = float(metadata.get("longitude")) if metadata.get("longitude") not in (None, "") else None
    except (TypeError, ValueError):
        latitude = longitude = None
    precision = str(metadata.get("location_precision") or "").strip()
    if latitude is None or longitude is None:
        latitude = country_info.get("latitude")
        longitude = country_info.get("longitude")
        if latitude is not None and longitude is not None:
            precision = "country"
    if not precision:
        precision = "unknown" if latitude is None or longitude is None else "country"
    return {
        "label": label,
        "country_code": country_code,
        "country_name": country_name or country_code or "Unknown",
        "city": city,
        "region": region,
        "latitude": latitude,
        "longitude": longitude,
        "precision": precision,
    }


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
        if event_type == PlatformEvent.EVENT_MARKETING_VISIT:
            invalidate_founder_analytics_cache()
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
        invalidate_founder_analytics_cache()
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
        invalidate_founder_analytics_cache()
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

    cached = cache.get(_FOUNDER_ANALYTICS_CACHE_KEY)
    if cached is None:
        now = now or timezone.now()
        since_7 = now - timedelta(days=7)
        since_30 = now - timedelta(days=30)
        marketing_visit_qs = PlatformEvent.objects.filter(
            event_type=PlatformEvent.EVENT_MARKETING_VISIT, occurred_at__gte=since_30
        )
        marketing_visits_30d = marketing_visit_qs.count()
        visits_7d = marketing_visit_qs.filter(occurred_at__gte=since_7).count()
        marketing_unique_sessions_30d = (
            marketing_visit_qs.exclude(session_key="")
            .values("session_key")
            .distinct()
            .count()
        )
        marketing_visits = list(
            marketing_visit_qs.only("id", "session_key", "metadata", "occurred_at")
            .order_by("-occurred_at", "-id")[:1000]
        )
        location_counts = {}
        country_codes = set()
        known_location_visits = 0
        map_points = {}
        for event in marketing_visits:
            location = _marketing_visit_location(event.metadata)
            label = location["label"]
            location_counts[label] = location_counts.get(label, 0) + 1
            if location["country_code"]:
                country_codes.add(location["country_code"])
            if location["latitude"] is not None and location["longitude"] is not None:
                known_location_visits += 1
                if location["precision"] in {"edge", "timezone"}:
                    map_key = (round(location["latitude"], 3), round(location["longitude"], 3), label)
                    point_label = label
                else:
                    map_key = (round(location["latitude"], 3), round(location["longitude"], 3), location["country_name"])
                    point_label = location["country_name"] or label
                point = map_points.setdefault(map_key, {
                    "location": point_label,
                    "country_code": location["country_code"],
                    "latitude": location["latitude"],
                    "longitude": location["longitude"],
                    "precision": location["precision"],
                    "total": 0,
                    "unique_sessions": set(),
                    "last_visit": event.occurred_at,
                })
                point["total"] += 1
                if event.session_key:
                    point["unique_sessions"].add(event.session_key)
                if event.occurred_at > point["last_visit"]:
                    point["last_visit"] = event.occurred_at
        top_marketing_locations = [
            {"location": location, "total": total}
            for location, total in sorted(location_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
        ]
        marketing_map_points = []
        for point in sorted(map_points.values(), key=lambda row: (-row["total"], row["location"])):
            marketing_map_points.append({
                "location": point["location"],
                "country_code": point["country_code"],
                "latitude": point["latitude"],
                "longitude": point["longitude"],
                "precision": point["precision"],
                "total": point["total"],
                "unique_sessions": len(point["unique_sessions"]),
                "last_visit_iso": point["last_visit"].isoformat(),
                "x_percent": round(((point["longitude"] + 180) / 360) * 100, 4),
                "y_percent": round(((90 - point["latitude"]) / 180) * 100, 4),
            })
        daily_rows = list(
            marketing_visit_qs
            .filter(occurred_at__gte=now - timedelta(days=13))
            .annotate(day=TruncDate("occurred_at"))
            .values("day")
            .annotate(total=Count("id"))
            .order_by("day")
        )
        daily_map = {row["day"]: row["total"] for row in daily_rows}
        marketing_daily_visits = []
        for offset in range(13, -1, -1):
            day = (now - timedelta(days=offset)).date()
            marketing_daily_visits.append({"day": day.isoformat(), "label": day.strftime("%d %b"), "total": daily_map.get(day, 0)})
        event_summary = PlatformEvent.objects.aggregate(
            lead_sessions_30d=Count(
                "session_key", distinct=True,
                filter=(
                    Q(event_type=PlatformEvent.EVENT_SIGNUP_VIEW, occurred_at__gte=since_30)
                    & ~Q(session_key="")
                ),
            ),
            registrations_7d=Count(
                "id", filter=Q(event_type=PlatformEvent.EVENT_REGISTRATION, occurred_at__gte=since_7)
            ),
            registrations_30d=Count(
                "id", filter=Q(event_type=PlatformEvent.EVENT_REGISTRATION, occurred_at__gte=since_30)
            ),
            latest_registration_id=Max(
                "id", filter=Q(event_type=PlatformEvent.EVENT_REGISTRATION)
            ),
            logins_7d=Count(
                "id", filter=Q(event_type=PlatformEvent.EVENT_LOGIN, occurred_at__gte=since_7)
            ),
            active_businesses_7d=Count(
                "business_id", distinct=True,
                filter=Q(
                    event_type=PlatformEvent.EVENT_MODULE_VIEW,
                    occurred_at__gte=since_7,
                    business__isnull=False,
                ),
            ),
            subscription_events_30d=Count(
                "id",
                filter=Q(
                    event_type__in=PlatformEvent.SUBSCRIPTION_EVENTS,
                    occurred_at__gte=since_30,
                ),
            ),
        )
        lead_sessions = event_summary["lead_sessions_30d"] or 0
        registrations = event_summary["registrations_30d"] or 0
        conversion = round((registrations / lead_sessions * 100), 1) if lead_sessions else 0
        top_modules = list(
            PlatformEvent.objects.filter(
                occurred_at__gte=since_30,
                event_type=PlatformEvent.EVENT_MODULE_VIEW,
                module__gt="",
            )
            .values("module").annotate(total=Count("id"))
            .order_by("-total", "module")[:8]
        )
        all_signup_contacts = founder_signup_contacts(include_deleted=True)
        active_signup_contacts = [contact for contact in all_signup_contacts if not contact["deleted"]]
        cached = {
            "marketing_visits_7d": visits_7d,
            "marketing_visits_30d": marketing_visits_30d,
            "marketing_unique_sessions_30d": marketing_unique_sessions_30d,
            "marketing_countries_30d": len(country_codes),
            "marketing_location_coverage_30d": round((known_location_visits / marketing_visits_30d * 100), 1) if marketing_visits_30d else 0,
            "marketing_map_points": marketing_map_points,
            "marketing_daily_visits": marketing_daily_visits,
            "top_marketing_locations": top_marketing_locations,
            "recent_marketing_visits": [
                {
                    "location": _marketing_visit_location(event.metadata)["label"],
                    "precision": _marketing_visit_location(event.metadata)["precision"],
                    "occurred_at": event.occurred_at,
                }
                for event in marketing_visits[:30]
            ],
            "lead_sessions_30d": lead_sessions,
            "registrations_7d": event_summary["registrations_7d"] or 0,
            "registrations_30d": registrations,
            "latest_registration_id": event_summary["latest_registration_id"] or 0,
            "signup_conversion_30d": conversion,
            "logins_7d": event_summary["logins_7d"] or 0,
            "active_businesses_7d": event_summary["active_businesses_7d"] or 0,
            "subscription_events_30d": event_summary["subscription_events_30d"] or 0,
            "signup_contacts_count": len(active_signup_contacts),
            "signup_contacts_total_count": len(all_signup_contacts),
            "signup_contacts": all_signup_contacts[:50],
            "top_modules": top_modules,
        }
        # Founder analytics is read-only operational telemetry. A very short
        # cache removes repeated full signup-history scans while signup/deletion
        # actions explicitly invalidate it.
        cache.set(_FOUNDER_ANALYTICS_CACHE_KEY, cached, timeout=20)

    result = dict(cached)
    result["recent_events"] = PlatformEvent.objects.select_related("business", "user").order_by("-occurred_at", "-id")[:30]
    return result

