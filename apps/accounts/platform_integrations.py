"""Founder-controlled availability for optional external platform integrations.

The gate deliberately does not delete tenant configuration or historical data.
It controls whether an integration may be surfaced or executed right now.
"""
from __future__ import annotations

import re

from django.core.cache import cache
from django.db import OperationalError, ProgrammingError

_CACHE_KEY = "inprofic:platform-integrations:shipbubble-enabled:v1"
_WHATSAPP_CACHE_KEY = "inprofic:platform-integrations:whatsapp-storefront-enabled:v1"
_CACHE_TTL = 3600


def shipbubble_platform_enabled() -> bool:
    # Rendering can call the integration-safe template filter dozens of times.
    # Reuse the first result inside the current request so production Redis is
    # not contacted once per rendered string.
    try:
        from core.context import get_request_cache
        request_cache = get_request_cache()
    except Exception:
        request_cache = None
    request_key = ("platform_integration", "shipbubble_enabled")
    if request_cache is not None and request_key in request_cache:
        return bool(request_cache[request_key])

    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        enabled = bool(cached)
    else:
        try:
            from .models import PlatformIntegrationSettings
            enabled = bool(PlatformIntegrationSettings.load().shipbubble_enabled)
        except (OperationalError, ProgrammingError):
            # Safe during first deploy before the migration has run.
            enabled = False
        cache.set(_CACHE_KEY, enabled, _CACHE_TTL)
    if request_cache is not None:
        request_cache[request_key] = enabled
    return enabled


def set_shipbubble_platform_enabled(enabled: bool) -> None:
    enabled = bool(enabled)
    cache.set(_CACHE_KEY, enabled, _CACHE_TTL)
    try:
        from core.context import get_request_cache
        request_cache = get_request_cache()
        if request_cache is not None:
            request_cache[("platform_integration", "shipbubble_enabled")] = enabled
    except Exception:
        pass


def whatsapp_storefront_platform_enabled() -> bool:
    """Return founder-controlled WhatsApp availability, cached per request."""
    try:
        from core.context import get_request_cache
        request_cache = get_request_cache()
    except Exception:
        request_cache = None
    request_key = ("platform_integration", "whatsapp_storefront_enabled")
    if request_cache is not None and request_key in request_cache:
        return bool(request_cache[request_key])

    cached = cache.get(_WHATSAPP_CACHE_KEY)
    if cached is not None:
        enabled = bool(cached)
    else:
        try:
            from .models import PlatformIntegrationSettings
            enabled = bool(PlatformIntegrationSettings.load().whatsapp_storefront_enabled)
        except (OperationalError, ProgrammingError):
            enabled = False
        cache.set(_WHATSAPP_CACHE_KEY, enabled, _CACHE_TTL)
    if request_cache is not None:
        request_cache[request_key] = enabled
    return enabled


def set_whatsapp_storefront_platform_enabled(enabled: bool) -> None:
    enabled = bool(enabled)
    cache.set(_WHATSAPP_CACHE_KEY, enabled, _CACHE_TTL)
    try:
        from core.context import get_request_cache
        request_cache = get_request_cache()
        if request_cache is not None:
            request_cache[("platform_integration", "whatsapp_storefront_enabled")] = enabled
    except Exception:
        pass


def redact_disabled_integrations(value, *, shipbubble_enabled=None):
    """Remove disabled provider branding from runtime/history presentation.

    Stored records remain untouched so re-enabling an integration restores their
    full provider-specific context. This function is presentation-only.

    Callers that redact several values in one request should resolve
    ``shipbubble_platform_enabled()`` once and pass it here. On production Redis this
    avoids turning a presentation loop into dozens of cache round trips.
    """
    if shipbubble_enabled is None:
        shipbubble_enabled = shipbubble_platform_enabled()
    if value in (None, "") or shipbubble_enabled:
        return value
    text = str(value)
    text = re.sub(r"\bShipbubble(?:\s+couriers)?\b", "delivery partner", text, flags=re.IGNORECASE)
    text = re.sub(r"\bshipbubble\b", "delivery partner", text, flags=re.IGNORECASE)
    return text
