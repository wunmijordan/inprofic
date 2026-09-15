import logging

from django.db import IntegrityError, transaction

from .models import CommerceNotification, CommerceSettings
from .realtime import publish_business_notifications_changed, publish_user_notifications_changed
from .webpush import schedule_push_dispatch

logger = logging.getLogger(__name__)


def notify_commerce(*, business, event_type, title, message="", target_url="/commerce/", dedupe_key="", recipient_user=None):
    """Persist tenant activity without allowing alerts to interrupt operations.

    ``recipient_user`` turns the alert into a tenant-scoped direct notification.
    Business-wide notices remain available to users whose workspace permission
    matches the notification category.
    """

    settings = CommerceSettings.raw_objects.filter(business=business).first()
    if settings and not settings.notifications_enabled:
        return None
    if settings and event_type in CommerceNotification.ORDER_EVENTS and not settings.notify_order_activity:
        return None
    if settings and event_type in CommerceNotification.PAYMENT_EVENTS and not settings.notify_payment_activity:
        return None
    if settings and event_type in CommerceNotification.DELIVERY_EVENTS and not settings.notify_delivery_activity:
        return None
    if recipient_user is not None:
        # A direct alert may only target an active member of this tenant.
        if not recipient_user.business_memberships.filter(business=business, active=True).exists():
            return None

    values = {
        "business": business,
        "recipient_user": recipient_user,
        "event_type": event_type,
        "title": str(title)[:160],
        "message": str(message)[:500],
        "target_url": str(target_url or "/commerce/")[:500],
        "dedupe_key": str(dedupe_key or "")[:180],
    }
    try:
        if values["dedupe_key"]:
            lookup = {
                "business": business,
                "recipient_user": recipient_user,
                "dedupe_key": values["dedupe_key"],
            }
            notice, created = CommerceNotification.raw_objects.get_or_create(
                **lookup,
                defaults={
                    key: value
                    for key, value in values.items()
                    if key not in {"business", "recipient_user", "dedupe_key"}
                },
            )
            if created:
                transaction.on_commit(
                    lambda: (
                        publish_user_notifications_changed(business.pk, recipient_user.pk)
                        if recipient_user
                        else publish_business_notifications_changed(business.pk)
                    )
                )
                schedule_push_dispatch()
            return notice
        notice = CommerceNotification.raw_objects.create(**values)
        transaction.on_commit(
            lambda: (
                publish_user_notifications_changed(business.pk, recipient_user.pk)
                if recipient_user
                else publish_business_notifications_changed(business.pk)
            )
        )
        schedule_push_dispatch()
        return notice
    except IntegrityError:
        return CommerceNotification.raw_objects.filter(
            business=business,
            recipient_user=recipient_user,
            dedupe_key=values["dedupe_key"],
        ).first()


def queue_commerce_notification(**kwargs):
    """Create after the business transaction commits; notification failure is non-fatal."""

    def create_notice():
        try:
            notify_commerce(**kwargs)
        except Exception:
            logger.exception("Could not create commerce/delivery activity notification")

    transaction.on_commit(create_notice)
