"""Founder-console realtime signals.

Signals contain only an event id. The authorized Founder console fetches the
current server-rendered snapshot before changing the UI.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

FOUNDER_SIGNUP_GROUP = "founder.console.signups"


def publish_founder_signup_changed(event_id=None):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    try:
        async_to_sync(channel_layer.group_send)(
            FOUNDER_SIGNUP_GROUP,
            {"type": "founder.signup.changed", "event_id": int(event_id or 0)},
        )
    except Exception:
        # Realtime delivery must never affect registration.
        logger.exception("Could not publish Founder signup realtime signal")
