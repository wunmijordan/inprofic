from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from inventory.models import StockMovement
from inventory.services import consume_transferred_physical_stock, record_finished_good_movement
from production.models import Order, OrderItem, ProductionCostSnapshot
from sales.models import Sale, SaleItem
from core.services import audit
from core.verticals import vertical_config
from .models import CommerceIntake, CommerceNotification, CommerceSettings
from .notification_services import queue_commerce_notification



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
    return {
        CommerceIntake.CHANNEL_PHYSICAL_STORE: product.allow_stock_order,
        CommerceIntake.CHANNEL_ONLINE: product.allow_online_order,
        CommerceIntake.CHANNEL_DISTRIBUTION: product.allow_distribution_order,
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
    """Resolve the new sales-channel contract while accepting legacy callers."""
    channel = (sales_channel or "").strip().lower()
    legacy_mode = (ordering_mode or "").strip().lower()
    if not channel:
        if legacy_mode == CommerceIntake.MODE_PREORDER:
            channel = CommerceIntake.CHANNEL_ONLINE
        elif legacy_mode == CommerceIntake.MODE_STOCK:
            channel = CommerceIntake.CHANNEL_PHYSICAL_STORE
        elif legacy_mode in dict(CommerceIntake.CHANNEL_CHOICES):
            channel = legacy_mode
    if channel not in dict(CommerceIntake.CHANNEL_CHOICES):
        raise ValidationError("Choose physical_store, online, or distribution as the order mode.")
    return channel, _fulfilment_mode(business, channel)


@transaction.atomic
def create_intake(*, business, source, ordering_mode=None, sales_channel=None, customer, items, idempotency_key="", external_order_id="", service_mode="", table_reference=""):
    sales_channel, ordering_mode = resolve_channel_and_fulfilment(
        business=business, sales_channel=sales_channel, ordering_mode=ordering_mode
    )
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
        minimum = _channel_minimum(product, sales_channel)
        if sales_channel == CommerceIntake.CHANNEL_DISTRIBUTION and qty < minimum:
            labels = vertical_config(business)["commerce_channels"]
            alternatives = [
                {"code": code, "label": labels[code]}
                for code in (CommerceIntake.CHANNEL_PHYSICAL_STORE, CommerceIntake.CHANNEL_ONLINE)
                if _channel_allowed(product, code)
            ]
            raise ChannelMinimumError(
                f"{product.display_name} requires at least {minimum} {product.finished_good.unit} for {labels[sales_channel]} pricing.",
                alternatives=alternatives,
            )
        if qty < minimum or (product.max_quantity is not None and qty > product.max_quantity):
            raise ValidationError(f"Quantity for {product.display_name} is outside the permitted range.")
        intake.items.create(
            storefront_product=product, finished_good=product.finished_good,
            requested_quantity=qty, unit_price=product.finished_good.selling_price_for(sales_channel),
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


def _make_production_order(intake, quantities, *, user=None):
    order = Order.raw_objects.create(
        business=intake.business, created_by=user, date=timezone.localdate(),
        order_type=intake.sales_channel if intake.sales_channel in {"distribution", "online"} else "online",
        customer_name=intake.customer_name, customer_region="", customer_group="",
        customer_payment_status="unpaid", customer_payment_method="Transfer",
        notes=f"Commerce {intake.public_number} — made-to-order/pre-order demand",
    )
    for item, qty in quantities:
        if qty <= 0:
            continue
        if item.finished_good.is_purchased_for_resale:
            raise ValidationError(f"{item.finished_good.name} is purchased for resale and cannot enter a production order.")
        OrderItem.objects.create(
            order=order, finished_good=item.finished_good, batch_qty=Decimal("0"), piece_qty=qty,
            production_batch_qty=Decimal("0"), production_piece_qty=qty, discount=Decimal("0"),
            price=item.unit_price,
        )
        item.production_quantity = qty
        item.save(update_fields=["production_quantity"])
    return order


def _make_physical_sale(intake, quantities, *, user=None):
    sale = Sale.raw_objects.create(
        business=intake.business, created_by=user, date=timezone.localdate(), customer=intake.customer_name,
        transaction_type="unpaid", unpaid_description=f"Commerce {intake.public_number} — payment pending/handled externally",
        source=(f"{intake.sales_channel}_order" if intake.sales_channel in {"distribution", "online"} else "walkin"),
        service_mode=intake.service_mode, table_reference=intake.table_reference,
    )
    for item, qty in quantities:
        if qty <= 0: continue
        good = item.finished_good
        locked = type(good).raw_objects.select_for_update().get(pk=good.pk, business=intake.business)
        from .checkout_services import available_physical_stock
        checkout = getattr(intake, "checkout_session", None)
        available = available_physical_stock(locked, exclude_checkout=checkout)
        if qty > available:
            raise ValidationError(f"{good.name} stock changed; only {available:.2f} is now available.")
        upb = good.units_per_batch or Decimal("1")
        batches = qty // upb if upb else Decimal("0")
        pieces = qty - batches * upb
        price = item.unit_price
        unit_cost = _current_unit_cost(good, sale.date)
        SaleItem.objects.create(sale=sale, finished_good=good, batch_qty=batches, piece_qty=pieces, discount=0, price=price, unit_cost=unit_cost)
        record_finished_good_movement(good, -qty, StockMovement.FG_SALE, note=f"Commerce stock order {intake.public_number}", reference=intake.public_number, affects_stock=True, unit_value=unit_cost)
        consume_transferred_physical_stock(good, qty)
        item.accepted_stock_quantity = qty
        item.save(update_fields=["accepted_stock_quantity"])
    return sale


@transaction.atomic
def accept_intake(intake, *, user=None):
    intake = CommerceIntake.raw_objects.select_for_update().prefetch_related("items__finished_good", "items__storefront_product").get(pk=intake.pk)
    if intake.status not in {CommerceIntake.STATUS_PENDING, CommerceIntake.STATUS_AWAITING_PREORDER}:
        raise ValidationError("Only pending commerce requests can be accepted.")
    settings, _ = CommerceSettings.raw_objects.get_or_create(business=intake.business, defaults={"created_by": user})
    items = list(intake.items.select_related("finished_good"))
    if intake.ordering_mode == CommerceIntake.MODE_PREORDER:
        made_items = [item for item in items if item.finished_good.is_made_in_house]
        resale_items = [item for item in items if item.finished_good.is_purchased_for_resale]
        order = None
        sale = None
        if resale_items:
            # Resale stock remains stock even when another item in the same
            # basket is made-to-order. This supports mixed baskets such as a
            # prepared meal plus a bottled drink without manufacturing the drink.
            sale = _make_physical_sale(
                intake, [(item, item.requested_quantity) for item in resale_items], user=user
            )
        if made_items:
            order = _make_production_order(
                intake, [(item, item.requested_quantity) for item in made_items], user=user
            )
        if not sale and not order:
            raise ValidationError("This order has no fulfilment-ready products.")
        intake.accepted_sale = sale
        intake.accepted_order = order
        intake.fulfilment_state = CommerceIntake.FULFIL_COMPLETE
        intake.status = CommerceIntake.STATUS_ACCEPTED
        intake.save(update_fields=["accepted_sale", "accepted_order", "fulfilment_state", "status", "updated_at"])
        if sale:
            from .payment_services import sync_payment_receipts_to_sales
            sync_payment_receipts_to_sales(intake)
        from core.services import audit
        from .checkout_services import release_checkout_reservation_for_intake
        release_checkout_reservation_for_intake(intake)
        audit(
            intake.business, user, "commerce_accept", intake,
            f"{intake.public_number} accepted with source-aware fulfilment",
            {"order_id": getattr(order, "pk", None), "sale_id": getattr(sale, "pk", None)},
        )
        return intake

    from .checkout_services import available_physical_stock
    checkout = getattr(intake, "checkout_session", None)
    availability = [
        (item, min(item.requested_quantity, available_physical_stock(item.finished_good, exclude_checkout=checkout)))
        for item in items
    ]
    shortages = [(item, item.requested_quantity - available) for item, available in availability if item.requested_quantity > available]
    policy = settings.insufficient_stock_policy
    if shortages and policy == CommerceSettings.POLICY_REJECT:
        intake.status = CommerceIntake.STATUS_REJECTED
        intake.rejection_reason = "Insufficient sellable stock for one or more products."
        intake.save(update_fields=["status", "rejection_reason", "updated_at"])
        from core.services import audit
        audit(intake.business, user, "commerce_reject", intake, f"{intake.public_number} rejected for insufficient stock", {})
        return intake
    if shortages and policy == CommerceSettings.POLICY_INVITE:
        intake.status = CommerceIntake.STATUS_AWAITING_PREORDER
        intake.rejection_reason = "Insufficient stock. Customer may confirm a Pre-order instead."
        intake.save(update_fields=["status", "rejection_reason", "updated_at"])
        from core.services import audit
        audit(intake.business, user, "commerce_preorder_invite", intake, f"{intake.public_number} invited to switch to Pre-order", {})
        return intake

    sale_quantities = [(item, available) for item, available in availability if available > 0]
    if sale_quantities:
        intake.accepted_sale = _make_physical_sale(intake, sale_quantities, user=user)
    if shortages and policy == CommerceSettings.POLICY_SPLIT:
        producible_shortages = [
            (item, qty) for item, qty in shortages
            if item.finished_good.is_made_in_house
        ]
        resale_shortages = [
            (item, qty) for item, qty in shortages
            if item.finished_good.is_purchased_for_resale
        ]
        if producible_shortages:
            intake.split_order = _make_production_order(intake, producible_shortages, user=user)
        if resale_shortages:
            intake.rejection_reason = "Purchased resale items were reduced to available supplier stock; they cannot be manufactured."
        intake.fulfilment_state = CommerceIntake.FULFIL_PARTIAL
    elif shortages and policy == CommerceSettings.POLICY_REDUCE:
        intake.fulfilment_state = CommerceIntake.FULFIL_PARTIAL
        intake.rejection_reason = "Quantity reduced to currently available stock."
    else:
        intake.fulfilment_state = CommerceIntake.FULFIL_COMPLETE
    if not intake.accepted_sale and not intake.split_order:
        intake.status = CommerceIntake.STATUS_REJECTED
        intake.rejection_reason = "No requested quantity is currently available."
    else:
        intake.status = CommerceIntake.STATUS_ACCEPTED
    intake.save(update_fields=["accepted_sale", "split_order", "status", "fulfilment_state", "rejection_reason", "updated_at"])
    # A customer may settle a headless order before staff accepts it. Allocate
    # the already-audited receipt now that a downstream Sale exists; do not post
    # a second cash movement.
    if intake.accepted_sale_id:
        from .payment_services import sync_payment_receipts_to_sales
        sync_payment_receipts_to_sales(intake)
    from core.services import audit
    from .checkout_services import release_checkout_reservation_for_intake
    release_checkout_reservation_for_intake(intake)
    audit(intake.business, user, "commerce_accept", intake, f"{intake.public_number} processed from sellable stock", {"sale_id": intake.accepted_sale_id, "split_order_id": intake.split_order_id, "policy": policy})
    return intake


@transaction.atomic
def switch_intake_to_preorder(intake, *, user=None):
    intake = CommerceIntake.raw_objects.select_for_update().prefetch_related("items__finished_good").get(pk=intake.pk)
    if intake.status != CommerceIntake.STATUS_AWAITING_PREORDER:
        raise ValidationError("This request is not waiting for a Pre-order decision.")
    if not intake.business.uses_production:
        raise ValidationError("Pre-order production is not available for this service profile.")
    for item in intake.items.all():
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
