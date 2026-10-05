"""Structured data and crawl rules for the INPROFIC marketing page (Nigeria-focused)."""
import json
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponsePermanentRedirect

SITE_NAME = "INPROFIC"
# Other ways people type the brand; Google reads WebSite.alternateName for the site name in results.
ALTERNATE_NAMES = ["Inprofic", "inprofic.com.ng", "INPROFIC Nigeria"]
LOCALE = "en-NG"

# What the product covers; mirrored in the visible "About" section of the home page.
FEATURES = ["Inventory management", "Production management", "Staff payroll", "Orders and sales"]

APP_PREFIXES_DISALLOWED = (
    "/admin/", "/users/", "/dashboard/", "/inventory/", "/procurement/", "/sales/", "/expenses/",
    "/orders/", "/runs/", "/batches/", "/commerce/", "/finance/", "/reports/", "/audit/", "/search/",
    "/business/", "/delivery/", "/attribution/", "/api/", "/ws/", "/pwa/", "/ops/",
)
# Login redirects (/accounts/login/?next=/inventory/ ...) are one URL per app page: pure crawl noise.
DISALLOWED_PATTERNS = ("/*?next=",)


def site_base(request):
    """Public origin used in canonical links, sitemap and structured data."""
    configured = getattr(settings, "SITE_URL", "")
    if configured and urlsplit(configured).scheme and urlsplit(configured).netloc:
        return configured
    return f"{request.scheme}://{request.get_host()}"


def absolute_url(request, path):
    return f"{site_base(request)}{path}"


def canonical_host_redirect(request):
    """301 anonymous GET/HEAD visitors on a non-canonical host to the canonical origin.

    Returns None when no redirect applies (no SITE_URL, already canonical, signed-in
    users whose session cookie belongs to this host, or non-GET methods)."""
    configured = getattr(settings, "SITE_URL", "")
    if not configured or request.method not in ("GET", "HEAD"):
        return None
    if getattr(request.user, "is_authenticated", False):
        return None
    target = urlsplit(configured)
    if not target.scheme or not target.netloc:
        return None
    if request.get_host().lower() == target.netloc.lower():
        return None
    return HttpResponsePermanentRedirect(f"{configured}{request.get_full_path()}")


def _dump(data):
    # "</" would let a value close the script tag early.
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def organization_graph(site_url, plans=None, logo_url=None):
    """Organization + WebSite + SoftwareApplication for the home page."""
    offers = []
    for plan in plans or []:
        price = getattr(plan, "monthly_price", None)
        if price is not None:
            offers.append({"@type": "Offer", "name": plan.name, "price": f"{price:.2f}", "priceCurrency": "NGN"})
    app = {
        "@type": "SoftwareApplication", "@id": f"{site_url}#software", "name": SITE_NAME,
        "applicationCategory": "BusinessApplication", "operatingSystem": "Web, Android, iOS (installable web app)",
        "url": site_url, "inLanguage": LOCALE, "areaServed": {"@type": "Country", "name": "Nigeria"},
        "description": "Inventory, production, staff payroll, orders and sales software for Nigerian businesses.",
        "featureList": FEATURES,
        "publisher": {"@id": f"{site_url}#org"},
    }
    if offers:
        app["offers"] = offers
    organization = {"@type": "Organization", "@id": f"{site_url}#org", "name": SITE_NAME, "alternateName": ALTERNATE_NAMES, "url": site_url, "areaServed": {"@type": "Country", "name": "Nigeria"}}
    if logo_url:
        organization["logo"] = {"@type": "ImageObject", "url": logo_url}
    return _dump({"@context": "https://schema.org", "@graph": [
        organization,
        {"@type": "WebSite", "@id": f"{site_url}#website", "url": site_url, "name": SITE_NAME, "alternateName": ALTERNATE_NAMES, "inLanguage": LOCALE, "publisher": {"@id": f"{site_url}#org"}},
        app,
    ]})
