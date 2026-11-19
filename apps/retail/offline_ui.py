"""Prepare the real workspace templates inside the encrypted offline package."""

import base64
import mimetypes
from copy import copy
from django.http import QueryDict
from django.template.loader import render_to_string


def prepared_interface(request, packages):
    from core.views import dashboard
    from inventory.views import inventory
    from commerce.views import storefront_pos
    from commerce.delivery_views import delivery_dashboard
    from .views import expiration

    pages = {}
    views = {
        "dashboard": dashboard,
        "inventory": inventory,
        "expiration": expiration,
        "sales": storefront_pos,
        "delivery": delivery_dashboard,
    }
    for name, view in views.items():
        if not packages.get(name):
            continue
        probe = copy(request)
        probe.method = "GET"
        probe.GET = QueryDict("view=simple")
        probe.POST = QueryDict()
        probe.offline_package_capture = True
        response = view(probe)
        if response.status_code == 200:
            pages[name] = response.content.decode()
    business = request.business
    from commerce.models import StorefrontProduct

    images = {}
    total_bytes = 0
    fields = [business.storefront_logo]
    if packages.get("sales"):
        fields += [
            product.image
            for product in StorefrontProduct.raw_objects.filter(
                business=business, published=True
            ).exclude(image="")[:100]
        ]
    for field in fields:
        if not field:
            continue
        mime = mimetypes.guess_type(field.name)[0]
        if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            continue
        try:
            with field.open("rb") as stream:
                data = stream.read(512 * 1024 + 1)
            if len(data) > 512 * 1024 or total_bytes + len(data) > 8 * 1024 * 1024:
                continue
            images[field.url.split("?")[0]] = (
                "data:" + mime + ";base64," + base64.b64encode(data).decode()
            )
            total_bytes += len(data)
        except (OSError, ValueError):
            continue
    return {
        "pages": pages,
        "images": images,
        "receipt": render_to_string(
            "commerce/_receipt_document.html",
            {
                "store_business": business,
                "offline_receipt": True,
            },
            request=request,
        ),
        "navigation": render_to_string(
            "_workspace_navigation.html",
            {
                "biz": business,
                "offline_mode": True,
                "packages": packages,
                "navigation_template": "pwa/_offline_nav_links.html",
            },
            request=request,
        ),
        "tenant": {
            "id": business.pk,
            "accent": business.accent_color,
            "background": business.background_color,
            "background_contrast": business.background_text_color,
            "accent_contrast": business.button_text_color,
        },
    }
