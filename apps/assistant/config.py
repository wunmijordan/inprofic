"""Layered availability for the staff assistant.

    environment kill switch  ->  founder console (platform)  ->  business settings  ->  member permissions

A feature is available only if every layer allows it. Settings are cached briefly and
invalidated on save so a founder's kill switch takes effect immediately in this process
and within ``TTL`` seconds elsewhere. Before the migration has run everything reads as
its default rather than erroring.
"""
from dataclasses import dataclass, field

from django.conf import settings
from django.core.cache import cache
from django.db import OperationalError, ProgrammingError

TTL = 30
_PLATFORM_KEY = "assistant:platform:v1"
_TENANT_KEY = "assistant:tenant:{}:v1"


@dataclass(frozen=True)
class PlatformConfig:
    assistant_enabled: bool = True
    quick_facts_enabled: bool = True
    free_text_enabled: bool = True
    open_to_all: bool = False
    disabled_facts: frozenset = field(default_factory=frozenset)


@dataclass(frozen=True)
class TenantConfig:
    enabled: bool = True
    free_text_enabled: bool = True
    disabled_facts: frozenset = field(default_factory=frozenset)


def platform_config():
    cached = cache.get(_PLATFORM_KEY)
    if cached is not None:
        return cached
    try:
        from .models import AssistantPlatformSettings
        row = AssistantPlatformSettings.load()
        config = PlatformConfig(row.assistant_enabled, row.quick_facts_enabled, row.free_text_enabled,
                                row.open_to_all_businesses, frozenset(row.disabled_quick_facts or []))
    except (OperationalError, ProgrammingError):
        return PlatformConfig()
    cache.set(_PLATFORM_KEY, config, TTL)
    return config


def tenant_config(business):
    key = _TENANT_KEY.format(business.pk)
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        from .models import AssistantBusinessSettings
        row = AssistantBusinessSettings.raw_objects.filter(business=business).first()
    except (OperationalError, ProgrammingError):
        return TenantConfig()
    config = TenantConfig() if row is None else TenantConfig(row.enabled, row.free_text_enabled, frozenset(row.disabled_quick_facts or []))
    cache.set(key, config, TTL)
    return config


def invalidate(business=None):
    cache.delete(_PLATFORM_KEY)
    if business is not None:
        cache.delete(_TENANT_KEY.format(business.pk))


def platform_allows(business):
    """Environment switch, founder master switch, and pilot/open-access rule."""
    if business is None or not settings.ASSISTANT_ENABLED:
        return False
    platform = platform_config()
    return platform.assistant_enabled and (platform.open_to_all or bool(business.assistant_enabled))


def tenant_allows(business):
    return platform_allows(business) and tenant_config(business).enabled


def quick_facts_allowed(business):
    return tenant_allows(business) and platform_config().quick_facts_enabled


def fact_allowed(business, key):
    platform, tenant = platform_config(), tenant_config(business)
    return quick_facts_allowed(business) and key not in platform.disabled_facts and key not in tenant.disabled_facts


def free_text_allowed(business):
    """Platform and business both permit model answers (a provider key is a separate question)."""
    return tenant_allows(business) and platform_config().free_text_enabled and tenant_config(business).free_text_enabled
