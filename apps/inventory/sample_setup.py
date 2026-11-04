"""Starter recipes so a new producer sees a working recipe, with ratios, on day one.

Everything is created with zero stock and a "(Sample)" suffix, so it cannot
distort real figures and is easy to recognise and delete. It is only offered to
production-style businesses that have no recipes yet.
"""
from decimal import Decimal

from django.db import transaction

from .models import FinishedGood, RawMaterial, RecipeItem

SUFFIX = " (Sample)"

# product: (name, unit, units_per_batch, price, base material key)
# materials: key -> (name, usage unit, cost per usage unit); recipe: key -> quantity per batch
SAMPLES = {
    "bakery": {
        "product": ("Bread", "loaf", "100", "500", "flour"),
        "materials": {"flour": ("Flour", "kg", "1.50"), "sugar": ("Sugar", "kg", "1.80"),
                      "salt": ("Salt", "g", "0.002"), "yeast": ("Yeast", "g", "0.02")},
        "recipe": {"flour": "20", "sugar": "1", "salt": "240", "yeast": "200"},
    },
    "restaurant": {
        "product": ("Jollof rice", "portion", "40", "1500", "rice"),
        "materials": {"rice": ("Rice", "kg", "1.40"), "tomato": ("Tomato paste", "kg", "2.50"),
                      "oil": ("Vegetable oil", "l", "3.00"), "pepper": ("Pepper mix", "kg", "4.00")},
        "recipe": {"rice": "10", "tomato": "1.5", "oil": "1", "pepper": "1"},
    },
    "general": {
        "product": ("Liquid soap", "bottle", "20", "800", "base"),
        "materials": {"base": ("Soap base", "l", "2.00"), "scent": ("Fragrance", "ml", "0.05"),
                      "colour": ("Colourant", "ml", "0.03")},
        "recipe": {"base": "10", "scent": "50", "colour": "20"},
    },
}


def available_for(business):
    """The sample for this business, or None when it does not apply."""
    if not business.uses_production or business.vertical not in SAMPLES:
        return None
    if RecipeItem.objects.filter(finished_good__business=business).exists():
        return None
    if RawMaterial.objects.filter(business=business, name__endswith=SUFFIX).exists():
        return None
    return SAMPLES[business.vertical]


@transaction.atomic
def load_sample(business, user=None):
    """Create the sample recipe. Returns the product, or None if it does not apply."""
    sample = available_for(business)
    if sample is None:
        return None
    materials = {}
    for key, (name, unit, cost) in sample["materials"].items():
        materials[key] = RawMaterial.objects.create(
            business=business, name=name + SUFFIX, usage_unit=unit, stock=Decimal("0"),
            cost_per_unit=Decimal(cost), created_by=user,
        )
    name, unit, per_batch, price, base_key = sample["product"]
    product = FinishedGood.objects.create(
        business=business, name=name + SUFFIX, unit=unit, units_per_batch=Decimal(per_batch),
        selling_price=Decimal(price), stock=Decimal("0"), base_material=materials[base_key], created_by=user,
    )
    for key, qty in sample["recipe"].items():
        RecipeItem.objects.create(finished_good=product, raw_material=materials[key], qty_per_batch=Decimal(qty))
    return product
