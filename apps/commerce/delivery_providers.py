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

from accounts.platform_integrations import glovo_platform_enabled
from core.services import audit

from .models import CommerceNotification, DeliveryAssignment, DeliveryEvent, DeliveryProviderAccount
from .notification_services import queue_commerce_notification
from .realtime import publish_delivery_changed


class ProviderDispatchError(ValidationError):
    pass


def _require_glovo_platform():
    if not glovo_platform_enabled():
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


def _glovo_token(account: DeliveryProviderAccount) -> str:
    _require_glovo_platform()
    if not account.api_key or not account.api_secret:
        raise ProviderDispatchError("Configure the Glovo client ID and client secret first.")
    response = _http_json(
        _endpoint(account, account.auth_endpoint or "/oauth/token"),
        payload={"grantType": "client_credentials", "clientId": account.api_key, "clientSecret": account.api_secret},
    )
    token = str(response.get("accessToken") or response.get("access_token") or "").strip()
    if not token:
        raise ProviderDispatchError("Glovo authentication succeeded without returning an access token.")
    return token


def _glovo_request(account: DeliveryProviderAccount, endpoint: str, *, payload=None, method="POST") -> dict:
    return _http_json(
        _endpoint(account, endpoint),
        payload=payload,
        method=method,
        headers={"Authorization": f"Bearer {_glovo_token(account)}"},
    )


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


def _duration_minutes(value, fallback=0):
    """Convert the ISO-8601 hour/minute duration returned by LaaS into minutes."""
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(value or "").strip())
    if not match:
        return int(fallback or 0)
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return max(1, hours * 60 + minutes + (1 if seconds else 0))


def _money(value, label):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProviderDispatchError(f"Glovo returned an invalid {label}.") from exc


def quote_glovo_delivery(account: DeliveryProviderAccount, *, destination_address, latitude=None, longitude=None) -> dict:
    _require_glovo_platform()
    if not account.is_configured_for_quote:
        raise ProviderDispatchError("Complete the Glovo LaaS live-quote configuration first.")
    delivery_address = {"rawAddress": (destination_address or "").strip()}
    if latitude not in (None, "") and longitude not in (None, ""):
        delivery_address["coordinates"] = {"latitude": float(latitude), "longitude": float(longitude)}
    response = _glovo_request(
        account,
        account.quote_endpoint,
        payload={
            "pickupDetails": {"addressBook": {"id": account.address_book_id}},
            "deliveryAddress": delivery_address,
        },
    )
    quote_id = str(response.get("quoteId") or response.get("id") or "").strip()
    if not quote_id:
        raise ProviderDispatchError("Glovo did not return a quote ID.")
    fee = _money(response.get("quotePrice"), "quote price")
    distance_m = Decimal(str(response.get("distanceInMeters") or 0))
    eta = response.get("estimatedTimeOfDelivery") or {}
    expires_at = parse_datetime(str(response.get("expiresAt") or ""))
    if expires_at and timezone.is_naive(expires_at):
        expires_at = timezone.make_aware(expires_at, timezone.get_current_timezone())
    return {
        "quote_id": quote_id,
        "fee": fee,
        "distance_km": (distance_m / Decimal("1000")).quantize(Decimal("0.01")),
        "eta_min_minutes": _duration_minutes(eta.get("lowerBound"), 20),
        "eta_max_minutes": _duration_minutes(eta.get("upperBound"), 60),
        "expires_at": expires_at or timezone.now() + timezone.timedelta(minutes=10),
        "currency": str(response.get("currencyCode") or "").strip(),
        "response": response,
    }


def _glovo_parcel_payload(assignment: DeliveryAssignment) -> dict:
    intake = assignment.intake
    quote = assignment.quote
    provider_response = ((quote.provider_payload or {}).get("response") if quote else {}) or {}
    currency = str(provider_response.get("currencyCode") or getattr(assignment.business, "currency", "NGN") or "NGN")
    subtotal = quote.subtotal if quote else intake.total
    payload = {
        "contact": {
            "name": intake.customer_name,
            "phone": intake.customer_phone,
        },
        "pickupOrderCode": intake.public_number,
        "price": {
            "paymentType": "PAID",
            "delivery": {"currencyCode": currency, "value": float(intake.delivery_fee or 0)},
            "parcel": {"currencyCode": currency, "value": float(subtotal or 0)},
        },
    }
    if intake.customer_email:
        payload["contact"]["email"] = intake.customer_email
    if intake.customer_address:
        payload["deliveryAddress"] = {"details": intake.customer_address[:250]}
    return payload


def _glovo_status(response: dict) -> str:
    status = response.get("status") or ""
    if isinstance(status, dict):
        status = status.get("state") or status.get("status") or ""
    return str(status or "CREATED").strip().upper()


def _map_provider_status(account: DeliveryProviderAccount, status: str) -> str:
    mapping = account.status_mapping if isinstance(account.status_mapping, dict) else {}
    return str(mapping.get(status) or mapping.get(status.lower()) or "").strip()


def ensure_builtin_provider_accounts(business):
    """Materialize optional built-ins only while the founder exposes them."""
    if not glovo_platform_enabled():
        return None, False
    existing = DeliveryProviderAccount.raw_objects.filter(
        business=business, provider_code=DeliveryProviderAccount.PROVIDER_GLOVO
    ).order_by("id").first()
    if existing:
        return existing, False
    name = "Glovo"
    if DeliveryProviderAccount.raw_objects.filter(business=business, name=name).exists():
        name = "Glovo delivery"
    account = DeliveryProviderAccount.raw_objects.create(
        business=business,
        name=name,
        provider_code=DeliveryProviderAccount.PROVIDER_GLOVO,
        active=False,
        sandbox=True,
        auto_dispatch=False,
        use_live_quotes=True,
        auth_endpoint="/oauth/token",
        quote_endpoint="/v2/laas/quotes",
        order_endpoint="/v2/laas/quotes/{quote_id}/parcels",
        cancel_endpoint="/v2/laas/parcels/{external_reference}/cancel",
        status_mapping={
            "CREATED": "assigned", "SCHEDULED": "assigned", "ACTIVATED": "assigned",
            "ACCEPTED": "assigned", "WAITING_FOR_PICKUP": "ready", "PICKED": "picked_up",
            "WAITING_FOR_DELIVERY": "out_for_delivery", "DELIVERED": "delivered",
            "REJECTED": "failed", "CANCELLED": "cancelled", "RETURNED": "returned",
        },
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
            f"Delivery {assignment.public_id} dispatched to configured delivery partner",
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
    if account.provider_code != DeliveryProviderAccount.PROVIDER_GLOVO:
        return assignment
    _require_glovo_platform()
    if not account.is_configured_for_dispatch:
        DeliveryEvent.raw_objects.create(
            business=assignment.business, created_by=actor, assignment=assignment, status=assignment.status,
            note="Glovo is enabled but not fully configured; delivery awaits manual dispatch.",
            metadata={"provider": "glovo", "configured": False},
        )
        return assignment
    quote_reference = (assignment.quote.provider_quote_reference if assignment.quote_id else "").strip()
    if not quote_reference:
        raise ProviderDispatchError("This delivery has no live Glovo quote reference. Request a fresh Glovo quote or dispatch it manually.")
    endpoint = account.order_endpoint.replace("{quote_id}", quote_reference)
    payload = _glovo_parcel_payload(assignment)
    response = _glovo_request(account, endpoint, payload=payload)
    provider_id = str(response.get("trackingNumber") or response.get("orderCode") or response.get("id") or "")[:160]
    status = _glovo_status(response)
    mapped = _map_provider_status(account, status)
    tracking_url = ""
    if provider_id:
        try:
            tracking = _glovo_request(account, f"/v2/laas/parcels/{provider_id}/parcel-tracking-link", payload=None, method="GET")
            tracking_url = str(tracking.get("link") or tracking.get("trackingUrl") or "")[:500]
        except ProviderDispatchError:
            tracking_url = ""
    assignment.provider_payload = {"request": payload, "response": response, "dispatched_at": timezone.now().isoformat()}
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
    now = timezone.now()
    pickup_fields = []
    if assignment.status in {DeliveryAssignment.STATUS_PICKED_UP, DeliveryAssignment.STATUS_OUT_FOR_DELIVERY} and not assignment.picked_up_at:
        assignment.picked_up_at = now
        pickup_fields.append("picked_up_at")
        if assignment.quote_id:
            assignment.eta_at = now + timezone.timedelta(minutes=assignment.quote.eta_max_minutes)
            pickup_fields.append("eta_at")
    assignment.save(update_fields=["provider_payload", "provider_order_id", "provider_status", "external_reference", "external_tracking_url", "status", *pickup_fields, "updated_at"])
    DeliveryEvent.raw_objects.create(
        business=assignment.business, created_by=actor, assignment=assignment, status=assignment.status,
        note="Delivery dispatched to Glovo LaaS.",
        metadata={"provider": "glovo", "tracking_number": provider_id, "provider_status": status},
    )
    audit(assignment.business, actor, "delivery_provider_dispatch", assignment, f"Delivery {assignment.public_id} dispatched to Glovo", {"tracking_number": provider_id, "provider_status": status})
    queue_commerce_notification(
        business=assignment.business,
        event_type=CommerceNotification.EVENT_DELIVERY_PROVIDER,
        title=f"Sent to Glovo · {assignment.intake.public_number}",
        message=f"Tracking {provider_id or 'pending'} · {status}",
        target_url="/delivery/",
        dedupe_key=f"delivery:{assignment.pk}:glovo-dispatch:{provider_id or quote_reference}",
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
    if account.provider_code != DeliveryProviderAccount.PROVIDER_GLOVO:
        return assignment
    _require_glovo_platform()
    if not account.cancel_endpoint:
        raise ProviderDispatchError("Configure the Glovo cancellation endpoint before cancelling an active provider job.")
    endpoint = account.cancel_endpoint.replace("{external_reference}", assignment.provider_order_id)
    response = _glovo_request(account, endpoint, payload={})
    DeliveryEvent.raw_objects.create(
        business=assignment.business, created_by=actor, assignment=assignment, status=assignment.status,
        note="Glovo cancellation request accepted; local delivery cancellation follows.",
        metadata={"provider": "glovo", "tracking_number": assignment.provider_order_id, "response": response},
    )
    audit(assignment.business, actor, "delivery_provider_cancel", assignment, f"Glovo cancellation requested for {assignment.public_id}", {"tracking_number": assignment.provider_order_id})
    queue_commerce_notification(
        business=assignment.business, event_type=CommerceNotification.EVENT_DELIVERY_PROVIDER,
        title=f"Glovo cancellation requested · {assignment.intake.public_number}",
        message=f"Tracking {assignment.provider_order_id}", target_url="/delivery/",
        dedupe_key=f"delivery:{assignment.pk}:glovo-cancel:{assignment.provider_order_id}",
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
        f"Configured delivery partner updated delivery {assignment.public_id}",
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


def ensure_glovo_webhooks(account: DeliveryProviderAccount, *, callback_url: str) -> dict:
    _require_glovo_platform()
    if account.provider_code != DeliveryProviderAccount.PROVIDER_GLOVO:
        raise ProviderDispatchError("Webhook registration is only available for the Glovo plug-in.")
    if not account.base_url or not account.api_key or not account.api_secret:
        raise ProviderDispatchError("Configure the Glovo API base URL, client ID and client secret first.")
    if not account.webhook_secret:
        raise ProviderDispatchError("Set a strong webhook secret before registering Glovo callbacks.")
    existing_response = _glovo_request(account, "/v2/laas/webhooks", payload=None, method="GET")
    existing = existing_response.get("webhookList") or (existing_response.get("data") or {}).get("webhookList") or []
    result = {"created": [], "existing": []}
    for event_type in ("STATUS_UPDATE", "POSITION_UPDATE"):
        match = next((item for item in existing if str(item.get("eventType", "")).upper() == event_type and item.get("callbackUrl") == callback_url and item.get("isActive", True)), None)
        if match:
            result["existing"].append(event_type)
            continue
        _glovo_request(
            account,
            "/v2/laas/webhooks",
            payload={
                "callbackUrl": callback_url,
                "eventType": event_type,
                "partnerSecret": account.webhook_secret,
                "retryConfig": {"maxRetryCount": 1},
            },
        )
        result["created"].append(event_type)
    return result


def consume_glovo_webhook(*, business, payload: dict, header_secret="", actor=None) -> DeliveryAssignment | None:
    _require_glovo_platform()
    header_secret = (header_secret or "").strip()
    if not header_secret:
        raise ProviderDispatchError("Missing Glovo webhook authorization secret.")
    account = DeliveryProviderAccount.raw_objects.filter(
        business=business,
        provider_code=DeliveryProviderAccount.PROVIDER_GLOVO,
        active=True,
        webhook_secret=header_secret,
    ).first()
    if not account:
        raise ProviderDispatchError("Invalid Glovo webhook authorization secret.")
    provider_id = str(payload.get("trackingNumber") or payload.get("tracking_number") or payload.get("id") or "").strip()
    status = str(payload.get("status") or payload.get("state") or "").strip().upper()
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
    assignment.provider_status = status[:80]
    assignment.provider_payload = {
        **(assignment.provider_payload or {}),
        "last_webhook": payload,
        "last_webhook_at": timezone.now().isoformat(),
    }
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
    assignment.save(update_fields=["status", "provider_status", "provider_payload", "delivered_at", *pickup_fields, "updated_at"])
    DeliveryEvent.raw_objects.create(
        business=business, created_by=actor, assignment=assignment, status=assignment.status,
        note="Glovo LaaS webhook update received.",
        metadata={"previous_status": previous, "provider_status": status, "event_type": payload.get("eventType"), "update_reason": payload.get("updateReason")},
    )
    audit(business, actor, "delivery_provider_webhook", assignment, f"Glovo updated delivery {assignment.public_id}", {"provider_status": status, "mapped_status": assignment.status})
    if status and (mapped or status != assignment.provider_status):
        queue_commerce_notification(
            business=business, event_type=CommerceNotification.EVENT_DELIVERY_PROVIDER,
            title=f"Glovo update · {assignment.intake.public_number}",
            message=f"{status} → {assignment.get_status_display()}", target_url="/delivery/",
            dedupe_key=f"delivery:{assignment.pk}:glovo-status:{status}:{payload.get('updateReason') or ''}",
        )
    publish_delivery_changed(
        business.pk, assignment.public_id, reason="provider_status",
        rider_user_ids=(assignment.driver.user_id,) if assignment.driver_id and assignment.driver and assignment.driver.user_id else (),
    )
    return assignment
