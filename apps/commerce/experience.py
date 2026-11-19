"""Discoverable public commerce settings, shared by hosted and headless clients."""

from django.urls import reverse
from django.utils import timezone
from core.verticals import vertical_config
from .opening_hours import opening_status
from .delivery_services import public_delivery_config


def commerce_experience(
    request, business, settings, *, include_methods=False, delivery=None
):
    from .models import CommercePaymentConfiguration

    currency = (
        CommercePaymentConfiguration.raw_objects.filter(business=business)
        .values_list("currency", flat=True)
        .first()
        or "NGN"
    )
    base = request.build_absolute_uri(f"/api/v1/storefronts/{business.slug}")
    hosted = request.build_absolute_uri(reverse("storefront", args=[business.slug]))
    vertical = vertical_config(business)
    delivery = delivery if delivery is not None else public_delivery_config(business)
    delivery = {
        **delivery,
        "quote_url": base + "/delivery/quote",
        "location_url": base + "/delivery/location",
    }
    result = {
        "version": "2026-11-19.1",
        "configuration_source": "business",
        "refresh": "on_catalogue_load",
        "business": {
            "name": business.name,
            "slug": business.slug,
            "vertical": business.vertical,
            "currency": currency.upper(),
            "currency_symbol": business.currency_symbol,
            "timezone": timezone.get_current_timezone_name(),
            "logo": (
                request.build_absolute_uri(business.storefront_logo.url)
                if business.storefront_logo
                else ""
            ),
            "phone": business.contact_phone,
            "email": business.contact_email,
            "address": business.contact_address,
        },
        "theme": {
            "accent": business.accent_color,
            "background": business.background_color,
            "background_text": business.background_text_color,
            "button_text": business.button_text_color,
            "fonts": {"heading": "Fraunces", "body": "Sora", "value": "IBM Plex Mono"},
        },
        "labels": {
            "channels": vertical["commerce_channels"],
            "storefront": vertical["storefront"],
        },
        "features": {
            "promotions": True,
            "promotion_portions": True,
            "custom_promotion_help": True,
            "multiple_promotion_channels": True,
            "scheduled_product_prices": True,
            "ribbons": True,
            "merchandising": True,
            "variants": True,
            "categories": True,
            "individual_options": True,
            "bulk_packs": True,
            "fulfilment_choice": True,
            "opening_hours": bool(settings.opening_hours_enabled),
            "delivery": delivery["enabled"],
            "verified_payments": True,
            "receipts": True,
            "attribution": True,
            "customer_accounts": {"mode": "hosted"},
        },
        "opening_hours": opening_status(settings),
        "delivery": delivery,
        "checkout_fields": {
            "customer": {
                "name": {"required": True},
                "phone": {"required": True},
                "email": {"required": False},
            },
            "customer_note": {"max_length": 500},
            "service_modes": [
                {"code": code, "label": label}
                for code, label in vertical["pos"]["service_modes"]
            ],
            "table_reference": {
                "enabled": vertical["pos"]["show_reference"],
                "max_length": 40,
            },
        },
        "pricing": {
            "authority": "server",
            "strategy": "distinct_qualifying_quantities",
            "stackable": False,
            "display_price": "active_campaign_or_channel_price",
            "currency_amounts": "decimal_strings",
            "quote_before_payment": True,
            "preserve_accepted_quotation_prices": True,
            "refresh_after": [
                "items",
                "quantities",
                "channel",
                "variant",
                "bulk_pack",
                "fulfilment_source",
                "selling_portion",
            ],
        },
        "ribbons": {
            "offers": {
                "position": "top_left",
                "terms": "promotions[].help_items",
                "heading": "promotions[].help_title",
                "selection_rules": ["product_portions", "reward_portions"],
            },
            "merchandising": {"position": "top_right", "source": "merchandising_badge"},
            "icons": {
                "featured": "star",
                "new_arrival": "sparkle",
                "staff_pick": "thumbs_up",
                "bestseller": "trophy",
            },
        },
        "endpoints": {
            "experience": base + "/experience",
            "products": base + "/products",
            "basket_quote": base + "/basket-quote",
            "checkouts": base + "/checkouts",
            "checkout": base + "/checkouts/{checkout_id}",
            "payment_methods": base + "/payment-methods",
            "payment": base + "/checkouts/{checkout_id}/payments",
            "current_payment": base + "/checkouts/{checkout_id}/payments/current",
            "transfer_claim": base + "/checkouts/{checkout_id}/payments/current/claim",
            "receipt": base + "/receipts/{receipt_id}",
            "order": base + "/orders/{order_id}",
            "privacy_policy": base + "/privacy-policy",
            "delivery_quote": base + "/delivery/quote",
            "delivery_location": base + "/delivery/location",
            "delivery_tracking": base + "/deliveries/{delivery_id}/tracking",
            "delivery_messages": base + "/deliveries/{delivery_id}/messages",
            "delivery_report": base + "/deliveries/{delivery_id}/report",
        },
        "managed_experience": {
            "storefront_url": hosted if settings.hosted_storefront_enabled else None,
            "order_now_url": (
                request.build_absolute_uri(
                    reverse("storefront_order_now", args=[business.slug])
                )
                if settings.order_now_link_enabled
                else None
            ),
            "accounts": {
                name: request.build_absolute_uri(reverse(route, args=[business.slug]))
                for name, route in [
                    ("login", "storefront_customer_login"),
                    ("register", "storefront_customer_register"),
                    ("account", "storefront_customer_account"),
                ]
            },
            "purpose": "Use the hosted experience for automatic UI feature updates; custom headless UIs render this contract.",
        },
    }
    if include_methods:
        from .payment_services import eligible_payment_methods

        result["payment_methods"] = eligible_payment_methods(business)
    return result


def validate_checkout_payload(data):
    from django.core.exceptions import ValidationError

    if not isinstance(data, dict):
        raise ValidationError("Checkout details must be an object.")
    items = data.get("items")
    if not isinstance(items, list) or not 0 < len(items) <= 100:
        raise ValidationError("Choose between 1 and 100 checkout items.")
    for name in (
        "order_mode",
        "sales_channel",
        "ordering_mode",
        "external_order_id",
        "service_mode",
        "table_reference",
        "requested_delivery_at",
        "customer_note",
    ):
        if data.get(name) is not None and not isinstance(data[name], str):
            raise ValidationError(f"{name} must be text.")
    customer = data.get("customer")
    if customer is not None:
        if not isinstance(customer, dict):
            raise ValidationError("Customer details must be an object.")
        for name in ("name", "phone", "email", "address"):
            if customer.get(name) is not None and not isinstance(customer[name], str):
                raise ValidationError(f"Customer {name} must be text.")
    for item in items:
        if not isinstance(item, dict):
            raise ValidationError("Each checkout item must be an object.")
        for name in (
            "product_id",
            "bulk_pack_id",
            "individual_option_id",
            "fulfilment_source",
            "product_option_value",
        ):
            if item.get(name) is not None and not isinstance(item[name], str):
                raise ValidationError(f"Item {name} must be text.")
