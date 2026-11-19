"""Shared card metadata; checkout remains the authority for campaign pricing."""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Count, Sum
from django.utils import timezone

from .models import (
    CommerceCheckoutItem,
    CommerceCheckoutSession,
    CommercePaymentReceipt,
)

BADGES = {
    "featured": ("Featured", "Selected by this business."),
    "new_arrival": ("New arrival", "Marked as a new arrival by this business."),
    "staff_pick": ("Staff pick", "Recommended by this business's team."),
    "bestseller": (
        "Bestseller",
        "Among the top three products by paid POS, storefront and API checkout quantity in the last 30 days, across at least two orders; reversed receipts excluded.",
    ),
}


def active_promotions(business, channel=None):
    from retail.models import ProductPromotion

    today = timezone.localdate()
    campaigns = ProductPromotion.raw_objects.filter(
        business=business, active=True, starts_on__lte=today, ends_on__gte=today
    )
    return [
        campaign
        for campaign in campaigns.prefetch_related(
            "products__portion_profile",
            "products__bulk_pack_profiles",
            "products__individual_sale_options",
            "products__channel_prices",
            "reward_products__portion_profile",
            "reward_products__bulk_pack_profiles",
            "reward_products__individual_sale_options",
            "reward_products__channel_prices",
        ).order_by("pk")
        if channel is None
        or "all" in campaign.channel_codes
        or channel in campaign.channel_codes
    ]


def campaign_unit_price(campaign, product_id, channel, selection="standard"):
    from retail.promotion_portions import selected_portion

    if selected_portion(campaign, product_id) != selection:
        return None
    try:
        raw = campaign.product_prices.get(str(product_id), {}).get(channel)
        value = Decimal(str(raw)) if raw is not None else None
        return value if value is not None and value.is_finite() and value > 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def offer_applies(offer, channel):
    return "all" in offer["channels"] or channel in offer["channels"]


def promotion_metadata(campaign, business, public_ids=None):
    products = [p for p in campaign.products.all() if p.business_id == business.pk]
    rewards = [
        p for p in campaign.reward_products.all() if p.business_id == business.pk
    ] or products
    if public_ids is not None:
        products = [p for p in products if p.pk in public_ids]
        rewards = [p for p in rewards if p.pk in public_ids]
    from retail.promotion_portions import portion_choices, selected_portion

    def portion_names(goods, reward=False):
        return ", ".join(
            f"{p.name} ({portion_choices(p).get(selected_portion(campaign, p.pk, reward=reward), {}).get('label', 'Unavailable portion')})"
            for p in goods
        )

    names = portion_names(products)
    if campaign.mode == "buy_get":
        help_items = [
            {
                "name": "Buy",
                "value": str(campaign.qualifying_quantity),
                "quantity_label": f"From {names}.",
            },
            {
                "name": "Get free",
                "value": str(campaign.free_quantity),
                "quantity_label": f"From {portion_names(rewards, reward=True)}. Automatically added as a separate free reward. Any quantity you add yourself is billed normally.",
            },
        ]
    else:
        help_items = [
            {
                "name": "Bundle",
                "quantity_label": f"Mix {campaign.qualifying_quantity} from {names} for {business.currency_symbol}{campaign.bundle_price}.",
            }
        ]
    help_items += [
        {
            "name": "When",
            "quantity_label": f"{campaign.starts_on:%A, %d %b %Y} to {campaign.ends_on:%A, %d %b %Y}.",
        },
        {
            "name": "Eligible items",
            "quantity_label": campaign.eligibility_rule.strip(),
        },
        {
            "name": "Pricing",
            "quantity_label": campaign.pricing_rule.strip(),
        },
    ]
    help_items = [item for item in help_items if item["quantity_label"]]
    offer = " ".join(
        f"{item['name']} {item.get('value', '')}".strip()
        + f": {item['quantity_label']}"
        for item in help_items
    )
    public_ids = public_ids or {}
    return {
        "id": campaign.pk,
        "name": campaign.name,
        "mode": campaign.mode,
        "kind": campaign.mode,  # Legacy public/offline clients.
        "description": offer,
        "help_items": help_items,
        "help_title": campaign.name,
        "eligibility_rule": campaign.eligibility_rule,
        "pricing_rule": campaign.pricing_rule,
        "automatic_rewards": campaign.mode == "buy_get",
        "channel": (
            campaign.channel_codes[0]
            if len(campaign.channel_codes) == 1
            else "multiple"
        ),
        "channels": campaign.channel_codes,
        "unit_prices": {
            public_ids[p.pk]: campaign.product_prices.get(str(p.pk), {})
            for p in products
            if p.pk in public_ids
        },
        "starts_on": campaign.starts_on.isoformat(),
        "ends_on": campaign.ends_on.isoformat(),
        "qualifying_quantity": campaign.qualifying_quantity,
        "free_quantity": campaign.free_quantity if campaign.mode == "buy_get" else None,
        "bundle_price": (
            str(campaign.bundle_price) if campaign.bundle_price is not None else None
        ),
        "product_ids": [public_ids[p.pk] for p in products if p.pk in public_ids],
        "reward_product_ids": [public_ids[p.pk] for p in rewards if p.pk in public_ids],
        "selection": "registered_portions",
        "product_portions": {
            public_ids[p.pk]: selected_portion(campaign, p.pk)
            for p in products
            if p.pk in public_ids
        },
        "reward_portions": {
            public_ids[p.pk]: selected_portion(campaign, p.pk, reward=True)
            for p in rewards
            if p.pk in public_ids
        },
        "reward_quantity": "whole_units",
        "price_quantity": "fractional_allowed",
        "stackable": False,
        "combination_scope": "distinct_qualifying_quantities",
    }


def attach_merchandising(business, products, channel=None):
    """One batched campaign lookup and one ranking query for every surface."""
    public_ids = {p.finished_good_id: str(p.public_id) for p in products}
    by_good = {pk: [] for pk in public_ids}
    campaigns = active_promotions(business, channel)
    for campaign in campaigns:
        payload = promotion_metadata(campaign, business, public_ids)
        if not payload["product_ids"] or (
            campaign.mode == "buy_get" and not payload["reward_product_ids"]
        ):
            continue
        involved = {*campaign.products.all(), *campaign.reward_products.all()}
        for good in involved:
            if good.pk in by_good and good.business_id == business.pk:
                by_good[good.pk].append(payload)
    paid_intakes = CommercePaymentReceipt.raw_objects.filter(
        business=business,
        reversed_at__isnull=True,
        payment__intake_id__isnull=False,
    ).values("payment__intake_id")
    reversed_intakes = CommercePaymentReceipt.raw_objects.filter(
        business=business,
        reversed_at__isnull=False,
        payment__intake_id__isnull=False,
    ).values("payment__intake_id")
    ranked = (
        CommerceCheckoutItem.objects.filter(
            checkout__business=business,
            finished_good__business=business,
            checkout__materialized_intake_id__in=paid_intakes,
            checkout__status=CommerceCheckoutSession.STATUS_MATERIALIZED,
            checkout__created_at__gte=timezone.now() - timedelta(days=30),
        )
        .exclude(checkout__materialized_intake_id__in=reversed_intakes)
        .values("finished_good_id")
        .annotate(
            sold=Sum("payable_quantity"), orders=Count("checkout_id", distinct=True)
        )
        .filter(orders__gte=2)
        .order_by("-sold", "finished_good_id")[:3]
    )
    bestsellers = {row["finished_good_id"] for row in ranked}
    for product in products:
        product.card_promotions = by_good[product.finished_good_id]
        from retail.promotion_portions import portion_choices

        choices = portion_choices(product.finished_good)
        product._promotion_selection_prices = {}
        product.card_offer_groups = []
        for selection in choices:
            for code in (
                [channel] if channel else ["online", "physical_store", "distribution"]
            ):
                if code not in choices[selection]["prices"]:
                    continue
                configured = [
                    campaign_unit_price(c, product.finished_good_id, code, selection)
                    for c in campaigns
                    if ("all" in c.channel_codes or code in c.channel_codes)
                    and any(g.pk == product.finished_good_id for g in c.products.all())
                ]
                configured = [price for price in configured if price is not None]
                if configured:
                    product._promotion_selection_prices[(selection, code)] = min(
                        configured
                    )
        for code in (
            [channel] if channel else ["online", "physical_store", "distribution"]
        ):
            offers = [
                offer for offer in product.card_promotions if offer_applies(offer, code)
            ]
            if offers:
                product.card_offer_groups.append(
                    {
                        "channel": code,
                        "title": (
                            offers[0]["help_title"]
                            if len(offers) == 1
                            else f"{len(offers)} offers"
                        ),
                        "count": len(offers),
                        "items": offers[0]["help_items"] if len(offers) == 1 else [],
                        "sections": (
                            [
                                {
                                    "title": offer["help_title"],
                                    "items": offer["help_items"],
                                }
                                for offer in offers
                            ]
                            if len(offers) > 1
                            else []
                        ),
                    }
                )
        product._promotion_prices = {
            code: price
            for (selection, code), price in product._promotion_selection_prices.items()
            if selection == "standard"
        }
        for pack in product.bulk_pack_options:
            pack.promotion_online_price = selection_price(
                product, "online", pack.profile_key, pack.price
            )
            pack.promotion_distribution_price = selection_price(
                product, "distribution", pack.profile_key, pack.price
            )
        for option in product.individual_sale_options:
            for code in ("online", "physical_store", "distribution"):
                setattr(
                    option,
                    f"promotion_{code}_price",
                    selection_price(
                        product, code, option.profile_key, option.price_for(code)
                    ),
                )
        code = product.merchandising_badge or (
            "bestseller" if product.finished_good_id in bestsellers else ""
        )
        label, description = BADGES.get(code, ("", ""))
        product.card_badge = (
            {"code": code, "label": label, "description": description}
            if label
            else None
        )
    return products


def selection_price(product, channel, selection, normal_price):
    return getattr(product, "_promotion_selection_prices", {}).get(
        (selection, channel), normal_price
    )


def basket_quote(business, data):
    """Exercise checkout validation/pricing inside a savepoint, retaining no records."""
    from uuid import uuid4
    from django.core.exceptions import ValidationError
    from django.db import transaction
    from .models import StorefrontProduct, CommerceIntake
    from .checkout_services import create_checkout, serialize_checkout

    from .experience import validate_checkout_payload

    validate_checkout_payload(data)
    rows = data["items"]
    products = {
        str(p.public_id): p
        for p in StorefrontProduct.raw_objects.filter(business=business, published=True)
        .select_related("finished_good__business", "finished_good__portion_profile")
        .prefetch_related(
            "finished_good__channel_prices",
            "finished_good__bulk_pack_profiles",
            "finished_good__individual_sale_options",
            "finished_good__composition_items__component_finished_good",
            "finished_good__composition_items__component_raw_material",
        )
    }
    items = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("product_id")) not in products:
            raise ValidationError("Unknown or unpublished product.")
        items.append(
            {
                "storefront_product": products[str(row["product_id"])],
                "quantity": row.get("quantity"),
                "bulk_pack_id": row.get("bulk_pack_id") or None,
                "individual_option_id": row.get("individual_option_id") or None,
                "fulfilment_source": row.get("fulfilment_source") or None,
                "product_option_value": row.get("product_option_value") or "",
            }
        )
    with transaction.atomic():
        checkout, _ = create_checkout(
            business=business,
            source=CommerceIntake.SOURCE_STOREFRONT,
            customer={"name": "Basket preview"},
            items=items,
            order_mode=data.get("order_mode") or "online",
            idempotency_key=f"preview-{uuid4().hex}",
        )
        result = {
            "subtotal": str(checkout.amount),
            "currency": checkout.currency,
            "promotion": checkout.promotion_snapshot,
            "items": serialize_checkout(checkout)["items"],
        }
        transaction.set_rollback(True)
    return result
