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
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return bool(cached)
    try:
        from .models import PlatformIntegrationSettings
        enabled = bool(PlatformIntegrationSettings.load().glovo_enabled)
    except (OperationalError, ProgrammingError):
        # Safe during first deploy before the migration has run.
        enabled = False
    cache.set(_CACHE_KEY, enabled, _CACHE_TTL)
    return enabled


def set_glovo_platform_enabled(enabled: bool) -> None:
    cache.set(_CACHE_KEY, bool(enabled), _CACHE_TTL)


def redact_disabled_integrations(value):
    """Remove disabled provider branding from runtime/history presentation.

    Stored records remain untouched so re-enabling an integration restores their
    full provider-specific context. This function is presentation-only.
    """
    if value in (None, "") or glovo_platform_enabled():
        return value
    text = str(value)
    text = re.sub(r"\bGlovo(?:\s+LaaS(?:\s+v2)?)?\b", "delivery partner", text, flags=re.IGNORECASE)
    text = re.sub(r"\bglovo_laas_v2\b", "delivery_partner", text, flags=re.IGNORECASE)
    text = re.sub(r"\bglovo\b", "delivery partner", text, flags=re.IGNORECASE)
    return text
