"""Brand assets that can be rendered by remote HTML email clients."""

from urllib.parse import urljoin

from django.conf import settings
from django.templatetags.static import static


def email_font_urls():
    """Return absolute URLs for the app's brand fonts when a public host exists.

    Web fonts are progressive enhancement: many email clients block them, so
    templates also provide system-font fallbacks.
    """
    site_url = (getattr(settings, "SITE_URL", "") or "").strip().rstrip("/")
    if not site_url:
        render_host = (getattr(settings, "RENDER_EXTERNAL_HOSTNAME", "") or "").strip()
        if render_host:
            site_url = f"https://{render_host}"
    if not site_url:
        return {"brand_sora_font_url": "", "brand_fraunces_font_url": ""}

    def absolute_static(path):
        asset_url = static(path)
        return asset_url if asset_url.startswith(("https://", "http://")) else urljoin(f"{site_url}/", asset_url.lstrip("/"))

    return {
        "brand_sora_font_url": absolute_static("core/fonts/Sora-Variable.woff2"),
        "brand_fraunces_font_url": absolute_static("core/fonts/Fraunces-SemiBold.woff2"),
    }
