from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal, InvalidOperation
from math import asin, atan2, cos, degrees, radians, sin, sqrt
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings as django_settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.services import business_has_module
from core.services import audit

from .models import (
    CommerceCheckoutSession,
    CommerceIntake,
    CommerceNotification,
    DeliveryArea,
    DeliveryAssignment,
    DeliveryDriver,
    DeliveryEvent,
    DeliveryIssue,
    DeliveryOrigin,
    DeliveryProviderAccount,
    DeliveryQuote,
    DeliveryRateBand,
    DeliverySettings,
)
from .notification_services import queue_commerce_notification
from .realtime import publish_delivery_changed


def _decimal(value, label):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"Enter a valid {label}.") from exc


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(lambda v: radians(float(v)), (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return Decimal(str(6371.0088 * 2 * asin(sqrt(a)))).quantize(Decimal("0.01"))


def _bearing_degrees(lat1, lon1, lat2, lon2):
    first, second = radians(float(lat1)), radians(float(lat2))
    delta = radians(float(lon2) - float(lon1))
    y = sin(delta) * cos(second)
    x = cos(first) * sin(second) - sin(first) * cos(second) * cos(delta)
    return (degrees(atan2(y, x)) + 360) % 360


def _destination_point(latitude, longitude, distance_km, bearing):
    """Return the geographic point reached from a centre at a bearing/distance."""
    angular = float(distance_km) / 6371.0088
    angle = radians(float(bearing))
    lat1 = radians(float(latitude))
    lon1 = radians(float(longitude))
    lat2 = asin(sin(lat1) * cos(angular) + cos(lat1) * sin(angular) * cos(angle))
    lon2 = lon1 + atan2(
        sin(angle) * sin(angular) * cos(lat1),
        cos(angular) - sin(lat1) * sin(lat2),
    )
    return (
        Decimal(str(degrees(lat2))).quantize(Decimal("0.0000001")),
        Decimal(str(((degrees(lon2) + 540) % 360) - 180)).quantize(Decimal("0.0000001")),
    )


def _area_reach_km(area, latitude, longitude):
    """Interpolate optional diagonal lobes while preserving the base circle."""
    bearing = _bearing_degrees(area.latitude, area.longitude, latitude, longitude)
    extensions = (
        (45, Decimal(area.extension_ne_km)),
        (135, Decimal(area.extension_se_km)),
        (225, Decimal(area.extension_sw_km)),
        (315, Decimal(area.extension_nw_km)),
    )
    direction, extension = min(extensions, key=lambda row: abs((bearing - row[0] + 180) % 360 - 180))
    delta = abs((bearing - direction + 180) % 360 - 180)
    weight = Decimal(str(max(0.0, 1 - (delta / 45))))
    return Decimal(area.radius_km) + (extension * weight)


def delivery_area_coverage_polygon(area, *, step_degrees=10):
    """Return a compact customer-safe polygon for the configured radial coverage.

    The server remains authoritative for inclusion checks.  The polygon exists so
    hosted/POS maps and headless websites can render exactly the same visual guide
    without reimplementing the diagonal-extension interpolation.
    """
    if area.latitude is None or area.longitude is None:
        return []
    points = []
    for bearing in range(0, 360, max(1, int(step_degrees))):
        probe_lat, probe_lon = _destination_point(area.latitude, area.longitude, 1, bearing)
        reach = _area_reach_km(area, probe_lat, probe_lon)
        lat, lon = _destination_point(area.latitude, area.longitude, reach, bearing)
        points.append({"latitude": str(lat), "longitude": str(lon)})
    return points


def delivery_area_distance_summary(area, origin=None):
    """Describe area geometry relative to the active delivery base.

    Named-area eligibility is governed by the centre radius/extensions, not the
    legacy max-distance field on a rate band.  Pricing still uses the precise
    base-to-destination distance.
    """
    if (
        not origin or origin.latitude is None or origin.longitude is None
        or area.latitude is None or area.longitude is None
    ):
        return {"centre_distance_km": None, "maximum_base_distance_km": None}
    centre = _haversine_km(origin.latitude, origin.longitude, area.latitude, area.longitude)
    boundary = delivery_area_coverage_polygon(area)
    max_distance = max(
        (_haversine_km(origin.latitude, origin.longitude, row["latitude"], row["longitude"]) for row in boundary),
        default=centre,
    )
    return {
        "centre_distance_km": centre,
        "maximum_base_distance_km": max_distance,
    }


def delivery_available(business):
    if not business_has_module(business, "delivery"):
        return False
    settings = DeliverySettings.raw_objects.filter(business=business).first()
    return bool(settings and settings.enabled)


def public_delivery_config(business):
    """Return the tenant's customer-safe delivery discovery contract."""
    enabled = delivery_available(business)
    areas = []
    if enabled:
        for area in DeliveryArea.raw_objects.filter(
            business=business, active=True
        ).select_related("rate_band").order_by("name", "id"):
            extensions = {
                "ne": str(area.extension_ne_km), "se": str(area.extension_se_km),
                "sw": str(area.extension_sw_km), "nw": str(area.extension_nw_km),
            }
            has_extensions = any(Decimal(value) > 0 for value in extensions.values())
            areas.append({
                "id": area.pk,
                "code": area.code,
                "name": area.name,
                "latitude": str(area.latitude) if area.latitude is not None else None,
                "longitude": str(area.longitude) if area.longitude is not None else None,
                "radius_km": str(area.radius_km),
                "coverage_shape": "circle_with_diagonal_extensions" if has_extensions else "circle",
                "diagonal_extensions_km": extensions,
                "coverage_polygon": delivery_area_coverage_polygon(area),
                "pricing": {
                    "band": area.rate_band.name,
                    "base_fee": str(area.rate_band.base_fee),
                    "per_km_fee": str(area.rate_band.per_km_fee),
                    "minimum_order": str(area.rate_band.minimum_order),
                    "eta_min_minutes": area.rate_band.eta_min_minutes,
                    "eta_max_minutes": area.rate_band.eta_max_minutes,
                    "distance_basis": "delivery_base_to_precise_destination",
                    "coverage_boundary_basis": "destination_centre_radius",
                } if area.rate_band_id and area.rate_band.active else None,
            })
    return {
        "enabled": enabled,
        "quote_required_before_checkout": enabled,
        "destination_area_supported": enabled,
        "destination_address_required": enabled,
        "destination_coordinates_supported": enabled,
        # Keep the original discovery fields stable for existing headless clients.
        "destination_address_validation": "server_geocode_or_map_pin" if enabled else None,
        "destination_address_flow": "server_geocode_then_map_pin_fallback" if enabled else None,
        "coverage_shape": "circle" if enabled else None,
        "coverage_geometry": "radius_with_optional_diagonal_extensions" if enabled else None,
        "coverage_geometry_version": 1 if enabled else None,
        "areas": areas,
    }


def _default_origin(business):
    origin = DeliveryOrigin.raw_objects.filter(business=business, active=True, is_default=True).first()
    return origin or DeliveryOrigin.raw_objects.filter(business=business, active=True).first()


def _rate_band(business, distance, area=None):
    if area and area.rate_band_id and area.rate_band.active:
        return area.rate_band
    return DeliveryRateBand.raw_objects.filter(
        business=business,
        active=True,
        min_distance_km__lte=distance,
    ).filter(
        Q(max_distance_km__isnull=True) | Q(max_distance_km__gte=distance)
    ).order_by("sort_order", "min_distance_km", "id").first()


def _geocode_results(address, area=None):
    """Return cached geocoder matches including a customer-safe display address."""
    query = ", ".join(part for part in [address.strip(), area.name if area else ""] if part)
    key = "delivery-geocode-v2:" + hashlib.sha256(query.casefold().encode("utf-8")).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    endpoint = getattr(django_settings, "DELIVERY_GEOCODER_URL", "https://nominatim.openstreetmap.org/search")
    params = urlencode({"q": query, "format": "jsonv2", "limit": 5, "addressdetails": 0})
    request = Request(
        f"{endpoint}?{params}",
        headers={"User-Agent": getattr(django_settings, "DELIVERY_GEOCODER_USER_AGENT", "INPROFIC-delivery/1.0")},
    )
    try:
        with urlopen(request, timeout=getattr(django_settings, "DELIVERY_GEOCODER_TIMEOUT_SECONDS", 4)) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    results = []
    for row in payload if isinstance(payload, list) else []:
        try:
            lat, lon = Decimal(str(row["lat"])), Decimal(str(row["lon"]))
        except (InvalidOperation, KeyError, TypeError, ValueError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            results.append({
                "latitude": lat,
                "longitude": lon,
                "address": str(row.get("display_name") or address).strip()[:500],
            })
    cache.set(key, results, getattr(django_settings, "DELIVERY_GEOCODER_CACHE_SECONDS", 86400))
    return results


def _geocode_candidates(address, area=None):
    # Compatibility helper retained for the quote-validation tests/callers.
    return [(row["latitude"], row["longitude"]) for row in _geocode_results(address, area=area)]


def _reverse_geocode(latitude, longitude):
    key = f"delivery-reverse-geocode:{Decimal(latitude):.6f}:{Decimal(longitude):.6f}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    endpoint = getattr(django_settings, "DELIVERY_REVERSE_GEOCODER_URL", "") or getattr(
        django_settings, "DELIVERY_GEOCODER_URL", "https://nominatim.openstreetmap.org/search"
    ).replace("/search", "/reverse")
    params = urlencode({"lat": str(latitude), "lon": str(longitude), "format": "jsonv2", "zoom": 18, "addressdetails": 0})
    request = Request(
        f"{endpoint}?{params}",
        headers={"User-Agent": getattr(django_settings, "DELIVERY_GEOCODER_USER_AGENT", "INPROFIC-delivery/1.0")},
    )
    try:
        with urlopen(request, timeout=getattr(django_settings, "DELIVERY_GEOCODER_TIMEOUT_SECONDS", 4)) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return ""
    value = str(payload.get("display_name") or "").strip()[:500] if isinstance(payload, dict) else ""
    cache.set(key, value, getattr(django_settings, "DELIVERY_GEOCODER_CACHE_SECONDS", 86400))
    return value


def _validate_area_point(area, latitude, longitude):
    if not area:
        return
    if area.latitude is None or area.longitude is None:
        raise ValidationError(f"{area.name} does not have a configured coverage centre.")
    zone_distance = _haversine_km(area.latitude, area.longitude, latitude, longitude)
    permitted_reach = _area_reach_km(area, latitude, longitude)
    if zone_distance > permitted_reach:
        raise ValidationError(
            f"This location is outside {area.name}'s configured delivery coverage. Choose another area or move the map pin."
        )


def resolve_delivery_location(*, business, address="", area_id=None, latitude=None, longitude=None):
    """Resolve typed address <-> map coordinates without creating a delivery quote.

    This is the synchronization contract used by hosted checkout, POS and headless
    integrations. Coverage is validated when a named area is supplied, but basket
    minimums and delivery fees are intentionally not evaluated here.
    """
    if not delivery_available(business):
        raise ValidationError("Delivery location lookup is unavailable for this business.")
    area = None
    if area_id:
        area = DeliveryArea.raw_objects.filter(business=business, pk=area_id, active=True).first()
        if not area:
            raise ValidationError("Choose a valid delivery area.")
    address = (address or "").strip()
    lat = _decimal(latitude, "destination latitude") if latitude not in (None, "") else None
    lon = _decimal(longitude, "destination longitude") if longitude not in (None, "") else None
    if (lat is None) != (lon is None):
        raise ValidationError("Provide both destination latitude and longitude, or neither.")
    if lat is not None:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValidationError("Destination coordinates are outside the valid range.")
        _validate_area_point(area, lat, lon)
        resolved_address = _reverse_geocode(lat, lon)
        return {
            "address": resolved_address or address,
            "latitude": lat,
            "longitude": lon,
            "validated_by": "map_pin",
            "address_resolved": bool(resolved_address),
            "area_id": area.pk if area else None,
            "area_name": area.name if area else None,
        }
    if not address:
        raise ValidationError("Enter a delivery address or place the destination pin on the map.")
    matches = _geocode_results(address, area=area)
    if area:
        if area.latitude is None or area.longitude is None:
            raise ValidationError(f"{area.name} does not have a configured coverage centre.")
        matches = [row for row in matches if _haversine_km(area.latitude, area.longitude, row["latitude"], row["longitude"]) <= _area_reach_km(area, row["latitude"], row["longitude"])]
    if not matches:
        raise ValidationError("We could not locate that address inside the selected delivery area. Enter a more precise address or place the map pin.")
    match = matches[0]
    _validate_area_point(area, match["latitude"], match["longitude"])
    return {
        "address": match["address"] or address,
        "latitude": match["latitude"],
        "longitude": match["longitude"],
        "validated_by": "geocoded_address",
        "address_resolved": True,
        "area_id": area.pk if area else None,
        "area_name": area.name if area else None,
    }


def _destination(*, business, origin, address, area_id=None, latitude=None, longitude=None, location_source=None):
    area = None
    if area_id:
        area = DeliveryArea.raw_objects.filter(
            business=business, pk=area_id, active=True
        ).select_related("rate_band").first()
        if not area:
            raise ValidationError("Choose a valid delivery area.")
    latitude_value = _decimal(latitude, "destination latitude") if latitude not in (None, "") else None
    longitude_value = _decimal(longitude, "destination longitude") if longitude not in (None, "") else None
    validation_source = (location_source if location_source in {"map_pin", "geocoded_address"} else "map_pin") if latitude_value is not None and longitude_value is not None else "geocoded_address"
    if (latitude_value is None) != (longitude_value is None):
        raise ValidationError("Provide both destination latitude and longitude, or neither.")
    if latitude_value is not None and not (-90 <= latitude_value <= 90 and -180 <= longitude_value <= 180):
        raise ValidationError("Destination coordinates are outside the valid range.")
    if latitude_value is None:
        candidates = _geocode_candidates(address, area=area)
        if area and area.latitude is not None and area.longitude is not None:
            candidates = [
                (lat, lon) for lat, lon in candidates
                if _haversine_km(area.latitude, area.longitude, lat, lon) <= _area_reach_km(area, lat, lon)
            ]
        if not candidates:
            raise ValidationError(
                "We could not locate that address inside the selected delivery area. Enter a more precise address or place the map pin."
            )
        latitude_value, longitude_value = candidates[0]
    _validate_area_point(area, latitude_value, longitude_value)
    return area, latitude_value, longitude_value, validation_source


def _native_quote_data(*, business, origin, area, latitude, longitude, subtotal):
    if origin.latitude is None or origin.longitude is None:
        raise ValidationError("Configure coordinates on the delivery origin before using INPROFIC distance rates.")
    if latitude is None or longitude is None:
        raise ValidationError("Choose a configured area or provide destination coordinates.")
    distance = _haversine_km(origin.latitude, origin.longitude, latitude, longitude)
    band = _rate_band(business, distance, area=area)
    if not band:
        raise ValidationError("This destination is outside the configured delivery coverage.")
    if subtotal < band.minimum_order:
        raise ValidationError(
            f"Minimum order for this delivery area is {business.currency_symbol}{band.minimum_order:,.2f}."
        )
    fee = (Decimal(band.base_fee) + Decimal(band.per_km_fee) * distance).quantize(Decimal("0.01"))
    return {
        "distance": distance,
        "fee": fee,
        "eta_min": band.eta_min_minutes,
        "eta_max": band.eta_max_minutes,
        "payload": {"pricing": "inprofic_distance_band", "rate_band_id": band.pk},
    }


def _glovo_quote_data(*, account, destination_address, latitude, longitude, subtotal, area):
    from .delivery_providers import ProviderDispatchError, quote_glovo_delivery

    if not account or account.provider_code != DeliveryProviderAccount.PROVIDER_GLOVO:
        return None
    if not account.use_live_quotes or not account.is_configured_for_quote:
        return None
    try:
        result = quote_glovo_delivery(
            account,
            destination_address=destination_address,
            latitude=latitude,
            longitude=longitude,
        )
    except ProviderDispatchError:
        return None
    if area and area.rate_band_id and subtotal < area.rate_band.minimum_order:
        raise ValidationError(
            f"Minimum order for this delivery area is {account.business.currency_symbol}{area.rate_band.minimum_order:,.2f}."
        )
    return {
        "distance": result["distance_km"],
        "fee": result["fee"],
        "eta_min": result["eta_min_minutes"],
        "eta_max": result["eta_max_minutes"],
        "expires_at": result["expires_at"],
        "provider_reference": result["quote_id"],
        "payload": {"provider": "glovo_laas_v2", "response": result["response"]},
    }


def _persist_quote(*, business, actor, group_id, selection_source, origin, area, address, latitude, longitude,
                   subtotal, provider, provider_account, data, expires_at, validation_source, settings):
    provider_payload = dict(data.get("payload") or {})
    provider_payload["destination_validation"] = validation_source
    # Snapshot customer-facing Hybrid policy beside the quote so the final
    # pre-payment review and headless checkout response cannot drift if the
    # tenant changes delivery settings after the customer accepts the quote.
    if settings.default_provider == DeliverySettings.PROVIDER_HYBRID:
        provider_payload["routing_policy"] = settings.hybrid_routing_policy
        provider_payload["switch_policy"] = settings.hybrid_switch_policy
        provider_payload["switch_policy_text"] = settings.customer_switch_policy_text
    return DeliveryQuote.raw_objects.create(
        business=business,
        created_by=actor,
        quote_group_id=group_id,
        selection_source=selection_source,
        origin=origin,
        area=area,
        destination_address=address[:255],
        destination_latitude=latitude,
        destination_longitude=longitude,
        distance_km=data["distance"],
        subtotal=subtotal,
        fee=data["fee"],
        total=subtotal + data["fee"],
        eta_min_minutes=data["eta_min"],
        eta_max_minutes=data["eta_max"],
        provider=provider,
        provider_account=provider_account,
        provider_quote_reference=data.get("provider_reference", ""),
        provider_payload=provider_payload,
        expires_at=data.get("expires_at") or expires_at,
    )


def create_delivery_quote_options(*, business, subtotal, destination_address, area_id=None, latitude=None, longitude=None,
                                  location_source=None, actor=None):
    """Create the delivery methods currently available for one destination.

    Hybrid is a routing mode, never a courier. Every persisted option resolves
    to either ``inhouse`` or ``third_party`` so execution, tracking and audit
    records always name the transport method that will actually carry the job.
    """
    if not business_has_module(business, "delivery"):
        raise ValidationError("Delivery is not included in this business plan.")
    settings, _ = DeliverySettings.raw_objects.get_or_create(business=business, defaults={"created_by": actor})
    if not settings.enabled:
        raise ValidationError("Delivery is not enabled for this business.")

    subtotal = _decimal(subtotal, "order subtotal").quantize(Decimal("0.01"))
    if subtotal < 0:
        raise ValidationError("Order subtotal cannot be negative.")
    origin = _default_origin(business)
    if not origin:
        raise ValidationError("Configure a delivery origin before quoting delivery.")
    address = (destination_address or "").strip()
    if not address:
        raise ValidationError("Enter the precise delivery address.")
    area, lat, lon, validation_source = _destination(
        business=business, origin=origin, address=address,
        area_id=area_id, latitude=latitude, longitude=longitude, location_source=location_source,
    )
    group_id = uuid.uuid4()
    base_expires = timezone.now() + timezone.timedelta(minutes=settings.quote_valid_minutes)
    options = []
    errors = []

    def add_inhouse(selection_source):
        try:
            data = _native_quote_data(
                business=business, origin=origin, area=area, latitude=lat, longitude=lon, subtotal=subtotal
            )
        except ValidationError as exc:
            errors.extend(exc.messages)
            return
        options.append(_persist_quote(
            business=business, actor=actor, group_id=group_id, selection_source=selection_source,
            origin=origin, area=area, address=address, latitude=lat, longitude=lon, subtotal=subtotal,
            provider=DeliverySettings.PROVIDER_INHOUSE, provider_account=None, data=data, expires_at=base_expires,
            validation_source=validation_source, settings=settings,
        ))

    def add_external(selection_source):
        account = settings.default_provider_account
        if not account or not account.active:
            errors.append("The external delivery provider is not configured.")
            return
        if account.provider_code == DeliveryProviderAccount.PROVIDER_GLOVO:
            data = _glovo_quote_data(
                account=account, destination_address=address, latitude=lat, longitude=lon, subtotal=subtotal, area=area
            )
            if not data:
                errors.append("Glovo could not provide a live quote for this destination.")
                return
        else:
            # Provider-neutral/manual couriers can still use the tenant's own
            # customer-facing rate bands until that plug-in supplies live rates.
            try:
                data = _native_quote_data(
                    business=business, origin=origin, area=area, latitude=lat, longitude=lon, subtotal=subtotal
                )
            except ValidationError as exc:
                errors.extend(exc.messages)
                return
            data["payload"] = {**data.get("payload", {}), "provider": account.provider_code, "live_quote": False}
        options.append(_persist_quote(
            business=business, actor=actor, group_id=group_id, selection_source=selection_source,
            origin=origin, area=area, address=address, latitude=lat, longitude=lon, subtotal=subtotal,
            provider=DeliverySettings.PROVIDER_THIRD_PARTY, provider_account=account, data=data, expires_at=base_expires,
            validation_source=validation_source, settings=settings,
        ))

    if settings.default_provider == DeliverySettings.PROVIDER_INHOUSE:
        add_inhouse(DeliveryQuote.SELECT_PLATFORM)
    elif settings.default_provider == DeliverySettings.PROVIDER_THIRD_PARTY:
        add_external(DeliveryQuote.SELECT_PLATFORM)
    else:
        source = (
            DeliveryQuote.SELECT_CUSTOMER
            if settings.hybrid_routing_policy == DeliverySettings.HYBRID_ROUTE_CUSTOMER
            else DeliveryQuote.SELECT_DISPATCHER
            if settings.hybrid_routing_policy == DeliverySettings.HYBRID_ROUTE_DISPATCHER
            else DeliveryQuote.SELECT_PLATFORM
        )
        add_inhouse(source)
        add_external(source)

    if not options:
        raise ValidationError(errors[0] if errors else "No delivery method is available for this destination.")
    return settings, options


def select_delivery_quote(settings, options):
    """Return the pre-payment quote selected by tenant routing policy, if any."""
    if len(options) == 1:
        return options[0]
    if settings.default_provider != DeliverySettings.PROVIDER_HYBRID:
        return options[0]
    policy = settings.hybrid_routing_policy
    if policy == DeliverySettings.HYBRID_ROUTE_CUSTOMER:
        return None
    inhouse = next((q for q in options if q.provider == DeliverySettings.PROVIDER_INHOUSE), None)
    external = next((q for q in options if q.provider == DeliverySettings.PROVIDER_THIRD_PARTY), None)
    if policy == DeliverySettings.HYBRID_ROUTE_LOWEST:
        return min(options, key=lambda q: (q.fee, q.eta_max_minutes, q.pk))
    if policy == DeliverySettings.HYBRID_ROUTE_FASTEST:
        return min(options, key=lambda q: (q.eta_max_minutes, q.fee, q.pk))
    if policy == DeliverySettings.HYBRID_ROUTE_GLOVO_FIRST:
        return external or inhouse
    # Dispatcher-choice and in-house-first both charge the stable in-house rate
    # when available. Dispatch may later switch methods subject to the visible
    # post-payment policy, without increasing the customer's paid fee.
    return inhouse or external


def create_delivery_quote(**kwargs):
    """Backward-compatible single quote API for non-interactive callers."""
    settings, options = create_delivery_quote_options(**kwargs)
    return select_delivery_quote(settings, options) or options[0]


def serialize_delivery_quote(quote):
    provider_label = "In-house delivery" if quote.provider == DeliverySettings.PROVIDER_INHOUSE else (
        quote.provider_account.get_provider_code_display() if quote.provider_account_id else "Delivery partner"
    )
    snapshot = quote.provider_payload or {}
    return {
        "quote_id": str(quote.public_id),
        "quote_group_id": str(quote.quote_group_id),
        "provider": quote.provider,
        "provider_label": provider_label,
        "selection_source": quote.selection_source,
        "routing_policy": snapshot.get("routing_policy"),
        "switch_policy": snapshot.get("switch_policy"),
        "switch_policy_text": snapshot.get("switch_policy_text", ""),
        "distance_km": str(quote.distance_km),
        "fee": f"{quote.fee:.2f}",
        "total": f"{quote.total:.2f}",
        "eta_min_minutes": quote.eta_min_minutes,
        "eta_max_minutes": quote.eta_max_minutes,
        "expires_at": quote.expires_at.isoformat(),
        "destination": {
            "address": quote.destination_address,
            "latitude": str(quote.destination_latitude) if quote.destination_latitude is not None else None,
            "longitude": str(quote.destination_longitude) if quote.destination_longitude is not None else None,
            "area_id": quote.area_id,
            "area_name": quote.area.name if quote.area_id else None,
            "validated_by": (quote.provider_payload or {}).get("destination_validation", ""),
        },
    }



def serialize_delivery_tracking(assignment):
    """Customer-safe delivery snapshot shared by hosted and headless tracking."""
    quote = assignment.quote if assignment.quote_id else None
    pickup_at = assignment.picked_up_at
    eta_min_at = None
    eta_max_at = None
    if pickup_at and quote:
        eta_min_at = pickup_at + timezone.timedelta(minutes=quote.eta_min_minutes)
        eta_max_at = pickup_at + timezone.timedelta(minutes=quote.eta_max_minutes)

    intake = assignment.intake
    timeline = [{
        "key": f"order-confirmed:{intake.public_id}",
        "status": "confirmed",
        "status_label": "Order confirmed",
        "note": "Your order was confirmed and entered fulfilment.",
        "created_at": intake.created_at.isoformat(),
    }]
    for event in assignment.events.all():
        timeline.append({
            "key": f"delivery-event:{event.pk}",
            "status": event.status,
            "status_label": event.get_status_display(),
            "note": event.note or "",
            "created_at": event.created_at.isoformat(),
        })

    return {
        "order": {
            "id": str(intake.public_id),
            "number": intake.public_number,
            "status": intake.status,
            "status_label": intake.get_status_display(),
            "payment_state": intake.payment_state,
            "fulfilment_state": intake.fulfilment_state,
            "confirmed_at": intake.created_at.isoformat(),
        },
        "delivery": {
            "id": str(assignment.public_id),
            "status": assignment.status,
            "status_label": assignment.get_status_display(),
            "status_note": assignment.status_note or "",
            "provider": assignment.provider,
            "provider_label": assignment.get_provider_display(),
            "driver": assignment.driver.name if assignment.driver_id else None,
            "driver_vehicle": assignment.driver.vehicle_type if assignment.driver_id else "",
            "picked_up_at": pickup_at.isoformat() if pickup_at else None,
            "eta_min_at": eta_min_at.isoformat() if eta_min_at else None,
            "eta_max_at": eta_max_at.isoformat() if eta_max_at else None,
            "delivered_at": assignment.delivered_at.isoformat() if assignment.delivered_at else None,
            "external_tracking_url": assignment.external_tracking_url or None,
            "updated_at": assignment.updated_at.isoformat(),
        },
        "timeline": timeline,
    }

def validate_delivery_quote(*, business, public_id, subtotal):
    if not business_has_module(business, "delivery"):
        raise ValidationError("Delivery is no longer included in this business plan.")
    settings = DeliverySettings.raw_objects.filter(business=business, enabled=True).first()
    if not settings:
        raise ValidationError("Delivery is no longer enabled for this business.")
    quote = DeliveryQuote.raw_objects.select_for_update().filter(
        business=business, public_id=public_id
    ).first()
    if not quote:
        raise ValidationError("Delivery quote was not found.")
    if quote.provider == DeliverySettings.PROVIDER_HYBRID:
        raise ValidationError("This legacy Hybrid quote must be refreshed before payment.")
    if quote.status != DeliveryQuote.STATUS_ACTIVE or quote.expires_at <= timezone.now():
        if quote.status == DeliveryQuote.STATUS_ACTIVE:
            quote.status = DeliveryQuote.STATUS_EXPIRED
            quote.save(update_fields=["status", "updated_at"])
        raise ValidationError("Delivery quote has expired. Request a fresh quote.")
    if CommerceCheckoutSession.raw_objects.filter(
        business=business, delivery_quote__quote_group_id=quote.quote_group_id
    ).exclude(
        status__in=[
            CommerceCheckoutSession.STATUS_CANCELLED,
            CommerceCheckoutSession.STATUS_EXPIRED,
        ]
    ).exists():
        raise ValidationError("This delivery quote is already attached to another checkout. Request a fresh quote.")
    subtotal = Decimal(subtotal).quantize(Decimal("0.01"))
    if Decimal(quote.subtotal).quantize(Decimal("0.01")) != subtotal:
        raise ValidationError("The basket changed after the delivery quote. Request a fresh quote.")
    return quote


def _notify_rider_assignment(assignment, *, previous_driver_id=None):
    driver = assignment.driver
    if previous_driver_id and (not driver or driver.pk != previous_driver_id):
        previous = DeliveryDriver.raw_objects.select_related("user").filter(
            business=assignment.business, pk=previous_driver_id
        ).first()
        if previous and previous.user_id:
            queue_commerce_notification(
                business=assignment.business, recipient_user=previous.user,
                event_type=CommerceNotification.EVENT_DELIVERY_ASSIGNED,
                title=f"Delivery reassigned · {assignment.intake.public_number}",
                message="This job is no longer assigned to you. Refresh My Deliveries before travelling.",
                target_url="/delivery/rider/",
                dedupe_key=f"delivery:{assignment.pk}:driver:{previous.pk}:unassigned:{assignment.driver_id or 'none'}",
            )
    if not driver or not driver.user_id or driver.pk == previous_driver_id:
        return
    queue_commerce_notification(
        business=assignment.business,
        recipient_user=driver.user,
        event_type=CommerceNotification.EVENT_DELIVERY_ASSIGNED,
        title=f"New delivery assigned · {assignment.intake.public_number}",
        message=f"{assignment.intake.customer_name} · {assignment.intake.customer_address or (assignment.quote.destination_address if assignment.quote_id else '')}",
        target_url="/delivery/rider/",
        dedupe_key=f"delivery:{assignment.pk}:driver:{driver.pk}:assigned:{assignment.method_switch_count}",
    )


def _notify_rider_status(assignment, *, actor=None):
    driver = assignment.driver
    if not driver or not driver.user_id or (actor is not None and getattr(actor, "pk", None) == driver.user_id):
        return
    queue_commerce_notification(
        business=assignment.business, recipient_user=driver.user,
        event_type=CommerceNotification.EVENT_DELIVERY_STATUS,
        title=f"Delivery update · {assignment.intake.public_number}",
        message=f"{assignment.get_status_display()}{' · ' + assignment.status_note if assignment.status_note else ''}",
        target_url="/delivery/rider/",
        dedupe_key=f"delivery:{assignment.pk}:driver:{driver.pk}:status:{assignment.status}:{assignment.events.count()}",
    )


def _notify_dispatch(assignment, event_type, title, message, dedupe_suffix):
    queue_commerce_notification(
        business=assignment.business,
        event_type=event_type,
        title=title,
        message=message,
        target_url="/delivery/",
        dedupe_key=f"delivery:{assignment.pk}:{dedupe_suffix}",
    )


@transaction.atomic
def ensure_delivery_assignment(intake, *, actor=None):
    if not intake.delivery_quote_id:
        return None
    existing = DeliveryAssignment.raw_objects.filter(business=intake.business, intake=intake).first()
    if existing:
        return existing
    if not business_has_module(intake.business, "delivery"):
        raise ValidationError("Delivery is no longer included in this business plan.")
    settings = DeliverySettings.raw_objects.filter(business=intake.business, enabled=True).first()
    if not settings:
        raise ValidationError("Delivery is no longer enabled for this business.")
    quote = DeliveryQuote.raw_objects.select_for_update().get(pk=intake.delivery_quote_id, business=intake.business)
    if quote.provider == DeliverySettings.PROVIDER_HYBRID:
        raise ValidationError("Delivery must resolve to In-house or an external provider before dispatch.")
    assignment = DeliveryAssignment.raw_objects.create(
        business=intake.business,
        created_by=actor,
        intake=intake,
        quote=quote,
        origin=quote.origin,
        provider=quote.provider,
        provider_account=quote.provider_account,
        status=DeliveryAssignment.STATUS_PENDING,
        # Customer ETA begins when the parcel is actually picked up, not when
        # the paid order first creates a dispatch assignment.
        eta_at=None,
    )
    DeliveryEvent.raw_objects.create(
        business=intake.business,
        created_by=actor,
        assignment=assignment,
        status=assignment.status,
        note="Delivery created from paid commerce checkout.",
    )
    quote.status = DeliveryQuote.STATUS_USED
    quote.save(update_fields=["status", "updated_at"])
    audit(
        intake.business, actor, "delivery_create", assignment,
        f"Delivery created for {intake.public_number}",
        {"order": intake.public_number, "fee": str(intake.delivery_fee), "provider": assignment.provider, "provider_account": assignment.provider_account_id},
    )
    _notify_dispatch(
        assignment,
        CommerceNotification.EVENT_DELIVERY_CREATED,
        f"Delivery ready for dispatch · {intake.public_number}",
        f"{intake.customer_name} · paid delivery fee {intake.business.currency_symbol}{intake.delivery_fee:,.2f}",
        "created",
    )
    if assignment.provider_account_id and assignment.provider_account.auto_dispatch:
        from .delivery_providers import ProviderDispatchError, dispatch_assignment_to_provider
        try:
            dispatch_assignment_to_provider(assignment, actor=actor)
            assignment.refresh_from_db()
        except ProviderDispatchError as exc:
            DeliveryEvent.raw_objects.create(
                business=intake.business,
                created_by=actor,
                assignment=assignment,
                status=assignment.status,
                note=f"Provider auto-dispatch failed; manual dispatch is still available. {str(exc)[:180]}",
                metadata={"provider_account": assignment.provider_account_id, "error": str(exc)},
            )
            audit(
                intake.business, actor, "delivery_provider_dispatch_failed", assignment,
                f"Provider auto-dispatch failed for {intake.public_number}",
                {"provider_account": assignment.provider_account_id, "error": str(exc)},
            )
            _notify_dispatch(
                assignment,
                CommerceNotification.EVENT_DELIVERY_PROVIDER,
                f"Delivery provider needs attention · {intake.public_number}",
                str(exc),
                f"provider-failed:{timezone.now().strftime('%Y%m%d%H%M')}",
            )
    transaction.on_commit(
        lambda business_id=assignment.business_id, delivery_id=assignment.public_id: publish_delivery_changed(
            business_id, delivery_id, reason="created"
        )
    )
    return assignment


@transaction.atomic
def update_delivery_status(*, assignment, status, actor=None, note="", driver=None, clear_driver=False,
                           proof_note="", proof_reference="", external_reference="", external_tracking_url=""):
    allowed = {value for value, _ in DeliveryAssignment.STATUS_CHOICES}
    if status not in allowed:
        raise ValidationError("Choose a valid delivery status.")
    assignment = DeliveryAssignment.raw_objects.select_for_update().select_related("driver__user", "intake", "business", "quote").get(
        pk=assignment.pk, business=assignment.business
    )
    previous = assignment.status
    previous_driver_id = assignment.driver_id
    previous_driver_user_id = assignment.driver.user_id if assignment.driver_id and assignment.driver else None
    transitions = {
        DeliveryAssignment.STATUS_PENDING: {DeliveryAssignment.STATUS_ASSIGNED, DeliveryAssignment.STATUS_READY, DeliveryAssignment.STATUS_CANCELLED},
        DeliveryAssignment.STATUS_ASSIGNED: {DeliveryAssignment.STATUS_READY, DeliveryAssignment.STATUS_PICKED_UP, DeliveryAssignment.STATUS_CANCELLED},
        DeliveryAssignment.STATUS_READY: {DeliveryAssignment.STATUS_ASSIGNED, DeliveryAssignment.STATUS_PICKED_UP, DeliveryAssignment.STATUS_CANCELLED},
        DeliveryAssignment.STATUS_PICKED_UP: {DeliveryAssignment.STATUS_OUT_FOR_DELIVERY, DeliveryAssignment.STATUS_FAILED, DeliveryAssignment.STATUS_RETURNED},
        DeliveryAssignment.STATUS_OUT_FOR_DELIVERY: {DeliveryAssignment.STATUS_DELIVERED, DeliveryAssignment.STATUS_FAILED, DeliveryAssignment.STATUS_RETURNED},
        DeliveryAssignment.STATUS_FAILED: {DeliveryAssignment.STATUS_ASSIGNED, DeliveryAssignment.STATUS_RETURNED, DeliveryAssignment.STATUS_CANCELLED},
        DeliveryAssignment.STATUS_RETURNED: set(),
        DeliveryAssignment.STATUS_DELIVERED: set(),
        DeliveryAssignment.STATUS_CANCELLED: set(),
    }
    if status != previous and status not in transitions.get(previous, set()):
        raise ValidationError(
            f"Delivery cannot move from {assignment.get_status_display()} to {dict(DeliveryAssignment.STATUS_CHOICES).get(status, status)}."
        )
    settings = DeliverySettings.raw_objects.filter(business=assignment.business).first()
    if not business_has_module(assignment.business, "delivery"):
        raise ValidationError("Delivery is no longer included in this business plan.")
    if driver is not None:
        if driver.business_id != assignment.business_id or not driver.active:
            raise ValidationError("Choose an active delivery driver from this business.")
        if assignment.provider == DeliverySettings.PROVIDER_INHOUSE and driver.provider != DeliveryDriver.PROVIDER_INHOUSE:
            raise ValidationError("Choose an in-house driver for an in-house delivery.")
        if assignment.provider == DeliverySettings.PROVIDER_THIRD_PARTY and driver.provider != DeliveryDriver.PROVIDER_THIRD_PARTY:
            raise ValidationError("Choose a third-party courier for an external-provider delivery.")
    effective_driver = None if clear_driver else (driver if driver is not None else assignment.driver)
    dispatch_states = {DeliveryAssignment.STATUS_ASSIGNED, DeliveryAssignment.STATUS_PICKED_UP, DeliveryAssignment.STATUS_OUT_FOR_DELIVERY}
    if status in dispatch_states and assignment.provider == DeliverySettings.PROVIDER_INHOUSE and not effective_driver:
        raise ValidationError("Assign an active driver before moving an in-house delivery into dispatch.")
    effective_external_reference = (external_reference or assignment.external_reference or "").strip()
    if status in dispatch_states and assignment.provider == DeliverySettings.PROVIDER_THIRD_PARTY and not (effective_driver or effective_external_reference or assignment.provider_order_id):
        raise ValidationError("Add a courier/provider reference before dispatch.")
    if status == DeliveryAssignment.STATUS_DELIVERED and settings and settings.require_proof_of_delivery and not (
        (proof_note or assignment.proof_note).strip() or (proof_reference or assignment.proof_reference).strip()
    ):
        raise ValidationError("Proof of delivery is required before marking this delivery as delivered.")
    assignment.status = status
    assignment.status_note = (note or "")[:255]
    if clear_driver:
        assignment.driver = None
    elif driver is not None:
        assignment.driver = driver
    if external_reference:
        assignment.external_reference = external_reference[:160]
    if external_tracking_url:
        external_tracking_url = external_tracking_url.strip()[:500]
        URLValidator(schemes=["http", "https"])(external_tracking_url)
        assignment.external_tracking_url = external_tracking_url
    if proof_note:
        assignment.proof_note = proof_note[:255]
    if proof_reference:
        assignment.proof_reference = proof_reference[:255]
    now = timezone.now()
    if status in {DeliveryAssignment.STATUS_PICKED_UP, DeliveryAssignment.STATUS_OUT_FOR_DELIVERY} and not assignment.picked_up_at:
        assignment.picked_up_at = now
        if assignment.quote_id:
            assignment.eta_at = now + timezone.timedelta(minutes=assignment.quote.eta_max_minutes)
    elif assignment.picked_up_at and assignment.quote_id and status not in {
        DeliveryAssignment.STATUS_DELIVERED, DeliveryAssignment.STATUS_RETURNED, DeliveryAssignment.STATUS_CANCELLED,
    }:
        # Repair legacy assignments whose ETA was previously anchored to order
        # confirmation instead of the actual pickup timestamp.
        assignment.eta_at = assignment.picked_up_at + timezone.timedelta(minutes=assignment.quote.eta_max_minutes)
    if status == DeliveryAssignment.STATUS_DELIVERED:
        assignment.delivered_at = now
    assignment.save()
    assignment.refresh_from_db()
    DeliveryEvent.raw_objects.create(
        business=assignment.business,
        created_by=actor,
        assignment=assignment,
        status=status,
        note=assignment.status_note,
        metadata={"previous_status": previous, "driver_id": assignment.driver_id, "external_reference": assignment.external_reference, "proof_reference": assignment.proof_reference},
    )
    audit(
        assignment.business, actor, "delivery_status", assignment,
        f"Delivery {assignment.public_id} changed from {previous} to {status}",
        {"previous_status": previous, "status": status, "note": assignment.status_note},
    )
    if assignment.driver_id and assignment.driver_id != previous_driver_id:
        assignment = DeliveryAssignment.raw_objects.select_related("driver__user", "intake", "quote").get(pk=assignment.pk)
        _notify_rider_assignment(assignment, previous_driver_id=previous_driver_id)
    if status != previous:
        _notify_dispatch(
            assignment,
            CommerceNotification.EVENT_DELIVERY_STATUS,
            f"Delivery {assignment.get_status_display()} · {assignment.intake.public_number}",
            assignment.status_note or assignment.intake.customer_name,
            f"status:{status}:{assignment.events.count()}",
        )
        _notify_rider_status(assignment, actor=actor)
    current_driver_user_id = assignment.driver.user_id if assignment.driver_id and assignment.driver else None
    rider_user_ids = tuple(value for value in (previous_driver_user_id, current_driver_user_id) if value)
    transaction.on_commit(
        lambda business_id=assignment.business_id, delivery_id=assignment.public_id, rider_ids=rider_user_ids: publish_delivery_changed(
            business_id, delivery_id, reason="status", rider_user_ids=rider_ids
        )
    )
    return assignment


def _method_quote_for_assignment(assignment, target_provider):
    intake = assignment.intake
    subtotal = sum((row.line_total for row in intake.items.all()), Decimal("0")).quantize(Decimal("0.01"))
    quote = assignment.quote or intake.delivery_quote
    settings, options = create_delivery_quote_options(
        business=assignment.business,
        subtotal=subtotal,
        destination_address=intake.customer_address or (quote.destination_address if quote else ""),
        area_id=quote.area_id if quote else None,
        latitude=quote.destination_latitude if quote else None,
        longitude=quote.destination_longitude if quote else None,
        actor=None,
    )
    target = next((row for row in options if row.provider == target_provider), None)
    if not target:
        label = "In-house" if target_provider == DeliverySettings.PROVIDER_INHOUSE else "delivery partner"
        raise ValidationError(f"{label} delivery is not currently available for this destination.")
    return settings, target


def switch_delivery_method(*, assignment, target_provider, actor=None, manager_approved=False):
    """Switch a paid Hybrid delivery before pickup without changing customer charge."""
    if target_provider not in {DeliverySettings.PROVIDER_INHOUSE, DeliverySettings.PROVIDER_THIRD_PARTY}:
        raise ValidationError("Choose In-house or the configured delivery partner.")
    if not business_has_module(assignment.business, "delivery"):
        raise ValidationError("Delivery is no longer included in this business plan.")
    current = DeliveryAssignment.raw_objects.select_related("intake", "quote", "provider_account", "business", "driver__user").get(pk=assignment.pk)
    settings = DeliverySettings.raw_objects.filter(business=current.business, enabled=True).first()
    if not settings or settings.default_provider != DeliverySettings.PROVIDER_HYBRID:
        raise ValidationError("Delivery-method switching is available only while Hybrid delivery is enabled.")
    if current.status not in {DeliveryAssignment.STATUS_PENDING, DeliveryAssignment.STATUS_ASSIGNED, DeliveryAssignment.STATUS_READY} or current.picked_up_at:
        raise ValidationError("Delivery method can only be switched before pickup.")
    if current.provider == target_provider:
        return current
    if settings.hybrid_switch_policy == DeliverySettings.SWITCH_LOCKED:
        raise ValidationError("This business locks the delivery method after payment.")

    _settings, target_quote = _method_quote_for_assignment(current, target_provider)
    paid_fee = Decimal(current.intake.delivery_fee or 0).quantize(Decimal("0.01"))
    new_cost = Decimal(target_quote.fee).quantize(Decimal("0.01"))
    if settings.hybrid_switch_policy == DeliverySettings.SWITCH_EQUAL_OR_LOWER and new_cost > paid_fee:
        raise ValidationError("This switch would cost more than the customer's paid delivery fee and is blocked by policy.")
    if settings.hybrid_switch_policy == DeliverySettings.SWITCH_APPROVAL_ABSORBS and new_cost > paid_fee and not manager_approved:
        raise ValidationError("A manager must approve this higher-cost switch. The customer will not be charged extra.")

    if current.provider == DeliverySettings.PROVIDER_THIRD_PARTY and current.provider_order_id:
        from .delivery_providers import cancel_assignment_with_provider
        cancel_assignment_with_provider(current, actor=actor)

    previous_provider = current.provider
    previous_account_id = current.provider_account_id
    previous_driver_id = current.driver_id
    previous_driver_user_id = current.driver.user_id if current.driver_id and current.driver else None
    with transaction.atomic():
        current = DeliveryAssignment.raw_objects.select_for_update().get(pk=current.pk)
        current.quote = target_quote
        current.origin = target_quote.origin
        current.provider = target_provider
        current.provider_account = target_quote.provider_account if target_provider == DeliverySettings.PROVIDER_THIRD_PARTY else None
        current.driver = None
        current.status = DeliveryAssignment.STATUS_PENDING
        current.status_note = "Delivery method switched before pickup."
        current.provider_order_id = ""
        current.provider_status = ""
        current.provider_payload = {}
        current.external_reference = ""
        current.external_tracking_url = ""
        current.method_switch_count += 1
        current.last_method_switched_at = timezone.now()
        current.eta_at = None
        current.save()
        DeliveryEvent.raw_objects.create(
            business=current.business,
            created_by=actor,
            assignment=current,
            status=current.status,
            note=current.status_note,
            metadata={
                "event": "method_switch",
                "from_provider": previous_provider,
                "to_provider": target_provider,
                "from_provider_account_id": previous_account_id,
                "to_provider_account_id": current.provider_account_id,
                "customer_paid_fee": str(paid_fee),
                "new_provider_cost": str(new_cost),
                "business_absorbed_difference": str(max(Decimal("0"), new_cost - paid_fee)),
                "manager_approved": bool(manager_approved),
            },
        )
        target_quote.status = DeliveryQuote.STATUS_USED
        target_quote.save(update_fields=["status", "updated_at"])
        audit(
            current.business, actor, "delivery_method_switch", current,
            f"Delivery {current.public_id} switched from {previous_provider} to {target_provider}",
            {"paid_fee": str(paid_fee), "new_cost": str(new_cost), "manager_approved": bool(manager_approved)},
        )
    if previous_driver_id:
        _notify_rider_assignment(current, previous_driver_id=previous_driver_id)
    _notify_dispatch(
        current,
        CommerceNotification.EVENT_DELIVERY_SWITCH,
        f"Delivery method changed · {current.intake.public_number}",
        f"{dict(DeliverySettings.PROVIDER_CHOICES).get(previous_provider)} → {dict(DeliverySettings.PROVIDER_CHOICES).get(target_provider)}. Customer fee unchanged.",
        f"switch:{current.method_switch_count}",
    )
    if current.provider_account_id and current.provider_account.auto_dispatch:
        from .delivery_providers import ProviderDispatchError, dispatch_assignment_to_provider
        try:
            current = dispatch_assignment_to_provider(current, actor=actor)
        except ProviderDispatchError as exc:
            _notify_dispatch(
                current,
                CommerceNotification.EVENT_DELIVERY_PROVIDER,
                f"Provider dispatch needs attention · {current.intake.public_number}",
                str(exc),
                f"switch-provider-failed:{current.method_switch_count}",
            )
    publish_delivery_changed(
        current.business_id, current.public_id, reason="method_switch",
        rider_user_ids=(previous_driver_user_id,) if previous_driver_user_id else (),
    )
    return current


def raise_delivery_issue(*, assignment, driver, category, details, actor=None):
    if assignment.business_id != driver.business_id or assignment.driver_id != driver.pk:
        raise ValidationError("You can only report issues for deliveries currently assigned to you.")
    if category not in {value for value, _ in DeliveryIssue.CATEGORY_CHOICES}:
        raise ValidationError("Choose a valid delivery issue category.")
    details = (details or "").strip()
    if len(details) < 5:
        raise ValidationError("Describe the issue so dispatch staff can act on it.")
    issue = DeliveryIssue.raw_objects.create(
        business=assignment.business,
        created_by=actor,
        assignment=assignment,
        reporter_driver=driver,
        category=category,
        details=details,
    )
    DeliveryEvent.raw_objects.create(
        business=assignment.business,
        created_by=actor,
        assignment=assignment,
        status=assignment.status,
        note=f"Rider issue: {issue.get_category_display()} — {details[:180]}",
        metadata={"event": "rider_issue", "issue_id": issue.pk, "category": category},
    )
    audit(
        assignment.business, actor, "delivery_issue", issue,
        f"Rider issue raised for {assignment.intake.public_number}",
        {"delivery_id": str(assignment.public_id), "category": category},
    )
    _notify_dispatch(
        assignment,
        CommerceNotification.EVENT_DELIVERY_ISSUE,
        f"Rider issue · {assignment.intake.public_number}",
        f"{driver.name}: {issue.get_category_display()} — {details[:220]}",
        f"issue:{issue.pk}",
    )
    return issue
