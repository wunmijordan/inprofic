from __future__ import annotations

from urllib.parse import urlparse

KNOWN_SOURCES = {
    "wa.me": "whatsapp",
    "whatsapp.com": "whatsapp",
    "instagram.com": "instagram",
    "l.instagram.com": "instagram",
    "facebook.com": "facebook",
    "m.facebook.com": "facebook",
    "l.facebook.com": "facebook",
    "tiktok.com": "tiktok",
    "youtube.com": "youtube",
    "google.com": "google",
    "linkedin.com": "linkedin",
    "x.com": "x",
    "twitter.com": "x",
}


def _clean(value, max_length):
    return str(value or "").strip()[:max_length]


def _referrer_source(referrer):
    try:
        host = (urlparse(referrer or "").hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""
    if not host:
        return ""
    for domain, source in KNOWN_SOURCES.items():
        if host == domain or host.endswith(f".{domain}"):
            return source
    return host[:80]


def normalize_attribution(payload=None, *, referrer=""):
    """Return a bounded, platform-neutral attribution snapshot.

    Accept both short names (source/campaign) and conventional UTM names.  The
    Commerce internal `source` field is intentionally separate: that records
    the transport (hosted storefront/API/connector), while this snapshot records
    where the customer came from.
    """
    payload = payload or {}
    if hasattr(payload, "get"):
        getter = payload.get
    else:
        getter = lambda _key, default="": default

    source = _clean(getter("source") or getter("utm_source"), 80).lower()
    medium = _clean(getter("medium") or getter("utm_medium"), 80).lower()
    campaign = _clean(getter("campaign") or getter("utm_campaign"), 120)
    content = _clean(getter("content") or getter("utm_content"), 120)
    term = _clean(getter("term") or getter("utm_term"), 120)
    safe_referrer = _clean(getter("referrer") or referrer, 500)

    inferred = False
    if not source:
        source = _referrer_source(safe_referrer)
        inferred = bool(source)
    if not source:
        source = "direct"
    if not medium and safe_referrer:
        medium = "referral"

    return {
        "source": source,
        "medium": medium,
        "campaign": campaign,
        "content": content,
        "term": term,
        "referrer": safe_referrer,
        "inferred": inferred,
    }


def attribution_model_kwargs(attribution):
    attribution = attribution or {}
    return {
        "attribution_source": _clean(attribution.get("source"), 80) or "direct",
        "attribution_medium": _clean(attribution.get("medium"), 80),
        "attribution_campaign": _clean(attribution.get("campaign"), 120),
        "attribution_content": _clean(attribution.get("content"), 120),
        "attribution_term": _clean(attribution.get("term"), 120),
        "attribution_referrer": _clean(attribution.get("referrer"), 500),
    }
