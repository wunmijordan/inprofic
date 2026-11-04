"""Complete finished-stock valuation shared by Books and financial packs."""
from decimal import Decimal, InvalidOperation
from django.db.models import OuterRef, Subquery
from .models import FinishedGood, StockMovement


def finished_stock_value(business, *, all_periods=False):
    manager = FinishedGood.raw_objects if all_periods else FinishedGood.objects
    latest = StockMovement.raw_objects.filter(
        business=business, finished_good_id=OuterRef("pk"), movement_type=StockMovement.FG_PURCHASE,
        quantity__gt=0,
    ).order_by("-occurred_at", "-id").values("unit_value")[:1]
    goods = (manager.filter(business=business, stock__gt=0).select_related("business")
             .prefetch_related("recipe_items__raw_material", "production_materials__raw_material")
             .annotate(prefetched_latest_purchase_unit_value=Subquery(latest)).order_by("pk"))
    total, count, errors = Decimal("0"), 0, []
    for good in goods.iterator(chunk_size=500):
        try:
            value = Decimal(good.stock) * Decimal(good.est_cost or 0)
            if not value.is_finite():
                raise ValueError("Non-finite cost")
            total += value
            count += 1
        except (InvalidOperation, TypeError, ValueError):
            errors.append(good.name)
    return total, count, errors
