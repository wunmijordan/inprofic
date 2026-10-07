import logging
from decimal import Decimal, ROUND_DOWN

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from inventory.models import FinishedGood, RawMaterial, StockMovement
from inventory.services import (
    consume_transferred_physical_stock, record_finished_good_movement,
    record_raw_material_movement,
)
from production.models import Order, OrderItem, ProductionCostSnapshot
from sales.models import Sale, SaleItem
from core.services import audit
from core.verticals import vertical_config
from inventory.portioning import active_bulk_packs
from .models import CommerceIntake, CommerceNotification, CommerceSettings
from .notification_services import queue_commerce_notification

logger = logging.getLogger(__name__)



# Hosted storefronts, the headless API and connectors sell through the Online channel only. Bulk
# options configured on a product ride on Online as made-to-order price options; the separate
# Distribution / Bulk channel is reserved for the in-premise POS.
EXTERNAL_SALES_CHANNELS = {
    CommerceIntake.CHANNEL_ONLINE,
}
EXTERNAL_COMMERCE_SOURCES = {
    CommerceIntake.SOURCE_STOREFRONT,
    CommerceIntake.SOURCE_API,
    CommerceIntake.SOURCE_CONNECTOR,
}


def validate_channel_for_source(source, channel):
    if source in EXTERNAL_COMMERCE_SOURCES and channel not in EXTERNAL_SALES_CHANNELS:
        raise ValidationError(
            "External storefronts and integrations order through the online channel only (bulk options are "
            "online price options). Physical-store and distribution/bulk channels are reserved for the in-premise POS."
        )
    return channel


def _current_unit_cost(good, on_date):
    latest_cost = ProductionCostSnapshot.objects.filter(
        finished_good=good, production_date__lte=on_date
    ).order_by("-production_date", "-id").first()
    latest_purchase = good.stock_movements.filter(
        movement_type=StockMovement.FG_PURCHASE, occurred_at__date__lte=on_date, quantity__gt=0
    ).order_by("-occurred_at", "-id").first()
    if (not good.business.uses_production or good.is_purchased_for_resale) and latest_purchase:
        return latest_purchase.unit_value
    return latest_cost.unit_cost if latest_cost else latest_purchase.unit_value if latest_purchase else good.est_cost


class ChannelMinimumError(ValidationError):
    def __init__(self, message, *, alternatives):
        super().__init__(message)
        self.alternatives = alternatives


def _channel_allowed(product, channel):
    if channel == CommerceIntake.CHANNEL_DISTRIBUTION:
        if not product.allow_distribution_order:
            return False
        return bool(
            active_bulk_packs(product.finished_good)
            or product.finished_good.explicit_selling_price_for(CommerceIntake.CHANNEL_DISTRIBUTION) is not None
        )
    return {
        CommerceIntake.CHANNEL_PHYSICAL_STORE: product.allow_stock_order,
        CommerceIntake.CHANNEL_ONLINE: product.allow_online_order,
    }.get(channel, False)


def _channel_minimum(product, channel):
    return {
        CommerceIntake.CHANNEL_PHYSICAL_STORE: product.min_quantity,
        CommerceIntake.CHANNEL_ONLINE: product.preorder_min_quantity,
        CommerceIntake.CHANNEL_DISTRIBUTION: product.distribution_min_quantity,
    }[channel]


def _fulfilment_mode(business, channel):
    if not business.uses_production:
        return CommerceIntake.MODE_STOCK
    return (
        CommerceIntake.MODE_STOCK
        if channel == CommerceIntake.CHANNEL_PHYSICAL_STORE
        else CommerceIntake.MODE_PREORDER
    )


def resolve_channel_and_fulfilment(*, business, sales_channel=None, ordering_mode=None):
    """Resolve the sales-channel contract while accepting older request formats."""
    channel = (sales_channel or "").strip().lower()
    submitted_mode = (ordering_mode or "").strip().lower()
    if not channel:
        if submitted_mode == CommerceIntake.MODE_PREORDER:
            channel = CommerceIntake.CHANNEL_ONLINE
        elif submitted_mode == CommerceIntake.MODE_STOCK:
            channel = CommerceIntake.CHANNEL_PHYSICAL_STORE
        elif submitted_mode in dict(CommerceIntake.CHANNEL_CHOICES):
            channel = submitted_mode
    if channel not in dict(CommerceIntake.CHANNEL_CHOICES):
        raise ValidationError("Choose physical_store, online, or distribution as the order mode.")
    return channel, _fulfilment_mode(business, channel)


@transaction.atomic
def create_intake(*, business, source, ordering_mode=None, sales_channel=None, customer, items, idempotency_key="", external_order_id="", service_mode="", table_reference=""):
    sales_channel, ordering_mode = resolve_channel_and_fulfilment(
        business=business, sales_channel=sales_channel, ordering_mode=ordering_mode
    )
    validate_channel_for_source(source, sales_channel)
    if idempotency_key:
        existing = CommerceIntake.raw_objects.filter(business=business, source=source, idempotency_key=idempotency_key).first()
        if existing:
            return existing, False
    intake = CommerceIntake.raw_objects.create(
        business=business, source=source, ordering_mode=ordering_mode, sales_channel=sales_channel,
        customer_name=(customer.get("name") or "").strip(), customer_phone=(customer.get("phone") or "").strip(),
        customer_email=(customer.get("email") or "").strip(), customer_address=(customer.get("address") or "").strip(),
        idempotency_key=idempotency_key, external_order_id=external_order_id,
        service_mode=service_mode or "", table_reference=table_reference or "",
    )
    if not intake.customer_name:
        raise ValidationError("Customer name is required.")
    if not items:
        raise ValidationError("Add at least one product.")
    seen_products = set()
    for row in items:
        product = row["storefront_product"]
        if product.pk in seen_products:
            raise ValidationError("Submit each product only once per commerce request.")
        seen_products.add(product.pk)
        qty = Decimal(str(row["quantity"]))
        if product.business_id != business.pk or not product.published:
            raise ValidationError("One of the selected products is not available for this storefront.")
        if not _channel_allowed(product, sales_channel):
            raise ValidationError(f"{product.display_name} is not available through the selected order mode.")
        if (
            sales_channel == CommerceIntake.CHANNEL_DISTRIBUTION
            and active_bulk_packs(product.finished_good)
        ):
            raise ValidationError(
                f"{product.display_name} uses configured Bulk / Distribution options. "
                "Create the order through the checkout contract and choose a bulk option."
            )
        minimum = _channel_minimum(product, sales_channel)
        if sales_channel == CommerceIntake.CHANNEL_DISTRIBUTION and qty < minimum:
            labels = vertical_config(business)["commerce_channels"]
            candidate_codes = (CommerceIntake.CHANNEL_ONLINE,) if source in EXTERNAL_COMMERCE_SOURCES else (CommerceIntake.CHANNEL_PHYSICAL_STORE, CommerceIntake.CHANNEL_ONLINE)
            alternatives = [
                {"code": code, "label": labels[code]}
                for code in candidate_codes
                if _channel_allowed(product, code)
            ]
            raise ChannelMinimumError(
                f"{product.display_name} requires at least {minimum} {product.finished_good.unit} for {labels[sales_channel]} pricing.",
                alternatives=alternatives,
            )
        if qty < minimum or (product.max_quantity is not None and qty > product.max_quantity):
            raise ValidationError(f"Quantity for {product.display_name} is outside the permitted range.")
        unit_price = product.finished_good.selling_price_for(sales_channel)
        if sales_channel == CommerceIntake.CHANNEL_DISTRIBUTION:
            unit_price = product.finished_good.explicit_selling_price_for(sales_channel)
            if unit_price is None:
                raise ValidationError(
                    f"{product.display_name} needs a Distribution channel price or an active bulk option."
                )
        intake.items.create(
            storefront_product=product, finished_good=product.finished_good,
            requested_quantity=qty, unit_price=unit_price,
        )
    audit(
        business,
        None,
        "commerce_intake_create",
        intake,
        f"{intake.public_number} received for {intake.customer_name} from {intake.get_source_display()}",
        {
            "customer_name": intake.customer_name,
            "source": intake.source,
            "sales_channel": intake.sales_channel,
            "external_order_id": intake.external_order_id,
        },
    )
    source_label = dict(CommerceIntake.SOURCE_CHOICES).get(source, "commerce channel")
    queue_commerce_notification(
        business=business,
        event_type=CommerceNotification.EVENT_INTAKE_RECEIVED,
        title=f"New order from {source_label}",
        message=(
            f"{intake.customer_name} placed {intake.public_number} through "
            f"{intake.display_sales_channel} for {business.currency_symbol}{intake.total:,.2f}."
        ),
        target_url="/commerce/",
        dedupe_key=f"intake:{intake.public_id}:received",
    )
    return intake, True


def _commerce_payment_snapshot(intake):
    """Return the confirmed commerce payment details used by Production.

    Commerce owns the actual cash/ledger posting. Production only needs a
    truthful payment snapshot so a paid made-to-order request does not look
    like a receivable while it waits for staff to begin production.
    """
    payment = intake.payments.filter(status="paid").order_by("-settled_at", "-id").first()
    if not payment:
        return "unpaid", "", None
    method = {
        "cash": "Cash",
        "pos_card": "Card",
        "paystack": "Card",
        "monnify": "Card",
        "bank_transfer": "Transfer",
    }.get(payment.method, "Transfer")
    receipt = payment.receipts.filter(reversed_at__isnull=True).select_related("account").order_by("-verified_at", "-id").first()
    return "paid", method, receipt.account if receipt else None


def _item_multiplier(item):
    value = Decimal(getattr(item, "fulfilment_quantity_per_unit", None) or 1)
    return value if value > 0 else Decimal("1")


def _internal_quantity(item, customer_quantity):
    return (Decimal(customer_quantity or 0) * _item_multiplier(item)).quantize(Decimal("0.01"))


def _customer_available(item, internal_available):
    return (Decimal(internal_available or 0) / _item_multiplier(item)).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )


def _composition_scope_applies(intake, scope, item=None):
    scope = (scope or "all").strip()
    if scope == "all":
        return True
    if scope == "bulk":
        # A bulk pack ordered through Online (external storefront/API) is still a bulk fulfilment.
        return intake.sales_channel == CommerceIntake.CHANNEL_DISTRIBUTION or bool(item is not None and item.bulk_pack_id)
    return scope == (intake.service_mode or "").strip()


@transaction.atomic
def consume_intake_item_assembly(intake, item, *, user=None):
    """Consume snapshotted additional portion/package contents exactly once.

    The base FinishedGood is handled by the existing stock/production flow.
    Additional finished/procured goods and raw/packaging materials retain their
    own inventory identity and are released only when their fulfilment scope
    applies to this order.
    """
    locked_item = type(item).objects.select_for_update().get(pk=item.pk)
    if locked_item.assembly_consumed_at:
        return False
    reference = intake.public_number
    for row in locked_item.contents_snapshot or []:
        kind = row.get("kind")
        if kind == "base_product" or not _composition_scope_applies(intake, row.get("scope"), locked_item):
            continue
        try:
            quantity = Decimal(str(row.get("total_quantity") or 0))
        except Exception as exc:
            raise ValidationError("A saved package component has an invalid quantity.") from exc
        if quantity <= 0:
            continue
        if kind == "finished_good":
            component = FinishedGood.raw_objects.select_for_update().get(
                pk=row.get("id"), business=intake.business
            )
            from .checkout_services import available_physical_stock
            available = available_physical_stock(component)
            if quantity > available:
                raise ValidationError(
                    f"{component.name} is required by {locked_item.finished_good.name}, "
                    f"but only {available:.2f} {component.unit} is available."
                )
            unit_cost = _current_unit_cost(component, timezone.localdate())
            record_finished_good_movement(
                component, -quantity, StockMovement.FG_SALE,
                note=f"Component used in commerce package {reference}", reference=reference,
                affects_stock=True, unit_value=unit_cost,
            )
            consume_transferred_physical_stock(component, quantity)
        elif kind == "raw_material":
            material = RawMaterial.raw_objects.select_for_update().get(
                pk=row.get("id"), business=intake.business
            )
            if quantity > Decimal(material.stock or 0):
                raise ValidationError(
                    f"{material.name} is required by {locked_item.finished_good.name}, "
                    f"but only {material.stock:.3f} {material.usage_unit} is available."
                )
            record_raw_material_movement(
                material, -quantity, StockMovement.RAW_CONSUMPTION,
                note=f"Component used in commerce package {reference}", reference=reference,
                unit_value=material.cost_per_unit,
            )
    locked_item.assembly_consumed_at = timezone.now()
    locked_item.save(update_fields=["assembly_consumed_at"])
    audit(
        intake.business, user, "commerce_assembly_consume", intake,
        f"Package contents consumed for {intake.public_number} / {locked_item.finished_good.name}",
        {"intake_item_id": locked_item.pk},
    )
    item.assembly_consumed_at = locked_item.assembly_consumed_at
    return True


def consume_commerce_assembly_for_order(order, *, user=None):
    good_ids = set(order.items.values_list("finished_good_id", flat=True))
    intakes = CommerceIntake.raw_objects.filter(business=order.business).filter(
        models.Q(accepted_order=order) | models.Q(split_order=order)
    ).prefetch_related("items")
    consumed = 0
    for intake in intakes:
        for item in intake.items.all():
            if item.finished_good_id in good_ids and not item.assembly_consumed_at:
                consumed += int(consume_intake_item_assembly(intake, item, user=user))
    return consumed


def _make_production_order(intake, quantities, *, user=None):
    payment_status, payment_method, payment_account = _commerce_payment_snapshot(intake)
    order = Order.raw_objects.create(
        business=intake.business, created_by=user, date=timezone.localdate(),
        order_type=intake.sales_channel if intake.sales_channel in {"distribution", "online"} else "online",
        customer_name=intake.customer_name, customer_region="", customer_group="",
        customer_payment_status=payment_status,
        customer_payment_method=payment_method,
        customer_payment_account=payment_account,
        transaction_type=("paid" if payment_status == "paid" else "unpaid"),
        payment_method=payment_method,
        account=payment_account,
        unpaid_description=("" if payment_status == "paid" else "Customer receivable — payment to be recorded through Finance."),
        notes=f"Commerce {intake.public_number} — paid order intake awaiting production" if payment_status == "paid" else f"Commerce {intake.public_number} — made-to-order/pre-order demand",
    )
    for item, customer_qty in quantities:
        if customer_qty <= 0:
            continue
        if item.finished_good.is_purchased_for_resale:
            raise ValidationError(f"{item.finished_good.name} is purchased for resale and cannot enter a production order.")
        internal_qty = _internal_quantity(item, customer_qty)
        OrderItem.objects.create(
            order=order, finished_good=item.finished_good, batch_qty=Decimal("0"), piece_qty=internal_qty,
            production_batch_qty=Decimal("0"), production_piece_qty=internal_qty, discount=Decimal("0"),
            price=item.unit_price,
            commercial_quantity=customer_qty,
            commercial_unit=item.customer_unit or item.finished_good.unit,
            commercial_unit_price=item.unit_price,
            product_option_label=item.product_option_label,
            product_option_value=item.product_option_value,
            commerce_addon_name=item.individual_option.name if item.individual_option_id else "",
        )
        item.production_quantity = internal_qty
        item.save(update_fields=["production_quantity"])
    return order


def _make_physical_sale(intake, quantities, *, user=None):
    sale = Sale.raw_objects.create(
        business=intake.business, created_by=user, date=timezone.localdate(), customer=intake.customer_name,
        transaction_type="unpaid", unpaid_description=f"Commerce {intake.public_number} — payment pending/handled externally",
        source=(f"{intake.sales_channel}_order" if intake.sales_channel in {"distribution", "online"} else "walkin"),
        service_mode=intake.service_mode, table_reference=intake.table_reference,
    )
    for item, customer_qty in quantities:
        if customer_qty <= 0:
            continue
        good = item.finished_good
        internal_qty = _internal_quantity(item, customer_qty)
        locked = type(good).raw_objects.select_for_update().get(pk=good.pk, business=intake.business)
        from .checkout_services import available_physical_stock
        checkout = getattr(intake, "checkout_session", None)
        available = available_physical_stock(locked, exclude_checkout=checkout)
        if internal_qty > available:
            customer_available = _customer_available(item, available)
            raise ValidationError(
                f"{good.name} stock changed; only {customer_available:.2f} "
                f"{item.customer_unit or good.unit} is now available."
            )
        upb = good.units_per_batch or Decimal("1")
        batches = internal_qty // upb if upb else Decimal("0")
        pieces = internal_qty - batches * upb
        price = item.unit_price
        unit_cost = _current_unit_cost(good, sale.date)
        SaleItem.objects.create(
            sale=sale, finished_good=good, batch_qty=batches, piece_qty=pieces, discount=0,
            price=price, unit_cost=unit_cost,
            commercial_quantity=customer_qty,
            commercial_unit=item.customer_unit or good.unit,
            commercial_unit_price=item.unit_price,
            product_option_label=item.product_option_label,
            product_option_value=item.product_option_value,
            commerce_addon_name=item.individual_option.name if item.individual_option_id else "",
        )
        record_finished_good_movement(good, -internal_qty, StockMovement.FG_SALE, note=f"Commerce stock order {intake.public_number}", reference=intake.public_number, affects_stock=True, unit_value=unit_cost)
        consume_transferred_physical_stock(good, internal_qty)
        item.accepted_stock_quantity = internal_qty
        item.save(update_fields=["accepted_stock_quantity"])
        consume_intake_item_assembly(intake, item, user=user)
    return sale


@transaction.atomic
def accept_intake(intake, *, user=None):
    intake = CommerceIntake.raw_objects.select_for_update().prefetch_related(
        "items__finished_good", "items__storefront_product"
    ).get(pk=intake.pk)
    if intake.status not in {CommerceIntake.STATUS_PENDING, CommerceIntake.STATUS_AWAITING_PREORDER}:
        raise ValidationError("Only pending commerce requests can be accepted.")

    items = list(intake.items.select_related("finished_good"))
    stock_items = [item for item in items if item.fulfilment_source in {
        item.FULFILMENT_STOCK, item.FULFILMENT_PROCURED,
    }]
    made_items = [item for item in items if item.fulfilment_source == item.FULFILMENT_MADE_TO_ORDER]

    sale = None
    order = None
    if stock_items:
        sale = _make_physical_sale(
            intake, [(item, item.requested_quantity) for item in stock_items], user=user
        )
    if made_items:
        if any(item.finished_good.is_purchased_for_resale for item in made_items):
            raise ValidationError("Purchased-for-resale products cannot be manufactured.")
        order = _make_production_order(
            intake, [(item, item.requested_quantity) for item in made_items], user=user
        )

    if not sale and not order:
        raise ValidationError("This order has no fulfilment-ready products.")

    intake.accepted_sale = sale
    intake.accepted_order = order
    intake.split_order = None
    if order:
        intake.fulfilment_state = CommerceIntake.FULFIL_PARTIAL if sale else CommerceIntake.FULFIL_PENDING
        intake.status = CommerceIntake.STATUS_ACCEPTED
    else:
        intake.fulfilment_state = CommerceIntake.FULFIL_COMPLETE
        intake.status = CommerceIntake.STATUS_FULFILLED
    intake.rejection_reason = ""
    intake.save(update_fields=[
        "accepted_sale", "accepted_order", "split_order", "fulfilment_state",
        "status", "rejection_reason", "updated_at",
    ])

    if sale:
        from .payment_services import sync_payment_receipts_to_sales
        sync_payment_receipts_to_sales(intake)
    from .checkout_services import release_checkout_reservation_for_intake
    release_checkout_reservation_for_intake(intake)
    audit(
        intake.business, user, "commerce_accept", intake,
        f"{intake.public_number} accepted with per-line fulfilment",
        {
            "order_id": getattr(order, "pk", None),
            "sale_id": getattr(sale, "pk", None),
            "stock_lines": len(stock_items),
            "made_to_order_lines": len(made_items),
        },
    )
    return intake


@transaction.atomic
def auto_process_paid_intake(intake, *, user=None):
    """Commit a fully-paid intake into stock fulfilment or Production.

    The operation is intentionally idempotent: already accepted/fulfilled
    intakes are returned unchanged, partial/unpaid intakes are ignored, and
    only a pending fully-paid intake is handed to the existing acceptance
    engine. This keeps the manual Commerce action available only as an
    exception/recovery path.
    """
    intake = CommerceIntake.raw_objects.select_for_update().get(pk=intake.pk, business=intake.business)
    if intake.payment_state != CommerceIntake.PAYMENT_CONFIRMED:
        return intake, False
    if intake.status in {CommerceIntake.STATUS_ACCEPTED, CommerceIntake.STATUS_FULFILLED}:
        return intake, False
    if intake.status != CommerceIntake.STATUS_PENDING:
        return intake, False
    processed = accept_intake(intake, user=user)
    if processed.status not in {CommerceIntake.STATUS_ACCEPTED, CommerceIntake.STATUS_FULFILLED}:
        raise ValidationError(
            processed.rejection_reason or "The paid order needs staff review before fulfilment can continue."
        )
    return processed, True


def attempt_auto_process_paid_intake(intake, *, user=None):
    """Best-effort automatic fulfilment for an already-created intake.

    Payment must never be lost because an operational stock/production handoff
    needs review. The nested savepoint rolls back only the fulfilment attempt;
    the confirmed payment remains intact and the intake stays visible for staff.
    """
    try:
        with transaction.atomic():
            return auto_process_paid_intake(intake, user=user)
    except Exception as exc:
        logger.exception("Automatic commerce fulfilment failed for intake %s", intake.pk)
        message = str(exc).strip() or "Automatic fulfilment needs staff review."
        CommerceIntake.raw_objects.filter(pk=intake.pk, business=intake.business).update(
            rejection_reason=f"Payment confirmed. Staff review required: {message}"[:255]
        )
        audit(
            intake.business, user, "commerce_auto_fulfil_review", intake,
            f"{intake.public_number} needs staff review after payment confirmation",
            {"reason": message[:500]},
        )
        queue_commerce_notification(
            business=intake.business,
            event_type=CommerceNotification.EVENT_PAYMENT_REVIEW,
            title="Paid order needs fulfilment review",
            message=f"{intake.public_number} is paid, but automatic fulfilment needs staff review.",
            target_url="/commerce/",
            dedupe_key=f"intake:{intake.public_id}:auto-review",
        )
        return CommerceIntake.raw_objects.get(pk=intake.pk, business=intake.business), False


@transaction.atomic
def switch_intake_to_preorder(intake, *, user=None):
    intake = CommerceIntake.raw_objects.select_for_update().prefetch_related("items__finished_good", "items__individual_option").get(pk=intake.pk)
    if intake.status != CommerceIntake.STATUS_AWAITING_PREORDER:
        raise ValidationError("This request is not waiting for a Pre-order decision.")
    if not intake.business.uses_production:
        raise ValidationError("Pre-order production is not available for this service profile.")
    for item in intake.items.all():
        if item.bulk_pack_id:
            raise ValidationError("Bulk-pack orders cannot be converted into the standard Online channel. Start a new Online checkout instead.")
        if item.individual_option_id:
            option = item.individual_option
            if not option.active or not option.online_enabled or option.online_price is None:
                raise ValidationError(f"{option.name} is not available through the Online channel.")
            item.unit_price = option.online_price
        else:
            if not item.storefront_product.allow_online_order:
                raise ValidationError(f"{item.finished_good.name} does not allow Pre-order.")
            item.unit_price = item.finished_good.selling_price_for("online")
        item.save(update_fields=["unit_price"])
    intake.ordering_mode = CommerceIntake.MODE_PREORDER
    intake.sales_channel = CommerceIntake.CHANNEL_ONLINE
    intake.status = CommerceIntake.STATUS_PENDING
    intake.rejection_reason = ""
    intake.save(update_fields=["ordering_mode", "sales_channel", "status", "rejection_reason", "updated_at"])
    return accept_intake(intake, user=user)
