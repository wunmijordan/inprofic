"""Keep a browser's single Web Push endpoint enabled for all eligible alerts."""

from django.db import transaction
from django.utils import timezone

from .models import CommercePushSubscription
from .webpush import endpoint_hash


@transaction.atomic
def sync_device_subscription(*, user, endpoint, p256dh, auth, user_agent=""):
    """Bind one browser endpoint to the user's eligible operational workspaces.

    Founder signup alerts and business alerts keep separate audience records,
    but both point to the same browser PushSubscription. Delivery still applies
    the existing per-business module and role permission checks.
    """
    from accounts.models import FounderPushSubscription
    from core.models import Business

    digest = endpoint_hash(endpoint)
    user_agent = (user_agent or "")[:300]

    # A PushSubscription belongs to one browser profile. If another account
    # registers it, disable every old owner's audience records first.
    CommercePushSubscription.objects.filter(endpoint_hash=digest).exclude(user=user).update(active=False)
    FounderPushSubscription.objects.filter(endpoint_hash=digest).exclude(user=user).update(active=False)

    if user.is_superuser:
        FounderPushSubscription.objects.update_or_create(
            endpoint_hash=digest,
            defaults={
                "user": user,
                "endpoint": endpoint,
                "p256dh": p256dh[:255],
                "auth": auth[:255],
                "user_agent": user_agent,
                "active": True,
                "failure_count": 0,
                "last_failure_at": None,
            },
        )
        businesses = Business.objects.all()
    else:
        # A non-founder cannot retain a platform-wide signup subscription.
        FounderPushSubscription.objects.filter(endpoint_hash=digest).update(active=False)
        businesses = Business.objects.filter(
            user_memberships__user=user,
            user_memberships__active=True,
        ).distinct()

    business_ids = set(businesses.values_list("pk", flat=True))
    existing = {
        row.business_id: row
        for row in CommercePushSubscription.objects.filter(
            user=user,
            endpoint_hash=digest,
        )
    }
    # Remove stale workspace bindings (for example, a membership that was
    # revoked after the device was first registered).
    CommercePushSubscription.objects.filter(
        user=user,
        endpoint_hash=digest,
    ).exclude(business_id__in=business_ids).update(active=False)

    to_create = []
    to_update = []
    for business_id in business_ids:
        row = existing.get(business_id)
        if row is None:
            to_create.append(CommercePushSubscription(
                business_id=business_id,
                user=user,
                endpoint=endpoint,
                endpoint_hash=digest,
                p256dh=p256dh[:255],
                auth=auth[:255],
                user_agent=user_agent,
                active=True,
                failure_count=0,
                last_failure_at=None,
            ))
            continue
        row.endpoint = endpoint
        row.p256dh = p256dh[:255]
        row.auth = auth[:255]
        row.user_agent = user_agent
        row.active = True
        row.failure_count = 0
        row.last_failure_at = None
        row.updated_at = timezone.now()
        to_update.append(row)

    if to_create:
        CommercePushSubscription.objects.bulk_create(to_create, ignore_conflicts=True, batch_size=250)
    if to_update:
        CommercePushSubscription.objects.bulk_update(
            to_update,
            ["endpoint", "p256dh", "auth", "user_agent", "active", "failure_count", "last_failure_at", "updated_at"],
            batch_size=250,
        )


@transaction.atomic
def disable_device_subscription(*, user, endpoint):
    """Disable this endpoint across founder and operational alert audiences."""
    from accounts.models import FounderPushSubscription

    digest = endpoint_hash(endpoint)
    commerce_count = CommercePushSubscription.objects.filter(
        user=user,
        endpoint_hash=digest,
    ).update(active=False)
    founder_count = FounderPushSubscription.objects.filter(
        user=user,
        endpoint_hash=digest,
    ).update(active=False)
    return commerce_count + founder_count
