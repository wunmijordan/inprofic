"""Server-side subscription payment integrations.

Provider secrets are read only from Django settings/environment. Browser callbacks
are never trusted as proof of payment; every successful-looking callback is
verified against the provider API before entitlements are extended.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError

from .models import SubscriptionPayment


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
            data = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GatewayError(f"Payment provider returned HTTP {exc.code}: {detail[:300]}") from exc
    except URLError as exc:
        raise GatewayError(f"Could not reach payment provider: {exc.reason}") from exc
    try:
        return json.loads(data or "{}")
    except json.JSONDecodeError as exc:
        raise GatewayError("Payment provider returned an unreadable response.") from exc


def _paystack_secret():
    key = getattr(settings, "PAYSTACK_SECRET_KEY", "")
    if not key:
        raise ImproperlyConfigured("PAYSTACK_SECRET_KEY is not configured.")
    return key


def initialize_paystack(payment: SubscriptionPayment, *, email: str, callback_url: str):
    secret = _paystack_secret()
    if not email:
        raise GatewayError("An email address is required for Paystack checkout.")
    amount_kobo = int((Decimal(payment.amount) * Decimal("100")).quantize(Decimal("1")))
    response = _json_request(
        "https://api.paystack.co/transaction/initialize",
        method="POST",
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
        payload={
            "email": email,
            "amount": str(amount_kobo),
            "currency": "NGN",
            "reference": payment.reference,
            "callback_url": callback_url,
            "metadata": json.dumps({
                "storetrack_subscription_payment_id": payment.pk,
                "business": payment.subscription.primary_business.name,
                "plan": payment.plan.code,
                "billing_cycle": payment.billing_cycle,
            }),
        },
    )
    if not response.get("status") or not response.get("data", {}).get("authorization_url"):
        raise GatewayError(response.get("message") or "Paystack could not initialize this payment.")
    data = response["data"]
    return {
        "checkout_url": data["authorization_url"],
        "provider_reference": data.get("reference") or payment.reference,
        "payload": response,
    }


def verify_paystack(payment: SubscriptionPayment):
    secret = _paystack_secret()
    response = _json_request(
        f"https://api.paystack.co/transaction/verify/{quote(payment.reference, safe='')}",
        headers={"Authorization": f"Bearer {secret}"},
    )
    data = response.get("data") or {}
    expected_kobo = int((Decimal(payment.amount) * Decimal("100")).quantize(Decimal("1")))
    ok = bool(
        response.get("status")
        and data.get("status") == "success"
        and int(data.get("amount") or 0) >= expected_kobo
        and str(data.get("currency") or "NGN").upper() == "NGN"
    )
    return ok, response


def paystack_signature_valid(raw_body: bytes, signature: str):
    secret = _paystack_secret().encode("utf-8")
    expected = hmac.new(secret, raw_body, hashlib.sha512).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


def _monnify_credentials():
    api_key = getattr(settings, "MONNIFY_API_KEY", "")
    secret_key = getattr(settings, "MONNIFY_SECRET_KEY", "")
    contract_code = getattr(settings, "MONNIFY_CONTRACT_CODE", "")
    if not (api_key and secret_key and contract_code):
        raise ImproperlyConfigured("MONNIFY_API_KEY, MONNIFY_SECRET_KEY and MONNIFY_CONTRACT_CODE must be configured.")
    return api_key, secret_key, contract_code


def _monnify_base_url():
    return getattr(settings, "MONNIFY_BASE_URL", "https://api.monnify.com").rstrip("/")


def _monnify_token():
    api_key, secret_key, _ = _monnify_credentials()
    credentials = base64.b64encode(f"{api_key}:{secret_key}".encode("utf-8")).decode("ascii")
    response = _json_request(
        f"{_monnify_base_url()}/api/v1/auth/login",
        method="POST",
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
        payload={},
    )
    token = (response.get("responseBody") or {}).get("accessToken")
    if not response.get("requestSuccessful") or not token:
        raise GatewayError(response.get("responseMessage") or "Monnify authentication failed.")
    return token


def initialize_monnify(payment: SubscriptionPayment, *, email: str, customer_name: str, callback_url: str):
    _, _, contract_code = _monnify_credentials()
    if not email:
        raise GatewayError("An email address is required for Monnify checkout.")
    token = _monnify_token()
    response = _json_request(
        f"{_monnify_base_url()}/api/v1/merchant/transactions/init-transaction",
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        payload={
            "amount": str(payment.amount),
            "customerName": customer_name or payment.subscription.primary_business.name,
            "customerEmail": email,
            "paymentReference": payment.reference,
            "paymentDescription": f"INPROFIC {payment.plan.name} {payment.get_billing_cycle_display()} subscription",
            "currencyCode": "NGN",
            "contractCode": contract_code,
            "redirectUrl": callback_url,
            "paymentMethods": ["CARD", "ACCOUNT_TRANSFER", "USSD"],
            "metadata": {
                "storetrackSubscriptionPaymentId": str(payment.pk),
                "plan": payment.plan.code,
                "billingCycle": payment.billing_cycle,
            },
        },
    )
    body = response.get("responseBody") or {}
    checkout_url = body.get("checkoutUrl")
    if not response.get("requestSuccessful") or not checkout_url:
        raise GatewayError(response.get("responseMessage") or "Monnify could not initialize this payment.")
    return {
        "checkout_url": checkout_url,
        "provider_reference": body.get("transactionReference") or payment.reference,
        "payload": response,
    }


def verify_monnify(payment: SubscriptionPayment):
    token = _monnify_token()
    response = _json_request(
        f"{_monnify_base_url()}/api/v2/merchant/transactions/query?paymentReference={quote(payment.reference, safe='')}",
        headers={"Authorization": f"Bearer {token}"},
    )
    body = response.get("responseBody") or {}
    try:
        amount_paid = Decimal(str(body.get("amountPaid") or "0"))
    except Exception:
        amount_paid = Decimal("0")
    ok = bool(
        response.get("requestSuccessful")
        and body.get("paymentStatus") == "PAID"
        and amount_paid >= Decimal(payment.amount)
        and str(body.get("currencyCode") or body.get("currency") or "NGN").upper() == "NGN"
    )
    return ok, response


def monnify_signature_valid(raw_body: bytes, signature: str):
    _, secret_key, _ = _monnify_credentials()
    # Monnify documents that sandbox webhook notifications may omit the
    # monnify-signature header. Sandbox events still undergo authoritative
    # server-side transaction verification before INPROFIC grants access.
    if not signature and "sandbox.monnify.com" in _monnify_base_url():
        return True
    expected = hmac.new(secret_key.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


def initialize_gateway(payment: SubscriptionPayment, *, email: str, customer_name: str, callback_url: str):
    if payment.provider == SubscriptionPayment.PROVIDER_PAYSTACK:
        return initialize_paystack(payment, email=email, callback_url=callback_url)
    if payment.provider == SubscriptionPayment.PROVIDER_MONNIFY:
        return initialize_monnify(payment, email=email, customer_name=customer_name, callback_url=callback_url)
    raise GatewayError("Choose Paystack or Monnify for online payment.")


def verify_gateway(payment: SubscriptionPayment):
    if payment.provider == SubscriptionPayment.PROVIDER_PAYSTACK:
        return verify_paystack(payment)
    if payment.provider == SubscriptionPayment.PROVIDER_MONNIFY:
        return verify_monnify(payment)
    raise GatewayError("This payment does not use an online gateway.")
