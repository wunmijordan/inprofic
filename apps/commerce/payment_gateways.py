"""Server-side gateway adapters for commerce payments.

The public commerce API receives no amount and never receives credentials.
Adapters initialize and verify transactions against tenant-owned configuration;
browser redirects are deliberately absent from the verification interface.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.core.exceptions import ValidationError

from .models import CommercePayment, CommercePaymentConfiguration


class GatewayError(ValidationError):
    pass


_PAYMENT_PROVIDER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/51.0.2704.103 Safari/537.36"
)


def _json_request(url, *, method="GET", headers=None, payload=None, timeout=30):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {
        "User-Agent": _PAYMENT_PROVIDER_USER_AGENT,
        "Accept": "application/json",
    }
    request_headers.update(headers or {})
    request = Request(url, data=body, method=method, headers=request_headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GatewayError(f"Payment provider returned HTTP {exc.code}: {detail[:240]}") from exc
    except URLError as exc:
        raise GatewayError(f"Could not reach payment provider: {exc.reason}") from exc
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise GatewayError("Payment provider returned an unreadable response.") from exc


def _require(value, message):
    if not value:
        raise GatewayError(message)
    return value


def _paystack_secret(config):
    return _require(config.paystack_secret_key, "Paystack is not fully configured for this business.")


def paystack_signature_valid(config, raw_body, signature):
    expected = hmac.new(_paystack_secret(config).encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)



def _payment_customer(payment):
    target = payment.checkout if payment.checkout_id else payment.intake
    if target is None:
        raise GatewayError("Payment is not attached to a checkout or order.")
    return target


def _payment_metadata(payment):
    return {
        "storetrack_payment_id": str(payment.public_id),
        "storetrack_checkout_id": str(payment.checkout.public_id) if payment.checkout_id else "",
        "storetrack_order_id": str(payment.intake.public_id) if payment.intake_id else "",
        "business_id": str(payment.business_id),
    }

def _paystack_customer_code(config, *, email, name="", phone=""):
    email = _require(email, "A customer email is required for Paystack Terminal payments.")
    headers = {"Authorization": f"Bearer {_paystack_secret(config)}", "Content-Type": "application/json"}
    try:
        response = _json_request(
            f"https://api.paystack.co/customer/{quote(email, safe='')}",
            headers={"Authorization": f"Bearer {_paystack_secret(config)}"},
        )
        data = response.get("data") or {}
        if response.get("status") and data.get("customer_code"):
            return data["customer_code"]
    except GatewayError:
        pass
    pieces = [part for part in (name or "Walk-in Customer").strip().split() if part]
    first_name = pieces[0] if pieces else "Walk-in"
    last_name = " ".join(pieces[1:]) if len(pieces) > 1 else "Customer"
    response = _json_request(
        "https://api.paystack.co/customer",
        method="POST",
        headers=headers,
        payload={
            "email": email,
            "first_name": first_name[:80],
            "last_name": last_name[:80],
            "phone": (phone or "")[:40],
            "metadata": {"source": "inprofic_commerce_pos"},
        },
    )
    data = response.get("data") or {}
    if not response.get("status") or not data.get("customer_code"):
        raise GatewayError(response.get("message") or "Paystack could not create the POS customer record.")
    return data["customer_code"]


def initialize_paystack_bank_transfer(payment, config):
    target = _payment_customer(payment)
    email = _require(target.customer_email, "A customer email is required for instant bank transfer.")
    amount_minor = int((Decimal(payment.amount) * Decimal("100")).quantize(Decimal("1")))
    expires_at = payment.expires_at.isoformat().replace("+00:00", "Z") if payment.expires_at else None
    response = _json_request(
        "https://api.paystack.co/charge",
        method="POST",
        headers={"Authorization": f"Bearer {_paystack_secret(config)}", "Content-Type": "application/json"},
        payload={
            "email": email,
            "amount": str(amount_minor),
            "currency": payment.currency,
            "reference": payment.reference,
            "bank_transfer": {"account_expires_at": expires_at},
            "metadata": _payment_metadata(payment),
        },
    )
    data = response.get("data") or {}
    if not response.get("status"):
        raise GatewayError(response.get("message") or "Paystack could not initialize bank transfer.")
    account = data.get("bank") or {}
    account_number = data.get("account_number") or account.get("account_number") or ""
    account_name = data.get("account_name") or account.get("account_name") or ""
    bank_name = data.get("bank_name") or account.get("name") or account.get("bank_name") or ""
    display_text = data.get("display_text") or data.get("message") or response.get("message") or "Transfer the exact amount to the temporary account shown."
    if not account_number:
        raise GatewayError("Paystack did not return temporary bank-transfer account details for this payment.")
    return {
        "authorization_url": "",
        "gateway_reference": data.get("reference") or payment.reference,
        "metadata": {
            "provider_status": data.get("status") or response.get("status"),
            "bank_name": bank_name,
            "account_name": account_name,
            "account_number": account_number,
            "display_text": display_text,
            "account_expires_at": data.get("account_expires_at") or expires_at,
        },
    }


def initialize_paystack_terminal(payment, config):
    target = _payment_customer(payment)
    terminal_id = _require(config.paystack_terminal_id, "Paystack Terminal ID is not configured for this business.")
    customer_email = (target.customer_email or config.paystack_terminal_customer_email or "").strip()
    customer_code = _paystack_customer_code(
        config, email=customer_email, name=target.customer_name, phone=target.customer_phone
    )
    amount_minor = int((Decimal(payment.amount) * Decimal("100")).quantize(Decimal("1")))
    request_response = _json_request(
        "https://api.paystack.co/paymentrequest",
        method="POST",
        headers={"Authorization": f"Bearer {_paystack_secret(config)}", "Content-Type": "application/json"},
        payload={
            "customer": customer_code,
            "amount": amount_minor,
            "currency": payment.currency,
            "description": f"INPROFIC in-premise sale {payment.reference}",
            "send_notification": False,
            "metadata": json.dumps(_payment_metadata(payment)),
        },
    )
    data = request_response.get("data") or {}
    request_id = data.get("id")
    request_code = data.get("request_code")
    offline_reference = data.get("offline_reference")
    if not request_response.get("status") or not request_id or not request_code or not offline_reference:
        raise GatewayError(request_response.get("message") or "Paystack could not create the POS payment request.")
    presence = _json_request(
        f"https://api.paystack.co/terminal/{quote(terminal_id, safe='')}/presence",
        headers={"Authorization": f"Bearer {_paystack_secret(config)}"},
    )
    state = presence.get("data") or {}
    if not presence.get("status") or not state.get("online") or not state.get("available"):
        raise GatewayError("The configured Paystack Terminal is offline or currently busy.")
    event_response = _json_request(
        f"https://api.paystack.co/terminal/{quote(terminal_id, safe='')}/event",
        method="POST",
        headers={"Authorization": f"Bearer {_paystack_secret(config)}", "Content-Type": "application/json"},
        payload={
            "type": "invoice",
            "action": "process",
            "data": {"id": request_id, "reference": offline_reference},
        },
    )
    event_id = (event_response.get("data") or {}).get("id")
    if not event_response.get("status") or not event_id:
        raise GatewayError(event_response.get("message") or "Paystack could not send the payment request to the terminal.")
    return {
        "authorization_url": "",
        "gateway_reference": request_code,
        "metadata": {
            "payment_request_id": request_id,
            "offline_reference": str(offline_reference),
            "terminal_id": terminal_id,
            "terminal_event_id": event_id,
            "customer_code": customer_code,
        },
    }


def verify_paystack_terminal(payment, config):
    code = payment.gateway_reference or (payment.gateway_metadata or {}).get("request_code")
    code = _require(code, "Paystack Terminal payment request reference is missing.")
    response = _json_request(
        f"https://api.paystack.co/paymentrequest/verify/{quote(str(code), safe='')}",
        headers={"Authorization": f"Bearer {_paystack_secret(config)}"},
    )
    data = response.get("data") or {}
    try:
        amount = Decimal(str(data.get("amount_paid") or data.get("amount") or "0")) / Decimal("100")
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    metadata = data.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    verified = bool(
        response.get("status")
        and (data.get("paid") is True or str(data.get("status") or "").lower() in {"paid", "success"})
        and amount == Decimal(payment.amount)
        and str(data.get("currency") or "").upper() == payment.currency.upper()
        and (not metadata or str(metadata.get("storetrack_payment_id") or "") == str(payment.public_id))
    )
    return verified, {
        "status": data.get("status"),
        "request_code": data.get("request_code"),
        "amount": str(amount),
        "currency": data.get("currency"),
        "paid_at": data.get("paid_at"),
        "payment_method": data.get("payment_method"),
    }


def initialize_paystack(payment, config):
    target = _payment_customer(payment)
    email = _require(target.customer_email, "A customer email is required for Paystack checkout.")
    amount_minor = int((Decimal(payment.amount) * Decimal("100")).quantize(Decimal("1")))
    response = _json_request(
        "https://api.paystack.co/transaction/initialize",
        method="POST",
        headers={"Authorization": f"Bearer {_paystack_secret(config)}", "Content-Type": "application/json"},
        payload={
            "email": email,
            "amount": str(amount_minor),
            "currency": payment.currency,
            "reference": payment.reference,
            "callback_url": payment.return_url,
            "channels": ["card", "bank", "ussd", "qr", "apple_pay"],
            "metadata": json.dumps(_payment_metadata(payment)),
        },
    )
    data = response.get("data") or {}
    if not response.get("status") or not data.get("authorization_url"):
        raise GatewayError(response.get("message") or "Paystack could not initialize the transaction.")
    return {
        "authorization_url": data["authorization_url"],
        "gateway_reference": data.get("reference") or payment.reference,
        "metadata": {"provider_status": response.get("status"), "access_code": data.get("access_code", "")},
    }


def verify_paystack(payment, config):
    response = _json_request(
        f"https://api.paystack.co/transaction/verify/{quote(payment.reference, safe='')}",
        headers={"Authorization": f"Bearer {_paystack_secret(config)}"},
    )
    data = response.get("data") or {}
    try:
        amount = Decimal(str(data.get("amount") or "0")) / Decimal("100")
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    metadata = data.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    verified = bool(
        response.get("status")
        and data.get("status") == "success"
        and str(data.get("reference") or "") == payment.reference
        and amount == Decimal(payment.amount)
        and str(data.get("currency") or "").upper() == payment.currency.upper()
        and str(metadata.get("storetrack_payment_id") or "") == str(payment.public_id)
        and str(metadata.get("storetrack_checkout_id") or "") == (str(payment.checkout.public_id) if payment.checkout_id else "")
        and str(metadata.get("storetrack_order_id") or "") == (str(payment.intake.public_id) if payment.intake_id else "")
        and str(metadata.get("business_id") or "") == str(payment.business_id)
    )
    return verified, {
        "status": data.get("status"),
        "reference": data.get("reference"),
        "gateway_reference": data.get("id"),
        "amount": str(amount),
        "currency": data.get("currency"),
        "paid_at": data.get("paid_at"),
    }


def _monnify_credentials(config):
    return (
        _require(config.monnify_api_key, "Monnify is not fully configured for this business."),
        _require(config.monnify_secret_key, "Monnify is not fully configured for this business."),
        _require(config.monnify_contract_code, "Monnify is not fully configured for this business."),
    )


def _monnify_token(config):
    api_key, secret_key, _ = _monnify_credentials(config)
    credentials = base64.b64encode(f"{api_key}:{secret_key}".encode("utf-8")).decode("ascii")
    response = _json_request(
        f"{config.monnify_base_url.rstrip('/')}/api/v1/auth/login",
        method="POST",
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
        payload={},
    )
    token = (response.get("responseBody") or {}).get("accessToken")
    if not response.get("requestSuccessful") or not token:
        raise GatewayError(response.get("responseMessage") or "Monnify authentication failed.")
    return token


def monnify_signature_valid(config, raw_body, signature):
    _, secret_key, _ = _monnify_credentials(config)
    # Monnify does not attach this header to sandbox notifications. Those
    # notifications still cannot settle funds until the transaction API
    # independently matches status, amount, currency, payment, order and tenant.
    if not signature and "sandbox.monnify.com" in config.monnify_base_url.lower():
        return True
    expected = hmac.new(secret_key.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


def _initialize_monnify_transaction(payment, config, *, payment_methods, token=None):
    target = _payment_customer(payment)
    email = _require(target.customer_email, "A customer email is required for Monnify checkout.")
    _, _, contract_code = _monnify_credentials(config)
    payload = {
        "amount": str(payment.amount),
        "customerName": target.customer_name,
        "customerEmail": email,
        "paymentReference": payment.reference,
        "paymentDescription": f"INPROFIC commerce {payment.reference}",
        "currencyCode": payment.currency,
        "contractCode": contract_code,
        "paymentMethods": list(payment_methods),
        "metaData": {
            "storetrackPaymentId": str(payment.public_id),
            "storetrackCheckoutId": str(payment.checkout.public_id) if payment.checkout_id else "",
            "storetrackOrderId": str(payment.intake.public_id) if payment.intake_id else "",
            "businessId": str(payment.business_id),
        },
    }
    if payment.return_url:
        payload["redirectUrl"] = payment.return_url
    response = _json_request(
        f"{config.monnify_base_url.rstrip('/')}/api/v1/merchant/transactions/init-transaction",
        method="POST",
        headers={"Authorization": f"Bearer {token or _monnify_token(config)}", "Content-Type": "application/json"},
        payload=payload,
    )
    body = response.get("responseBody") or {}
    if not response.get("requestSuccessful") or not body.get("transactionReference"):
        raise GatewayError(response.get("responseMessage") or "Monnify could not initialize the transaction.")
    return body


def initialize_monnify(payment, config):
    body = _initialize_monnify_transaction(
        payment, config, payment_methods=["CARD", "ACCOUNT_TRANSFER", "USSD"]
    )
    if not body.get("checkoutUrl"):
        raise GatewayError("Monnify did not return a secure checkout URL.")
    return {
        "authorization_url": body["checkoutUrl"],
        "gateway_reference": body.get("transactionReference") or payment.reference,
        "metadata": {"provider_status": True},
    }


def initialize_monnify_bank_transfer(payment, config):
    """Create a one-time Monnify transfer account for this exact checkout.

    Monnify confirms the transfer asynchronously. INPROFIC never asks the
    customer or staff to manually attest that the transfer happened.
    """
    token = _monnify_token(config)
    body = _initialize_monnify_transaction(
        payment, config, payment_methods=["ACCOUNT_TRANSFER"], token=token
    )
    transaction_reference = body.get("transactionReference")
    bank_code = _require(
        (config.monnify_transfer_bank_code or "").strip(),
        "A Monnify transfer bank code is required for automated bank transfer.",
    )
    response = _json_request(
        f"{config.monnify_base_url.rstrip('/')}/api/v1/merchant/bank-transfer/init-payment",
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        payload={"transactionReference": transaction_reference, "bankCode": bank_code},
    )
    account = response.get("responseBody") or {}
    if not response.get("requestSuccessful") or not account.get("accountNumber"):
        raise GatewayError(response.get("responseMessage") or "Monnify could not issue a temporary transfer account.")
    return {
        "authorization_url": "",
        "gateway_reference": transaction_reference,
        "metadata": {
            "provider_status": response.get("requestSuccessful"),
            "bank_name": account.get("bankName") or "",
            "account_name": account.get("accountName") or "",
            "account_number": account.get("accountNumber") or "",
            "display_text": "Transfer the exact amount to this one-time Monnify account. Confirmation is automatic.",
            "account_expires_at": payment.expires_at.isoformat() if payment.expires_at else "",
            "monnify_transaction_reference": transaction_reference,
            "monnify_bank_code": bank_code,
        },
    }


def verify_monnify(payment, config):
    response = _json_request(
        f"{config.monnify_base_url.rstrip('/')}/api/v2/merchant/transactions/query?paymentReference={quote(payment.reference, safe='')}",
        headers={"Authorization": f"Bearer {_monnify_token(config)}"},
    )
    body = response.get("responseBody") or {}
    try:
        amount = Decimal(str(body.get("amountPaid") or "0"))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    metadata = body.get("metaData") or body.get("metadata") or {}
    verified = bool(
        response.get("requestSuccessful")
        and body.get("paymentStatus") == "PAID"
        and str(body.get("paymentReference") or "") == payment.reference
        and amount == Decimal(payment.amount)
        and str(body.get("currencyCode") or body.get("currency") or "").upper() == payment.currency.upper()
        and str(metadata.get("storetrackPaymentId") or "") == str(payment.public_id)
        and str(metadata.get("storetrackCheckoutId") or "") == (str(payment.checkout.public_id) if payment.checkout_id else "")
        and str(metadata.get("storetrackOrderId") or "") == (str(payment.intake.public_id) if payment.intake_id else "")
        and str(metadata.get("businessId") or "") == str(payment.business_id)
    )
    return verified, {
        "status": body.get("paymentStatus"),
        "reference": body.get("paymentReference"),
        "gateway_reference": body.get("transactionReference"),
        "amount": str(amount),
        "currency": body.get("currencyCode") or body.get("currency"),
        "paid_at": body.get("paidOn") or body.get("completedOn"),
    }


def initialize_gateway(payment: CommercePayment, config: CommercePaymentConfiguration):
    if payment.method == CommercePayment.METHOD_PAYSTACK:
        return initialize_paystack(payment, config)
    if payment.method == CommercePayment.METHOD_MONNIFY:
        return initialize_monnify(payment, config)
    if payment.method == CommercePayment.METHOD_BANK_TRANSFER and payment.gateway_provider == CommercePayment.GATEWAY_PAYSTACK:
        return initialize_paystack_bank_transfer(payment, config)
    if payment.method == CommercePayment.METHOD_BANK_TRANSFER and payment.gateway_provider == CommercePayment.GATEWAY_MONNIFY:
        return initialize_monnify_bank_transfer(payment, config)
    if payment.method == CommercePayment.METHOD_POS_CARD and payment.gateway_provider == CommercePayment.GATEWAY_PAYSTACK:
        return initialize_paystack_terminal(payment, config)
    raise GatewayError("This payment does not use a configured online gateway.")


def verify_gateway(payment: CommercePayment, config: CommercePaymentConfiguration):
    if payment.method == CommercePayment.METHOD_PAYSTACK:
        return verify_paystack(payment, config)
    if payment.method == CommercePayment.METHOD_MONNIFY:
        return verify_monnify(payment, config)
    if payment.method == CommercePayment.METHOD_BANK_TRANSFER and payment.gateway_provider == CommercePayment.GATEWAY_PAYSTACK:
        return verify_paystack(payment, config)
    if payment.method == CommercePayment.METHOD_BANK_TRANSFER and payment.gateway_provider == CommercePayment.GATEWAY_MONNIFY:
        return verify_monnify(payment, config)
    if payment.method == CommercePayment.METHOD_POS_CARD and payment.gateway_provider == CommercePayment.GATEWAY_PAYSTACK:
        return verify_paystack_terminal(payment, config)
    raise GatewayError("This payment does not use a configured online gateway.")
