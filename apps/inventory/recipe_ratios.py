"""Make "how much of each material goes with the base material" explicit.

Recipes store absolute quantities per batch (``RecipeItem.qty_per_batch``) and a
product may name one ``base_material`` that sizes production. The relationship
between ingredients is therefore implicit: if a batch is 20 kg flour, 1 kg sugar
and 240 g salt, sugar is 1 kg per 20 kg flour. This module derives that
relationship for display, without changing what is stored or how production is
costed or scaled.

    "1 kg per 20 kg Flour"        ingredient quantity against the base quantity
    "0.05 kg per 1 kg Flour"      the same ratio per single base unit
    "5% of Flour"                 shown when both units are mass or volume

Packaging and other per-batch inputs (``ProductionMaterial``) are not recipe
ratios, so they are reported as "per batch" only.
"""
from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal("0")
# Conversion between a unit and its pair so percentages work across g/kg, ml/l.
_UNIT_FACTORS = {
    ("g", "kg"): Decimal("0.001"), ("kg", "g"): Decimal("1000"),
    ("ml", "l"): Decimal("0.001"), ("l", "ml"): Decimal("1000"),
}


def q(value, places=4):
    """Readable decimal: 1.0000 -> '1', 0.0500 -> '0.05', 240.00 -> '240'."""
    if value is None:
        return ""
    quantum = Decimal(1).scaleb(-places)
    text = f"{Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _same_dimension(unit_a, unit_b):
    a, b = (unit_a or "").strip().lower(), (unit_b or "").strip().lower()
    if not a or not b:
        return None
    if a == b:
        return Decimal("1")
    return _UNIT_FACTORS.get((a, b))


def describe_relation(qty, unit, base_qty, base_unit, base_name):
    """Texts relating ``qty unit`` to ``base_qty base_unit base_name`` (or None if undefined)."""
    qty, base_qty = Decimal(qty or 0), Decimal(base_qty or 0)
    if base_qty <= 0:
        return None
    per_one = qty / base_qty
    relation = {
        "text": f"{q(qty)} {unit} per {q(base_qty)} {base_unit} {base_name}".replace("  ", " "),
        "per_unit_text": f"{q(per_one)} {unit} per 1 {base_unit} {base_name}".replace("  ", " "),
        "percent": None, "percent_text": "",
    }
    factor = _same_dimension(unit, base_unit)
    if factor is not None:
        percent = (qty * factor) / base_qty * 100
        relation["percent"] = percent
        relation["percent_text"] = f"{q(percent, 2)}% of {base_name}"
    return relation


def product_relations(good, recipe_lines=None):
    """Relationship of every recipe line of ``good`` to its base material.

    Returns ``{"has_base", "base", "rows", "warnings"}``. Each row has
    ``relation`` (str, '' when not applicable) plus the structured pieces.
    """
    lines = list(recipe_lines) if recipe_lines is not None else list(
        good.recipe_items.select_related("raw_material").order_by("raw_material__name")
    )
    base = good.base_material
    result = {"has_base": bool(base), "base": None, "rows": [], "warnings": []}
    base_line = next((line for line in lines if base and line.raw_material_id == base.pk), None)
    if base:
        if not base_line or (base_line.qty_per_batch or ZERO) <= 0:
            result["warnings"].append(
                f"{base.name} is set as the base material but has no recipe quantity, so ratios cannot be shown."
            )
        else:
            result["base"] = {
                "id": base.pk, "name": base.name, "unit": base.usage_unit, "qty_per_batch": base_line.qty_per_batch,
                "text": f"{q(base_line.qty_per_batch)} {base.usage_unit} {base.name} per batch",
            }
    for line in lines:
        material = line.raw_material
        row = {
            "material_id": material.pk, "name": material.name, "unit": material.usage_unit,
            "qty_per_batch": line.qty_per_batch, "qty_text": f"{q(line.qty_per_batch)} {material.usage_unit}",
            "is_base": bool(base and material.pk == base.pk), "flexible": line.flexible_usage,
            "relation": "", "per_unit_text": "", "percent_text": "",
        }
        if result["base"] and not row["is_base"]:
            info = describe_relation(
                line.qty_per_batch, material.usage_unit, result["base"]["qty_per_batch"],
                result["base"]["unit"], result["base"]["name"],
            )
            if info:
                row["relation"], row["per_unit_text"], row["percent_text"] = info["text"], info["per_unit_text"], info["percent_text"]
        elif row["is_base"]:
            row["relation"] = "Base material"
        result["rows"].append(row)
    return result


def material_relations(material):
    """How a raw material relates to the base material in each product that uses it."""
    from .models import RecipeItem
    lines = list(
        RecipeItem.objects.filter(raw_material=material)
        .select_related("finished_good", "finished_good__base_material").order_by("finished_good__name")
    )
    base_material_of = {line.finished_good_id: line.finished_good.base_material_id for line in lines
                        if line.finished_good.base_material_id}
    base_qty = {}
    if base_material_of:
        for item in RecipeItem.objects.filter(finished_good_id__in=base_material_of):
            if item.raw_material_id == base_material_of.get(item.finished_good_id):
                base_qty[item.finished_good_id] = item
    used_in = []
    for line in lines:
        good = line.finished_good
        base = good.base_material
        entry = {
            "product_id": good.pk, "product": good.name, "qty_per_batch": line.qty_per_batch,
            "qty_text": f"{q(line.qty_per_batch)} {material.usage_unit} per batch",
            "is_base": bool(base and base.pk == material.pk), "base_name": base.name if base else "",
            "relation": "", "percent_text": "",
        }
        if entry["is_base"]:
            entry["relation"] = "Base material"
        elif base and good.pk in base_qty:
            base_line = base_qty[good.pk]
            info = describe_relation(line.qty_per_batch, material.usage_unit, base_line.qty_per_batch,
                                     base.usage_unit, base.name)
            if info:
                entry["relation"], entry["percent_text"] = info["text"], info["percent_text"]
        used_in.append(entry)
    base_for = [entry["product"] for entry in used_in if entry["is_base"]]
    return {"used_in": used_in, "base_for": base_for}


def recipe_overview(business, limit=400):
    """Flat rows for every product that has a base material: one query for lines."""
    from .models import FinishedGood, RecipeItem
    goods = {
        g.pk: g for g in FinishedGood.objects.filter(business=business, base_material__isnull=False)
        .select_related("base_material").order_by("name")
    }
    if not goods:
        return []
    by_good = {}
    for line in RecipeItem.objects.filter(finished_good_id__in=goods).select_related("raw_material").order_by("raw_material__name"):
        by_good.setdefault(line.finished_good_id, []).append(line)
    rows = []
    for good_id, good in goods.items():
        info = product_relations(good, by_good.get(good_id, []))
        for row in info["rows"]:
            rows.append({"product": good.name, "base": good.base_material.name, **row})
        for warning in info["warnings"]:
            rows.append({"product": good.name, "base": good.base_material.name, "name": "—", "qty_text": "",
                         "relation": warning, "percent_text": "", "per_unit_text": "", "is_base": False})
        if len(rows) >= limit:
            break
    return rows


def pool_ratio_warnings(order, tolerance=Decimal("0.02")):
    """Explain when a base-material *pool* does not match the recipe's own ratios.

    In an order-level base-material pool the whole pool is released as the base
    ingredient, while every other ingredient follows the ordered batches. If the
    pool is larger or smaller than the ordered batches need, the other
    ingredients no longer sit at the recipe ratio to the base. Nothing is
    changed here; this only reports the difference so staff can decide.
    """
    from production.models import Order, OrderItem
    if order.production_basis != Order.BASIS_BASE_MATERIAL:
        return []
    warnings = []
    items = order.items.select_related("finished_good", "finished_good__base_material").prefetch_related(
        "finished_good__recipe_items__raw_material"
    )
    for item in items:
        good = item.finished_good
        base = good.base_material
        if item.production_basis != OrderItem.BASIS_BASE_MATERIAL or not base:
            continue
        lines = list(good.recipe_items.all())
        base_line = next((line for line in lines if line.raw_material_id == base.pk), None)
        share = item.base_material_quantity or ZERO
        if not base_line or (base_line.qty_per_batch or ZERO) <= 0 or share <= 0:
            continue
        upb = good.units_per_batch or Decimal("1")
        batches = item.effective_production_batch_qty + item.effective_production_piece_qty / upb
        recipe_need = base_line.qty_per_batch * batches
        if abs(share - recipe_need) / share <= tolerance:
            continue
        pool_batches = share / base_line.qty_per_batch
        others = []
        for line in lines:
            if line.raw_material_id == base.pk:
                continue
            unit = line.raw_material.usage_unit
            others.append({
                "name": line.raw_material.name,
                "released": f"{q(line.qty_per_batch * batches)} {unit}",
                "at_ratio": f"{q(line.qty_per_batch * pool_batches)} {unit}",
            })
        warnings.append({
            "product": good.name, "base": base.name, "unit": base.usage_unit,
            "pool": q(share), "recipe_need": q(recipe_need), "others": others,
            "direction": "less" if share > recipe_need else "more",
        })
    return warnings
