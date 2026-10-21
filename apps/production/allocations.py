"""Channel allocations for a multi-channel production order.

A production order is produced exactly once - one approval, one release of
materials, one batch and cost snapshot per product - however many channels its
output is meant for. This module owns everything that is specific to *where the
output goes*:

* ``replace_allocations`` records the manager's assignment (product by product,
  channel by channel) and puts the order in the ``multi_channel`` state.
* ``post_item_allocations`` runs at completion, once per product, and delivers the
  batch's saleable output to the allocations: shelf stock, a non-stock purpose,
  Distribution market stock, or a customer sale with its own payment.
* ``reversal_figures`` tells ``order_reverse`` what each allocation posted so it
  can be undone exactly.

Single-channel orders never touch any of this; their completion path is unchanged.

Fill rules when less is saleable than was allocated: customer allocations are
served first (they carry a receivable and a promise), in their saved order, then
stock-type allocations. A customer is billed only for what was delivered.
"""
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from core.models import FinancialTransaction
from core.services import record_cash
from inventory.models import StockMovement
from inventory.services import receive_market_production, record_finished_good_movement

from .models import Order, OrderAllocation, OrderAllocationLine

ZERO = Decimal("0")
CENT = Decimal("0.01")
# One of these per order at most: a second one would only be a typo for the first.
SINGLETON_KINDS = ("market", "stock")

# Order-level channel fields that no longer apply once an order is multi-channel.
ORDER_CHANNEL_RESET = {
    "is_market_stock": False, "production_destination": "store", "non_stock_purpose": "", "customer": None,
    "customer_name": "", "customer_region": "", "customer_group": "", "transaction_type": "paid",
    "unpaid_description": "", "payment_method": "Cash", "account": None, "customer_payment_status": "paid",
    "customer_payment_method": "Transfer", "customer_payment_account": None,
}


def cents(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def per_batch(good):
    return good.units_per_batch or Decimal("1")


def to_batches_and_pieces(units, size):
    """Express ``units`` as whole batches plus the loose pieces that are left over."""
    batches = (units / size).to_integral_value(rounding=ROUND_FLOOR)
    return cents(batches), cents(units - batches * size)


def item_units(item):
    return cents(item.total_units)


def kind_of(instance):
    """Classify a validated channel (an Order/OrderAllocation-like object)."""
    channel = getattr(instance, "order_type", None) or getattr(instance, "channel", "")
    if channel == "distribution" and instance.is_market_stock:
        return "market"
    if channel in ("distribution", "online"):
        return "customer"
    if instance.production_destination == "non_stock":
        return "non_stock"
    return "stock"


def duplicate_kind_errors(kinds_by_letter):
    """Error messages for allocations that should have been one (two market-stock, two store-stock)."""
    errors = []
    for kind in SINGLETON_KINDS:
        letters = [letter for letter, k in kinds_by_letter if k == kind]
        if len(letters) > 1:
            what = "Distribution market stock" if kind == "market" else "Physical Store stock"
            errors.append(f"{what} appears in destinations {' and '.join(letters)}. Put it in one destination.")
    return errors


def replace_allocations(order, destinations):
    """Save the assignment, replacing any earlier one, and make the order multi-channel.

    ``destinations`` is a list of ``(channel_instance, {order_item_pk: units})`` in
    the order they should be filled; ``channel_instance`` is the validated, unsaved
    Order produced by OrderForm for that destination.
    """
    items = {item.pk: item for item in order.items.select_related("finished_good")}
    order.allocations.all().delete()
    for position, (channel, units) in enumerate(destinations):
        paid = channel.customer_payment_status == "paid"
        allocation = OrderAllocation.objects.create(
            order=order, position=position, channel=channel.order_type, is_market_stock=bool(channel.is_market_stock),
            production_destination=channel.production_destination or "store",
            non_stock_purpose=channel.non_stock_purpose or "", customer=channel.customer,
            customer_name=(channel.customer.name if channel.customer_id else channel.customer_name) or "",
            payment_status=channel.customer_payment_status or "paid",
            payment_method=channel.customer_payment_method or "Transfer",
            payment_account=channel.customer_payment_account if paid else None,
        )
        for item_pk, quantity in units.items():
            if quantity <= 0:
                continue
            item = items[item_pk]
            batches, pieces = to_batches_and_pieces(quantity, per_batch(item.finished_good))
            OrderAllocationLine.objects.create(
                allocation=allocation, order_item=item, batch_qty=batches, piece_qty=pieces,
                price=item.finished_good.selling_price_for(channel.order_type, channel.customer),
                discount=item.discount or ZERO,
            )
    order.order_type = Order.TYPE_MULTI
    for name, value in ORDER_CHANNEL_RESET.items():
        setattr(order, name, value)
    order.save()
    return list(order.allocations.all())


def clear_allocations(order):
    order.allocations.all().delete()


def _customer_sale(order, allocation, sales, *, user, completed_date):
    """The one sale an allocation's customer receives, created on first use."""
    from sales.models import Sale
    sale = sales.get(allocation.pk)
    if sale is None:
        paid = allocation.payment_status == "paid"
        sale = Sale.objects.create(
            business=order.business, date=completed_date, customer=allocation.customer_display,
            customer_master=allocation.customer, payment_method=allocation.payment_method or "Transfer",
            transaction_type="paid" if paid else "unpaid",
            unpaid_description="" if paid else "Customer receivable — payment to be recorded through Finance.",
            account=allocation.payment_account if paid else None, source=f"{allocation.channel}_order",
            linked_order=order, created_by=user,
        )
        sales[allocation.pk] = sale
    return sale


def post_item_allocations(order, item, batch, *, unit_cost, location, user, completed_date, sales):
    """Deliver one finished batch to its allocations; returns what to record on the batch.

    ``sales`` is a dict shared across the order's items so each customer allocation
    ends up with a single sale covering all of its products.
    """
    from sales.models import SaleItem

    good = item.finished_good
    size = per_batch(good)
    available = min(batch.saleable_units, Decimal(item.production_total_units))
    lines = list(
        item.allocation_lines.select_related("allocation__customer", "allocation__payment_account")
        .order_by("allocation__position", "allocation_id")
    )
    ordered = [l for l in lines if l.allocation.is_customer] + [l for l in lines if not l.allocation.is_customer]
    customer_units = ZERO
    market_units = ZERO
    for line in ordered:
        allocation = line.allocation
        delivered = cents(min(line.total_units, available))
        available -= delivered
        line.delivered_units = delivered
        line.save(update_fields=["delivered_units", "updated_at"])
        if delivered <= 0:
            continue
        if allocation.is_market:
            market_units += delivered
        elif allocation.is_stock:
            record_finished_good_movement(
                good, delivered, StockMovement.FG_PRODUCTION,
                note=f"Planned store production — batch {batch.batch_number}",
                reference=batch.batch_number, affects_stock=True, unit_value=unit_cost, location=location,
                on_date=completed_date,
            )
        elif allocation.is_non_stock:
            record_finished_good_movement(
                good, delivered, StockMovement.FG_PRODUCTION,
                note=f"Non-stock production ({allocation.non_stock_purpose}) — batch {batch.batch_number}",
                reference=batch.batch_number, affects_stock=False, unit_value=unit_cost, location=location,
                on_date=completed_date,
            )
        else:
            record_finished_good_movement(
                good, delivered, StockMovement.FG_PRODUCTION,
                note=f"Customer-order production batch {batch.batch_number} for {allocation.customer_display or 'customer'}",
                reference=batch.batch_number, affects_stock=False, unit_value=unit_cost, location=location,
                on_date=completed_date,
            )
            customer_units += delivered
            sale = _customer_sale(order, allocation, sales, user=user, completed_date=completed_date)
            batches, pieces = to_batches_and_pieces(delivered, size)
            SaleItem.objects.create(
                sale=sale, finished_good=good, batch_qty=batches, piece_qty=pieces, discount=line.discount,
                price=line.price, unit_cost=unit_cost, production_batch=batch,
            )
    if market_units > 0:
        receive_market_production(batch, market_units, user=user)
    return {"customer_units": customer_units, "leftover_planned": cents(available)}


def finalize_customer_sales(order, sales, *, user):
    """Link each customer allocation to its sale and record the cash of paid ones."""
    for allocation in order.allocations.select_related("customer", "payment_account"):
        sale = sales.get(allocation.pk)
        if sale is None:
            continue
        if allocation.payment_status == "paid" and sale.total > 0:
            record_cash(
                order.business, user, date=sale.date, amount=sale.total,
                transaction_type=FinancialTransaction.INCOME, category="Customer order payment",
                description=f"Payment received for order #{order.display_number} — {allocation.customer_display or 'customer'}",
                payment_method=sale.payment_method, reference=f"ORDER-{order.pk}", account=allocation.payment_account,
            )
        allocation.sale = sale
        allocation.save(update_fields=["sale", "updated_at"])


def reversal_figures(order, batch):
    """What this batch's allocations posted: shelf stock, customer units, market-stock units."""
    stock = customer = market = ZERO
    for line in OrderAllocationLine.objects.filter(
        allocation__order=order, order_item_id=batch.order_item_id
    ).select_related("allocation"):
        if line.allocation.is_stock:
            stock += line.delivered_units
        elif line.allocation.is_customer:
            customer += line.delivered_units
        elif line.allocation.is_market:
            market += line.delivered_units
    return {"stock": stock, "customer": customer, "market": market}


def copy_allocations(source, target, item_map):
    """Copy a reversed order's assignment onto its re-created order (delivered units reset)."""
    for allocation in source.allocations.prefetch_related("lines"):
        clone = OrderAllocation.objects.create(
            order=target, position=allocation.position, channel=allocation.channel,
            is_market_stock=allocation.is_market_stock, production_destination=allocation.production_destination,
            non_stock_purpose=allocation.non_stock_purpose, customer=allocation.customer,
            customer_name=allocation.customer_name, payment_status=allocation.payment_status,
            payment_method=allocation.payment_method, payment_account=allocation.payment_account,
        )
        for line in allocation.lines.all():
            new_item = item_map.get(line.order_item_id)
            if new_item is None:
                continue
            OrderAllocationLine.objects.create(
                allocation=clone, order_item=new_item, batch_qty=line.batch_qty, piece_qty=line.piece_qty,
                price=line.price, discount=line.discount,
            )
