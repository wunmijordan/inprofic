import json
from uuid import UUID

from django.http import JsonResponse
from django.db.models import Q
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from accounts.services import user_has_permission
from accounts.platform_integrations import redact_disabled_integrations

from .models import CommerceNotification, CommerceNotificationRead, CommerceSettings, DeliverySettings
from .realtime import publish_user_notifications_changed


def _access_profile(request):
    if not (request.user.is_authenticated and getattr(request, "business", None)):
        return {"commerce": False, "delivery": False, "rider": False, "inventory": False}
    return {
        "commerce": user_has_permission(request.user, request.business, "commerce", "view"),
        "delivery": user_has_permission(request.user, request.business, "delivery", "view"),
        "rider": user_has_permission(request.user, request.business, "delivery_rider", "view"),
        "inventory": user_has_permission(request.user, request.business, "inventory", "view"),
    }


def _notification_authorized(request):
    access = _access_profile(request)
    return access["commerce"] or access["delivery"] or access["rider"]


def _push_authorized(request):
    return any(_access_profile(request).values())


def _unread(request):
    access = _access_profile(request)
    if not (access["commerce"] or access["delivery"] or access["rider"]):
        return CommerceNotification.raw_objects.none()
    visible = Q(recipient_user=request.user)
    if access["commerce"]:
        # Commerce staff see ordinary commerce activity plus delivery activity so
        # paid-order handoffs are not lost between teams.
        visible |= Q(recipient_user__isnull=True)
    elif access["delivery"]:
        visible |= Q(recipient_user__isnull=True, event_type__in=CommerceNotification.DELIVERY_EVENTS)
    # Rider-only users intentionally see only direct alerts addressed to them.
    return CommerceNotification.raw_objects.filter(
        business=request.business
    ).filter(visible).exclude(
        event_type__in=CommerceNotification.INVENTORY_EVENTS
    ).exclude(reads__user=request.user)


@never_cache
@require_GET
def notification_feed(request):
    if not _notification_authorized(request):
        return JsonResponse({"detail": "Commerce notification access is unavailable."}, status=403)
    settings = CommerceSettings.raw_objects.filter(business=request.business).first()
    access = _access_profile(request)
    rider_only = access["rider"] and not access["commerce"] and not access["delivery"]
    delivery_settings = DeliverySettings.raw_objects.filter(business=request.business).first() if rider_only else None
    enabled = settings.notifications_enabled if settings else True
    if not enabled:
        return JsonResponse({
            "enabled": False,
            "sound_enabled": False,
            "sound_repeat_minutes": 0,
            "sound_tune": "double_ping",
            "desktop_enabled": False,
            "rider_profile": rider_only,
            "poll_seconds": 8,
            "unread_count": 0,
            "notifications": [],
        })
    unread = _unread(request)
    rows = list(unread[:25])
    return JsonResponse({
        "enabled": True,
        "sound_enabled": (
            delivery_settings.rider_alert_sound_enabled
            if rider_only and delivery_settings else
            (settings.notification_sound_enabled if settings else True)
        ),
        "sound_repeat_minutes": min(1440, max(0,
            delivery_settings.rider_alert_sound_repeat_minutes
            if rider_only and delivery_settings else
            (settings.notification_sound_repeat_minutes if settings else 2)
        )),
        "sound_tune": (
            delivery_settings.rider_alert_sound_tune
            if rider_only and delivery_settings else
            (settings.notification_sound_tune if settings else "double_ping")
        ),
        "desktop_enabled": settings.notification_desktop_enabled if settings else True,
        "rider_profile": rider_only,
        "poll_seconds": 8,
        "unread_count": unread.count(),
        "notifications": [
            {
                "id": str(row.public_id),
                "event_type": row.event_type,
                "title": redact_disabled_integrations(row.title),
                "message": redact_disabled_integrations(row.message),
                "target_url": row.target_url,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    })


@never_cache
@require_POST
def notification_read(request):
    if not _notification_authorized(request):
        return JsonResponse({"detail": "Commerce notification access is unavailable."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "The submitted notification details are invalid."}, status=400)

    unread = _unread(request)
    if payload.get("all") is True:
        notices = list(unread.only("pk"))
    else:
        try:
            public_ids = [UUID(str(value)) for value in payload.get("notification_ids", [])]
        except (TypeError, ValueError, AttributeError):
            return JsonResponse({"detail": "notification_ids must contain valid UUIDs."}, status=400)
        notices = list(unread.filter(public_id__in=public_ids).only("pk"))
    CommerceNotificationRead.objects.bulk_create(
        [CommerceNotificationRead(notification=notice, user=request.user) for notice in notices],
        ignore_conflicts=True,
    )
    publish_user_notifications_changed(request.business.pk, request.user.pk)
    return JsonResponse({"read": len(notices), "unread_count": _unread(request).count()})

@never_cache
@require_GET
def push_config(request):
    if not _push_authorized(request):
        return JsonResponse({"detail": "Operational notification access is unavailable."}, status=403)
    from django.conf import settings as django_settings
    from .models import CommercePushSubscription
    from .webpush import configured

    return JsonResponse({
        "configured": configured(),
        "public_key": django_settings.WEB_PUSH_VAPID_PUBLIC_KEY if configured() else "",
        "active_devices": CommercePushSubscription.objects.filter(
            business=request.business, user=request.user, active=True
        ).count(),
    })


@never_cache
@require_POST
def push_subscribe(request):
    if not _push_authorized(request):
        return JsonResponse({"detail": "Operational notification access is unavailable."}, status=403)
    from .models import CommercePushSubscription
    from .webpush import configured, endpoint_hash, kick_push_dispatcher

    if not configured():
        return JsonResponse({"detail": "Web Push is not configured on this deployment."}, status=503)
    try:
        payload = json.loads(request.body or b"{}")
        endpoint = str(payload.get("endpoint") or "").strip()
        keys = payload.get("keys") or {}
        p256dh = str(keys.get("p256dh") or "").strip()
        auth = str(keys.get("auth") or "").strip()
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({"detail": "Submit a valid PushSubscription."}, status=400)
    if not endpoint.startswith("https://") or len(endpoint) > 3000 or not p256dh or not auth:
        return JsonResponse({"detail": "The PushSubscription is incomplete."}, status=400)
    if len(p256dh) > 255 or len(auth) > 255:
        return JsonResponse({"detail": "The PushSubscription keys are invalid."}, status=400)

    digest = endpoint_hash(endpoint)
    # A browser PushSubscription belongs to one origin/device. If another user
    # signs into the same browser and explicitly enables alerts, transfer that
    # endpoint to the current account instead of leaking the previous user's
    # tenant notifications to the shared device.
    CommercePushSubscription.objects.filter(endpoint_hash=digest).exclude(user=request.user).update(active=False)
    subscription, _created = CommercePushSubscription.objects.update_or_create(
        business=request.business,
        user=request.user,
        endpoint_hash=digest,
        defaults={
            "endpoint": endpoint,
            "p256dh": p256dh,
            "auth": auth,
            "user_agent": request.headers.get("User-Agent", "")[:300],
            "active": True,
            "failure_count": 0,
            "last_failure_at": None,
        },
    )
    # If notifications were created moments before this device subscribed,
    # let the durable dispatcher reconcile pending work without blocking here.
    kick_push_dispatcher()
    return JsonResponse({"subscribed": True, "subscription_id": subscription.pk})


@never_cache
@require_POST
def push_unsubscribe(request):
    if not _push_authorized(request):
        return JsonResponse({"detail": "Operational notification access is unavailable."}, status=403)
    from .models import CommercePushSubscription
    from .webpush import endpoint_hash

    try:
        payload = json.loads(request.body or b"{}")
        endpoint = str(payload.get("endpoint") or "").strip()
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({"detail": "The submitted notification details are invalid."}, status=400)
    if not endpoint:
        return JsonResponse({"detail": "A notification destination is required."}, status=400)
    updated = CommercePushSubscription.objects.filter(
        user=request.user,
        endpoint_hash=endpoint_hash(endpoint),
    ).update(active=False)
    return JsonResponse({"subscribed": False, "updated": updated})
