"""Registered selling selections shared by campaign forms, cards and checkout."""

from inventory.portioning import (
    active_bulk_packs,
    active_individual_options,
    customer_unit,
)

CHANNELS = ("physical_store", "online", "distribution")


def portion_choices(good):
    choices = {
        "standard": {
            "label": f"Standard · {customer_unit(good)}",
            "prices": {code: str(good.selling_price_for(code)) for code in CHANNELS},
        }
    }
    for pack in active_bulk_packs(good):
        if pack.business_id == good.business_id:
            choices[pack.profile_key] = {
                "label": pack.name,
                "prices": {
                    code: str(pack.price) for code in ("online", "distribution")
                },
            }
    for option in active_individual_options(good):
        if option.business_id == good.business_id:
            choices[option.profile_key] = {
                "label": f"{option.name} · {option.customer_unit or good.unit}",
                "prices": {
                    code: str(option.price_for(code))
                    for code in CHANNELS
                    if option.channel_enabled(code)
                    and option.price_for(code) is not None
                },
            }
    return choices


def selected_portion(campaign, good_id, *, reward=False):
    selections = campaign.reward_portions if reward else campaign.product_portions
    return selections.get(str(good_id), "standard")


def row_portion(row):
    option = row[7] or row[8]
    return option.profile_key if option is not None else "standard"


def matches_portion(campaign, row, *, reward=False):
    return selected_portion(campaign, row[1].pk, reward=reward) == row_portion(row)
