from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from .analytics import record_platform_event
from .models import PlatformEvent
from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_save, post_delete
from uuid import uuid4


def invalidate_marketing(sender, **kwargs):
    def bump():
        cache.set("marketing:revision", uuid4().hex, timeout=None)
    # Invalidate readers now and once committed, so neither transaction-local
    # previews nor another worker can retain a pre-commit page.
    bump()
    transaction.on_commit(bump)


for label in ("SubscriptionPlan", "SubscriptionPlanModule", "SubscriptionPolicySettings", "SubscriptionPromotion",
              "MarketingPromoCampaign", "MarketingTrustSettings", "MarketingTrustLogo", "PayrollAddonTier", "PayrollStaffBatch"):
    post_save.connect(invalidate_marketing, sender=f"accounts.{label}", dispatch_uid=f"marketing-save-{label}")
    post_delete.connect(invalidate_marketing, sender=f"accounts.{label}", dispatch_uid=f"marketing-delete-{label}")


@receiver(user_logged_in)
def capture_login(sender, request, user, **kwargs):
    record_platform_event(PlatformEvent.EVENT_LOGIN, request=request, user=user)


@receiver(user_logged_out)
def capture_logout(sender, request, user, **kwargs):
    if user is not None:
        record_platform_event(PlatformEvent.EVENT_LOGOUT, request=request, user=user)
