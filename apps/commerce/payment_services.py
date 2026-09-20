from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import CashAccount, FinancialTransaction
from core.services import audit, record_cash
from sales.models import CustomerPayment, Sale

from .models import (
    CommerceGatewayEvent,
    CommerceCheckoutSession,
    CommerceIntake,
    CommerceNotification,
    CommercePayment,
    CommercePaymentAllocation,
    CommercePaymentClaim,
    CommercePaymentConfiguration,
    CommercePaymentReceipt,
)
from .payment_gateways import initialize_gateway, verify_gateway
from .notification_services import queue_commerce_notification


PAYMENT_METHOD_TO_SALE_METHOD = {
    CommercePayment.METHOD_PAYSTACK: "Card",
    CommercePayment.METHOD_MONNIFY: "Card",
    CommercePayment.METHOD_BANK_TRANSFER: "Transfer",
    CommercePayment.METHOD_TRANSFER: "Transfer",
    CommercePayment.METHOD_CASH: "Cash",
    CommercePayment.METHOD_POS_CARD: "Card / POS",
}


def payment_configuration(business):
    config, _ = CommercePaymentConfiguration.raw_objects.get_or_create(
        business=business, defaults={"created_by": None}
    )
    return config


def _validate_return_url(value):
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationError("Enter a complete return address beginning with http:// or https://.")
    return value


def _method_enabled(config, method):
    checks = {
        CommercePayment.METHOD_PAYSTACK: config.paystack_enabled,
        CommercePayment.METHOD_MONNIFY: config.monnify_enabled,
        CommercePayment.METHOD_BANK_TRANSFER: config.bank_transfer_enabled,
        CommercePayment.METHOD_TRANSFER: config.transfer_enabled,
        CommercePayment.METHOD_CASH: config.cash_enabled,
        CommercePayment.METHOD_POS_CARD: config.paystack_terminal_enabled,
    }
    return bool(checks.get(method))


def _configured_active_account(account, business):
    return bool(account and account.active and account.business_id == business.pk)


def eligible_payment_methods(business, *, surface="public"):
    """Return configured methods for the requested trust surface.

    Public/headless commerce may use provider-confirmed methods or the native
    proof-backed Transfer method. Cash and physical POS card flows remain
    available only on the authenticated in-premise staff UI.
    """
    config = payment_configuration(business)
    methods = []
    if surface == "pos":
        if config.cash_enabled and _configured_active_account(config.cash_account, business):
            methods.append({"code": CommercePayment.METHOD_CASH, "label": "Cash"})
        if config.transfer_enabled and _configured_active_account(config.transfer_account, business):
            methods.append({
                "code": CommercePayment.METHOD_TRANSFER,
                "label": "Transfer",
                "confirmation": "staff_confirmation",
                "bank_name": (config.bank_name or "").strip(),
                "account_name": (config.bank_account_name or "").strip(),
                "account_number": (config.bank_account_number or "").strip(),
                "instructions": (config.bank_instructions or "").strip(),
            })
        if (
            config.paystack_terminal_enabled
            and config.paystack_enabled
            and bool(config.paystack_secret_key and config.paystack_terminal_id and config.paystack_terminal_customer_email)
            and _configured_active_account(config.paystack_terminal_account, business)
        ):
            methods.append({"code": CommercePayment.METHOD_POS_CARD, "label": "Card on POS terminal"})
        return methods

    if (
        config.paystack_enabled
        and bool(config.paystack_secret_key)
        and _configured_active_account(config.paystack_account, business)
    ):
        methods.append({"code": CommercePayment.METHOD_PAYSTACK, "label": "Card / secure checkout (Paystack)"})
    if (
        config.monnify_enabled
        and bool(config.monnify_api_key and config.monnify_secret_key and config.monnify_contract_code and config.monnify_base_url)
        and _configured_active_account(config.monnify_account, business)
    ):
        methods.append({"code": CommercePayment.METHOD_MONNIFY, "label": "Secure checkout (Monnify)"})
    if (
        config.transfer_enabled
        and _configured_active_account(config.transfer_account, business)
        and (config.bank_name or "").strip()
        and (config.bank_account_name or "").strip()
        and (config.bank_account_number or "").strip()
    ):
        methods.append({
            "code": CommercePayment.METHOD_TRANSFER,
            "label": "Transfer",
            "requires_payment_proof": True,
            "confirmation": "staff_review",
        })
    if config.bank_transfer_enabled and _configured_active_account(config.bank_cash_account, business):
        provider = config.bank_transfer_provider
        provider_ready = False
        if provider == CommercePaymentConfiguration.BANK_TRANSFER_PROVIDER_PAYSTACK:
            provider_ready = bool(config.paystack_enabled and config.paystack_secret_key)
        elif provider == CommercePaymentConfiguration.BANK_TRANSFER_PROVIDER_MONNIFY:
            provider_ready = bool(
                config.monnify_enabled
                and config.monnify_api_key
                and config.monnify_secret_key
                and config.monnify_contract_code
                and config.monnify_base_url
                and config.monnify_transfer_bank_code
            )
        if provider_ready:
            methods.append({
                "code": CommercePayment.METHOD_BANK_TRANSFER,
                "label": f"Instant bank transfer ({provider.title()})",
            })
    return methods


def _assert_method_eligible(business, method, *, surface="public"):
    if method not in {row["code"] for row in eligible_payment_methods(business, surface=surface)}:
        raise ValidationError("This payment method is not enabled and fully configured for this storefront.")


def _manual_instructions(config, method):
    if method == CommercePayment.METHOD_TRANSFER:
        return config.bank_instructions or "Transfer the exact amount to the account shown, then upload your payment proof. An authorized staff member will verify it before the order is marked paid."
    if method == CommercePayment.METHOD_BANK_TRANSFER:
        return "Transfer the exact amount to the temporary account shown. INPROFIC will confirm it automatically through the payment provider."
    if method == CommercePayment.METHOD_CASH:
        return config.cash_instructions or "Pay an authorized staff member. Cash remains pending until the receipt is confirmed in INPROFIC."
    return ""


def current_payment(intake):
    return intake.payments.exclude(status=CommercePayment.STATUS_CANCELLED).order_by("-created_at", "-id").first()


def current_checkout_payment(checkout):
    qs = CommercePayment.raw_objects.filter(business=checkout.business)
    if checkout.materialized_intake_id:
        qs = qs.filter(Q(checkout=checkout) | Q(intake_id=checkout.materialized_intake_id))
    else:
        qs = qs.filter(checkout=checkout)
    return qs.exclude(status=CommercePayment.STATUS_CANCELLED).order_by("-created_at", "-id").first()


def capture_checkout_gateway_email(checkout, method, email):
    """Save provider-required email only when the selected gateway needs it."""
    method = (method or "").strip().lower()
    if method not in {CommercePayment.METHOD_PAYSTACK, CommercePayment.METHOD_MONNIFY, CommercePayment.METHOD_BANK_TRANSFER}:
        return
    if checkout.customer_email:
        return
    email = (email or "").strip()
    if not email:
        raise ValidationError("Email address is required by this payment provider.")
    validate_email(email)
    checkout.customer_email = email
    checkout.save(update_fields=["customer_email", "updated_at"])


def serialize_payment(payment, config=None):
    if payment is None:
        return None
    config = config or payment_configuration(payment.business)
    bank_account = None
    if payment.method == CommercePayment.METHOD_TRANSFER:
        bank_account = {
            "bank_name": config.bank_name,
            "account_name": config.bank_account_name,
            "account_number": config.bank_account_number,
            "account_expires_at": None,
            "display_text": config.bank_instructions or "",
        }
    elif payment.method == CommercePayment.METHOD_BANK_TRANSFER:
        meta = payment.gateway_metadata or {}
        if meta.get("account_number"):
            bank_account = {
                "bank_name": meta.get("bank_name", ""),
                "account_name": meta.get("account_name", ""),
                "account_number": meta.get("account_number", ""),
                "account_expires_at": meta.get("account_expires_at"),
                "display_text": meta.get("display_text", ""),
            }
    latest_claim = None
    if payment.method == CommercePayment.METHOD_TRANSFER or (
        payment.method == CommercePayment.METHOD_BANK_TRANSFER and not payment.gateway_provider
    ):
        latest_claim = payment.claims.order_by("-created_at", "-id").first()
    latest_receipt = payment.receipts.filter(reversed_at__isnull=True).order_by("-verified_at", "-id").first()
    return {
        "payment_id": str(payment.public_id),
        "method": payment.method,
        "status": payment.status,
        "amount": f"{payment.amount:.2f}",
        "currency": payment.currency,
        "reference": payment.reference,
        "gateway_reference": payment.gateway_reference or "",
        "gateway_provider": payment.gateway_provider or "",
        "authorization_url": payment.authorization_url or "",
        "instructions": payment.instructions or "",
        "bank_account": bank_account,
        "proof_required": payment.method == CommercePayment.METHOD_TRANSFER,
        "expires_at": payment.expires_at.isoformat() if payment.expires_at else None,
        "amount_paid": f"{payment.amount_paid:.2f}",
        "balance": f"{payment.balance:.2f}",
        "verified_at": payment.verified_at.isoformat() if payment.verified_at else None,
        "settled_at": payment.settled_at.isoformat() if payment.settled_at else None,
        "checkout_id": str(payment.checkout.public_id) if payment.checkout_id else None,
        "order_id": str(payment.intake.public_id) if payment.intake_id else None,
        "receipt_id": str(latest_receipt.public_id) if latest_receipt else None,
        "receipt_path": (f"/shop/{payment.business.slug}/receipts/{latest_receipt.public_id}/" if latest_receipt else None),
        "claim": ({
            "payer_name": latest_claim.payer_name,
            "transfer_reference": latest_claim.transfer_reference,
            "status": latest_claim.status,
            "proof_received": bool(latest_claim.payment_proof),
            "submitted_at": latest_claim.created_at.isoformat(),
            "reviewed_at": latest_claim.reviewed_at.isoformat() if latest_claim.reviewed_at else None,
            "mismatch_reason": latest_claim.mismatch_reason,
        } if latest_claim else None),
    }


def _account_for(config, method, business, actor):
    configured = {
        CommercePayment.METHOD_PAYSTACK: config.paystack_account,
        CommercePayment.METHOD_MONNIFY: config.monnify_account,
        CommercePayment.METHOD_BANK_TRANSFER: config.bank_cash_account,
        CommercePayment.METHOD_TRANSFER: config.transfer_account,
        CommercePayment.METHOD_CASH: config.cash_account,
        CommercePayment.METHOD_POS_CARD: config.paystack_terminal_account,
    }.get(method)
    if configured and configured.active:
        if configured.business_id != business.pk:
            raise ValidationError("The configured settlement account belongs to another business.")
        return configured
    # Existing intake payments can use a fallback account. Keep that
    # behavior for old records/endpoints; new checkout initiation requires an
    # explicitly configured account through _assert_method_eligible().
    preferred_type = "cash" if method == CommercePayment.METHOD_CASH else "card" if method == CommercePayment.METHOD_POS_CARD else "bank"
    account = CashAccount.raw_objects.filter(
        business=business, active=True, account_type=preferred_type
    ).order_by("id").first()
    if account is None:
        base_name = "Commerce Cash" if preferred_type == "cash" else "Commerce Bank"
        name = base_name
        if CashAccount.raw_objects.filter(business=business, name=name).exists():
            name = f"{base_name} {uuid4().hex[:8].upper()}"
        account = CashAccount.raw_objects.create(
            business=business,
            created_by=actor,
            name=name,
            account_type=preferred_type,
        )
    return account


def _payment_target(payment):
    return payment.checkout if payment.checkout_id else payment.intake


def _payment_display_reference(payment):
    if payment.intake_id:
        return payment.intake.public_number
    if payment.checkout_id:
        return f"checkout {payment.checkout.public_id}"
    return payment.reference


def _target_amount(target):
    if isinstance(target, CommerceCheckoutSession):
        return Decimal(target.amount).quantize(Decimal("0.01"))
    return Decimal(target.total).quantize(Decimal("0.01"))


def initiate_payment(*, intake=None, checkout=None, method, idempotency_key, return_url="", surface="public"):
    """Initialize payment for either an existing intake or a checkout."""
    if (intake is None) == (checkout is None):
        raise ValidationError("Choose exactly one payment target.")
    target = checkout or intake
    method = (method or "").strip().lower()
    idempotency_key = (idempotency_key or "").strip()
    if not idempotency_key:
        raise ValidationError("Idempotency-Key header is required.")
    if len(idempotency_key) > 160:
        raise ValidationError("Idempotency-Key cannot exceed 160 characters.")
    if method not in dict(CommercePayment.METHOD_CHOICES):
        raise ValidationError("Choose a supported payment method.")
    return_url = _validate_return_url(return_url)
    config = payment_configuration(target.business)
    if checkout is not None:
        _assert_method_eligible(target.business, method, surface=surface)
    elif surface == "public":
        _assert_method_eligible(target.business, method, surface="public")
    elif not _method_enabled(config, method):
        raise ValidationError("This payment method is not enabled for this storefront.")
    if method in {CommercePayment.METHOD_PAYSTACK, CommercePayment.METHOD_MONNIFY} and not return_url:
        raise ValidationError("A return address is required for online payments.")
    gateway_provider = {
        CommercePayment.METHOD_PAYSTACK: CommercePayment.GATEWAY_PAYSTACK,
        CommercePayment.METHOD_MONNIFY: CommercePayment.GATEWAY_MONNIFY,
        CommercePayment.METHOD_BANK_TRANSFER: config.bank_transfer_provider,
        CommercePayment.METHOD_POS_CARD: CommercePayment.GATEWAY_PAYSTACK,
    }.get(method, CommercePayment.GATEWAY_NONE)

    payment_created = False
    with transaction.atomic():
        if checkout is not None:
            from .checkout_services import expire_checkout_if_needed
            locked = CommerceCheckoutSession.raw_objects.select_for_update().prefetch_related("items").get(
                pk=target.pk, business=target.business
            )
            expire_checkout_if_needed(locked)
            if locked.status != CommerceCheckoutSession.STATUS_AWAITING_PAYMENT:
                raise ValidationError("Payment can only start while this checkout is awaiting payment and its reservation is valid.")
            target_filter = {"checkout": locked}
        else:
            locked = CommerceIntake.raw_objects.select_for_update().prefetch_related("items").get(
                pk=target.pk, business=target.business
            )
            if locked.status in {CommerceIntake.STATUS_CANCELLED, CommerceIntake.STATUS_REJECTED}:
                raise ValidationError("Payment is not available for a cancelled or rejected order.")
            target_filter = {"intake": locked}

        amount = _target_amount(locked)
        if amount <= 0:
            raise ValidationError("This checkout/order has no payable balance.")
        existing = CommercePayment.raw_objects.filter(
            business=locked.business, idempotency_key=idempotency_key, **target_filter
        ).first()
        if existing:
            if existing.method != method:
                raise ValidationError("This idempotency key was already used for another payment method.")
            payment = existing
        else:
            compatible = CommercePayment.raw_objects.filter(
                business=locked.business, method=method,
                status__in=CommercePayment.ACTIVE_STATUSES, amount=amount, **target_filter
            ).order_by("-created_at", "-id").first()
            if compatible:
                payment = compatible
            else:
                blocking = CommercePayment.raw_objects.filter(
                    business=locked.business,
                    status__in=[CommercePayment.STATUS_AWAITING_VERIFICATION, CommercePayment.STATUS_PARTIALLY_PAID],
                    **target_filter,
                ).exclude(method=method).first()
                if blocking:
                    raise ValidationError("Resolve the current claimed or partially paid payment before selecting another method.")
                CommercePayment.raw_objects.filter(
                    business=locked.business,
                    status__in=[CommercePayment.STATUS_PENDING, CommercePayment.STATUS_AWAITING_CUSTOMER],
                    **target_filter,
                ).exclude(method=method).update(status=CommercePayment.STATUS_CANCELLED)
                payment = CommercePayment.raw_objects.create(
                    business=locked.business,
                    intake=locked if checkout is None else None,
                    checkout=locked if checkout is not None else None,
                    method=method,
                    gateway_provider=gateway_provider,
                    amount=amount,
                    currency=config.currency.upper(),
                    reference=f"STP-{uuid4().hex[:20].upper()}",
                    idempotency_key=idempotency_key,
                    return_url=return_url,
                    expires_at=(locked.reservation_expires_at if checkout is not None else None),
                    instructions=_manual_instructions(config, method),
                    status=(CommercePayment.STATUS_PENDING if method == CommercePayment.METHOD_CASH else CommercePayment.STATUS_AWAITING_CUSTOMER),
                )
                payment_created = True

    if gateway_provider and not payment.authorization_url and not payment.gateway_metadata:
        try:
            initialized = initialize_gateway(payment, config)
        except Exception as exc:
            CommercePayment.raw_objects.filter(pk=payment.pk).update(last_error=str(exc)[:500])
            raise
        with transaction.atomic():
            payment = CommercePayment.raw_objects.select_for_update().get(pk=payment.pk)
            payment.authorization_url = initialized["authorization_url"]
            payment.gateway_reference = initialized.get("gateway_reference") or payment.reference
            payment.gateway_metadata = initialized.get("metadata") or {}
            payment.last_error = ""
            payment.status = CommercePayment.STATUS_AWAITING_CUSTOMER
            payment.save(update_fields=[
                "authorization_url", "gateway_reference", "gateway_metadata",
                "last_error", "status", "updated_at",
            ])
    # Cash at the staff POS is verified immediately by the same request.
    # Do not publish a transient "payment started" event between creation and
    # settlement; on SQLite its push-dispatch writer could race the financial
    # ledger write. The normal verified-payment notification is emitted once
    # settlement succeeds. Other payment methods retain the pending alert.
    if payment_created and not (surface == "pos" and method in {CommercePayment.METHOD_CASH, CommercePayment.METHOD_TRANSFER}):
        queue_commerce_notification(
            business=payment.business,
            event_type=CommerceNotification.EVENT_PAYMENT_STARTED,
            title=f"{payment.get_method_display()} payment started",
            message=(
                f"Payment {payment.reference} was opened for "
                f"{payment.currency} {payment.amount:,.2f}; confirmation is still pending."
            ),
            target_url="/finance/commerce-payments/",
            dedupe_key=f"payment:{payment.public_id}:started",
        )
    return payment


@transaction.atomic
def submit_bank_claim(*, payment, payer_name, transfer_reference, payment_proof=None):
    """Submit manual evidence for the native no-gateway Transfer flow.

    The legacy no-gateway bank-transfer branch remains accepted for historical
    records, but newly eligible public methods use METHOD_TRANSFER.
    """
    payment = CommercePayment.raw_objects.select_for_update().get(pk=payment.pk, business=payment.business)
    manual_transfer = payment.method == CommercePayment.METHOD_TRANSFER
    legacy_manual = payment.method == CommercePayment.METHOD_BANK_TRANSFER and not payment.gateway_provider
    if not (manual_transfer or legacy_manual):
        raise ValidationError("Transfer evidence can only be submitted for a manual transfer payment.")
    if payment.gateway_provider:
        raise ValidationError("This bank transfer is verified automatically by the payment provider; no manual claim is required.")
    if payment.status in {CommercePayment.STATUS_PAID, CommercePayment.STATUS_CANCELLED, CommercePayment.STATUS_REFUNDED}:
        raise ValidationError("This payment no longer accepts transfer claims.")
    payer_name = (payer_name or "").strip()
    transfer_reference = (transfer_reference or "").strip().upper()
    if not payer_name or not transfer_reference:
        raise ValidationError("Payer name and transfer reference are required.")
    if manual_transfer:
        if payment_proof is None:
            raise ValidationError("Attach the transfer payment proof before submitting.")
        if getattr(payment_proof, "size", 0) > 10 * 1024 * 1024:
            raise ValidationError("Payment proof must be 10 MB or smaller.")
        if Path(getattr(payment_proof, "name", "")).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".pdf"}:
            raise ValidationError("Payment proof must be a JPG, PNG, WEBP or PDF file.")
    existing = CommercePaymentClaim.raw_objects.filter(
        business=payment.business, transfer_reference=transfer_reference
    ).first()
    if existing:
        if existing.payment_id == payment.pk:
            return existing, False
        raise ValidationError("That transfer reference is already attached to another order.")
    try:
        claim = CommercePaymentClaim.raw_objects.create(
            business=payment.business,
            payment=payment,
            payer_name=payer_name,
            transfer_reference=transfer_reference,
            payment_proof=payment_proof if manual_transfer else None,
        )
    except IntegrityError as exc:
        raise ValidationError("That transfer reference has already been submitted.") from exc
    payment.status = CommercePayment.STATUS_AWAITING_VERIFICATION
    payment.save(update_fields=["status", "updated_at"])
    audit(
        payment.business, None, "payment_claim", claim,
        f"Transfer claim submitted for {_payment_display_reference(payment)}",
        {"payment_reference": payment.reference, "transfer_reference": transfer_reference, "proof_received": bool(claim.payment_proof)},
    )
    queue_commerce_notification(
        business=payment.business,
        event_type=CommerceNotification.EVENT_PAYMENT_CLAIM,
        title="Transfer claim needs verification",
        message=(
            f"{payer_name} submitted transfer reference {transfer_reference} "
            f"for payment {payment.reference}."
        ),
        target_url="/finance/commerce-payments/",
        dedupe_key=f"claim:{claim.pk}:submitted",
    )
    return claim, True


def _linked_sales(intake):
    sale_ids = []
    if intake.accepted_sale_id:
        sale_ids.append(intake.accepted_sale_id)
    order_ids = [value for value in (intake.accepted_order_id, intake.split_order_id) if value]
    if order_ids:
        sale_ids.extend(Sale.raw_objects.filter(
            business=intake.business, linked_order_id__in=order_ids
        ).values_list("pk", flat=True))
    return Sale.raw_objects.filter(business=intake.business, pk__in=sale_ids).prefetch_related("items", "payments").order_by("date", "id")


@transaction.atomic
def sync_payment_receipts_to_sales(intake):
    """Allocate already-posted intake receipts when/after downstream sales exist.

    Returns the amount newly allocated during this call. Callers that create a
    downstream Sale can use this to avoid posting the same cash receipt twice.
    """
    intake = CommerceIntake.raw_objects.select_for_update().get(pk=intake.pk, business=intake.business)
    sales = list(_linked_sales(intake))
    if not sales:
        return Decimal("0")
    newly_allocated = Decimal("0")
    receipts = CommercePaymentReceipt.raw_objects.filter(
        business=intake.business, payment__intake=intake, reversed_at__isnull=True
    ).prefetch_related("allocations").order_by("verified_at", "id")
    for receipt in receipts:
        allocated = receipt.allocations.aggregate(value=Sum("amount"))["value"] or Decimal("0")
        remaining = receipt.amount - allocated
        for sale in sales:
            if remaining <= 0:
                break
            paid = sale.payments.aggregate(value=Sum("amount"))["value"] or Decimal("0")
            outstanding = max(Decimal("0"), sale.total - paid)
            if outstanding <= 0:
                continue
            amount = min(remaining, outstanding)
            customer_payment = CustomerPayment.raw_objects.create(
                business=intake.business,
                created_by=receipt.verified_by,
                date=receipt.verified_at.date(),
                customer=sale.customer,
                customer_master=sale.customer_master,
                amount=amount,
                payment_method=PAYMENT_METHOD_TO_SALE_METHOD[receipt.payment.method],
                reference=receipt.payment.reference[:80],
                notes=f"Allocated from commerce payment for {intake.public_number}",
                sale=sale,
                account=receipt.account,
            )
            CommercePaymentAllocation.objects.create(
                receipt=receipt, sale=sale, customer_payment=customer_payment, amount=amount
            )
            newly_allocated += amount
            paid += amount
            sale.transaction_type = "paid" if paid >= sale.total else "partial"
            sale.save(update_fields=["transaction_type", "updated_at"])
            if sale.linked_order_id and paid >= sale.total:
                sale.linked_order.customer_payment_status = "paid"
                sale.linked_order.save(update_fields=["customer_payment_status", "updated_at"])
            remaining -= amount
    return newly_allocated


def sync_commerce_payments_for_order(order):
    intakes = CommerceIntake.raw_objects.filter(business=order.business).filter(
        Q(accepted_order=order) | Q(split_order=order)
    )
    newly_allocated = Decimal("0")
    for intake in intakes:
        newly_allocated += sync_payment_receipts_to_sales(intake)
    return newly_allocated


def _refresh_payment(payment):
    paid = CommercePaymentReceipt.raw_objects.filter(
        business=payment.business, payment=payment, reversed_at__isnull=True
    ).aggregate(value=Sum("amount"))["value"] or Decimal("0")
    payment.amount_paid = paid
    if paid >= payment.amount:
        payment.status = CommercePayment.STATUS_PAID
        payment.settled_at = payment.settled_at or timezone.now()
    elif paid > 0:
        payment.status = CommercePayment.STATUS_PARTIALLY_PAID
        payment.settled_at = None
    elif payment.gateway_provider or payment.method in {CommercePayment.METHOD_PAYSTACK, CommercePayment.METHOD_MONNIFY}:
        payment.status = CommercePayment.STATUS_AWAITING_CUSTOMER
        payment.settled_at = None
    elif payment.method in {CommercePayment.METHOD_TRANSFER, CommercePayment.METHOD_BANK_TRANSFER} and payment.claims.filter(status=CommercePaymentClaim.STATUS_SUBMITTED).exists():
        payment.status = CommercePayment.STATUS_AWAITING_VERIFICATION
        payment.settled_at = None
    else:
        payment.status = CommercePayment.STATUS_PENDING
        payment.settled_at = None
    payment.save(update_fields=["amount_paid", "status", "settled_at", "updated_at"])
    if payment.intake_id:
        payment.intake.payment_state = (
            CommerceIntake.PAYMENT_CONFIRMED if payment.status == CommercePayment.STATUS_PAID
            else CommerceIntake.PAYMENT_FAILED if payment.status == CommercePayment.STATUS_FAILED
            else CommerceIntake.PAYMENT_PENDING
        )
        payment.intake.save(update_fields=["payment_state", "updated_at"])


@transaction.atomic
def record_verified_payment(
    *, payment, amount, actor, idempotency_key, external_reference="", note="",
    location="", claim=None, verified_at=None, staff_pos_confirmed=False,
):
    payment = CommercePayment.raw_objects.select_for_update().select_related("intake", "checkout").get(
        pk=payment.pk, business=payment.business
    )
    checkout_before_payment = payment.checkout
    existing = CommercePaymentReceipt.raw_objects.filter(
        business=payment.business, payment=payment, idempotency_key=idempotency_key
    ).first()
    if existing:
        return existing, False
    try:
        amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Enter a valid confirmed amount.") from exc
    if amount <= 0 or amount > payment.balance:
        raise ValidationError("Confirmed amount must be greater than zero and cannot exceed the payment balance.")
    if payment.status == CommercePayment.STATUS_REFUNDED or (
        payment.status == CommercePayment.STATUS_CANCELLED
        and payment.method in {CommercePayment.METHOD_BANK_TRANSFER, CommercePayment.METHOD_TRANSFER, CommercePayment.METHOD_CASH}
    ):
        raise ValidationError("This payment cannot receive funds in its current state.")
    if payment.method in {CommercePayment.METHOD_TRANSFER, CommercePayment.METHOD_BANK_TRANSFER} and not payment.gateway_provider and claim is None:
        claim = payment.claims.filter(status=CommercePaymentClaim.STATUS_SUBMITTED).order_by("created_at", "id").first()
        if claim is None and not (payment.method == CommercePayment.METHOD_TRANSFER and staff_pos_confirmed and actor is not None):
            raise ValidationError("A submitted transfer claim is required before verification.")
    external_reference = (external_reference or (claim.transfer_reference if claim else "")).strip()
    if external_reference and CommercePaymentReceipt.raw_objects.filter(
        business=payment.business, external_reference=external_reference
    ).exists():
        raise ValidationError("That external payment reference has already been settled.")
    config = payment_configuration(payment.business)
    account = _account_for(config, payment.method, payment.business, actor)
    verified_at = verified_at or timezone.now()
    ledger = record_cash(
        payment.business,
        actor,
        date=verified_at.date(),
        amount=amount,
        transaction_type=FinancialTransaction.INCOME,
        category="Commerce customer payment",
        description=f"Payment received for commerce {_payment_display_reference(payment)}",
        payment_method=PAYMENT_METHOD_TO_SALE_METHOD[payment.method],
        reference=payment.reference,
        account=account,
    )
    receipt = CommercePaymentReceipt.raw_objects.create(
        business=payment.business,
        created_by=actor,
        payment=payment,
        amount=amount,
        external_reference=external_reference,
        idempotency_key=idempotency_key,
        account=account,
        financial_transaction=ledger,
        verified_by=actor,
        verified_at=verified_at,
        location=(location or "").strip(),
        note=(note or "").strip(),
    )
    if claim:
        claim = CommercePaymentClaim.raw_objects.select_for_update().get(pk=claim.pk, business=payment.business)
        claim.status = CommercePaymentClaim.STATUS_ACCEPTED
        claim.reviewed_by = actor
        claim.reviewed_at = verified_at
        claim.confirmed_amount = amount
        claim.review_note = (note or "").strip()
        claim.mismatch_reason = "" if amount == payment.balance else "Partial payment confirmed; balance remains due."
        claim.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "confirmed_amount",
            "review_note", "mismatch_reason", "updated_at",
        ])
    payment.verified_by = actor
    payment.verified_at = verified_at
    payment.save(update_fields=["verified_by", "verified_at", "updated_at"])
    _refresh_payment(payment)
    if payment.intake_id:
        sync_payment_receipts_to_sales(payment.intake)
        if payment.status == CommercePayment.STATUS_PAID:
            from .services import attempt_auto_process_paid_intake
            attempt_auto_process_paid_intake(payment.intake, user=actor)
    elif payment.checkout_id and payment.status == CommercePayment.STATUS_PAID:
        from .checkout_services import attempt_materialize_paid_checkout
        attempt_materialize_paid_checkout(payment.checkout, actor=actor)
        payment.refresh_from_db(fields=["intake", "checkout"])
    audit(
        payment.business, actor, "payment_verify", receipt,
        f"Payment verified for {_payment_display_reference(payment)}",
        {"payment_reference": payment.reference, "amount": str(amount), "method": payment.method},
    )
    checkout_needs_review = False
    if checkout_before_payment:
        checkout_needs_review = CommerceCheckoutSession.raw_objects.filter(
            pk=checkout_before_payment.pk,
            business=payment.business,
            status=CommerceCheckoutSession.STATUS_PAID_REVIEW,
        ).exists()
    if checkout_needs_review:
        queue_commerce_notification(
            business=payment.business,
            event_type=CommerceNotification.EVENT_PAYMENT_REVIEW,
            title="Paid checkout needs immediate review",
            message=(
                f"{payment.currency} {amount:,.2f} was verified for {payment.reference}, "
                "but its order could not be created automatically. No second charge is needed."
            ),
            target_url="/finance/commerce-payments/",
            dedupe_key=f"receipt:{receipt.pk}:review",
        )
    else:
        state = "fully paid" if payment.status == CommercePayment.STATUS_PAID else f"{payment.balance:,.2f} remaining"
        queue_commerce_notification(
            business=payment.business,
            event_type=CommerceNotification.EVENT_PAYMENT_CONFIRMED,
            title="Commerce payment confirmed",
            message=f"{payment.currency} {amount:,.2f} was verified for {payment.reference} · {state}.",
            target_url="/finance/commerce-payments/",
            dedupe_key=f"receipt:{receipt.pk}:verified",
        )
    return receipt, True


@transaction.atomic
def reject_bank_claim(*, claim, actor, reason):
    claim = CommercePaymentClaim.raw_objects.select_for_update().select_related("payment__intake", "payment__checkout").get(
        pk=claim.pk, business=claim.business
    )
    if claim.status != CommercePaymentClaim.STATUS_SUBMITTED:
        raise ValidationError("Only an awaiting-verification claim can be rejected.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Give a reason for rejecting the transfer claim.")
    claim.status = CommercePaymentClaim.STATUS_REJECTED
    claim.reviewed_by = actor
    claim.reviewed_at = timezone.now()
    claim.mismatch_reason = reason
    claim.save(update_fields=["status", "reviewed_by", "reviewed_at", "mismatch_reason", "updated_at"])
    _refresh_payment(claim.payment)
    audit(
        claim.business, actor, "payment_claim_reject", claim,
        f"Bank transfer claim rejected for {_payment_display_reference(claim.payment)}",
        {"payment_reference": claim.payment.reference, "reason": reason},
    )
    return claim


@transaction.atomic
def reverse_payment_receipt(*, receipt, actor, reason):
    receipt = CommercePaymentReceipt.raw_objects.select_for_update().select_related("payment__intake", "payment__checkout", "account").get(
        pk=receipt.pk, business=receipt.business
    )
    if receipt.reversed_at:
        return receipt, False
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Give a reason for reversing the receipt.")
    now = timezone.now()
    reversal = record_cash(
        receipt.business,
        actor,
        date=now.date(),
        amount=receipt.amount,
        transaction_type=FinancialTransaction.OUTFLOW,
        category="Commerce payment reversal",
        description=f"Reversal of payment for commerce {_payment_display_reference(receipt.payment)}",
        payment_method=PAYMENT_METHOD_TO_SALE_METHOD[receipt.payment.method],
        reference=f"REV-{receipt.payment.reference}"[:80],
        account=receipt.account,
    )
    receipt.financial_transaction.reversed = True
    receipt.financial_transaction.save(update_fields=["reversed"])
    receipt.reversed_at = now
    receipt.reversed_by = actor
    receipt.reversal_reason = reason
    receipt.reversal_transaction = reversal
    receipt.save(update_fields=[
        "reversed_at", "reversed_by", "reversal_reason", "reversal_transaction", "updated_at",
    ])
    for allocation in receipt.allocations.select_related("sale", "customer_payment"):
        reversal_payment = CustomerPayment.raw_objects.create(
            business=receipt.business,
            created_by=actor,
            date=now.date(),
            customer=allocation.customer_payment.customer,
            customer_master=allocation.customer_payment.customer_master,
            amount=-allocation.amount,
            payment_method=allocation.customer_payment.payment_method,
            reference=f"REV-{receipt.payment.reference}"[:80],
            notes=f"Compensating reversal: {reason}"[:255],
            sale=allocation.sale,
            account=receipt.account,
        )
        allocation.reversal_customer_payment = reversal_payment
        allocation.save(update_fields=["reversal_customer_payment"])
        paid = allocation.sale.payments.aggregate(value=Sum("amount"))["value"] or Decimal("0")
        allocation.sale.transaction_type = "paid" if paid >= allocation.sale.total else "partial" if paid > 0 else "unpaid"
        allocation.sale.save(update_fields=["transaction_type", "updated_at"])
        if allocation.sale.linked_order_id and paid < allocation.sale.total:
            allocation.sale.linked_order.customer_payment_status = "unpaid"
            allocation.sale.linked_order.save(update_fields=["customer_payment_status", "updated_at"])
    _refresh_payment(receipt.payment)
    audit(
        receipt.business, actor, "payment_reverse", receipt,
        f"Payment receipt reversed for {_payment_display_reference(receipt.payment)}",
        {"payment_reference": receipt.payment.reference, "amount": str(receipt.amount), "reason": reason},
    )
    return receipt, True


def process_gateway_event(*, event):
    """Verify remotely outside a DB transaction, then settle exactly once."""
    payment = CommercePayment.raw_objects.select_related("intake", "checkout", "business").get(pk=event.payment_id)
    config = payment_configuration(payment.business)
    verified, verification = verify_gateway(payment, config)
    with transaction.atomic():
        event = CommerceGatewayEvent.raw_objects.select_for_update().get(pk=event.pk, business=payment.business)
        if event.processed_at:
            return event
        if not verified:
            event.error = "Provider verification did not match the expected payment."
            event.payload = {**(event.payload or {}), "verification": verification}
            event.save(update_fields=["error", "payload", "updated_at"])
            return event
        if payment.balance > 0:
            record_verified_payment(
                payment=payment,
                amount=payment.balance,
                actor=None,
                idempotency_key=f"gateway-event:{event.pk}",
                external_reference=payment.gateway_reference or payment.reference,
                note=f"Verified {payment.get_method_display()} gateway payment.",
            )
        event.provider_verified = True
        event.payload = {**(event.payload or {}), "verification": verification}
        event.processed_at = timezone.now()
        event.error = ""
        event.save(update_fields=["provider_verified", "payload", "processed_at", "error", "updated_at"])
    return event
