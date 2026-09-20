"""Durable, tenant-safe Web Push delivery for commerce notifications.

Normal commerce requests only persist the notification and schedule a tiny
best-effort dispatcher thread after commit. Delivery state lives in the
database, so a process restart cannot lose the notification; the scheduled
maintenance command retries pending work later.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from accounts.models import RoleModulePermission, UserBusiness, UserModulePermission
from accounts.services import business_has_module

from accounts.platform_integrations import redact_disabled_integrations

from .models import (
    CommerceNotification,
    CommercePushDelivery,
    CommercePushSubscription,
    CommerceSettings,
    DeliverySettings,
)

logger = logging.getLogger(__name__)
_dispatch_lock = threading.Lock()


def endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256((endpoint or "").encode("utf-8")).hexdigest()


def configured() -> bool:
    return bool(
        getattr(settings, "WEB_PUSH_VAPID_PRIVATE_KEY", "")
        and getattr(settings, "WEB_PUSH_VAPID_PUBLIC_KEY", "")
        and getattr(settings, "WEB_PUSH_VAPID_SUBJECT", "")
    )


def _eligible_subscription_ids(business, subscriptions, module):
    """Resolve one module's view permission for all subscribed users in bounded queries."""
    if not subscriptions or not business_has_module(business, module):
        return set()

    user_ids = {subscription.user_id for subscription in subscriptions}
    superuser_ids = {
        subscription.user_id
        for subscription in subscriptions
        if getattr(subscription.user, "is_superuser", False)
    }
    memberships = list(
        UserBusiness.objects.filter(
            business=business, active=True, user_id__in=user_ids - superuser_ids,
        ).select_related("role")
    )
    membership_by_user = {membership.user_id: membership for membership in memberships}
    role_ids = {membership.role_id for membership in memberships}
    role_access = {
        permission.role_id: permission.can_view
        for permission in RoleModulePermission.objects.filter(
            role_id__in=role_ids, module=module,
        )
    }
    user_access = {
        permission.membership_id: permission.can_view
        for permission in UserModulePermission.objects.filter(
            membership_id__in=[membership.pk for membership in memberships], module=module,
        )
    }

    allowed = set(superuser_ids)
    for user_id, membership in membership_by_user.items():
        override = user_access.get(membership.pk)
        if override is True or (override is None and role_access.get(membership.role_id, False)):
            allowed.add(user_id)
    return {subscription.pk for subscription in subscriptions if subscription.user_id in allowed}


def _access_sets(business, subscriptions):
    commerce_ids = _eligible_subscription_ids(business, subscriptions, "commerce")
    delivery_ids = _eligible_subscription_ids(business, subscriptions, "delivery")
    rider_ids = _eligible_subscription_ids(business, subscriptions, "delivery_rider")
    inventory_ids = _eligible_subscription_ids(business, subscriptions, "inventory")
    return {
        "commerce": commerce_ids,
        "delivery": delivery_ids,
        "rider": rider_ids,
        "inventory": inventory_ids,
        "delivery_staff": commerce_ids | delivery_ids,
        "activity": commerce_ids | delivery_ids | rider_ids,
    }


def _sync_inventory_push_notices():
    """Materialize one targeted Web Push carrier per subscribed inventory user.

    Inventory's in-app state remains in InventoryAlertState. CommerceNotification
    is used only as the existing durable Web Push outbox carrier and is excluded
    from the Commerce tray/feed.
    """
    from inventory.alert_services import inventory_alert_feed
    from inventory.models import InventoryAlertState

    subscriptions = list(
        CommercePushSubscription.objects.filter(active=True)
        .select_related("business", "user")
        .order_by("business_id", "user_id", "pk")
    )
    by_business = {}
    for subscription in subscriptions:
        bucket = by_business.setdefault(subscription.business_id, {"business": subscription.business, "subscriptions": []})
        bucket["subscriptions"].append(subscription)

    visible = {}
    now = timezone.now()
    for bucket in by_business.values():
        business = bucket["business"]
        subs = bucket["subscriptions"]
        inventory_ids = _eligible_subscription_ids(business, subs, "inventory")
        users = {}
        for subscription in subs:
            if subscription.pk in inventory_ids:
                users.setdefault(subscription.user_id, {"user": subscription.user, "subscriptions": []})["subscriptions"].append(subscription)
        for user_bucket in users.values():
            user = user_bucket["user"]
            feed = inventory_alert_feed(business=business, user=user)
            alerts = feed.get("alerts") or []
            if not feed.get("enabled") or not alerts:
                continue
            raw_count = int(feed.get("raw_count") or 0)
            finished_count = int(feed.get("finished_count") or 0)
            parts = []
            if raw_count:
                parts.append(f"{raw_count} raw material{'s' if raw_count != 1 else ''}")
            if finished_count:
                parts.append(f"{finished_count} finished good{'s' if finished_count != 1 else ''}")
            low_count = sum(1 for row in alerts if row.get("severity") == "low")
            title = "Inventory stock needs attention"
            message = f"{', '.join(parts)} need attention" + (f" · {low_count} low-stock" if low_count else "") + "."
            notice, created = CommerceNotification.raw_objects.get_or_create(
                business=business, recipient_user=user, dedupe_key="inventory-alert-summary",
                defaults={
                    "event_type": CommerceNotification.EVENT_INVENTORY_ALERT,
                    "title": title, "message": message, "target_url": "/inventory/",
                },
            )
            changed = []
            for field, value in (("event_type", CommerceNotification.EVENT_INVENTORY_ALERT), ("title", title), ("message", message), ("target_url", "/inventory/")):
                if getattr(notice, field) != value:
                    setattr(notice, field, value); changed.append(field)
            if changed:
                notice.save(update_fields=changed + ["updated_at"])

            tokens = {(row.get("type"), int(row.get("item_id"))) for row in alerts if row.get("type") and row.get("item_id")}
            states = list(InventoryAlertState.raw_objects.filter(
                business=business, user=user, is_active=True,
                alert_type__in={token[0] for token in tokens}, object_id__in={token[1] for token in tokens},
            )) if tokens else []
            latest_state_change = max((state.updated_at for state in states), default=None)
            user_sub_ids = {subscription.pk for subscription in user_bucket["subscriptions"]}
            last_sent = (
                CommercePushDelivery.objects.filter(
                    notification=notice, subscription_id__in=user_sub_ids, sent_at__isnull=False
                ).order_by("-sent_at").values_list("sent_at", flat=True).first()
            )
            force = bool(created or (latest_state_change and (last_sent is None or latest_state_change > last_sent)))
            visible[notice.pk] = {
                "force": force,
                "repeat": int(feed.get("sound_repeat_minutes") or 0) if feed.get("sound_enabled") else 0,
                "subscription_ids": user_sub_ids,
            }
    return visible


def _enqueue_notifications(limit=30):
    """Create delivery outbox rows once for each durable notification."""
    if not configured():
        return 0

    now = timezone.now()
    recent_cutoff = now - timedelta(minutes=30)
    CommerceNotification.raw_objects.filter(
        push_processed_at__isnull=True, created_at__lt=recent_cutoff
    ).update(push_processed_at=now)
    notices = list(
        CommerceNotification.raw_objects.filter(
            push_processed_at__isnull=True, created_at__gte=recent_cutoff
        )
        .select_related("business")
        .order_by("created_at", "pk")[:limit]
    )
    if not notices:
        return 0

    created = 0
    processed_ids = []
    by_business = {}
    for notice in notices:
        processed_ids.append(notice.pk)
        by_business.setdefault(notice.business_id, {"business": notice.business, "notices": []})["notices"].append(notice)

    for bucket in by_business.values():
        business = bucket["business"]
        commerce_settings = CommerceSettings.raw_objects.filter(business=business).only(
            "notifications_enabled", "notification_desktop_enabled"
        ).first()
        subscriptions = list(
            CommercePushSubscription.objects.filter(business=business, active=True).select_related("user")
        )
        access = _access_sets(business, subscriptions)
        rows = []
        for notice in bucket["notices"]:
            if notice.event_type in CommerceNotification.INVENTORY_EVENTS:
                category_ids = access["inventory"]
            else:
                if commerce_settings and (not commerce_settings.notifications_enabled or not commerce_settings.notification_desktop_enabled):
                    continue
                category_ids = access["delivery_staff"] if notice.event_type in CommerceNotification.DELIVERY_EVENTS else access["commerce"]
            if notice.recipient_user_id:
                allowed_ids = {
                    subscription.pk for subscription in subscriptions
                    if subscription.user_id == notice.recipient_user_id
                    and subscription.pk in (access["inventory"] if notice.event_type in CommerceNotification.INVENTORY_EVENTS else access["activity"])
                }
            else:
                allowed_ids = category_ids
            rows.extend(
                CommercePushDelivery(notification=notice, subscription=subscription)
                for subscription in subscriptions if subscription.pk in allowed_ids
            )
        if rows:
            CommercePushDelivery.objects.bulk_create(rows, ignore_conflicts=True, batch_size=250)
            created += len(rows)

    CommerceNotification.raw_objects.filter(pk__in=processed_ids).update(push_processed_at=timezone.now())
    return created


def _requeue_due_repeats(inventory_visible=None, *, limit=800):
    """Re-arm at most one unread Commerce/Rider reminder per device plus due Inventory summary pushes."""
    now = timezone.now()
    inventory_visible = inventory_visible or {}
    changed = []

    # Inventory summaries use the Inventory alert/snooze state supplied by the sync pass.
    if inventory_visible:
        deliveries = list(CommercePushDelivery.objects.filter(
            status=CommercePushDelivery.STATUS_SENT,
            notification_id__in=inventory_visible.keys(),
            subscription__active=True,
        ).select_related("subscription")[:limit])
        for delivery in deliveries:
            meta = inventory_visible.get(delivery.notification_id) or {}
            if delivery.subscription_id not in meta.get("subscription_ids", set()):
                continue
            repeat = int(meta.get("repeat") or 0)
            due = bool(meta.get("force")) or bool(repeat and delivery.sent_at and delivery.sent_at <= now - timedelta(minutes=repeat))
            if not due:
                continue
            delivery.status = CommercePushDelivery.STATUS_PENDING
            delivery.attempts = 0
            delivery.next_attempt_at = now
            delivery.sent_at = None
            delivery.last_error = ""
            delivery.updated_at = now
            changed.append(delivery)

    # Commerce and rider reminders repeat only while still unread. Keep one newest unread reminder per device.
    sent = list(CommercePushDelivery.objects.filter(
        status=CommercePushDelivery.STATUS_SENT, subscription__active=True,
    ).exclude(notification__event_type__in=CommerceNotification.INVENTORY_EVENTS).select_related(
        "notification__business", "subscription__user", "subscription__business"
    ).prefetch_related("notification__reads").order_by("subscription_id", "-notification__created_at", "-notification_id")[:limit])
    business_cache = {}
    chosen_subscriptions = set()
    for delivery in sent:
        subscription = delivery.subscription
        if subscription.pk in chosen_subscriptions:
            continue
        if any(read.user_id == subscription.user_id for read in delivery.notification.reads.all()):
            continue
        business = delivery.notification.business
        cache = business_cache.get(business.pk)
        if cache is None:
            subs = list(CommercePushSubscription.objects.filter(business=business, active=True).select_related("user"))
            cache = {
                "access": _access_sets(business, subs),
                "commerce": CommerceSettings.raw_objects.filter(business=business).first(),
                "delivery": DeliverySettings.raw_objects.filter(business=business).first(),
            }
            business_cache[business.pk] = cache
        access = cache["access"]
        notice = delivery.notification
        if notice.recipient_user_id:
            eligible = notice.recipient_user_id == subscription.user_id and subscription.pk in access["activity"]
        elif notice.event_type in CommerceNotification.DELIVERY_EVENTS:
            eligible = subscription.pk in access["delivery_staff"]
        else:
            eligible = subscription.pk in access["commerce"]
        if not eligible:
            continue
        chosen_subscriptions.add(subscription.pk)
        commerce_settings = cache["commerce"]
        if commerce_settings and (not commerce_settings.notifications_enabled or not commerce_settings.notification_desktop_enabled):
            continue
        rider_only = subscription.pk in access["rider"] and subscription.pk not in access["commerce"] and subscription.pk not in access["delivery"]
        if rider_only:
            delivery_settings = cache["delivery"]
            enabled = delivery_settings.rider_alert_sound_enabled if delivery_settings else True
            repeat = delivery_settings.rider_alert_sound_repeat_minutes if delivery_settings else 2
        else:
            enabled = commerce_settings.notification_sound_enabled if commerce_settings else True
            repeat = commerce_settings.notification_sound_repeat_minutes if commerce_settings else 2
        repeat = min(1440, max(0, int(repeat or 0)))
        if not enabled or not repeat or not delivery.sent_at or delivery.sent_at > now - timedelta(minutes=repeat):
            continue
        delivery.status = CommercePushDelivery.STATUS_PENDING
        delivery.attempts = 0
        delivery.next_attempt_at = now
        delivery.sent_at = None
        delivery.last_error = ""
        delivery.updated_at = now
        changed.append(delivery)

    if changed:
        CommercePushDelivery.objects.bulk_update(
            changed, ["status", "attempts", "next_attempt_at", "sent_at", "last_error", "updated_at"], batch_size=250
        )
    return len(changed)


def _payload(delivery):
    notice = delivery.notification
    return json.dumps(
        {
            "type": "commerce.notification",
            "channel": "inventory" if notice.event_type in CommerceNotification.INVENTORY_EVENTS else ("delivery" if notice.event_type in CommerceNotification.DELIVERY_EVENTS else "commerce"),
            "id": str(notice.public_id),
            "title": redact_disabled_integrations(notice.title),
            "body": redact_disabled_integrations(notice.message),
            "url": notice.target_url or "/commerce/",
            "business": notice.business.name,
            "icon": "/static/core/pwa/icon-192.png",
            "badge": "/static/core/pwa/icon-192.png",
        },
        separators=(",", ":"),
    )


def _send_pending(limit=40):
    if not configured():
        return {"sent": 0, "failed": 0, "expired": 0}

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.error("WEB_PUSH is configured but pywebpush is not installed")
        return {"sent": 0, "failed": 0, "expired": 0}

    now = timezone.now()
    deliveries = list(
        CommercePushDelivery.objects.filter(
            status=CommercePushDelivery.STATUS_PENDING,
            next_attempt_at__lte=now,
            subscription__active=True,
        )
        .select_related("notification__business", "subscription")
        .order_by("next_attempt_at", "pk")[:limit]
    )
    if not deliveries:
        return {"sent": 0, "failed": 0, "expired": 0}

    changed_deliveries = []
    changed_subscriptions = {}
    counts = {"sent": 0, "failed": 0, "expired": 0}
    subject = settings.WEB_PUSH_VAPID_SUBJECT
    private_key = settings.WEB_PUSH_VAPID_PRIVATE_KEY
    timeout = float(getattr(settings, "WEB_PUSH_TIMEOUT_SECONDS", 5))

    for delivery in deliveries:
        subscription = delivery.subscription
        delivery.attempts += 1
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                },
                data=_payload(delivery),
                vapid_private_key=private_key,
                vapid_claims={"sub": subject},
                timeout=timeout,
                ttl=300,
            )
        except WebPushException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            subscription.last_failure_at = now
            subscription.failure_count = min(32767, subscription.failure_count + 1)
            if status_code in {404, 410}:
                subscription.active = False
                delivery.status = CommercePushDelivery.STATUS_EXPIRED
                counts["expired"] += 1
            elif delivery.attempts >= 4:
                delivery.status = CommercePushDelivery.STATUS_FAILED
                counts["failed"] += 1
            else:
                # Backoff 1, 2, 4 minutes without blocking commerce requests.
                delivery.next_attempt_at = now + timedelta(minutes=2 ** (delivery.attempts - 1))
            delivery.last_error = str(exc)[:500]
            changed_subscriptions[subscription.pk] = subscription
        except Exception as exc:  # keep unexpected transport failures non-fatal
            subscription.last_failure_at = now
            subscription.failure_count = min(32767, subscription.failure_count + 1)
            delivery.last_error = str(exc)[:500]
            if delivery.attempts >= 4:
                delivery.status = CommercePushDelivery.STATUS_FAILED
                counts["failed"] += 1
            else:
                delivery.next_attempt_at = now + timedelta(minutes=2 ** (delivery.attempts - 1))
            changed_subscriptions[subscription.pk] = subscription
            logger.warning("Commerce Web Push delivery failed: %s", type(exc).__name__)
        else:
            delivery.status = CommercePushDelivery.STATUS_SENT
            delivery.attempts = 0
            delivery.sent_at = now
            delivery.last_error = ""
            subscription.failure_count = 0
            subscription.last_success_at = now
            changed_subscriptions[subscription.pk] = subscription
            counts["sent"] += 1
        delivery.updated_at = now
        subscription.updated_at = now
        changed_deliveries.append(delivery)

    if changed_deliveries:
        CommercePushDelivery.objects.bulk_update(
            changed_deliveries,
            ["status", "attempts", "next_attempt_at", "sent_at", "last_error", "updated_at"],
            batch_size=250,
        )
    if changed_subscriptions:
        CommercePushSubscription.objects.bulk_update(
            list(changed_subscriptions.values()),
            ["active", "failure_count", "last_success_at", "last_failure_at", "updated_at"],
            batch_size=250,
        )
    return counts


def dispatch_pending_pushes(*, notice_limit=30, delivery_limit=40):
    """Sync due operational alerts, enqueue new pushes, re-arm repeats, and drain the durable outbox."""
    if not configured():
        return {"configured": False, "queued": 0, "requeued": 0, "sent": 0, "failed": 0, "expired": 0}
    close_old_connections()
    try:
        inventory_visible = _sync_inventory_push_notices()
        queued = _enqueue_notifications(limit=notice_limit)
        requeued = _requeue_due_repeats(inventory_visible)
        counts = _send_pending(limit=delivery_limit)
        return {"configured": True, "queued": queued, "requeued": requeued, **counts}
    finally:
        close_old_connections()


def kick_push_dispatcher():
    """Start one best-effort in-process dispatcher without delaying the response.

    The outbox is durable; if Render restarts before this thread runs, the
    scheduled maintenance command will retry later.
    """
    if not configured() or not _dispatch_lock.acquire(blocking=False):
        return

    def runner():
        try:
            # Drain small bursts without spawning one thread per notification.
            # A hard loop cap preserves the free Render instance for web traffic.
            for _ in range(4):
                dispatch_pending_pushes(notice_limit=50, delivery_limit=80)
                if not CommerceNotification.raw_objects.filter(push_processed_at__isnull=True).exists():
                    break
        except Exception:
            logger.exception("Background commerce Web Push dispatch failed")
        finally:
            _dispatch_lock.release()

    threading.Thread(target=runner, name="commerce-web-push", daemon=True).start()


def schedule_push_dispatch():
    """Register the lightweight kick only after the durable notice commits."""
    transaction.on_commit(kick_push_dispatcher)
