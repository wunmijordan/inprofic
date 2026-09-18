"""Small, transport-only helpers for tenant-safe commerce realtime signals."""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def business_notification_group(business_id):
    return f"commerce.notifications.business.{int(business_id)}"


def user_notification_group(business_id, user_id):
    return f"commerce.notifications.business.{int(business_id)}.user.{int(user_id)}"


def public_delivery_group(business_id, delivery_public_id):
    return f"commerce.delivery.public.{int(business_id)}.{delivery_public_id}"


def _publish(group, event_type, **payload):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    try:
        async_to_sync(channel_layer.group_send)(
            group,
            {"type": event_type, **payload},
        )
    except Exception:
        # Realtime transport must never roll back durable business state.
        logger.exception("Could not publish commerce realtime signal")


def publish_business_notifications_changed(business_id, reason="created"):
    _publish(business_notification_group(business_id), "notifications.changed", reason=reason)


def publish_user_notifications_changed(business_id, user_id, reason="read"):
    _publish(user_notification_group(business_id, user_id), "notifications.changed", reason=reason)


def publish_delivery_changed(business_id, delivery_public_id, reason="status", rider_user_ids=()):
    """Wake both tenant staff and the customer-safe tracking socket.

    The signal intentionally contains only the opaque delivery UUID and a reason.
    Consumers fetch the authoritative snapshot over normal HTTP before rendering.
    """
    delivery_id = str(delivery_public_id)
    payload = {"delivery_id": delivery_id, "reason": reason}
    _publish(business_notification_group(business_id), "delivery.changed", **payload)
    _publish(public_delivery_group(business_id, delivery_id), "delivery.changed", **payload)
    for user_id in {int(value) for value in rider_user_ids if value}:
        _publish(user_notification_group(business_id, user_id), "delivery.changed", **payload)
