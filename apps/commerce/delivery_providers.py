from __future__ import annotations

import base64
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.platform_integrations import shipbubble_platform_enabled
from core.services import audit

from .models import CommerceNotification, DeliveryAssignment, DeliveryEvent, DeliveryProviderAccount
from .notification_services import queue_commerce_notification
from .realtime import publish_delivery_changed


class ProviderDispatchError(ValidationError):
    pass


def _require_shipbubble_platform():
    if not shipbubble_platform_enabled():
        raise ProviderDispatchError("This external delivery provider is not currently available.")


def _endpoint(account: DeliveryProviderAccount, endpoint: str) -> str:
    endpoint = (endpoint or "").strip()
    if not endpoint:
        raise ProviderDispatchError("Configure the provider endpoint before using this integration.")
    if endpoint.startswith(("http://", "https://")):
        return endpoint
    if not account.base_url:
        raise ProviderDispatchError("Configure the provider API base URL before using this integration.")
    return urljoin(account.base_url.rstrip("/") + "/", endpoint.lstrip("/"))


def _http_json(url: str, *, payload=None, method="POST", headers=None) -> dict:
    body = None if payload is None else json.dumps(payload, default=str).encode("utf-8")
    req_headers = {"Accept": "application/json", "User-Agent": "INPROFIC-Delivery/2.0"}
    if body is not None:
        req_headers["Content-Type"] = "application/json"
    req_headers.update(headers or {})
    req = Request(url, data=body, headers=req_headers, method=method)
    try:
        with urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw or "{}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:700]
        raise ProviderDispatchError(f"Provider rejected the request ({exc.code}). {detail}") from exc
    except URLError as exc:
        raise ProviderDispatchError(f"Provider endpoint could not be reached: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ProviderDispatchError("Provider returned a non-JSON response.") from exc


SHIPBUBBLE_BASE_URL = "https://api.shipbubble.com/v1"
SHIPBUBBLE_STATUS_MAP = {
    "pending": "assigned", "confirmed": "assigned", "assigned": "assigned", "ready": "ready",
    "picked_up": "picked_up", "in_transit": "picked_up", "completed": "delivered", "delivered": "delivered",
    "failed": "failed", "cancelled": "cancelled", "canceled": "cancelled", "returned": "returned",
}


def _shipbubble_request(account: DeliveryProviderAccount, path: str, *, payload=None, method="POST") -> dict:
    """Call the Shipbubble API with the business's own key (Bearer auth)."""
    _require_shipbubble_platform()
    if not account.api_key:
        raise ProviderDispatchError("Enter your Shipbubble API key first.")
    base = (account.base_url or SHIPBUBBLE_BASE_URL).rstrip("/")
    response = _http_json(
        f"{base}/{path.lstrip('/')}", payload=payload, method=method,
        headers={"Authorization": f"Bearer {account.api_key}"},
    )
    if not isinstance(response, dict):
        return {}
    if str(response.get("status") or "").lower() == "failed":
        errors = response.get("errors")
        detail = "; ".join(str(e) for e in errors) if isinstance(errors, list) and errors else ""
        raise ProviderDispatchError(f"Shipbubble: {response.get('message') or 'the request failed'}{(' — ' + detail) if detail else ''}")
    return response


def _sb_meta(account) -> dict:
    return account.metadata if isinstance(account.metadata, dict) else {}


def _sb_contact(account) -> dict:
    try:
        business = account.business
    except Exception:  # an unsaved/unlinked account has no business yet
        business = None
    return {
        "name": (getattr(business, "name", "") or "Store")[:100],
        "email": getattr(business, "contact_email", "") or "noreply@inprofic.com",
        "phone": getattr(business, "contact_phone", "") or "08000000000",
    }


def _sb_with_country(address: str) -> str:
    address = (address or "").strip()
    return address if "nigeria" in address.lower() else f"{address}, Nigeria"


def _sb_validate_address(account, *, name, email, phone, address, latitude=None, longitude=None) -> int:
    payload = {"name": name, "email": email, "phone": phone, "address": _sb_with_country(address)}
    if latitude not in (None, "") and longitude not in (None, ""):
        payload["latitude"] = float(latitude)
        payload["longitude"] = float(longitude)
    data = (_shipbubble_request(account, "shipping/address/validate", payload=payload).get("data") or {})
    code = data.get("address_code")
    if not code:
        raise ProviderDispatchError("Shipbubble could not validate that address. Check it includes the area, city and state.")
    return int(code)


def _sb_pickup_code(account) -> int:
    """The validated pickup address code; validated once from the delivery base and then remembered."""
    stored = (account.address_book_id or "").strip()
    if stored.isdigit():
        return int(stored)
    from .models import DeliveryOrigin
    origin = DeliveryOrigin.raw_objects.filter(business=account.business, active=True).order_by("-is_default", "id").first()
    if not origin:
        raise ProviderDispatchError("Add a delivery base (pickup address) before using Shipbubble.")
    contact = _sb_contact(account)
    code = _sb_validate_address(
        account, name=contact["name"], email=contact["email"], phone=contact["phone"],
        address=origin.address, latitude=origin.latitude, longitude=origin.longitude,
    )
    account.address_book_id = str(code)
    account.save(update_fields=["address_book_id", "updated_at"])
    return code


def _sb_category_id(account) -> int:
    meta = _sb_meta(account)
    if str(meta.get("category_id") or "").isdigit():
        return int(meta["category_id"])
    data = _shipbubble_request(account, "shipping/labels/categories", method="GET").get("data") or []
    pick = next((c for c in data if "food" in str(c.get("category", "")).lower()), None) or (data[0] if data else None)
    if not pick:
        raise ProviderDispatchError("Shipbubble returned no package categories.")
    account.metadata = {**meta, "category_id": pick["category_id"]}
    account.save(update_fields=["metadata", "updated_at"])
    return int(pick["category_id"])


def _sb_package(account, subtotal) -> dict:
    meta = _sb_meta(account)

    def number(key, default):
        try:
            value = Decimal(str(meta.get(key) or default))
            return value if value > 0 else Decimal(str(default))
        except (InvalidOperation, ValueError):
            return Decimal(str(default))

    amount = int(Decimal(str(subtotal or 0))) or 1
    return {
        "package_items": [{
            "name": "Order", "description": "Customer order",
            "unit_weight": str(number("default_weight_kg", 1)), "unit_amount": str(amount), "quantity": "1",
        }],
        "package_dimension": {
            "length": int(number("package_length_cm", 30)), "width": int(number("package_width_cm", 30)),
            "height": int(number("package_height_cm", 15)),
        },
    }


def _sb_pickup_date() -> str:
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    now = timezone.now().astimezone(ZoneInfo("Africa/Lagos"))
    # Rates requested after 6 PM are processed the next day.
    return (now + timedelta(days=1 if now.hour >= 18 else 0)).strftime("%Y-%m-%d")


def _sb_fetch_rates(account, *, sender_code, receiver_code, subtotal, instructions="") -> dict:
    payload = {
        "sender_address_code": sender_code, "reciever_address_code": receiver_code,  # (sic) Shipbubble's spelling
        "pickup_date": _sb_pickup_date(), "category_id": _sb_category_id(account),
        "service_type": "pickup", **_sb_package(account, subtotal),
    }
    if instructions:
        payload["delivery_instructions"] = instructions[:250]
    data = _shipbubble_request(account, "shipping/fetch_rates", payload=payload).get("data") or {}
    if not data.get("request_token") or not data.get("couriers"):
        raise ProviderDispatchError("Shipbubble has no courier available for this destination right now.")
    return data


def _sb_pick_courier(data: dict, account, wanted: dict | None = None) -> dict:
    couriers = data.get("couriers") or []
    wanted = wanted or {}
    for courier in couriers:
        if wanted.get("service_code") and str(courier.get("service_code")) == str(wanted["service_code"]) \
                and str(courier.get("courier_id")) == str(wanted.get("courier_id")):
            return courier
    key = "fastest_courier" if _sb_meta(account).get("courier_preference") == "fastest" else "cheapest_courier"
    return data.get(key) or min(couriers, key=lambda c: Decimal(str(c.get("rate_card_amount") or c.get("total") or 0)))


def _sb_minutes_until(value, fallback):
    from zoneinfo import ZoneInfo
    parsed = parse_datetime(str(value or "").replace(" ", "T"))
    if not parsed:
        return fallback
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, ZoneInfo("Africa/Lagos"))
    return max(1, min(32000, int((parsed - timezone.now()).total_seconds() // 60)))

def _generic_headers(account: DeliveryProviderAccount) -> dict:
    """Build headers for the provider-neutral adapter contract.

    Custom providers can point INPROFIC at their own integration service.  The
    optional metadata keys are intentionally small and explicit so arbitrary
    provider-specific code never leaks into the delivery engine:

    - auth_type: api_key (default), bearer, basic, or none
    - api_key_header: defaults to X-API-Key
    - api_secret_header: optional second secret header
    - headers: optional static string header mapping
    """
    metadata = account.metadata if isinstance(account.metadata, dict) else {}
    headers = {}
    for key, value in (metadata.get("headers") or {}).items():
        if isinstance(key, str) and isinstance(value, (str, int, float, bool)):
            headers[key] = str(value)
    auth_type = str(metadata.get("auth_type") or "api_key").strip().lower()
    if auth_type == "bearer" and account.api_key:
        headers["Authorization"] = f"Bearer {account.api_key}"
    elif auth_type == "basic" and (account.api_key or account.api_secret):
        token = base64.b64encode(f"{account.api_key}:{account.api_secret}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    elif auth_type == "api_key":
        if account.api_key:
            headers[str(metadata.get("api_key_header") or "X-API-Key")] = account.api_key
        elif account.api_secret:
            headers[str(metadata.get("api_secret_header") or "X-API-Secret")] = account.api_secret
    if account.api_secret and account.api_key and metadata.get("api_secret_header"):
        headers[str(metadata["api_secret_header"])] = account.api_secret
    return headers


def _generic_request(account: DeliveryProviderAccount, endpoint: str, *, payload=None, method="POST") -> dict:
    return _http_json(
        _endpoint(account, endpoint),
        payload=payload,
        method=method,
        headers=_generic_headers(account),
    )


def test_generic_provider_connection(account: DeliveryProviderAccount) -> dict:
    """Run an explicitly non-mutating health check for a custom provider adapter."""
    if account.provider_code != DeliveryProviderAccount.PROVIDER_GENERIC:
        raise ProviderDispatchError("Connection testing is available only for custom delivery partners.")
    if not account.health_endpoint:
        raise ProviderDispatchError("Configure a non-mutating health endpoint before testing this connection.")
    return _generic_request(account, account.health_endpoint, payload=None, method="GET")


def _generic_tracking_url(account: DeliveryProviderAccount, provider_id: str, explicit: str = "") -> str:
    """Resolve a tracking URL without giving the provider control over INPROFIC state.

    A custom partner may return a full URL per job.  Otherwise the merchant can
    configure a public tracking URL/template. ``{external_reference}`` is the
    preferred placeholder; a plain base URL falls back to appending the escaped
    provider reference.
    """
    explicit = str(explicit or "").strip()
    if explicit:
        return explicit[:500]
    provider_id = str(provider_id or "").strip()
    template = str(account.tracking_base_url or "").strip()
    if not provider_id or not template:
        return ""
    escaped = quote(provider_id, safe="")
    if "{external_reference}" in template:
        return template.replace("{external_reference}", escaped)[:500]
    return urljoin(template.rstrip("/") + "/", escaped)[:500]


def _generic_dispatch_payload(assignment: DeliveryAssignment, account: DeliveryProviderAccount) -> dict:
    """Stable v1 payload for merchant-owned custom delivery adapters."""
    intake = assignment.intake
    quote = assignment.quote
    origin = assignment.origin
    return {
        "schema": "inprofic.delivery.v1",
        "event": "dispatch",
        "partner": {
            "store_id": account.store_id or "",
        },
        "delivery": {
            "id": str(assignment.public_id),
            "order_number": intake.public_number,
            "customer": {
                "name": intake.customer_name,
                "phone": intake.customer_phone,
                "email": intake.customer_email or "",
            },
            "pickup": {
                "name": origin.name,
                "address": origin.address,
                "latitude": float(origin.latitude) if origin.latitude is not None else None,
                "longitude": float(origin.longitude) if origin.longitude is not None else None,
            },
            "destination": {
                "address": intake.customer_address or (quote.destination_address if quote else ""),
                "latitude": float(quote.destination_latitude) if quote and quote.destination_latitude is not None else None,
                "longitude": float(quote.destination_longitude) if quote and quote.destination_longitude is not None else None,
            },
            "amounts": {
                "currency": getattr(assignment.business, "currency", "NGN") or "NGN",
                "delivery_fee": str(intake.delivery_fee or 0),
                "order_total": str(intake.total or 0),
            },
            "quote": {
                "id": str(quote.public_id) if quote else "",
                "distance_km": str(quote.distance_km) if quote else "",
                "eta_min_minutes": quote.eta_min_minutes if quote else None,
                "eta_max_minutes": quote.eta_max_minutes if quote else None,
            },
        },
    }


def _money(value, label):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProviderDispatchError(f"Shipbubble returned an invalid {label}.") from exc


def quote_shipbubble_delivery(account: DeliveryProviderAccount, *, destination_address, latitude=None, longitude=None, subtotal=0) -> dict:
    """Live courier rate for the checkout. The customer pays the courier's displayed rate."""
    _require_shipbubble_platform()
    if not account.is_configured_for_quote:
        raise ProviderDispatchError("Add your Shipbubble API key and activate the account first.")
    contact = _sb_contact(account)
    sender = _sb_pickup_code(account)
    # The real customer details are not known at quote time, so the quote uses the
    # business contact; dispatch re-validates the address with the real recipient.
    receiver = _sb_validate_address(
        account, name="Customer", email=contact["email"], phone=contact["phone"],
        address=destination_address, latitude=latitude, longitude=longitude,
    )
    data = _sb_fetch_rates(account, sender_code=sender, receiver_code=receiver, subtotal=subtotal)
    courier = _sb_pick_courier(data, account)
    fee = _money(courier.get("rate_card_amount") if courier.get("rate_card_amount") is not None else courier.get("total"), "rate")
    eta_max = _sb_minutes_until(courier.get("delivery_eta_time"), 24 * 60)
    eta_min = min(eta_max, _sb_minutes_until(courier.get("pickup_eta_time"), 60))
    trimmed = {k: courier.get(k) for k in (
        "courier_id", "courier_name", "service_code", "service_type", "rate_card_amount", "total",
        "pickup_eta", "delivery_eta", "pickup_eta_time", "delivery_eta_time",
    )}
    return {
        "quote_id": str(data["request_token"])[:160],
        "fee": fee,
        "distance_km": Decimal("0.00"),
        "eta_min_minutes": eta_min,
        "eta_max_minutes": eta_max,
        "expires_at": timezone.now() + timezone.timedelta(minutes=30),
        "currency": str(courier.get("currency") or courier.get("rate_card_currency") or "NGN"),
        "response": {"courier": trimmed},
    }

def _shipbubble_status(value) -> str:
    return re.sub(r"[\s-]+", "_", str(value or "pending").strip().lower())

def _map_provider_status(account: DeliveryProviderAccount, status: str) -> str:
    mapping = account.status_mapping if isinstance(account.status_mapping, dict) else {}
    return str(mapping.get(status) or mapping.get(status.lower()) or "").strip()


def ensure_builtin_provider_accounts(business):
    """Materialize the optional Shipbubble account only while the founder exposes it."""
    if not shipbubble_platform_enabled():
        return None, False
    existing = DeliveryProviderAccount.raw_objects.filter(
        business=business, provider_code=DeliveryProviderAccount.PROVIDER_SHIPBUBBLE
    ).order_by("id").first()
    if existing:
        return existing, False
    name = "Shipbubble"
    if DeliveryProviderAccount.raw_objects.filter(business=business, name=name).exists():
        name = "Shipbubble couriers"
    account = DeliveryProviderAccount.raw_objects.create(
        business=business, name=name, provider_code=DeliveryProviderAccount.PROVIDER_SHIPBUBBLE,
        active=False, sandbox=False, auto_dispatch=False, use_live_quotes=True,
        base_url=SHIPBUBBLE_BASE_URL, status_mapping=dict(SHIPBUBBLE_STATUS_MAP),
        metadata={"courier_preference": "cheapest", "default_weight_kg": "1",
                  "package_length_cm": "30", "package_width_cm": "30", "package_height_cm": "15"},
    )
    return account, True

def dispatch_assignment_to_provider(assignment: DeliveryAssignment, *, actor=None) -> DeliveryAssignment:
    assignment = DeliveryAssignment.raw_objects.select_related("provider_account", "origin", "quote", "intake", "business", "driver__user").get(pk=assignment.pk)
    account = assignment.provider_account
    if not account or not account.active:
        return assignment
    if account.provider_code == DeliveryProviderAccount.PROVIDER_GENERIC:
        if not account.auto_dispatch:
            return assignment
        if not account.is_configured_for_automatic_dispatch:
            raise ProviderDispatchError(
                "Complete the custom delivery partner's order endpoint and API credentials before enabling automatic dispatch."
            )
        payload = _generic_dispatch_payload(assignment, account)
        response = _generic_request(account, account.order_endpoint, payload=payload)
        provider_id = str(
            response.get("external_reference") or response.get("tracking_number")
            or response.get("trackingNumber") or response.get("orderCode")
            or response.get("reference") or response.get("id") or ""
        ).strip()[:160]
        status = str(response.get("status") or response.get("state") or "ASSIGNED").strip()
        mapped = _map_provider_status(account, status)
        tracking_url = _generic_tracking_url(account, provider_id, response.get("tracking_url") or response.get("trackingUrl") or "")
        assignment.provider_payload = {
            "contract": "inprofic.delivery.v1",
            "request": payload,
            "response": response,
            "dispatched_at": timezone.now().isoformat(),
        }
        assignment.provider_order_id = provider_id
        assignment.provider_status = status[:80]
        if provider_id:
            assignment.external_reference = provider_id
        if tracking_url:
            assignment.external_tracking_url = tracking_url
        valid_statuses = {value for value, _ in DeliveryAssignment.STATUS_CHOICES}
        if mapped in valid_statuses:
            assignment.status = mapped
        elif status.lower() in valid_statuses:
            assignment.status = status.lower()
        elif assignment.status == DeliveryAssignment.STATUS_PENDING:
            assignment.status = DeliveryAssignment.STATUS_ASSIGNED
        assignment.save(update_fields=[
            "provider_payload", "provider_order_id", "provider_status", "external_reference",
            "external_tracking_url", "status", "updated_at",
        ])
        DeliveryEvent.raw_objects.create(
            business=assignment.business, created_by=actor, assignment=assignment, status=assignment.status,
            note=f"Delivery sent to {account.name}.",
            metadata={"provider": "generic", "provider_account": account.pk, "provider_status": status},
        )
        audit(
            assignment.business, actor, "delivery_provider_dispatch", assignment,
            f"Delivery {assignment.public_id} dispatched to delivery partner",
            {"provider_account": account.pk, "tracking_number": provider_id, "provider_status": status},
        )
        queue_commerce_notification(
            business=assignment.business,
            event_type=CommerceNotification.EVENT_DELIVERY_PROVIDER,
            title=f"Sent to {account.name} · {assignment.intake.public_number}",
            message=f"Reference {provider_id or 'pending'} · {status}",
            target_url="/delivery/",
            dedupe_key=f"delivery:{assignment.pk}:provider-dispatch:{account.pk}:{provider_id or assignment.public_id}",
        )
        publish_delivery_changed(
            assignment.business_id, assignment.public_id, reason="provider_dispatch",
            rider_user_ids=(assignment.driver.user_id,) if assignment.driver_id and assignment.driver and assignment.driver.user_id else (),
        )
        return assignment
    if account.provider_code != DeliveryProviderAccount.PROVIDER_SHIPBUBBLE:
        return assignment
    _require_shipbubble_platform()
    if not account.is_configured_for_dispatch:
        DeliveryEvent.raw_objects.create(
            business=assignment.business, created_by=actor, assignment=assignment, status=assignment.status,
            note="Shipbubble is enabled but not fully configured; delivery awaits manual dispatch.",
            metadata={"provider": "shipbubble", "configured": False},
        )
        return assignment
    intake = assignment.intake
    contact = _sb_contact(account)
    sender = _sb_pickup_code(account)
    # Re-validate with the real recipient so the courier label carries their name and phone.
    receiver = _sb_validate_address(
        account, name=intake.customer_name or "Customer", email=intake.customer_email or contact["email"],
        phone=intake.customer_phone or contact["phone"], address=intake.customer_address,
        latitude=getattr(intake, "latitude", None), longitude=getattr(intake, "longitude", None),
    )
    subtotal = assignment.quote.subtotal if assignment.quote_id else intake.total
    rates = _sb_fetch_rates(account, sender_code=sender, receiver_code=receiver, subtotal=subtotal)
    quoted = ((assignment.quote.provider_payload or {}).get("response") or {}).get("courier") if assignment.quote_id else {}
    courier = _sb_pick_courier(rates, account, quoted or {})
    request_body = {"request_token": rates["request_token"], "service_code": courier.get("service_code"), "courier_id": str(courier.get("courier_id"))}
    response = _shipbubble_request(account, "shipping/labels", payload=request_body)
    data = response.get("data") or {}
    provider_id = str(data.get("order_id") or "")[:160]
    status = _shipbubble_status(data.get("status"))
    mapped = _map_provider_status(account, status) or SHIPBUBBLE_STATUS_MAP.get(status, "")
    tracking_url = str(data.get("tracking_url") or "")[:500]
    assignment.provider_payload = {
        "request": request_body, "courier": {k: courier.get(k) for k in ("courier_id", "courier_name", "service_code", "total", "rate_card_amount")},
        "response": {"order_id": provider_id, "status": status, "courier": data.get("courier")},
        "dispatched_at": timezone.now().isoformat(),
    }
    assignment.provider_order_id = provider_id
    assignment.provider_status = status[:80]
    if provider_id:
        assignment.external_reference = provider_id
    if tracking_url:
        assignment.external_tracking_url = tracking_url
    valid_statuses = {value for value, _ in DeliveryAssignment.STATUS_CHOICES}
    if mapped in valid_statuses:
        assignment.status = mapped
    elif assignment.status == DeliveryAssignment.STATUS_PENDING:
        assignment.status = DeliveryAssignment.STATUS_ASSIGNED
    assignment.save(update_fields=["provider_payload", "provider_order_id", "provider_status", "external_reference", "external_tracking_url", "status", "updated_at"])
    DeliveryEvent.raw_objects.create(
        business=assignment.business, created_by=actor, assignment=assignment, status=assignment.status,
        note=f"Booked with {courier.get('courier_name') or 'a Shipbubble courier'}.",
        metadata={"provider": "shipbubble", "order_id": provider_id, "provider_status": status},
    )
    audit(assignment.business, actor, "delivery_provider_dispatch", assignment,
          f"Delivery {assignment.public_id} booked with Shipbubble", {"order_id": provider_id, "provider_status": status})
    queue_commerce_notification(
        business=assignment.business, event_type=CommerceNotification.EVENT_DELIVERY_PROVIDER,
        title=f"Booked on Shipbubble · {intake.public_number}",
        message=f"{courier.get('courier_name') or 'Courier'} · {provider_id or 'pending'}", target_url="/delivery/",
        dedupe_key=f"delivery:{assignment.pk}:shipbubble-dispatch:{provider_id}",
    )
    publish_delivery_changed(
        assignment.business_id, assignment.public_id, reason="provider_dispatch",
        rider_user_ids=(assignment.driver.user_id,) if assignment.driver_id and assignment.driver and assignment.driver.user_id else (),
    )
    return assignment


def cancel_assignment_with_provider(assignment: DeliveryAssignment, *, actor=None):
    assignment = DeliveryAssignment.raw_objects.select_related("provider_account", "business").get(pk=assignment.pk)
    account = assignment.provider_account
    if not account or not assignment.provider_order_id:
        return assignment
    if account.provider_code == DeliveryProviderAccount.PROVIDER_GENERIC:
        if not account.cancel_endpoint:
            return assignment
        endpoint = account.cancel_endpoint.replace("{external_reference}", assignment.provider_order_id)
        response = _generic_request(
            account,
            endpoint,
            payload={
                "schema": "inprofic.delivery.v1",
                "event": "cancel",
                "delivery_id": str(assignment.public_id),
                "external_reference": assignment.provider_order_id,
            },
        )
        DeliveryEvent.raw_objects.create(
            business=assignment.business, created_by=actor, assignment=assignment, status=assignment.status,
            note=f"Cancellation request sent to {account.name}; local cancellation follows.",
            metadata={"provider": "generic", "provider_account": account.pk, "response": response},
        )
        audit(
            assignment.business, actor, "delivery_provider_cancel", assignment,
            f"Delivery partner cancellation requested for {assignment.public_id}",
            {"provider_account": account.pk, "tracking_number": assignment.provider_order_id},
        )
        return assignment
    if account.provider_code != DeliveryProviderAccount.PROVIDER_SHIPBUBBLE:
        return assignment
    _require_shipbubble_platform()
    # Shipbubble only cancels a shipment that has not been processed yet.
    response = _shipbubble_request(account, f"shipping/labels/cancel/{quote(assignment.provider_order_id, safe='')}", payload={})
    DeliveryEvent.raw_objects.create(
        business=assignment.business, created_by=actor, assignment=assignment, status=assignment.status,
        note="Shipbubble cancelled the booking; local delivery cancellation follows.",
        metadata={"provider": "shipbubble", "order_id": assignment.provider_order_id, "response": {"message": response.get("message")}},
    )
    audit(assignment.business, actor, "delivery_provider_cancel", assignment,
          f"Shipbubble cancellation requested for {assignment.public_id}", {"order_id": assignment.provider_order_id})
    queue_commerce_notification(
        business=assignment.business, event_type=CommerceNotification.EVENT_DELIVERY_PROVIDER,
        title=f"Shipbubble booking cancelled · {assignment.intake.public_number}",
        message=f"Order {assignment.provider_order_id}", target_url="/delivery/",
        dedupe_key=f"delivery:{assignment.pk}:shipbubble-cancel:{assignment.provider_order_id}",
    )
    return assignment


def consume_generic_provider_webhook(*, business, account_id, payload: dict, header_secret="", actor=None) -> DeliveryAssignment | None:
    """Consume the stable INPROFIC v1 status callback for a custom partner.

    This is deliberately provider-neutral.  A courier or merchant-owned adapter
    only needs to send the external reference plus its status; the tenant's
    status mapping translates that value into INPROFIC's delivery states.
    """
    secret = (header_secret or "").strip()
    if secret.lower().startswith("bearer "):
        secret = secret[7:].strip()
    if not secret:
        raise ProviderDispatchError("Missing delivery-partner webhook secret.")
    account = DeliveryProviderAccount.raw_objects.filter(
        business=business,
        pk=account_id,
        provider_code=DeliveryProviderAccount.PROVIDER_GENERIC,
        active=True,
        webhook_secret=secret,
    ).first()
    if not account:
        raise ProviderDispatchError("Invalid delivery-partner webhook secret.")
    provider_id = str(
        payload.get("external_reference") or payload.get("tracking_number")
        or payload.get("trackingNumber") or payload.get("reference") or payload.get("id") or ""
    ).strip()
    status = str(payload.get("status") or payload.get("state") or "").strip()
    if not provider_id:
        return None
    assignment = DeliveryAssignment.raw_objects.select_related("quote", "intake", "driver__user").filter(
        business=business, provider_account=account, provider_order_id=provider_id
    ).first()
    if not assignment:
        assignment = DeliveryAssignment.raw_objects.select_related("quote", "intake", "driver__user").filter(
            business=business, provider_account=account, external_reference=provider_id
        ).first()
    if not assignment:
        return None
    mapped = _map_provider_status(account, status)
    valid_statuses = {value for value, _ in DeliveryAssignment.STATUS_CHOICES}
    previous = assignment.status
    if mapped in valid_statuses:
        assignment.status = mapped
    elif status.lower() in valid_statuses:
        assignment.status = status.lower()
    assignment.provider_status = status[:80]
    tracking_url = _generic_tracking_url(account, provider_id, payload.get("tracking_url") or payload.get("trackingUrl") or "")
    if tracking_url:
        assignment.external_tracking_url = tracking_url
    assignment.provider_payload = {
        **(assignment.provider_payload or {}),
        "last_webhook": payload,
        "last_webhook_at": timezone.now().isoformat(),
    }
    now = timezone.now()
    update_fields = ["status", "provider_status", "provider_payload", "external_tracking_url", "updated_at"]
    if assignment.status in {DeliveryAssignment.STATUS_PICKED_UP, DeliveryAssignment.STATUS_OUT_FOR_DELIVERY} and not assignment.picked_up_at:
        assignment.picked_up_at = now
        update_fields.append("picked_up_at")
        if assignment.quote_id:
            assignment.eta_at = now + timezone.timedelta(minutes=assignment.quote.eta_max_minutes)
            update_fields.append("eta_at")
    if assignment.status == DeliveryAssignment.STATUS_DELIVERED and not assignment.delivered_at:
        assignment.delivered_at = now
        update_fields.append("delivered_at")
    assignment.save(update_fields=update_fields)
    DeliveryEvent.raw_objects.create(
        business=business, created_by=actor, assignment=assignment, status=assignment.status,
        note=f"{account.name} status update received.",
        metadata={"previous_status": previous, "provider_status": status, "provider_account": account.pk},
    )
    audit(
        business, actor, "delivery_provider_webhook", assignment,
        f"Delivery partner updated delivery {assignment.public_id}",
        {"provider_account": account.pk, "provider_status": status, "mapped_status": assignment.status},
    )
    if status:
        queue_commerce_notification(
            business=business, event_type=CommerceNotification.EVENT_DELIVERY_PROVIDER,
            title=f"{account.name} update · {assignment.intake.public_number}",
            message=f"{status} → {assignment.get_status_display()}", target_url="/delivery/",
            dedupe_key=f"delivery:{assignment.pk}:provider-status:{account.pk}:{status}:{payload.get('event_id') or ''}",
        )
    publish_delivery_changed(
        business.pk, assignment.public_id, reason="provider_status",
        rider_user_ids=(assignment.driver.user_id,) if assignment.driver_id and assignment.driver and assignment.driver.user_id else (),
    )
    return assignment


def consume_shipbubble_webhook(*, business, raw_body: bytes, payload: dict, signature="", actor=None) -> DeliveryAssignment | None:
    """Apply a signed Shipbubble status event. The signature is HMAC-SHA512 of the body keyed by the account's API key."""
    import hashlib
    import hmac
    _require_shipbubble_platform()
    signature = (signature or "").strip().lower()
    if not signature:
        raise ProviderDispatchError("Missing Shipbubble webhook signature.")
    account = None
    for candidate in DeliveryProviderAccount.raw_objects.filter(
        business=business, provider_code=DeliveryProviderAccount.PROVIDER_SHIPBUBBLE, active=True,
    ).exclude(api_key=""):
        expected = hmac.new(candidate.api_key.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
        if hmac.compare_digest(expected, signature):
            account = candidate
            break
    if not account:
        raise ProviderDispatchError("Invalid Shipbubble webhook signature.")
    event = str(payload.get("event") or "")
    if event == "wallet.threshold.alert":
        queue_commerce_notification(
            business=business, event_type=CommerceNotification.EVENT_DELIVERY_PROVIDER,
            title="Shipbubble wallet is running low", message="Top up your Shipbubble wallet so courier bookings are not declined.",
            target_url="/delivery/", dedupe_key=f"shipbubble:{business.pk}:wallet-low:{timezone.now():%Y%m%d}",
        )
        return None
    if not event.startswith("shipment."):
        return None
    provider_id = str(payload.get("order_id") or "").strip()
    if not provider_id:
        return None
    status = "cancelled" if event == "shipment.cancelled" else _shipbubble_status(payload.get("status"))
    assignment = DeliveryAssignment.raw_objects.select_related("quote", "intake", "driver__user").filter(
        business=business, provider_account=account, provider_order_id=provider_id
    ).first()
    if not assignment:
        return None
    mapped = _map_provider_status(account, status) or SHIPBUBBLE_STATUS_MAP.get(status, "")
    valid_statuses = {value for value, _ in DeliveryAssignment.STATUS_CHOICES}
    previous = assignment.status
    if mapped in valid_statuses:
        assignment.status = mapped
    assignment.provider_status = status[:80]
    tracking_url = str(payload.get("tracking_url") or "")[:500]
    if tracking_url and not assignment.external_tracking_url:
        assignment.external_tracking_url = tracking_url
    assignment.provider_payload = {**(assignment.provider_payload or {}), "last_webhook": payload, "last_webhook_at": timezone.now().isoformat()}
    now = timezone.now()
    pickup_fields = []
    if assignment.status in {DeliveryAssignment.STATUS_PICKED_UP, DeliveryAssignment.STATUS_OUT_FOR_DELIVERY} and not assignment.picked_up_at:
        assignment.picked_up_at = now
        pickup_fields.append("picked_up_at")
        if assignment.quote_id:
            assignment.eta_at = now + timezone.timedelta(minutes=assignment.quote.eta_max_minutes)
            pickup_fields.append("eta_at")
    if assignment.status == DeliveryAssignment.STATUS_DELIVERED and not assignment.delivered_at:
        assignment.delivered_at = now
    assignment.save(update_fields=["status", "provider_status", "provider_payload", "external_tracking_url", "delivered_at", *pickup_fields, "updated_at"])
    DeliveryEvent.raw_objects.create(
        business=business, created_by=actor, assignment=assignment, status=assignment.status,
        note="Shipbubble status update received.",
        metadata={"previous_status": previous, "provider_status": status, "event_type": event},
    )
    audit(business, actor, "delivery_provider_webhook", assignment, f"Shipbubble updated delivery {assignment.public_id}",
          {"provider_status": status, "mapped_status": assignment.status})
    if status != previous:
        queue_commerce_notification(
            business=business, event_type=CommerceNotification.EVENT_DELIVERY_PROVIDER,
            title=f"Shipbubble update · {assignment.intake.public_number}",
            message=f"{status} → {assignment.get_status_display()}", target_url="/delivery/",
            dedupe_key=f"delivery:{assignment.pk}:shipbubble-status:{status}",
        )
    publish_delivery_changed(
        business.pk, assignment.public_id, reason="provider_status",
        rider_user_ids=(assignment.driver.user_id,) if assignment.driver_id and assignment.driver and assignment.driver.user_id else (),
    )
    return assignment
