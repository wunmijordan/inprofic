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

from .models import (
    CommerceNotification,
    CommercePushDelivery,
    CommercePushSubscription,
    CommerceSettings,
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


def _enqueue_notifications(limit=30):
    """Create delivery outbox rows once for each durable notification."""
    if not configured():
        return 0

    now = timezone.now()
    recent_cutoff = now - timedelta(minutes=30)
    # Web Push is for timely alerts, not replaying an old unread history after
    # keys are first configured. Old notices remain visible in the durable
    # in-app feed but are retired from the push outbox.
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
        by_business.setdefault(
            notice.business_id, {"business": notice.business, "notices": []}
        )["notices"].append(notice)

    for bucket in by_business.values():
        business = bucket["business"]
        commerce_settings = CommerceSettings.raw_objects.filter(business=business).only(
            "notifications_enabled", "notification_desktop_enabled"
        ).first()
        if commerce_settings and (
            not commerce_settings.notifications_enabled
            or not commerce_settings.notification_desktop_enabled
        ):
            continue

        subscriptions = list(
            CommercePushSubscription.objects.filter(business=business, active=True)
            .select_related("user")
        )
        commerce_subscription_ids = _eligible_subscription_ids(business, subscriptions, "commerce")
        delivery_subscription_ids = _eligible_subscription_ids(business, subscriptions, "delivery")
        rider_subscription_ids = _eligible_subscription_ids(business, subscriptions, "delivery_rider")
        any_delivery_ids = commerce_subscription_ids | delivery_subscription_ids
        any_activity_ids = any_delivery_ids | rider_subscription_ids
        rows = []
        for notice in bucket["notices"]:
            if notice.recipient_user_id:
                allowed_ids = {
                    subscription.pk for subscription in subscriptions
                    if subscription.user_id == notice.recipient_user_id and subscription.pk in any_activity_ids
                }
            elif notice.event_type in CommerceNotification.DELIVERY_EVENTS:
                allowed_ids = any_delivery_ids
            else:
                allowed_ids = commerce_subscription_ids
            rows.extend(
                CommercePushDelivery(notification=notice, subscription=subscription)
                for subscription in subscriptions
                if subscription.pk in allowed_ids
            )
        if rows:
            CommercePushDelivery.objects.bulk_create(rows, ignore_conflicts=True, batch_size=250)
            created += len(rows)

    CommerceNotification.raw_objects.filter(pk__in=processed_ids).update(
        push_processed_at=timezone.now()
    )
    return created


def _payload(delivery):
    notice = delivery.notification
    return json.dumps(
        {
            "type": "commerce.notification",
            "id": str(notice.public_id),
            "title": notice.title,
            "body": notice.message,
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
    """Idempotently enqueue recent notifications and drain the durable outbox."""
    if not configured():
        return {"configured": False, "queued": 0, "sent": 0, "failed": 0, "expired": 0}
    close_old_connections()
    try:
        queued = _enqueue_notifications(limit=notice_limit)
        counts = _send_pending(limit=delivery_limit)
        return {"configured": True, "queued": queued, **counts}
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
