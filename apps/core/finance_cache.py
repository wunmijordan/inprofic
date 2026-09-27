"""Small, mutation-aware cache helpers for the tenant finance alert feed."""

from django.core.cache import cache


FINANCE_ALERT_CACHE_VERSION = "v1"
FINANCE_ALERT_CACHE_TIMEOUT = 60


def finance_alert_cache_key(business_id):
    return f"finance:alerts:{FINANCE_ALERT_CACHE_VERSION}:{int(business_id)}"


def invalidate_finance_alert_cache(business_id):
    if business_id:
        cache.delete(finance_alert_cache_key(business_id))
