"""Helpers for orders logged by production-floor users (Baker / Chef roles).

Those users create an ordinary pending order through the normal order form and
can save the flexible-material amounts from the order page. The amounts live on
an ``OrderFlexibleMaterialReport`` and only pre-fill the existing approval and
completion forms; nothing is released or completed from them.
"""
from decimal import Decimal

from .models import OrderFlexibleMaterialReport

QUANTUM = Decimal("0.0001")
ZERO = Decimal("0")


def kitchen_report(order):
    """The order's flexible material report, or ``None`` (never raises for plain orders)."""
    try:
        return order.flexible_report
    except OrderFlexibleMaterialReport.DoesNotExist:
        return None


def multiplier_for(good, batch_qty, piece_qty):
    """Production batches equivalent for a Physical Store line (batches + pieces/upb)."""
    upb = good.units_per_batch or Decimal("1")
    return (batch_qty or ZERO) + ((piece_qty or ZERO) / upb)


def summarize_report(order):
    """Readable summary of what the kitchen recorded, for detail screens."""
    report = kitchen_report(order)
    if report is None:
        return None
    lines = []
    for item in order.items.select_related("finished_good").prefetch_related("finished_good__recipe_items__raw_material"):
        good = item.finished_good
        multiplier = multiplier_for(good, item.batch_qty, item.piece_qty)
        output = report.output_for(item.pk)
        flexible = []
        for link in good.recipe_items.all():
            reported = report.per_batch_for(item.pk, link.pk)
            if reported is not None:
                flexible.append({"name": link.raw_material.name, "unit": link.raw_material.usage_unit, "total": reported * multiplier})
        lines.append({
            "product": good.name, "unit": good.unit, "ordered": item.total_units,
            "produced": output["produced"], "wastage": output["wastage"], "reason": output["reason"], "flexible": flexible,
        })
    central = []
    if report.flexible_totals:
        from inventory.models import RawMaterial
        names = {m.pk: m for m in RawMaterial.objects.filter(pk__in=[int(k) for k in report.flexible_totals])}
        for material_id, total in report.flexible_totals.items():
            material = names.get(int(material_id))
            if material:
                central.append({"name": material.name, "unit": material.usage_unit, "total": Decimal(str(total))})
    return {"report": report, "lines": lines, "central": central}
