from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    DistributionReturn,
    FinishedGood,
    InventoryLocation,
    OperationalSupplyDispense,
    ProductionMaterial,
    RawMaterial,
    RawMaterialMeasurementChange,
    RecipeItem,
    StockAdjustment,
    MarketStockLot,
    MarketStockMovement,
    StockMovement,
)


def _scaled(value, factor, decimal_places, *, divide=False):
    if value is None:
        return None
    result = Decimal(value) / factor if divide else Decimal(value) * factor
    quantum = Decimal("1").scaleb(-decimal_places)
    return result.quantize(quantum)


def _scale_queryset(queryset, fields, factor, *, divide_fields=()):
    """Scale decimal fields in bounded batches and return converted row count."""
    model = queryset.model
    divide_fields = set(divide_fields)
    names = list(fields)
    count = 0
    batch = []
    for obj in queryset.iterator(chunk_size=500):
        for name in names:
            value = getattr(obj, name)
            if value is None:
                continue
            field = model._meta.get_field(name)
            scaled = _scaled(value, factor, field.decimal_places, divide=name in divide_fields)
            if Decimal(value) != 0 and scaled == 0:
                raise ValidationError(
                    f"The measurement conversion would round a live {model._meta.verbose_name} quantity to zero. "
                    "Choose a finer usage unit or a conversion ratio that the current quantity precision can represent."
                )
            setattr(obj, name, scaled)
        batch.append(obj)
        if len(batch) >= 500:
            model._base_manager.bulk_update(batch, names, batch_size=500)
            count += len(batch)
            batch.clear()
    if batch:
        model._base_manager.bulk_update(batch, names, batch_size=500)
        count += len(batch)
    return count


@transaction.atomic
def change_raw_material_measurement(*, business, material, new_measurement, conversion_ratio, reason, actor=None):
    """Convert the current operational measurement basis without rewriting history.

    ``conversion_ratio`` is NEW usage units per one OLD usage unit. Current
    stock/reorder quantities and live recipe/input definitions are converted.
    Completed movements, procurement receipts, approved production usage and
    frozen cost lines remain untouched; the measurement-change row is the
    explicit boundary auditors use to interpret records on either side.
    """
    from core.services import audit
    from procurement.models import RawMaterialCostSnapshot
    from production.models import ProductionRunMaterial

    locked = RawMaterial.raw_objects.select_for_update().get(pk=material.pk, business=business)
    old = {
        "purchase_unit": locked.purchase_unit,
        "package_qty": str(locked.package_qty),
        "package_unit": locked.package_unit,
        "usage_unit": locked.usage_unit,
        "usage_conversion_factor": str(locked.usage_conversion_factor),
        "stock": str(locked.stock),
        "reorder_level": str(locked.reorder_level),
        "usage_unit_cost": str(locked.cost_per_unit),
    }
    new = {
        "purchase_unit": (new_measurement.get("purchase_unit") or "").strip(),
        "package_qty": str(new_measurement.get("package_qty") or Decimal("1")),
        "package_unit": (new_measurement.get("package_unit") or "").strip(),
        "usage_unit": (new_measurement.get("usage_unit") or "").strip(),
        "usage_conversion_factor": str(new_measurement.get("usage_conversion_factor") or Decimal("1")),
    }
    ratio = Decimal(conversion_ratio or 1)
    if ratio <= 0:
        raise ValidationError("Measurement conversion ratio must be greater than zero.")
    usage_changed = old["usage_unit"] != new["usage_unit"]
    if not usage_changed:
        ratio = Decimal("1")

    converted = {"historical_records_rewritten": 0}
    if ratio != 1:
        locked.stock = _scaled(locked.stock, ratio, RawMaterial._meta.get_field("stock").decimal_places)
        locked.reorder_level = _scaled(locked.reorder_level, ratio, RawMaterial._meta.get_field("reorder_level").decimal_places)
        locked.cost_per_unit = _scaled(locked.cost_per_unit, ratio, RawMaterial._meta.get_field("cost_per_unit").decimal_places, divide=True)

        converted["recipe_items"] = _scale_queryset(
            RecipeItem.objects.filter(raw_material=locked), ["qty_per_batch"], ratio
        )
        converted["production_materials"] = _scale_queryset(
            ProductionMaterial.objects.filter(raw_material=locked), ["qty_per_batch"], ratio
        )
        converted["draft_production_run_materials"] = _scale_queryset(
            ProductionRunMaterial.raw_objects.filter(
                business=business, raw_material=locked, production_run__status="draft"
            ),
            ["planned_quantity", "actual_quantity"], ratio,
        )

    locked.purchase_unit = new["purchase_unit"]
    locked.package_qty = Decimal(new["package_qty"])
    locked.package_unit = new["package_unit"]
    locked.usage_unit = new["usage_unit"]
    locked.usage_conversion_factor = Decimal(new["usage_conversion_factor"])
    locked.save(update_fields=[
        "purchase_unit", "package_qty", "package_unit", "usage_unit",
        "usage_conversion_factor", "stock", "reorder_level", "cost_per_unit", "updated_at",
    ])

    # Future production-cost lookups need a cost snapshot expressed in the new
    # usage basis. Existing procurement/cost snapshots remain immutable.
    RawMaterialCostSnapshot.raw_objects.create(
        business=business,
        created_by=actor,
        raw_material=locked,
        effective_date=timezone.localdate(),
        purchase_order_item=None,
        purchase_unit_cost=(locked.cost_per_unit * locked.total_conversion_factor).quantize(Decimal("0.01")),
        usage_unit_cost=locked.cost_per_unit,
        supplier="Measurement basis conversion",
    )

    new.update({
        "stock": str(locked.stock),
        "reorder_level": str(locked.reorder_level),
        "usage_unit_cost": str(locked.cost_per_unit),
    })
    change = RawMaterialMeasurementChange.raw_objects.create(
        business=business, created_by=actor, raw_material=locked, conversion_ratio=ratio,
        reason=(reason or "").strip()[:255], old_measurement=old, new_measurement=new,
        converted_records=converted,
    )
    audit(
        business, actor, "measurement_change", locked,
        f"Measurement basis changed for {locked.name}",
        {
            "change_id": change.pk, "ratio": str(ratio), "old": old, "new": new,
            "converted_records": converted, "historical_records_preserved": True,
        },
    )
    return locked, change


def default_location(business):
    loc, _ = InventoryLocation.objects.get_or_create(business=business, name="Main Store", defaults={"location_type": "store", "created_by": None})
    return loc


@transaction.atomic
def record_raw_material_movement(material, quantity, movement_type, *, note="", reference="", unit_value=None, location=None):
    quantity = Decimal(quantity)
    material.stock = (material.stock + quantity).quantize(Decimal("0.001"))
    material.save(update_fields=["stock"])
    StockMovement.objects.create(
        business=material.business, raw_material=material, movement_type=movement_type,
        quantity=quantity, affects_stock=True, balance_after=material.stock, note=note,
        reference=reference, unit_value=Decimal(unit_value if unit_value is not None else material.cost_per_unit),
        location=location or default_location(material.business),
    )
    return material.stock


@transaction.atomic
def record_finished_good_movement(good, quantity, movement_type, *, note="", affects_stock=True, reference="", unit_value=None, location=None):
    quantity = Decimal(quantity)
    if affects_stock:
        # Serialise concurrent receipts, sales, and production movements for
        # this product so a stale in-memory balance cannot overwrite another
        # committed stock change.
        locked_good = FinishedGood.raw_objects.select_for_update().get(
            pk=good.pk,
            business_id=good.business_id,
        )
        if locked_good.stock is None:
            raise ValueError(f"{locked_good.name} is not configured for physical-store stock.")
        locked_good.stock = (locked_good.stock + quantity).quantize(Decimal("0.01"))
        locked_good.save(update_fields=["stock"])
        good.stock = locked_good.stock
    StockMovement.objects.create(
        business=good.business, finished_good=good, movement_type=movement_type,
        quantity=quantity, affects_stock=affects_stock, balance_after=good.stock if affects_stock else None,
        note=note, reference=reference, unit_value=Decimal(unit_value if unit_value is not None else good.est_cost),
        location=location or default_location(good.business),
    )
    return good.stock


def _sellable_market_lots(good, on_date, *, lock=False):
    queryset = MarketStockLot.raw_objects.filter(
        business=good.business,
        finished_good=good,
        active=True,
        quantity_available__gt=0,
        received_date__lte=on_date,
    ).filter(
        models.Q(expiry_date__isnull=True) | models.Q(expiry_date__gte=on_date)
    ).order_by(models.F("expiry_date").asc(nulls_last=True), "received_date", "id")
    return queryset.select_for_update() if lock else queryset


def market_stock_available(good, on_date):
    return sum(
        (Decimal(lot.quantity_available) for lot in _sellable_market_lots(good, on_date)),
        Decimal("0"),
    )


def _record_market_movement(lot, *, date, movement_type, quantity, user, customer=None, sale=None, note=""):
    return MarketStockMovement.raw_objects.create(
        business=lot.business,
        created_by=user,
        lot=lot,
        date=date,
        movement_type=movement_type,
        quantity=quantity,
        balance_after=lot.quantity_available,
        customer=customer,
        sale=sale,
        unit_value=lot.unit_cost,
        note=note,
    )


@transaction.atomic
def receive_market_production(batch, quantity, *, user):
    """Receive new unassigned Distribution output without touching shelf stock."""
    quantity = Decimal(quantity).quantize(Decimal("0.01"))
    if quantity <= 0:
        return None
    lot, created = MarketStockLot.raw_objects.get_or_create(
        production_batch=batch,
        source=MarketStockLot.SOURCE_PRODUCTION,
        defaults={
            "business": batch.business,
            "created_by": user,
            "finished_good": batch.finished_good,
            "received_date": batch.production_date,
            "expiry_date": batch.expiry_date,
            "quantity_received": quantity,
            "quantity_available": quantity,
            "unit_cost": batch.unit_cost,
        },
    )
    if not created:
        return lot
    _record_market_movement(
        lot,
        date=batch.production_date,
        movement_type=MarketStockMovement.PRODUCTION_IN,
        quantity=quantity,
        user=user,
        note=f"Market-stock production batch {batch.batch_number}",
    )
    record_finished_good_movement(
        batch.finished_good,
        quantity,
        StockMovement.FG_MARKET_PRODUCTION,
        note=f"Distribution Market Stock received — batch {batch.batch_number}",
        reference=batch.batch_number,
        affects_stock=False,
        unit_value=batch.unit_cost,
    )
    return lot


@transaction.atomic
def release_market_stock(*, business, good, customer, quantity, date, payment_status, payment_method, account, user, note=""):
    """Allocate sellable Market Stock FEFO and create its Distribution sale."""
    from core.models import FinancialTransaction
    from core.services import audit, record_cash
    from sales.models import Sale, SaleItem

    quantity = Decimal(quantity).quantize(Decimal("0.01"))
    if quantity <= 0:
        raise ValidationError("Release quantity must be greater than zero.")
    if good.business_id != business.pk or customer.business_id != business.pk:
        raise ValidationError("Product and customer must belong to the active business.")
    if payment_status == "paid" and (not account or account.business_id != business.pk):
        raise ValidationError("Select the active business account that received payment.")

    good = FinishedGood.raw_objects.select_for_update().get(pk=good.pk, business=business)
    lots = list(_sellable_market_lots(good, date, lock=True))
    available = sum((Decimal(lot.quantity_available) for lot in lots), Decimal("0"))
    if quantity > available:
        raise ValidationError(f"Only {available:.2f} {good.unit} is available in sellable Market Stock.")

    sale = Sale.raw_objects.create(
        business=business,
        created_by=user,
        date=date,
        customer=customer.name,
        customer_master=customer,
        transaction_type="paid" if payment_status == "paid" else "unpaid",
        unpaid_description="" if payment_status == "paid" else "Distribution Market Stock receivable — payment to be recorded through Finance.",
        account=account if payment_status == "paid" else None,
        payment_method=payment_method or "Transfer",
        source="distribution_order",
    )
    price = good.selling_price_for("distribution", customer)
    remaining = quantity
    for lot in lots:
        if remaining <= 0:
            break
        taken = min(remaining, Decimal(lot.quantity_available))
        lot.quantity_available = (Decimal(lot.quantity_available) - taken).quantize(Decimal("0.01"))
        if lot.quantity_available == 0:
            lot.active = False
            lot.closed_reason = "released"
        lot.save(update_fields=["quantity_available", "active", "closed_reason", "updated_at"])
        upb = good.units_per_batch or Decimal("1")
        batches = taken // upb if upb else Decimal("0")
        pieces = taken - (batches * upb)
        SaleItem.objects.create(
            sale=sale,
            finished_good=good,
            batch_qty=batches,
            piece_qty=pieces,
            discount=Decimal("0"),
            price=price,
            unit_cost=lot.unit_cost,
            production_batch=lot.production_batch,
        )
        _record_market_movement(
            lot,
            date=date,
            movement_type=MarketStockMovement.RELEASE,
            quantity=-taken,
            user=user,
            customer=customer,
            sale=sale,
            note=note or f"Released to {customer.name}",
        )
        record_finished_good_movement(
            good,
            -taken,
            StockMovement.FG_MARKET_RELEASE,
            note=f"Market Stock released to {customer.name}",
            reference=f"SALE-{sale.pk}",
            affects_stock=False,
            unit_value=lot.unit_cost,
        )
        remaining -= taken

    good.total_delivered_to_customers += quantity
    good.save(update_fields=["total_delivered_to_customers", "updated_at"])
    if sale.transaction_type == "paid":
        record_cash(
            business,
            user,
            date=date,
            amount=sale.total,
            transaction_type=FinancialTransaction.INCOME,
            category="Distribution Market Stock sale",
            description=f"Payment received for Market Stock sale #{sale.pk}",
            payment_method=sale.payment_method,
            reference=f"SALE-{sale.pk}",
            account=account,
        )
    audit(
        business,
        user,
        "release_market_stock",
        sale,
        f"{quantity:.2f} {good.unit} of {good.name} released to {customer.name}",
        {"sale_id": sale.pk, "product_id": good.pk, "quantity": str(quantity)},
    )
    return sale


@transaction.atomic
def transfer_market_stock_to_physical(*, business, good, quantity, date, user, reason):
    """Move FEFO Market Stock to shelf, including the explicit non-shelf-product exception."""
    from core.services import audit

    quantity = Decimal(quantity).quantize(Decimal("0.01"))
    if quantity <= 0:
        raise ValidationError("Transfer quantity must be greater than zero.")
    if good.business_id != business.pk:
        raise ValidationError("Product must belong to the active business.")
    good = FinishedGood.raw_objects.select_for_update().get(pk=good.pk, business=business)
    lots = list(_sellable_market_lots(good, date, lock=True))
    available = sum((Decimal(lot.quantity_available) for lot in lots), Decimal("0"))
    if quantity > available:
        raise ValidationError(f"Only {available:.2f} {good.unit} is available in sellable Market Stock.")

    remaining = quantity
    total_value = Decimal("0")
    for lot in lots:
        if remaining <= 0:
            break
        taken = min(remaining, Decimal(lot.quantity_available))
        total_value += taken * lot.unit_cost
        lot.quantity_available = (Decimal(lot.quantity_available) - taken).quantize(Decimal("0.01"))
        if lot.quantity_available == 0:
            lot.active = False
            lot.closed_reason = "transferred_physical"
        lot.save(update_fields=["quantity_available", "active", "closed_reason", "updated_at"])
        _record_market_movement(
            lot,
            date=date,
            movement_type=MarketStockMovement.TRANSFER_PHYSICAL,
            quantity=-taken,
            user=user,
            note=reason,
        )
        remaining -= taken

    if good.stock is None:
        good.stock = Decimal("0")
        good.save(update_fields=["stock", "updated_at"])
    unit_value = total_value / quantity if quantity else Decimal("0")
    record_finished_good_movement(
        good,
        quantity,
        StockMovement.FG_MARKET_TRANSFER,
        note=f"Distribution Market Stock transferred to Physical Store — {reason}",
        reference=f"MARKET-TRANSFER-{date:%Y%m%d}",
        affects_stock=True,
        unit_value=unit_value,
    )
    good.transferred_market_stock = Decimal(good.transferred_market_stock or 0) + quantity
    good.save(update_fields=["transferred_market_stock", "updated_at"])
    audit(
        business,
        user,
        "transfer_market_stock",
        good,
        f"{quantity:.2f} {good.unit} of {good.name} transferred to Physical Store",
        {"quantity": str(quantity), "reason": reason},
    )
    return good


@transaction.atomic
def record_distribution_return(*, business, sale_item, quantity, date, condition, reason, user):
    """Record a Distribution return as redistributable Market Stock or damage."""
    from core.services import audit
    from sales.models import SaleItem

    quantity = Decimal(quantity).quantize(Decimal("0.01"))
    item = SaleItem.objects.select_for_update().select_related(
        "sale", "finished_good", "production_batch"
    ).get(pk=sale_item.pk)
    if item.sale.business_id != business.pk or item.sale.source != "distribution_order":
        raise ValidationError("Select a Distribution sale belonging to the active business.")
    already_returned = sum(
        (row.quantity for row in DistributionReturn.raw_objects.filter(sale_item=item)),
        Decimal("0"),
    )
    returnable = max(Decimal("0"), item.total_units - already_returned)
    if quantity <= 0 or quantity > returnable:
        raise ValidationError(f"Only {returnable:.2f} units remain returnable on this sale line.")
    expiry_date = item.production_batch.expiry_date if item.production_batch else None
    if condition == DistributionReturn.REDISTRIBUTABLE and expiry_date and expiry_date < date:
        raise ValidationError("This batch is already expired and cannot return to sellable Market Stock.")
    unit_value = item.unit_cost or (
        item.production_batch.unit_cost if item.production_batch else item.finished_good.est_cost
    )
    returned = DistributionReturn.raw_objects.create(
        business=business,
        created_by=user,
        sale_item=item,
        date=date,
        quantity=quantity,
        condition=condition,
        reason=reason,
        unit_value=unit_value,
    )
    if condition == DistributionReturn.REDISTRIBUTABLE:
        lot = MarketStockLot.raw_objects.create(
            business=business,
            created_by=user,
            finished_good=item.finished_good,
            production_batch=item.production_batch,
            source_sale_item=item,
            source=MarketStockLot.SOURCE_RETURN,
            received_date=date,
            expiry_date=expiry_date,
            quantity_received=quantity,
            quantity_available=quantity,
            unit_cost=unit_value,
        )
        returned.market_lot = lot
        returned.save(update_fields=["market_lot", "updated_at"])
        _record_market_movement(
            lot,
            date=date,
            movement_type=MarketStockMovement.RETURN_IN,
            quantity=quantity,
            user=user,
            customer=item.sale.customer_master,
            sale=item.sale,
            note=reason,
        )
        record_finished_good_movement(
            item.finished_good,
            quantity,
            StockMovement.FG_MARKET_RETURN,
            note=f"Redistributable return from Sale #{item.sale_id} — {reason}",
            reference=f"RETURN-{returned.pk}",
            affects_stock=False,
            unit_value=unit_value,
        )
    else:
        record_finished_good_movement(
            item.finished_good,
            -quantity,
            StockMovement.FG_DISTRIBUTION_DAMAGE,
            note=f"Damaged Distribution return from Sale #{item.sale_id} — {reason}",
            reference=f"RETURN-{returned.pk}",
            affects_stock=False,
            unit_value=unit_value,
        )
    audit(
        business,
        user,
        "distribution_return",
        returned,
        f"{quantity:.2f} {item.finished_good.unit} returned from Distribution sale #{item.sale_id}",
        {"condition": condition, "reason": reason, "writeoff_value": str(returned.writeoff_value)},
    )
    return returned


@transaction.atomic
def reconcile_expired_market_lot(*, business, lot, date, user, reason):
    """Write off the exact remaining balance of an expired Market Stock lot."""
    from core.services import audit

    lot = MarketStockLot.raw_objects.select_for_update().select_related("finished_good").get(
        pk=lot.pk, business=business
    )
    if not lot.expiry_date or lot.expiry_date >= date:
        raise ValidationError("Only a lot whose expiry date has passed can be reconciled as expired.")
    quantity = Decimal(lot.quantity_available)
    if quantity <= 0:
        raise ValidationError("This expired lot has no remaining quantity to reconcile.")
    lot.quantity_available = Decimal("0")
    lot.active = False
    lot.closed_reason = "expired"
    lot.save(update_fields=["quantity_available", "active", "closed_reason", "updated_at"])
    _record_market_movement(
        lot,
        date=date,
        movement_type=MarketStockMovement.EXPIRY,
        quantity=-quantity,
        user=user,
        note=reason,
    )
    record_finished_good_movement(
        lot.finished_good,
        -quantity,
        StockMovement.FG_MARKET_EXPIRY,
        note=f"Expired Distribution Market Stock — {reason}",
        reference=f"EXPIRED-LOT-{lot.pk}",
        affects_stock=False,
        unit_value=lot.unit_cost,
    )
    audit(
        business,
        user,
        "expire_market_stock",
        lot,
        f"{quantity:.2f} {lot.finished_good.unit} of {lot.finished_good.name} written off as expired",
        {"quantity": str(quantity), "value": str(quantity * lot.unit_cost), "reason": reason},
    )
    return quantity


@transaction.atomic
def reverse_market_production_lot(*, batch, date, user):
    """Reverse an untouched new-style Market Stock lot for an order reversal."""
    lot = MarketStockLot.raw_objects.select_for_update().filter(
        production_batch=batch,
        source=MarketStockLot.SOURCE_PRODUCTION,
    ).first()
    if lot is None:
        return False
    if lot.movements.exclude(movement_type=MarketStockMovement.PRODUCTION_IN).exists():
        raise ValidationError("Market Stock from this batch already has downstream movements.")
    quantity = Decimal(lot.quantity_available)
    if quantity != Decimal(lot.quantity_received):
        raise ValidationError("Market Stock from this batch is no longer untouched.")
    lot.quantity_available = Decimal("0")
    lot.active = False
    lot.closed_reason = "reversed"
    lot.save(update_fields=["quantity_available", "active", "closed_reason", "updated_at"])
    _record_market_movement(
        lot,
        date=date,
        movement_type=MarketStockMovement.REVERSAL,
        quantity=-quantity,
        user=user,
        note=f"Reversal of market-stock production batch {batch.batch_number}",
    )
    record_finished_good_movement(
        batch.finished_good,
        -quantity,
        StockMovement.ADJUSTMENT,
        note=f"Reversal of Distribution Market Stock — batch {batch.batch_number}",
        reference=f"REV-{batch.batch_number}",
        affects_stock=False,
        unit_value=batch.unit_cost,
    )
    return True


def consume_transferred_physical_stock(good, quantity):
    """Reduce the remaining market-transfer shelf allowance after a shelf sale."""
    quantity = Decimal(quantity).quantize(Decimal("0.01"))
    amount = min(Decimal(good.transferred_market_stock or 0), quantity)
    if amount <= 0:
        return Decimal("0")
    FinishedGood.raw_objects.filter(pk=good.pk).update(
        transferred_market_stock=F("transferred_market_stock") - amount
    )
    good.transferred_market_stock -= amount
    return amount
