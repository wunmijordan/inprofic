from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.services import audit
from core.verticals import vertical_config
from inventory.models import FinishedGood

from .models import (
    CommerceCheckoutItem,
    CommerceCheckoutSession,
    CommerceIntake,
    CommerceIntakeItem,
    CommerceNotification,
    CommercePayment,
    CommercePaymentReceipt,
    CommerceSettings,
    StorefrontProduct,
)
from .notification_services import queue_commerce_notification

logger = logging.getLogger(__name__)
from .services import (
    ChannelMinimumError,
    _channel_allowed,
    _channel_minimum,
    auto_process_paid_intake,
    resolve_channel_and_fulfilment,
)


class CheckoutAvailabilityError(ValidationError):
    def __init__(self, message, *, code="insufficient_stock", suggested_order_modes=None, details=None):
        super().__init__(message)
        self.code = code
        self.suggested_order_modes = suggested_order_modes or []
        self.details = details or []


def _decimal_quantity(value):
    try:
        qty = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Enter a valid quantity.") from exc
    if qty <= 0:
        raise ValidationError("Quantity must be greater than zero.")
    return qty


def _active_reservation_filter(now=None):
    now = now or timezone.now()
    # Materialized sessions retain their hold until the downstream stock sale is
    # actually created. Awaiting/paid sessions hold only until the reservation
    # expiry time.
    from django.db.models import Q
    return (
        Q(checkout__reservation_released_at__isnull=True)
        & (
            Q(checkout__status=CommerceCheckoutSession.STATUS_MATERIALIZED)
            | Q(
                checkout__status__in=[
                    CommerceCheckoutSession.STATUS_AWAITING_PAYMENT,
                    CommerceCheckoutSession.STATUS_PAID,
                ],
                checkout__reservation_expires_at__gt=now,
            )
        )
    )


def reserved_stock_for_good(good, *, exclude_checkout=None, now=None):
    qs = CommerceCheckoutItem.objects.filter(
        checkout__business=good.business,
        finished_good=good,
        reserved_stock_quantity__gt=0,
    ).filter(_active_reservation_filter(now))
    if exclude_checkout is not None:
        checkout_id = getattr(exclude_checkout, "pk", exclude_checkout)
        qs = qs.exclude(checkout_id=checkout_id)
    return qs.aggregate(total=Sum("reserved_stock_quantity"))["total"] or Decimal("0")


def available_physical_stock(good, *, exclude_checkout=None, now=None):
    physical = Decimal(good.physical_saleable_stock or 0)
    reserved = reserved_stock_for_good(good, exclude_checkout=exclude_checkout, now=now)
    return max(Decimal("0"), physical - reserved)


def _mode_alternatives(product, business, quantity):
    labels = vertical_config(business)["commerce_channels"]
    alternatives = []
    for code in (
        CommerceIntake.CHANNEL_PHYSICAL_STORE,
        CommerceIntake.CHANNEL_ONLINE,
        CommerceIntake.CHANNEL_DISTRIBUTION,
    ):
        if not _channel_allowed(product, code):
            continue
        minimum = _channel_minimum(product, code)
        if quantity < minimum:
            continue
        alternatives.append({
            "code": code,
            "label": labels.get(code, dict(CommerceIntake.CHANNEL_CHOICES).get(code, code)),
        })
    return alternatives


def expire_checkout_if_needed(checkout, *, now=None):
    now = now or timezone.now()
    if (
        checkout.status == CommerceCheckoutSession.STATUS_AWAITING_PAYMENT
        and checkout.reservation_expires_at
        and checkout.reservation_expires_at <= now
    ):
        checkout.status = CommerceCheckoutSession.STATUS_EXPIRED
        checkout.reservation_released_at = checkout.reservation_released_at or now
        checkout.save(update_fields=["status", "reservation_released_at", "updated_at"])
    return checkout


@transaction.atomic
def cancel_unpaid_checkout(checkout, *, reason=""):
    """Release an unpaid reservation after a staff-side initiation failure.

    Never cancels a checkout that has a verified receipt. This is intentionally
    narrow so a transient POS/gateway failure cannot strand stock while still
    preserving any payment that actually reached the ledger.
    """
    checkout = CommerceCheckoutSession.raw_objects.select_for_update().get(
        pk=checkout.pk, business=checkout.business
    )
    if checkout.status != CommerceCheckoutSession.STATUS_AWAITING_PAYMENT:
        return checkout, False
    has_receipt = CommercePaymentReceipt.raw_objects.filter(
        business=checkout.business, payment__checkout=checkout, reversed_at__isnull=True
    ).exists()
    if has_receipt:
        return checkout, False
    now = timezone.now()
    checkout.status = CommerceCheckoutSession.STATUS_CANCELLED
    checkout.reservation_released_at = checkout.reservation_released_at or now
    checkout.materialization_error = (reason or "").strip()[:500]
    checkout.save(update_fields=[
        "status", "reservation_released_at", "materialization_error", "updated_at"
    ])
    CommercePayment.raw_objects.filter(
        business=checkout.business, checkout=checkout, status__in=CommercePayment.ACTIVE_STATUSES
    ).update(status=CommercePayment.STATUS_CANCELLED)
    return checkout, True


@transaction.atomic
def create_checkout(
    *, business, source, customer, items, idempotency_key, external_order_id="",
    order_mode=None, ordering_mode=None, service_mode="", table_reference="",
):
    """Validate and snapshot a basket without creating CommerceIntake.

    For stock orders, FinishedGood rows are locked while the reservation is
    calculated so concurrent INPROFIC checkouts cannot reserve the same units.
    """
    idempotency_key = (idempotency_key or "").strip()
    if not idempotency_key:
        raise ValidationError("Idempotency-Key header is required.")
    if len(idempotency_key) > 120:
        raise ValidationError("Idempotency-Key cannot exceed 120 characters.")

    existing = CommerceCheckoutSession.raw_objects.filter(
        business=business, source=source, idempotency_key=idempotency_key
    ).prefetch_related("items__storefront_product", "items__finished_good").first()
    if existing:
        return expire_checkout_if_needed(existing), False

    sales_channel, fulfilment_mode = resolve_channel_and_fulfilment(
        business=business, sales_channel=order_mode, ordering_mode=ordering_mode
    )
    customer = customer or {}
    customer_name = (customer.get("name") or "").strip()
    if not customer_name:
        raise ValidationError("Customer name is required.")
    if not items:
        raise ValidationError("Add at least one product.")

    product_ids = [row["storefront_product"].pk for row in items]
    if len(product_ids) != len(set(product_ids)):
        raise ValidationError("Submit each product only once per checkout.")

    # Lock every relevant FinishedGood in a deterministic order. All INPROFIC
    # checkout reservation writers use this same lock boundary.
    good_ids = sorted({row["storefront_product"].finished_good_id for row in items})
    locked_goods = {
        good.pk: good
        for good in FinishedGood.raw_objects.select_for_update().filter(
            business=business, pk__in=good_ids
        ).order_by("pk")
    }
    if len(locked_goods) != len(good_ids):
        raise ValidationError("One or more products do not belong to this business.")

    settings, _ = CommerceSettings.raw_objects.get_or_create(
        business=business, defaults={"created_by": None}
    )
    from .payment_services import payment_configuration
    payment_config = payment_configuration(business)
    currency = (payment_config.currency or "NGN").upper()
    now = timezone.now()
    expires_at = now + timedelta(minutes=settings.checkout_reservation_minutes)
    prepared = []
    total = Decimal("0")

    for row in items:
        product = row["storefront_product"]
        qty = _decimal_quantity(row.get("quantity"))
        if product.business_id != business.pk or not product.published:
            raise ValidationError("One of the selected products is not available for this storefront.")
        if not _channel_allowed(product, sales_channel):
            raise ValidationError(f"{product.display_name} is not available through the selected order mode.")
        minimum = _channel_minimum(product, sales_channel)
        if qty < minimum:
            raise ChannelMinimumError(
                f"{product.display_name} requires at least {minimum} {product.finished_good.unit} for this order mode.",
                alternatives=_mode_alternatives(product, business, qty),
            )
        if product.max_quantity is not None and qty > product.max_quantity:
            raise ValidationError(
                f"Quantity for {product.display_name} exceeds the maximum of {product.max_quantity}."
            )

        good = locked_goods[product.finished_good_id]
        price = Decimal(good.selling_price_for(sales_channel)).quantize(Decimal("0.01"))
        payable_qty = qty
        reserve_qty = Decimal("0")
        # Bought-in resale products are always fulfilled from purchased stock,
        # even inside a production vertical and even when the storefront sales
        # channel would normally mean made-to-order production.
        stock_fulfilment = (
            fulfilment_mode == CommerceIntake.MODE_STOCK
            or good.source_type == FinishedGood.SOURCE_PURCHASED_FOR_RESALE
        )
        production_qty = qty if (not stock_fulfilment and fulfilment_mode == CommerceIntake.MODE_PREORDER) else Decimal("0")

        if stock_fulfilment:
            available = available_physical_stock(good, now=now)
            reserve_qty = min(qty, available)
            shortage = qty - reserve_qty
            if shortage > 0:
                policy = settings.insufficient_stock_policy
                details = [{
                    "product_id": str(product.public_id),
                    "product": product.display_name,
                    "requested": str(qty),
                    "available_now": str(available),
                    "shortfall": str(shortage),
                }]
                if policy == CommerceSettings.POLICY_REDUCE and reserve_qty > 0:
                    payable_qty = reserve_qty
                elif (
                    policy == CommerceSettings.POLICY_SPLIT
                    and business.uses_production
                    and good.source_type == FinishedGood.SOURCE_MADE_IN_HOUSE
                    and product.allow_online_order
                ):
                    production_qty = shortage
                elif policy == CommerceSettings.POLICY_INVITE:
                    suggestions = [m for m in _mode_alternatives(product, business, qty) if m["code"] != sales_channel]
                    raise CheckoutAvailabilityError(
                        "Insufficient stock. The customer may switch to a Pre-order mode before paying.",
                        suggested_order_modes=suggestions,
                        details=details,
                    )
                else:
                    raise CheckoutAvailabilityError(
                        "Insufficient sellable stock for one or more products.",
                        suggested_order_modes=_mode_alternatives(product, business, qty),
                        details=details,
                    )
            if reserve_qty <= 0 and payable_qty <= 0:
                raise CheckoutAvailabilityError(
                    f"{product.display_name} is currently out of sellable stock.",
                    suggested_order_modes=_mode_alternatives(product, business, qty),
                    details=[{"product_id": str(product.public_id), "requested": str(qty), "available_now": "0"}],
                )

        line_total = (payable_qty * price).quantize(Decimal("0.01"))
        total += line_total
        prepared.append((product, good, qty, payable_qty, reserve_qty, production_qty, price))

    total = total.quantize(Decimal("0.01"))
    if total <= 0:
        raise ValidationError("This checkout has no payable amount.")
    # Preserve stock-mode checkouts so an insufficient-stock SPLIT continues
    # through the existing sale + production-shortfall workflow. A channel that
    # is normally pre-order becomes stock only when every item is bought-in
    # resale stock and therefore has nothing to manufacture.
    effective_fulfilment_mode = fulfilment_mode
    if fulfilment_mode == CommerceIntake.MODE_PREORDER and not any(row[5] > 0 for row in prepared):
        effective_fulfilment_mode = CommerceIntake.MODE_STOCK

    try:
        checkout = CommerceCheckoutSession.raw_objects.create(
            business=business,
            source=source,
            external_order_id=(external_order_id or "").strip(),
            idempotency_key=idempotency_key,
            ordering_mode=effective_fulfilment_mode,
            sales_channel=sales_channel,
            customer_name=customer_name,
            customer_phone=(customer.get("phone") or "").strip(),
            customer_email=(customer.get("email") or "").strip(),
            customer_address=(customer.get("address") or "").strip(),
            service_mode=(service_mode or "").strip(),
            table_reference=(table_reference or "").strip(),
            currency=currency,
            amount=total,
            reservation_expires_at=expires_at,
        )
    except IntegrityError:
        # Race-safe retry of the same idempotent request.
        return CommerceCheckoutSession.raw_objects.get(
            business=business, source=source, idempotency_key=idempotency_key
        ), False

    CommerceCheckoutItem.objects.bulk_create([
        CommerceCheckoutItem(
            checkout=checkout,
            storefront_product=product,
            finished_good=good,
            requested_quantity=qty,
            payable_quantity=payable_qty,
            reserved_stock_quantity=reserve_qty,
            production_quantity=production_qty,
            unit_price=price,
        )
        for product, good, qty, payable_qty, reserve_qty, production_qty, price in prepared
    ])
    audit(
        business, None, "commerce_checkout_create", checkout,
        f"Checkout {checkout.public_id} for {checkout.customer_name} created before intake",
        {
            "customer_name": checkout.customer_name,
            "source": source,
            "sales_channel": sales_channel,
            "amount": str(total),
            "expires_at": expires_at.isoformat(),
        },
    )
    # A staff POS checkout is an in-progress counter transaction, not a new
    # customer checkout that needs to alert the main app before payment.
    # Deferring POS activity until settlement also prevents the Web Push
    # dispatcher from competing with the immediate cash-settlement write on
    # SQLite. Public/external checkout notifications keep their existing flow.
    if source != CommerceIntake.SOURCE_STAFF_POS:
        source_label = dict(CommerceIntake.SOURCE_CHOICES).get(source, "commerce channel")
        channel_label = vertical_config(business)["commerce_channels"].get(sales_channel, sales_channel)
        queue_commerce_notification(
            business=business,
            event_type=CommerceNotification.EVENT_CHECKOUT_RECEIVED,
            title=f"New checkout from {source_label}",
            message=(
                f"{checkout.customer_name} selected {channel_label} for "
                f"{business.currency_symbol}{total:,.2f}. Payment is still pending."
            ),
            target_url="/commerce/",
            dedupe_key=f"checkout:{checkout.public_id}:received",
        )
    return checkout, True


def checkout_total_paid(checkout):
    return CommercePaymentReceipt.raw_objects.filter(
        business=checkout.business,
        payment__checkout=checkout,
        reversed_at__isnull=True,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")


def serialize_checkout(checkout):
    expire_checkout_if_needed(checkout)
    intake = checkout.materialized_intake
    return {
        "checkout_id": str(checkout.public_id),
        "status": checkout.status,
        "order_mode": checkout.sales_channel,
        "fulfilment_mode": checkout.ordering_mode,
        "amount": f"{checkout.amount:.2f}",
        "currency": checkout.currency,
        "reservation_expires_at": checkout.reservation_expires_at.isoformat() if checkout.reservation_expires_at else None,
        "order_id": str(intake.public_id) if intake else None,
        "order_number": intake.public_number if intake else None,
        "payment_status": (
            CommercePayment.raw_objects.filter(business=checkout.business)
            .filter(
                Q(checkout=checkout)
                | Q(intake_id=checkout.materialized_intake_id)
            )
            .exclude(status=CommercePayment.STATUS_CANCELLED)
            .order_by("-created_at", "-id")
            .values_list("status", flat=True)
            .first()
        ),
        "order": ({
            "id": str(intake.public_id),
            "number": intake.public_number,
            "status": intake.status,
            "payment_state": intake.payment_state,
            "fulfilment_state": intake.fulfilment_state,
        } if intake else None),
        "materialization_error": checkout.materialization_error if checkout.status == CommerceCheckoutSession.STATUS_PAID_REVIEW else "",
        "items": [
            {
                "product_id": str(row.storefront_product.public_id),
                "name": row.storefront_product.display_name,
                "requested_quantity": str(row.requested_quantity),
                "payable_quantity": str(row.payable_quantity),
                "reserved_stock_quantity": str(row.reserved_stock_quantity),
                "production_quantity": str(row.production_quantity),
                "unit_price": f"{row.unit_price:.2f}",
                "line_total": f"{row.line_total:.2f}",
            }
            for row in checkout.items.select_related("storefront_product").all()
        ],
    }


@transaction.atomic
def materialize_paid_checkout(checkout, *, actor=None, allow_expired_recovery=False):
    """Create exactly one intake for a fully paid checkout.

    This function is safe to retry. It keeps the stock reservation attached to
    the materialized checkout until the intake is operationally accepted and
    the physical stock mutation occurs.
    """
    checkout = CommerceCheckoutSession.raw_objects.select_for_update().prefetch_related(
        "items__storefront_product", "items__finished_good"
    ).get(pk=checkout.pk, business=checkout.business)
    if checkout.materialized_intake_id:
        return checkout.materialized_intake, False

    paid = checkout_total_paid(checkout)
    if paid < checkout.amount:
        raise ValidationError("Checkout cannot create an order until full payment is verified.")

    now = timezone.now()
    late = bool(checkout.reservation_expires_at and checkout.reservation_expires_at <= now)
    if late and not allow_expired_recovery:
        checkout.status = CommerceCheckoutSession.STATUS_PAID_REVIEW
        checkout.paid_at = checkout.paid_at or now
        checkout.reservation_released_at = checkout.reservation_released_at or now
        checkout.materialization_error = "Payment was verified after the checkout reservation expired. Staff review is required before creating the order."
        checkout.save(update_fields=[
            "status", "paid_at", "reservation_released_at", "materialization_error", "updated_at"
        ])
        return None, False

    stock_items = [row for row in checkout.items.all() if row.reserved_stock_quantity > 0]
    for row in sorted(stock_items, key=lambda item: item.finished_good_id):
        good = FinishedGood.raw_objects.select_for_update().get(
            pk=row.finished_good_id, business=checkout.business
        )
        available = available_physical_stock(good, exclude_checkout=checkout, now=now)
        if row.reserved_stock_quantity > available:
            checkout.status = CommerceCheckoutSession.STATUS_PAID_REVIEW
            checkout.paid_at = checkout.paid_at or now
            checkout.reservation_released_at = checkout.reservation_released_at or now
            checkout.materialization_error = (
                f"{good.name} changed after checkout; {row.reserved_stock_quantity} was reserved but only {available} can now be safely allocated."
            )
            checkout.save(update_fields=[
                "status", "paid_at", "reservation_released_at", "materialization_error", "updated_at"
            ])
            return None, False

    checkout.status = CommerceCheckoutSession.STATUS_PAID
    checkout.paid_at = checkout.paid_at or now
    checkout.materialization_error = ""
    checkout.save(update_fields=["status", "paid_at", "materialization_error", "updated_at"])

    intake = CommerceIntake.raw_objects.create(
        business=checkout.business,
        created_by=actor,
        source=checkout.source,
        external_order_id=checkout.external_order_id,
        idempotency_key=f"checkout:{checkout.public_id}",
        ordering_mode=checkout.ordering_mode,
        sales_channel=checkout.sales_channel,
        customer_name=checkout.customer_name,
        customer_phone=checkout.customer_phone,
        customer_email=checkout.customer_email,
        customer_address=checkout.customer_address,
        service_mode=checkout.service_mode,
        table_reference=checkout.table_reference,
        payment_state=CommerceIntake.PAYMENT_CONFIRMED,
    )
    CommerceIntakeItem.objects.bulk_create([
        CommerceIntakeItem(
            intake=intake,
            storefront_product=row.storefront_product,
            finished_good=row.finished_good,
            # Reduce-policy checkouts charge only the reduced payable quantity;
            # split/preorder checkouts keep the full requested quantity.
            requested_quantity=row.payable_quantity,
            unit_price=row.unit_price,
        )
        for row in checkout.items.all()
    ])
    CommercePayment.raw_objects.filter(
        business=checkout.business, checkout=checkout
    ).update(intake=intake, checkout=None)
    checkout.materialized_intake = intake
    checkout.status = CommerceCheckoutSession.STATUS_MATERIALIZED
    checkout.save(update_fields=["materialized_intake", "status", "updated_at"])
    audit(
        checkout.business, actor, "commerce_checkout_materialize", checkout,
        f"Paid checkout materialized as {intake.public_number}",
        {"intake_id": str(intake.public_id), "amount": str(checkout.amount)},
    )
    # Full payment is the operational handoff point. Stock-backed lines consume
    # their reservation immediately; made-to-order lines become pending
    # Production orders for staff to continue. Any failure bubbles to the
    # recovery wrapper so a verified payment is retained for review.
    auto_process_paid_intake(intake, user=actor)
    intake.refresh_from_db()
    return intake, True


def attempt_materialize_paid_checkout(checkout, *, actor=None, allow_expired_recovery=False):
    """Best-effort materialization that never discards an already verified payment."""
    try:
        with transaction.atomic():
            return materialize_paid_checkout(
                checkout, actor=actor, allow_expired_recovery=allow_expired_recovery
            )
    except Exception as exc:
        logger.exception("Paid checkout materialization/fulfilment failed for checkout %s", checkout.pk)
        # The nested savepoint above rolls back any partial intake work. Record a
        # recoverable state in a fresh transaction while leaving receipts intact.
        CommerceCheckoutSession.raw_objects.filter(pk=checkout.pk, business=checkout.business).update(
            status=CommerceCheckoutSession.STATUS_PAID_REVIEW,
            paid_at=timezone.now(),
            reservation_released_at=timezone.now(),
            materialization_error=str(exc)[:500],
        )
        return None, False


def release_checkout_reservation_for_intake(intake):
    checkout = CommerceCheckoutSession.raw_objects.filter(
        business=intake.business, materialized_intake=intake, reservation_released_at__isnull=True
    ).first()
    if checkout:
        checkout.reservation_released_at = timezone.now()
        checkout.save(update_fields=["reservation_released_at", "updated_at"])
    return checkout
