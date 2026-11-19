"""Server-owned reward candidates; customer basket rows always remain paid rows."""

from decimal import Decimal
from commerce.models import StorefrontProduct
from .promotion_portions import portion_choices, selected_portion


def reward_candidates(business, channel, campaigns):
    goods = {
        g.pk
        for campaign in campaigns
        if campaign.mode == "buy_get"
        for g in (list(campaign.reward_products.all()) or list(campaign.products.all()))
        if g.business_id == business.pk
    }
    products = {
        p.finished_good_id: p
        for p in StorefrontProduct.raw_objects.filter(
            business=business, published=True, finished_good_id__in=goods
        )
        .select_related("finished_good__portion_profile", "finished_good__business")
        .prefetch_related(
            "finished_good__channel_prices",
            "finished_good__bulk_pack_profiles",
            "finished_good__individual_sale_options",
        )
    }
    result = {}
    for campaign in campaigns:
        if campaign.mode != "buy_get":
            continue
        options = []
        for good in list(campaign.reward_products.all()) or list(
            campaign.products.all()
        ):
            product = products.get(good.pk)
            if product is None:
                continue
            selection = selected_portion(campaign, good.pk, reward=True)
            choice = portion_choices(product.finished_good).get(selection, {})
            if channel not in choice.get("prices", {}):
                continue
            row = {"storefront_product": product}
            if selection.startswith("bulk:"):
                row["bulk_pack_id"] = selection.split(":", 1)[1]
            elif selection.startswith("individual:"):
                row["individual_option_id"] = selection.split(":", 1)[1]
            choices = product.finished_good.storefront_option_choices
            if choices:
                row["product_option_value"] = choices[0]
            options.append((Decimal(choice["prices"][channel]), good.pk, row))
        result[campaign.pk] = [
            row
            for _, _, row in sorted(options, key=lambda option: (option[0], option[1]))
        ]
    return result
