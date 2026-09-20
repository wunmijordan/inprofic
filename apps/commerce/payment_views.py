import hashlib
import json
from decimal import InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from accounts.services import is_business_admin, user_has_permission
from core.models import Business

from .forms import CommercePaymentConfigurationForm
from .models import (
    CommerceCheckoutSession,
    CommerceGatewayEvent,
    CommerceIntegration,
    CommerceIntake,
    CommercePayment,
    CommercePaymentClaim,
    CommercePaymentConfiguration,
    CommercePaymentReceipt,
)
from .payment_gateways import (
    GatewayError,
    monnify_signature_valid,
    paystack_signature_valid,
)
from .payment_services import (
    capture_checkout_gateway_email,
    current_checkout_payment,
    current_payment,
    eligible_payment_methods,
    initiate_payment,
    payment_configuration,
    process_gateway_event,
    record_verified_payment,
    reject_bank_claim,
    reverse_payment_receipt,
    serialize_payment,
    submit_bank_claim,
)
from .checkout_services import attempt_materialize_paid_checkout, serialize_checkout
from .views import _commerce_enabled, _settings_for


def _error(exc):
    return "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


def _request_payload(request):
    """Keep JSON compatibility while allowing multipart proof uploads."""
    if (request.content_type or "").split(";", 1)[0].strip().lower() == "multipart/form-data":
        return request.POST
    data = json.loads(request.body or b"{}")
    if not isinstance(data, dict):
        raise ValidationError("The payment request format is invalid.")
    return data


def _headless_context(request, business_slug):
    business = get_object_or_404(Business, slug=business_slug)
    if not _commerce_enabled(business) or not _settings_for(business).api_enabled:
        return business, None
    key = request.headers.get("X-INPROFIC-Key", "") or request.headers.get(
        "X-" + "Store" + "Track-Key", ""
    )
    integration = CommerceIntegration.raw_objects.filter(
        business=business,
        active=True,
        integration_type=CommerceIntegration.TYPE_API,
        api_key=key,
    ).first()
    return business, integration


@require_http_methods(["GET"])
def api_payment_methods(request, business_slug):
    business, integration = _headless_context(request, business_slug)
    if integration is None:
        return JsonResponse({"detail": "Invalid or disabled commerce API credential."}, status=403)
    return JsonResponse({
        "currency": payment_configuration(business).currency.upper(),
        "methods": eligible_payment_methods(business),
    })


@csrf_exempt
@require_POST
def api_checkout_payment_initiate(request, business_slug, checkout_id):
    business, integration = _headless_context(request, business_slug)
    if integration is None:
        return JsonResponse({"detail": "Invalid or disabled commerce API credential."}, status=403)
    checkout = get_object_or_404(
        CommerceCheckoutSession.raw_objects.prefetch_related("items"),
        business=business, public_id=checkout_id,
    )
    try:
        data = _request_payload(request)
        method = (data.get("method") or "").strip().lower()
        capture_checkout_gateway_email(
            checkout, method, data.get("customer_email") or data.get("email")
        )
        payment = initiate_payment(
            checkout=checkout,
            method=method,
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            return_url=data.get("return_url", ""),
        )
        payload = serialize_payment(payment)
        payload["checkout"] = serialize_checkout(checkout)
        return JsonResponse(payload, status=200)
    except (json.JSONDecodeError, ValidationError, GatewayError, TypeError, ValueError) as exc:
        return JsonResponse({"detail": _error(exc)}, status=400)


@require_http_methods(["GET"])
def api_checkout_payment_current(request, business_slug, checkout_id):
    business, integration = _headless_context(request, business_slug)
    if integration is None:
        return JsonResponse({"detail": "Invalid or disabled commerce API credential."}, status=403)
    checkout = get_object_or_404(
        CommerceCheckoutSession.raw_objects,
        business=business, public_id=checkout_id,
    )
    payment = current_checkout_payment(checkout)
    return JsonResponse({
        "checkout": serialize_checkout(checkout),
        "payment": serialize_payment(payment) if payment else None,
    })


@csrf_exempt
@require_POST
def api_checkout_payment_claim(request, business_slug, checkout_id):
    business, integration = _headless_context(request, business_slug)
    if integration is None:
        return JsonResponse({"detail": "Invalid or disabled commerce API credential."}, status=403)
    checkout = get_object_or_404(
        CommerceCheckoutSession.raw_objects,
        business=business, public_id=checkout_id,
    )
    payment = current_checkout_payment(checkout)
    if payment is None:
        return JsonResponse({"detail": "No current payment exists for this checkout."}, status=404)
    try:
        data = _request_payload(request)
        claim, created = submit_bank_claim(
            payment=payment,
            payer_name=data.get("payer_name"),
            transfer_reference=data.get("transfer_reference"),
            payment_proof=request.FILES.get("payment_proof"),
        )
        payment.refresh_from_db()
        payload = serialize_payment(payment)
        payload["claim_created"] = created
        payload["checkout"] = serialize_checkout(checkout)
        return JsonResponse(payload, status=201 if created else 200)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        return JsonResponse({"detail": _error(exc)}, status=400)


@csrf_exempt
@require_POST
def api_payment_initiate(request, business_slug, public_id):
    business, integration = _headless_context(request, business_slug)
    if integration is None:
        return JsonResponse({"detail": "Invalid or disabled commerce API credential."}, status=403)
    intake = get_object_or_404(
        CommerceIntake.raw_objects.prefetch_related("items"), business=business, public_id=public_id
    )
    try:
        data = _request_payload(request)
        payment = initiate_payment(
            intake=intake,
            method=data.get("method"),
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            return_url=data.get("return_url", ""),
        )
        return JsonResponse(serialize_payment(payment), status=200)
    except (json.JSONDecodeError, ValidationError, GatewayError, TypeError, ValueError) as exc:
        return JsonResponse({"detail": _error(exc)}, status=400)


@require_http_methods(["GET"])
def api_payment_current(request, business_slug, public_id):
    business, integration = _headless_context(request, business_slug)
    if integration is None:
        return JsonResponse({"detail": "Invalid or disabled commerce API credential."}, status=403)
    intake = get_object_or_404(CommerceIntake.raw_objects, business=business, public_id=public_id)
    payment = current_payment(intake)
    if payment is None:
        return JsonResponse({"detail": "No payment has been initiated for this order."}, status=404)
    return JsonResponse(serialize_payment(payment))


@csrf_exempt
@require_POST
def api_payment_claim(request, business_slug, public_id):
    business, integration = _headless_context(request, business_slug)
    if integration is None:
        return JsonResponse({"detail": "Invalid or disabled commerce API credential."}, status=403)
    intake = get_object_or_404(CommerceIntake.raw_objects, business=business, public_id=public_id)
    payment = current_payment(intake)
    if payment is None:
        return JsonResponse({"detail": "No current payment exists for this order."}, status=404)
    try:
        data = _request_payload(request)
        claim, created = submit_bank_claim(
            payment=payment,
            payer_name=data.get("payer_name"),
            transfer_reference=data.get("transfer_reference"),
            payment_proof=request.FILES.get("payment_proof"),
        )
        payment.refresh_from_db()
        payload = serialize_payment(payment)
        payload["claim_created"] = created
        return JsonResponse(payload, status=201 if created else 200)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        return JsonResponse({"detail": _error(exc)}, status=400)


def _safe_gateway_payload(value):
    blocked = {"authorization", "authorization_code", "card", "token", "secret", "api_key"}
    if isinstance(value, dict):
        return {
            str(key): _safe_gateway_payload(item)
            for key, item in value.items()
            if str(key).lower() not in blocked
        }
    if isinstance(value, list):
        return [_safe_gateway_payload(item) for item in value[:50]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _payload_metadata(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _gateway_event_identity(provider, payload):
    """Return provider references, our public payment id, and event type.

    Terminal and transfer events don't always use the same field as hosted
    checkout webhooks, so matching is deliberately provider-aware and remains
    tenant constrained in ``gateway_webhook``.
    """
    references = set()
    payment_public_id = ""
    if provider == CommercePayment.GATEWAY_PAYSTACK:
        data = payload.get("data") or {}
        for key in ("reference", "request_code", "offline_reference"):
            value = data.get(key)
            if value not in (None, ""):
                references.add(str(value))
        metadata = _payload_metadata(data.get("metadata"))
        payment_public_id = str(metadata.get("storetrack_payment_id") or "")
        event_type = str(payload.get("event") or "")
    else:
        data = payload.get("eventData") or payload.get("event_data") or {}
        for key in ("paymentReference", "payment_reference", "transactionReference", "transaction_reference"):
            value = data.get(key)
            if value not in (None, ""):
                references.add(str(value))
        metadata = _payload_metadata(data.get("metaData") or data.get("metadata"))
        payment_public_id = str(metadata.get("storetrackPaymentId") or metadata.get("storetrack_payment_id") or "")
        event_type = str(payload.get("eventType") or payload.get("event_type") or "")
    return references, payment_public_id, event_type


def _gateway_event_can_settle(provider, event_type):
    normalized = (event_type or "").strip()
    if provider == CommercePayment.GATEWAY_PAYSTACK:
        return normalized in {"charge.success", "paymentrequest.success"}
    return normalized.upper() == "SUCCESSFUL_TRANSACTION"


@csrf_exempt
@require_POST
def gateway_webhook(request, business_slug, provider):
    if provider not in {CommercePayment.METHOD_PAYSTACK, CommercePayment.METHOD_MONNIFY}:
        return JsonResponse({"detail": "Unknown payment provider."}, status=404)
    business = get_object_or_404(Business, slug=business_slug)
    config = CommercePaymentConfiguration.raw_objects.filter(business=business).first()
    if config is None:
        return JsonResponse({"detail": "Payment provider is not configured."}, status=404)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON payload."}, status=400)
    references, payment_public_id, event_type = _gateway_event_identity(provider, payload)
    payment_qs = CommercePayment.raw_objects.filter(
        business=business,
    ).filter(
        Q(gateway_provider=provider) | Q(gateway_provider="", method=provider)
    ).select_related("intake", "checkout")
    payment = None
    if payment_public_id:
        payment = payment_qs.filter(public_id=payment_public_id).first()
    if payment is None and references:
        payment = payment_qs.filter(
            Q(reference__in=references)
            | Q(gateway_reference__in=references)
            | Q(gateway_metadata__offline_reference__in=list(references))
        ).first()
    signature = (
        request.headers.get("x-paystack-signature", "")
        if provider == CommercePayment.METHOD_PAYSTACK
        else request.headers.get("monnify-signature", "")
    )
    try:
        signature_valid = (
            paystack_signature_valid(config, request.body, signature)
            if provider == CommercePayment.METHOD_PAYSTACK
            else monnify_signature_valid(config, request.body, signature)
        )
    except (ValidationError, GatewayError):
        signature_valid = False
    event_key = hashlib.sha256(
        provider.encode("utf-8") + b":" + signature.encode("utf-8") + b":" + request.body
    ).hexdigest()
    event, created = CommerceGatewayEvent.raw_objects.get_or_create(
        business=business,
        provider=provider,
        event_key=event_key,
        defaults={
            "payment": payment,
            "event_type": event_type,
            "signature_valid": signature_valid,
            "payload": _safe_gateway_payload(payload),
            "error": "" if payment else "No tenant-owned payment matched the provider reference.",
        },
    )
    if not signature_valid:
        if created:
            event.error = "Invalid webhook signature."
            event.processed_at = timezone.now()
            event.save(update_fields=["error", "processed_at", "updated_at"])
        return JsonResponse({"detail": "Invalid webhook signature."}, status=403)
    if not created and event.processed_at:
        return JsonResponse({"received": True, "duplicate": True})
    if payment is None:
        # A tenant-owned gateway account may emit signed events for payments
        # outside this Commerce flow (for example a Paystack Terminal
        # charge.success whose transaction reference differs from the invoice
        # offline_reference). Record/acknowledge it, but never settle without a
        # tenant-owned CommercePayment match and authoritative verification.
        if created:
            event.processed_at = timezone.now()
            event.error = "No tenant-owned Commerce payment matched this signed event; ignored."
            event.save(update_fields=["processed_at", "error", "updated_at"])
        return JsonResponse({"received": True, "ignored": True})
    if not _gateway_event_can_settle(provider, event_type):
        # Signed non-success notifications (pending/failed/etc.) are useful audit
        # evidence but must not trigger settlement or webhook retry storms.
        event.payment = payment
        event.processed_at = timezone.now()
        event.error = ""
        event.save(update_fields=["payment", "processed_at", "error", "updated_at"])
        return JsonResponse({"received": True, "settlement_attempted": False})
    try:
        event = process_gateway_event(event=event)
    except (ValidationError, GatewayError, TypeError, ValueError) as exc:
        event.error = _error(exc)[:500]
        event.save(update_fields=["error", "updated_at"])
        return JsonResponse({"detail": "Provider verification could not be completed."}, status=400)
    if not event.provider_verified:
        return JsonResponse({"detail": "Provider verification did not match this payment."}, status=400)
    return JsonResponse({"received": True, "duplicate": not created})


def _can_verify(user, business):
    return is_business_admin(user, business) or user_has_permission(user, business, "finance", "edit")


@login_required
def payment_settings(request):
    if not is_business_admin(request.user, request.business):
        return render(request, "403.html", status=403)
    config = payment_configuration(request.business)
    form = CommercePaymentConfigurationForm(
        request.POST or None, instance=config, business=request.business
    )
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.business = request.business
        saved.created_by = saved.created_by or request.user
        saved.save()
        messages.success(request, "Commerce payment settings saved. Payment credentials remain securely stored.")
        return redirect("commerce_payment_settings")
    return render(request, "commerce/payment_settings.html", {"form": form, "config": config})


@login_required
def payment_queue(request):
    if not user_has_permission(request.user, request.business, "finance", "view") and not is_business_admin(request.user, request.business):
        return render(request, "403.html", status=403)
    payments = list(CommercePayment.objects.select_related(
        "intake", "checkout", "verified_by"
    ).prefetch_related(
        "claims__reviewed_by", "receipts__verified_by", "receipts__reversed_by",
        "gateway_events",
    )[:100])
    for payment in payments:
        payment.confirmation_token = f"{payment.public_id}:{payment.updated_at.isoformat()}"
        payment.manual_transfer_claim_allowed = bool(
            payment.method in {CommercePayment.METHOD_TRANSFER, CommercePayment.METHOD_BANK_TRANSFER} and not payment.gateway_provider
        )
        payment.provider_reconcile_allowed = bool(
            payment.gateway_provider in {CommercePayment.GATEWAY_PAYSTACK, CommercePayment.GATEWAY_MONNIFY}
            or (not payment.gateway_provider and payment.method in {CommercePayment.METHOD_PAYSTACK, CommercePayment.METHOD_MONNIFY})
        )
    return render(request, "commerce/payments.html", {
        "payments": payments,
        "can_verify": _can_verify(request.user, request.business),
        "can_manage_payment_settings": is_business_admin(request.user, request.business),
    })


@login_required
def payment_claim_proof(request, claim_id):
    if not user_has_permission(request.user, request.business, "finance", "view") and not is_business_admin(request.user, request.business):
        return render(request, "403.html", status=403)
    claim = get_object_or_404(CommercePaymentClaim, business=request.business, pk=claim_id)
    if not claim.payment_proof:
        raise Http404("No payment proof is attached to this claim.")
    response = FileResponse(claim.payment_proof.open("rb"), as_attachment=False, filename=claim.payment_proof.name.rsplit("/", 1)[-1])
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    response["Content-Security-Policy"] = "sandbox"
    return response


@login_required
@require_POST
def payment_confirm(request, public_id):
    if not _can_verify(request.user, request.business):
        return render(request, "403.html", status=403)
    payment = get_object_or_404(CommercePayment, business=request.business, public_id=public_id)
    try:
        if payment.gateway_provider or payment.method in {CommercePayment.METHOD_PAYSTACK, CommercePayment.METHOD_MONNIFY, CommercePayment.METHOD_POS_CARD}:
            raise ValidationError("Gateway-backed payments cannot be manually approved. Use provider reconciliation; settlement occurs only after provider verification.")
        claim = None
        claim_id = request.POST.get("claim_id")
        if claim_id:
            claim = get_object_or_404(
                CommercePaymentClaim, business=request.business, payment=payment, pk=claim_id
            )
        token = (request.POST.get("confirmation_token") or "").strip()
        if not token:
            raise ValidationError("Payment confirmation could not be completed. Reload the page and try again.")
        receipt, created = record_verified_payment(
            payment=payment,
            amount=request.POST.get("amount"),
            actor=request.user,
            idempotency_key=f"manual:{token}",
            external_reference=request.POST.get("external_reference", ""),
            note=request.POST.get("note", ""),
            location=request.POST.get("location", ""),
            claim=claim,
        )
        messages.success(request, "Payment confirmed." if created else "That confirmation was already recorded.")
    except (ValidationError, InvalidOperation, TypeError, ValueError) as exc:
        messages.error(request, _error(exc))
    return redirect("commerce_payment_queue")


@login_required
@require_POST
def payment_claim_reject(request, claim_id):
    if not _can_verify(request.user, request.business):
        return render(request, "403.html", status=403)
    claim = get_object_or_404(CommercePaymentClaim, business=request.business, pk=claim_id)
    try:
        reject_bank_claim(claim=claim, actor=request.user, reason=request.POST.get("reason"))
        messages.success(request, "Transfer claim rejected; its audit history was retained.")
    except ValidationError as exc:
        messages.error(request, _error(exc))
    return redirect("commerce_payment_queue")


@login_required
@require_POST
def payment_reconcile(request, public_id):
    if not _can_verify(request.user, request.business):
        return render(request, "403.html", status=403)
    payment = get_object_or_404(
        CommercePayment.raw_objects.filter(business=request.business, public_id=public_id).filter(
            Q(gateway_provider__in=[CommercePayment.GATEWAY_PAYSTACK, CommercePayment.GATEWAY_MONNIFY])
            | Q(gateway_provider="", method__in=[CommercePayment.METHOD_PAYSTACK, CommercePayment.METHOD_MONNIFY])
        )
    )
    provider = payment.gateway_provider or payment.method
    event = CommerceGatewayEvent.raw_objects.create(
        business=request.business,
        created_by=request.user,
        payment=payment,
        provider=provider,
        event_key=f"manual-reconcile:{payment.public_id}:{timezone.now().timestamp()}",
        event_type="manual_reconcile",
        signature_valid=True,
        payload={"requested_by_user_id": request.user.pk},
    )
    try:
        event = process_gateway_event(event=event)
        if event.provider_verified:
            messages.success(request, "Provider verification completed and the payment is reconciled.")
        else:
            messages.error(request, "The provider response did not match the expected payment.")
    except (ValidationError, GatewayError, TypeError, ValueError) as exc:
        event.error = _error(exc)[:500]
        event.save(update_fields=["error", "updated_at"])
        messages.error(request, "Provider reconciliation could not be completed.")
    return redirect("commerce_payment_queue")


@login_required
@require_POST
def checkout_recover(request, checkout_id):
    if not _can_verify(request.user, request.business):
        return render(request, "403.html", status=403)
    checkout = get_object_or_404(
        CommerceCheckoutSession.raw_objects,
        business=request.business, public_id=checkout_id,
    )
    if checkout.status != CommerceCheckoutSession.STATUS_PAID_REVIEW:
        messages.error(request, "Only a fully paid checkout awaiting fulfilment review can be recovered.")
        return redirect("commerce_payment_queue")
    intake, created = attempt_materialize_paid_checkout(
        checkout, actor=request.user, allow_expired_recovery=True
    )
    if intake:
        messages.success(request, f"Paid checkout recovered as {intake.public_number}." if created else f"Checkout already materialized as {intake.public_number}.")
    else:
        checkout.refresh_from_db()
        messages.error(request, checkout.materialization_error or "The paid checkout still cannot be materialized safely.")
    return redirect("commerce_payment_queue")


@login_required
@require_POST
def payment_receipt_reverse(request, receipt_id):
    if not _can_verify(request.user, request.business):
        return render(request, "403.html", status=403)
    receipt = get_object_or_404(CommercePaymentReceipt, business=request.business, pk=receipt_id)
    try:
        receipt, created = reverse_payment_receipt(
            receipt=receipt, actor=request.user, reason=request.POST.get("reason")
        )
        messages.success(request, "Receipt reversed with compensating finance entries." if created else "Receipt was already reversed.")
    except ValidationError as exc:
        messages.error(request, _error(exc))
    return redirect("commerce_payment_queue")
