import json
from uuid import UUID

from django.http import JsonResponse
from django.db.models import Count, Q, Window
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from accounts.services import user_has_permission
from accounts.platform_integrations import glovo_platform_enabled, redact_disabled_integrations
from core.performance import performance_section

from .models import CommerceNotification, CommerceNotificationRead, CommerceSettings, DeliverySettings
from .realtime import publish_user_notifications_changed


def _access_profile(request):
    cached = getattr(request, "_commerce_access_profile", None)
    if cached is not None:
        return cached
    if not (request.user.is_authenticated and getattr(request, "business", None)):
        profile = {"commerce": False, "delivery": False, "rider": False, "inventory": False}
        request._commerce_access_profile = profile
        return profile
    profile = {
        "commerce": user_has_permission(request.user, request.business, "commerce", "view"),
        "delivery": user_has_permission(request.user, request.business, "delivery", "view"),
        "rider": user_has_permission(request.user, request.business, "delivery_rider", "view"),
        "inventory": user_has_permission(request.user, request.business, "inventory", "view"),
    }
    request._commerce_access_profile = profile
    return profile


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
        # Commerce staff see ordinary commerce activity plus general Delivery
        # activity, but dispatch-ready handoffs are reserved for users who
        # actually have Delivery workspace access.
        visible |= (
            Q(recipient_user__isnull=True)
            & ~Q(event_type__in=CommerceNotification.DISPATCH_EVENTS)
        )
    if access["delivery"]:
        visible |= Q(
            recipient_user__isnull=True,
            event_type__in=CommerceNotification.DELIVERY_EVENTS,
        )
    # Rider-only users intentionally see only direct alerts addressed to them.
    return CommerceNotification.raw_objects.filter(
        business=request.business
    ).filter(visible).exclude(
        event_type__in=CommerceNotification.INVENTORY_EVENTS
    ).exclude(reads__user=request.user)


@never_cache
@require_GET
def notification_feed(request):
    with performance_section(request, "notification.access"):
        if not _notification_authorized(request):
            return JsonResponse({"detail": "Commerce notification access is unavailable."}, status=403)
        access = _access_profile(request)
    with performance_section(request, "notification.settings"):
        settings = CommerceSettings.raw_objects.filter(business=request.business).first()
    rider_only = access["rider"] and not access["commerce"] and not access["delivery"]
    with performance_section(request, "notification.rider_settings"):
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
            "poll_seconds": 15,
            "unread_count": 0,
            "notifications": [],
        })
    with performance_section(request, "notification.fetch"):
        unread = _unread(request)
        rows = list(
            unread.annotate(_unread_total=Window(expression=Count("pk")))[:25]
        )
        unread_count = int(rows[0]._unread_total) if rows else 0
    with performance_section(request, "notification.serialize"):
        integration_enabled = glovo_platform_enabled()
        notifications = [
            {
                "id": str(row.public_id),
                "event_type": row.event_type,
                "title": redact_disabled_integrations(row.title, glovo_enabled=integration_enabled),
                "message": redact_disabled_integrations(row.message, glovo_enabled=integration_enabled),
                "target_url": row.target_url,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
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
        "poll_seconds": 15,
        "unread_count": unread_count,
        "notifications": notifications,
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
    from .webpush import configured, kick_push_dispatcher
    from .push_subscriptions import sync_device_subscription

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

    sync_device_subscription(
        user=request.user,
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        user_agent=request.headers.get("User-Agent", ""),
    )
    # If notifications were created moments before this device subscribed,
    # let the durable dispatcher reconcile pending work without blocking here.
    kick_push_dispatcher()
    return JsonResponse({"subscribed": True})


@never_cache
@require_POST
def push_unsubscribe(request):
    if not _push_authorized(request):
        return JsonResponse({"detail": "Operational notification access is unavailable."}, status=403)
    from .push_subscriptions import disable_device_subscription

    try:
        payload = json.loads(request.body or b"{}")
        endpoint = str(payload.get("endpoint") or "").strip()
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({"detail": "The submitted notification details are invalid."}, status=400)
    if not endpoint:
        return JsonResponse({"detail": "A notification destination is required."}, status=400)
    updated = disable_device_subscription(user=request.user, endpoint=endpoint)
    return JsonResponse({"subscribed": False, "updated": updated})
