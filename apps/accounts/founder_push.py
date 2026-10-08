"""Send platform signup alerts to subscribed Founder devices."""

from __future__ import annotations

import json
import logging
import threading

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

logger = logging.getLogger(__name__)


def kick_founder_signup_push(event_id):
    """Start best-effort delivery after signup without delaying registration."""
    if not (
        getattr(settings, "WEB_PUSH_VAPID_PRIVATE_KEY", "")
        and getattr(settings, "WEB_PUSH_VAPID_PUBLIC_KEY", "")
        and getattr(settings, "WEB_PUSH_VAPID_SUBJECT", "")
    ):
        return
    thread = threading.Thread(
        target=_send_founder_signup_push,
        args=(int(event_id),),
        name="founder-signup-push",
        daemon=True,
    )
    thread.start()


def _send_founder_signup_push(event_id):
    close_old_connections()
    try:
        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            logger.error("Founder Web Push is configured but pywebpush is not installed")
            return

        from .models import FounderPushSubscription, PlatformEvent

        event = PlatformEvent.objects.filter(
            pk=event_id, event_type=PlatformEvent.EVENT_REGISTRATION,
        ).select_related("business").first()
        if event is None:
            return
        metadata = event.metadata or {}
        business = (metadata.get("business_name") or getattr(event.business, "name", "") or "A new business").strip()
        service = (metadata.get("vertical") or getattr(event.business, "vertical", "") or "").strip()
        body = business + (f" · {service}" if service else "")
        payload = json.dumps({
            "type": "founder.signup",
            "id": str(event.pk),
            "title": "New INPROFIC business signup",
            "body": body,
            "url": "/users/founder/subscriptions/?workspace=management#platform-management",
            "icon": "/static/core/pwa/icon-mark-192.png",
            "icon_light": "/static/core/pwa/icon-mark-192.png",
            "icon_dark": "/static/core/pwa/icon-mark-on-dark-192.png",
            "badge": "/static/core/pwa/icon-mark-monochrome-192.png",
        }, separators=(",", ":"))
        subscriptions = FounderPushSubscription.objects.filter(
            active=True, user__is_superuser=True,
        )
        now = timezone.now()
        for subscription in subscriptions.iterator(chunk_size=100):
            try:
                webpush(
                    subscription_info={
                        "endpoint": subscription.endpoint,
                        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                    },
                    data=payload,
                    vapid_private_key=settings.WEB_PUSH_VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": settings.WEB_PUSH_VAPID_SUBJECT},
                    timeout=float(getattr(settings, "WEB_PUSH_TIMEOUT_SECONDS", 5)),
                    ttl=300,
                )
            except WebPushException as exc:
                subscription.last_failure_at = now
                subscription.failure_count = min(32767, subscription.failure_count + 1)
                if getattr(getattr(exc, "response", None), "status_code", None) in {404, 410}:
                    subscription.active = False
                subscription.save(update_fields=["active", "failure_count", "last_failure_at", "updated_at"])
                logger.warning("Founder signup Web Push failed for a device (HTTP %s)", getattr(getattr(exc, "response", None), "status_code", "unknown"))
            except Exception as exc:
                subscription.last_failure_at = now
                subscription.failure_count = min(32767, subscription.failure_count + 1)
                subscription.save(update_fields=["failure_count", "last_failure_at", "updated_at"])
                logger.warning("Founder signup Web Push failed for a device (%s)", type(exc).__name__)
            else:
                subscription.last_success_at = now
                subscription.failure_count = 0
                subscription.save(update_fields=["failure_count", "last_success_at", "updated_at"])
    except Exception:
        logger.exception("Founder signup Web Push dispatch failed")
    finally:
        close_old_connections()
