"""Recipe-versus-actual variance.

Answers "did we use what the recipe says, and did we get the output it promises?"

* Ingredient variance compares, per raw material, the quantity the recipe planned
  for approved or completed orders (``OrderMaterialUsage.planned_quantity``) with
  the quantity actually released (``actual_quantity``), plus stock written off
  through adjustments (shrinkage the recipe cannot explain).
* Output variance compares planned, produced and wasted units on batches.

Everything goes through scoped managers, so Live / Backlog / Combined behaves as
on every other screen. Values use each material's current cost per usage unit.
"""
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum

ZERO = Decimal("0")
HUNDRED = Decimal("100")
MONEY = DecimalField(max_digits=24, decimal_places=6)

# Variance beyond this share of the planned quantity is flagged for review.
REVIEW_THRESHOLD = Decimal("5")


def _pct(part, whole):
    whole = Decimal(whole or 0)
    return None if whole == 0 else Decimal(part or 0) / whole * HUNDRED


def _status(variance_pct, shrink_qty):
    if variance_pct is not None and abs(variance_pct) > REVIEW_THRESHOLD:
        return "review"
    if shrink_qty:
        return "watch"
    return "ok"


def ingredient_variance(business, start, end):
    """Per raw material: planned vs actual release, write-offs and value impact."""
    from inventory.models import RawMaterial, StockAdjustment
    from production.models import Order, OrderMaterialUsage

    orders = Order.objects.filter(
        business=business, status__in=("approved", "completed"), approved_date__range=(start, end)
    )
    usage = {
        row["raw_material_id"]: row
        for row in OrderMaterialUsage.objects.filter(business=business, order__in=orders)
        .values("raw_material_id")
        .annotate(planned=Sum("planned_quantity"), actual=Sum("actual_quantity"), orders=Count("order", distinct=True),
                  flexible=Count("pk", filter=Q(flexible=True)))
    }
    shrink = {
        row["raw_material_id"]: row
        for row in StockAdjustment.objects.filter(
            business=business, date__range=(start, end), reversed=False, raw_material__isnull=False, quantity__lt=0
        ).values("raw_material_id").annotate(
            qty=Sum("quantity"), value=Sum(ExpressionWrapper(F("quantity") * F("unit_value"), output_field=MONEY))
        )
    }
    materials = {m.pk: m for m in RawMaterial.objects.filter(business=business, pk__in=set(usage) | set(shrink))}
    rows = []
    for pk, material in materials.items():
        planned = (usage.get(pk) or {}).get("planned") or ZERO
        actual = (usage.get(pk) or {}).get("actual") or ZERO
        variance = actual - planned
        shrink_qty = -((shrink.get(pk) or {}).get("qty") or ZERO)
        shrink_value = -((shrink.get(pk) or {}).get("value") or ZERO)
        variance_pct = _pct(variance, planned)
        rows.append({
            "material_id": pk, "name": material.name, "unit": material.usage_unit,
            "planned": planned, "actual": actual, "variance": variance, "variance_pct": variance_pct,
            "variance_value": variance * (material.cost_per_unit or ZERO),
            "shrink_qty": shrink_qty, "shrink_value": shrink_value,
            "orders": (usage.get(pk) or {}).get("orders", 0), "flexible_lines": (usage.get(pk) or {}).get("flexible", 0),
            "status": _status(variance_pct, shrink_qty),
        })
    order = {"review": 0, "watch": 1, "ok": 2}
    rows.sort(key=lambda r: (order[r["status"]], -abs(r["variance_value"] + r["shrink_value"]), r["name"]))
    return rows


def output_variance(business, start, end):
    """Per product: planned vs produced vs wasted units and the cost of waste."""
    from production.models import ProductionBatch
    rows = []
    batches = ProductionBatch.objects.filter(
        business=business, production_date__range=(start, end), is_reversed=False
    ).values("finished_good_id", "finished_good__name").annotate(
        planned=Sum("planned_units"), produced=Sum("produced_units"), wasted=Sum("wastage_units"),
        cost=Sum("total_cost"), batches=Count("pk"),
    ).order_by("finished_good__name")
    for row in batches:
        planned, produced, wasted = row["planned"] or ZERO, row["produced"] or ZERO, row["wasted"] or ZERO
        total_units = produced + wasted
        unit_cost = (row["cost"] or ZERO) / produced if produced else ZERO
        rows.append({
            "product_id": row["finished_good_id"], "name": row["finished_good__name"] or "—", "batches": row["batches"],
            "planned": planned, "produced": produced, "wasted": wasted,
            "yield_pct": _pct(produced, planned), "waste_pct": _pct(wasted, total_units),
            "output_gap": produced - planned, "waste_value": wasted * unit_cost,
        })
    rows.sort(key=lambda r: -(r["waste_value"] or ZERO))
    return rows


def variance_report(business, start, end):
    ingredients = ingredient_variance(business, start, end)
    outputs = output_variance(business, start, end)
    return {
        "ingredients": ingredients, "outputs": outputs,
        "totals": {
            "review_count": sum(1 for r in ingredients if r["status"] == "review"),
            "variance_value": sum((r["variance_value"] for r in ingredients), ZERO),
            "shrink_value": sum((r["shrink_value"] for r in ingredients), ZERO),
            "waste_value": sum((r["waste_value"] for r in outputs), ZERO),
        },
    }
