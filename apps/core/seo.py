"""Structured data and crawl rules for the INPROFIC marketing page (Nigeria-focused)."""
import json

SITE_NAME = "INPROFIC"
LOCALE = "en-NG"

# What the product covers; mirrored in the visible "About" section of the home page.
FEATURES = ["Inventory management", "Production management", "Staff payroll", "Orders and sales"]

APP_PREFIXES_DISALLOWED = (
    "/admin/", "/users/", "/dashboard/", "/inventory/", "/procurement/", "/sales/", "/expenses/",
    "/orders/", "/runs/", "/batches/", "/commerce/", "/finance/", "/reports/", "/audit/", "/search/",
    "/business/", "/delivery/", "/attribution/", "/api/", "/ws/", "/pwa/", "/ops/",
)


def _dump(data):
    # "</" would let a value close the script tag early.
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def organization_graph(site_url, plans=None):
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
    return _dump({"@context": "https://schema.org", "@graph": [
        {"@type": "Organization", "@id": f"{site_url}#org", "name": SITE_NAME, "url": site_url, "areaServed": {"@type": "Country", "name": "Nigeria"}},
        {"@type": "WebSite", "@id": f"{site_url}#website", "url": site_url, "name": SITE_NAME, "inLanguage": LOCALE, "publisher": {"@id": f"{site_url}#org"}},
        app,
    ]})
