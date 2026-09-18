from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.services import audit

from .models import CommerceNotification, DeliveryAssignment, DeliveryEvent, DeliveryProviderAccount
from .notification_services import queue_commerce_notification
from .realtime import publish_delivery_changed


class ProviderDispatchError(ValidationError):
    pass


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
    """Materialize provider-neutral built-ins for tenants created after migrations."""
    account, created = DeliveryProviderAccount.raw_objects.get_or_create(
        business=business,
        name="Glovo",
        defaults={
            "provider_code": DeliveryProviderAccount.PROVIDER_GLOVO,
            "active": True,
            "sandbox": True,
            "auto_dispatch": False,
            "use_live_quotes": True,
            "auth_endpoint": "/oauth/token",
            "quote_endpoint": "/v2/laas/quotes",
            "order_endpoint": "/v2/laas/quotes/{quote_id}/parcels",
            "cancel_endpoint": "/v2/laas/parcels/{external_reference}/cancel",
            "status_mapping": {
                "CREATED": "assigned", "SCHEDULED": "assigned", "ACTIVATED": "assigned",
                "ACCEPTED": "assigned", "WAITING_FOR_PICKUP": "ready", "PICKED": "picked_up",
                "WAITING_FOR_DELIVERY": "out_for_delivery", "DELIVERED": "delivered",
                "REJECTED": "failed", "CANCELLED": "cancelled", "RETURNED": "returned",
            },
        },
    )
    return account, created


def dispatch_assignment_to_provider(assignment: DeliveryAssignment, *, actor=None) -> DeliveryAssignment:
    assignment = DeliveryAssignment.raw_objects.select_related("provider_account", "origin", "quote", "intake", "business", "driver__user").get(pk=assignment.pk)
    account = assignment.provider_account
    if not account or not account.active:
        return assignment
    if account.provider_code != DeliveryProviderAccount.PROVIDER_GLOVO:
        return assignment
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
    if not account or account.provider_code != DeliveryProviderAccount.PROVIDER_GLOVO or not assignment.provider_order_id:
        return assignment
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


def ensure_glovo_webhooks(account: DeliveryProviderAccount, *, callback_url: str) -> dict:
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
