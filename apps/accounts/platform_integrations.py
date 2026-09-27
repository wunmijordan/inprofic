"""Founder-controlled availability for optional external platform integrations.

The gate deliberately does not delete tenant configuration or historical data.
It controls whether an integration may be surfaced or executed right now.
"""
from __future__ import annotations

import re

from django.core.cache import cache
from django.db import OperationalError, ProgrammingError

_CACHE_KEY = "inprofic:platform-integrations:glovo-enabled:v1"
_CACHE_TTL = 3600


def glovo_platform_enabled() -> bool:
    # Rendering can call the integration-safe template filter dozens of times.
    # Reuse the first result inside the current request so production Redis is
    # not contacted once per rendered string.
    try:
        from core.context import get_request_cache
        request_cache = get_request_cache()
    except Exception:
        request_cache = None
    request_key = ("platform_integration", "glovo_enabled")
    if request_cache is not None and request_key in request_cache:
        return bool(request_cache[request_key])

    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        enabled = bool(cached)
    else:
        try:
            from .models import PlatformIntegrationSettings
            enabled = bool(PlatformIntegrationSettings.load().glovo_enabled)
        except (OperationalError, ProgrammingError):
            # Safe during first deploy before the migration has run.
            enabled = False
        cache.set(_CACHE_KEY, enabled, _CACHE_TTL)
    if request_cache is not None:
        request_cache[request_key] = enabled
    return enabled


def set_glovo_platform_enabled(enabled: bool) -> None:
    enabled = bool(enabled)
    cache.set(_CACHE_KEY, enabled, _CACHE_TTL)
    try:
        from core.context import get_request_cache
        request_cache = get_request_cache()
        if request_cache is not None:
            request_cache[("platform_integration", "glovo_enabled")] = enabled
    except Exception:
        pass


def redact_disabled_integrations(value, *, glovo_enabled=None):
    """Remove disabled provider branding from runtime/history presentation.

    Stored records remain untouched so re-enabling an integration restores their
    full provider-specific context. This function is presentation-only.

    Callers that redact several values in one request should resolve
    ``glovo_platform_enabled()`` once and pass it here. On production Redis this
    avoids turning a presentation loop into dozens of cache round trips.
    """
    if glovo_enabled is None:
        glovo_enabled = glovo_platform_enabled()
    if value in (None, "") or glovo_enabled:
        return value
    text = str(value)
    text = re.sub(r"\bGlovo(?:\s+LaaS(?:\s+v2)?)?\b", "delivery partner", text, flags=re.IGNORECASE)
    text = re.sub(r"\bglovo_laas_v2\b", "delivery_partner", text, flags=re.IGNORECASE)
    text = re.sub(r"\bglovo\b", "delivery partner", text, flags=re.IGNORECASE)
    return text
