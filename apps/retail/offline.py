"""Authenticated, confirmed replay. Offline entries never bypass current rules."""

import json
import uuid
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from datetime import timedelta
from django.views.decorators.http import require_POST
from accounts.services import (
    is_business_admin,
    can_use_commerce_storefront,
    user_has_permission,
)
from commerce.models import StorefrontProduct, CommerceIntake
from commerce.checkout_services import (
    create_checkout,
    available_physical_stock_for_goods,
)
from commerce.payment_services import initiate_payment, record_verified_payment
from inventory.models import MarketStockLot
from core.services import audit
from .models import OfflineEntry, CashierCheckout
from .services import pos_context, lock_business


def access(request):
    branch, device, session = pos_context(request, require_session=False)
    if not is_business_admin(request.user, request.business) and (
        not device
        or device.status != "approved"
        or (device.branch_id and device.branch_id != branch.pk)
    ):
        raise PermissionDenied(
            "Offline packages require a registered, approved device."
        )
    return branch, device, session


@login_required
def snapshot(request):
    branch, device, session = access(request)
    dashboard_allowed = user_has_permission(
        request.user, request.business, "dashboard", "view"
    )
    delivery_allowed = user_has_permission(
        request.user, request.business, "delivery", "view"
    )
    inventory = user_has_permission(request.user, request.business, "inventory", "view")
    sell = can_use_commerce_storefront(request.user, request.business, "edit")
    if not inventory and not sell and not dashboard_allowed and not delivery_allowed:
        raise PermissionDenied
    products = []
    if inventory or sell:
        catalogue = list(
            StorefrontProduct.raw_objects.filter(
                business=request.business, published=True, allow_stock_order=True
            ).select_related("finished_good__business")
        )
        stock = available_physical_stock_for_goods(
            [product.finished_good for product in catalogue], branch=branch
        )
        for product in catalogue:
            good = product.finished_good
            if not good.can_sell_from_physical_store:
                continue
            from inventory.portioning import selection_for

            selection = selection_for(good, channel="physical_store")
            available = (
                stock.get(good.pk, Decimal("0")) / selection["multiplier"]
            ).quantize(Decimal(".01"), rounding=ROUND_DOWN)
            products.append(
                {
                    "id": str(product.public_id),
                    "promotion_order": good.pk,
                    "name": product.display_name,
                    "barcode": good.barcode,
                    "price": str(good.selling_price_for("physical_store")),
                    "stock": str(available),
                    "unit": selection["customer_unit"],
                }
            )
    lots = []
    if inventory:
        lots = [
            {
                "id": lot.pk,
                "product": lot.finished_good.name,
                "expiry": lot.expiry_date.isoformat() if lot.expiry_date else "",
                "quantity": str(lot.quantity_available),
            }
            for lot in MarketStockLot.raw_objects.filter(
                business=request.business, active=True, quantity_available__gt=0
            ).select_related("finished_good")
        ]
    dashboard = {}
    if dashboard_allowed:
        from copy import copy
        from django.http import QueryDict
        from core.dashboard_metrics import dashboard_metric

        for metric in ("sales", "revenue", "profit"):
            probe = copy(request)
            probe.GET = QueryDict("metric=" + metric + "&period=day")
            result = dashboard_metric(probe)
            if result.status_code == 200:
                dashboard[metric] = json.loads(result.content)
    deliveries = []
    drivers = []
    if delivery_allowed:
        from commerce.models import DeliveryAssignment, DeliveryDriver

        assignments = (
            DeliveryAssignment.raw_objects.filter(
                business=request.business,
                batch__isnull=True,
                status__in=["pending", "ready", "assigned"],
            )
            .select_related("intake", "driver", "origin")
            .order_by("-pk")[:200]
        )
        deliveries = [
            {
                "id": assignment.pk,
                "public_id": str(assignment.public_id),
                "order": assignment.intake.public_number,
                "customer": assignment.intake.customer_name,
                "address": assignment.intake.customer_address,
                "phone": assignment.intake.customer_phone,
                "origin": assignment.origin.name,
                "status": assignment.status,
                "updated_at": assignment.updated_at.isoformat(),
                "driver_id": assignment.driver_id,
                "provider": assignment.provider,
            }
            for assignment in assignments
        ]
        drivers = [
            {"id": driver.pk, "name": driver.name, "provider": driver.provider}
            for driver in DeliveryDriver.raw_objects.filter(
                business=request.business, active=True
            )
        ]
    packages = {
        "dashboard": dashboard_allowed,
        "inventory": inventory,
        "inventory_edit": user_has_permission(
            request.user, request.business, "inventory", "edit"
        ),
        "expiration": inventory,
        "sales": sell,
        "delivery": delivery_allowed,
        "delivery_edit": user_has_permission(
            request.user, request.business, "delivery", "edit"
        ),
    }
    from .offline_ui import prepared_interface

    interface = (
        prepared_interface(request, packages)
        if request.GET.get("interface") != "0"
        else None
    )
    from commerce.merchandising import active_promotions, promotion_metadata

    public_ids = (
        {p.finished_good_id: str(p.public_id) for p in catalogue}
        if packages.get("sales")
        else {}
    )
    promotions = (
        [
            promotion_metadata(p, request.business, public_ids)
            for p in active_promotions(request.business, "physical_store")
        ]
        if public_ids
        else []
    )
    response = JsonResponse(
        {
            "owner": f"{request.business.pk}:{request.user.pk}:{branch.pk}",
            "business": request.business.name,
            "currency": request.business.currency_symbol,
            "branch": branch.name,
            "branch_id": branch.pk,
            "saved_at": timezone.now().isoformat(),
            "cashier_session_id": session.pk if session else None,
            "csrf": get_token(request),
            "products": products,
            "promotions": promotions,
            "timezone": timezone.get_current_timezone_name(),
            "lots": lots,
            "dashboard": dashboard,
            "deliveries": deliveries,
            "drivers": drivers,
            "packages": packages,
            "ui": interface,
            "user": request.user.get_short_name(),
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
def sync(request):
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValidationError("Invalid offline sync request.")
        if payload.get("confirmed") is not True:
            raise ValidationError("Confirm before syncing offline entries.")
        rows = payload.get("entries", [])
        if (
            not isinstance(rows, list)
            or len(rows) > 100
            or not all(isinstance(row, dict) for row in rows)
        ):
            raise ValidationError("Sync up to 100 entries at a time.")
        branch, device, session = access(request)
        owner = f"{request.business.pk}:{request.user.pk}:{branch.pk}"
        if payload.get("owner") != owner:
            raise PermissionDenied(
                "Return to the original business, user and branch before syncing."
            )
    except (ValueError, TypeError, ValidationError) as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    results = []
    for row in rows:
        try:
            with transaction.atomic():
                # Serialize replays, including SQLite, before testing idempotency.
                lock_business(request.business)
                key = uuid.UUID(str(row.get("id", "")))
                existing = OfflineEntry.raw_objects.filter(
                    business=request.business, user=request.user, public_id=key
                ).first()
                if existing:
                    results.append(
                        {"id": str(key), "ok": True, "result": existing.result}
                    )
                    continue
                kind = row.get("kind")
                data = row.get("data", {})
                if not isinstance(data, dict):
                    raise ValidationError("Invalid offline entry.")
                if kind == "cash_sale":
                    if not can_use_commerce_storefront(
                        request.user, request.business, "edit"
                    ):
                        raise PermissionDenied
                    pos_context(request)
                    recorded_at = (
                        parse_datetime(str(row.get("created_at") or ""))
                        or timezone.now()
                    )
                    if timezone.is_naive(recorded_at):
                        recorded_at = timezone.make_aware(recorded_at)
                    if recorded_at > timezone.now() + timedelta(minutes=5):
                        raise ValidationError("The offline sale time is in the future.")
                    from core.ledger import validate_entry_date

                    validate_entry_date(
                        request.business, timezone.localtime(recorded_at).date()
                    )
                    recorded_session = data.get("cashier_session_id")
                    if recorded_session and (
                        not session or session.pk != recorded_session
                    ):
                        raise ValidationError(
                            "The original cashier session is no longer open. Review this cash receipt before syncing."
                        )
                    products = []
                    if (
                        not isinstance(data.get("items"), list)
                        or not 0 < len(data["items"]) <= 100
                    ):
                        raise ValidationError("Choose 1–100 product lines.")
                    for item in data["items"]:
                        product = StorefrontProduct.raw_objects.select_related(
                            "finished_good"
                        ).get(business=request.business, public_id=item["id"])
                        live = product.finished_good.selling_price_for("physical_store")
                        if Decimal(item["price"]) != live:
                            raise ValidationError(
                                f"{product.display_name}: price changed; review this sale."
                            )
                        products.append(
                            {
                                "storefront_product": product,
                                "quantity": item["quantity"],
                            }
                        )
                    checkout, _ = create_checkout(
                        business=request.business,
                        source=CommerceIntake.SOURCE_STAFF_POS,
                        customer={
                            "name": str(data.get("customer") or "Walk-in Customer")[
                                :160
                            ],
                            "phone": str(data.get("customer_phone") or "")[:40],
                            "email": str(data.get("customer_email") or "")[:254],
                        },
                        items=products,
                        order_mode="physical_store",
                        branch=branch,
                        service_mode=str(data.get("service_mode") or "")[:20],
                        table_reference=str(data.get("table_reference") or "")[:40],
                        recorded_at=recorded_at,
                        idempotency_key=f"offline:{request.user.pk}:{key}",
                    )
                    if "reward_items" in data:
                        saved_rewards = sorted(
                            (
                                str(row.get("product_id") or row.get("id")),
                                Decimal(str(row["quantity"])),
                            )
                            for row in data["reward_items"]
                        )
                        current_rewards = sorted(
                            (str(row["product_id"]), Decimal(str(row["quantity"])))
                            for row in checkout.promotion_snapshot.get("rewards", [])
                        )
                        if saved_rewards != current_rewards:
                            raise ValidationError(
                                "Promotional rewards changed; review this sale before confirming sync."
                            )
                    expected = sum(
                        (
                            (
                                Decimal(item["price"]) * Decimal(item["quantity"])
                            ).quantize(Decimal(".01"))
                            for item in data["items"]
                        ),
                        Decimal("0"),
                    )
                    if data.get("total") is not None:
                        expected = Decimal(str(data["total"]))
                    if checkout.amount != expected:
                        raise ValidationError(
                            "The payable total changed; review current promotions before confirming this sale."
                        )
                    if session and (
                        not recorded_session or session.pk == recorded_session
                    ):
                        CashierCheckout.objects.get_or_create(
                            checkout=checkout, defaults={"session": session}
                        )
                    payment = initiate_payment(
                        checkout=checkout,
                        method="cash",
                        surface="pos",
                        idempotency_key=f"offline-payment:{key}",
                    )
                    receipt, _ = record_verified_payment(
                        payment=payment,
                        amount=checkout.amount,
                        actor=request.user,
                        idempotency_key=f"offline-receipt:{key}",
                        note="Cash received offline; explicitly confirmed on sync",
                        verified_at=recorded_at,
                    )
                    result = {
                        "receipt": str(receipt.public_id),
                        "amount": str(checkout.amount),
                    }
                elif kind == "dispatch_assignment":
                    if not user_has_permission(
                        request.user, request.business, "delivery", "edit"
                    ):
                        raise PermissionDenied
                    from commerce.models import DeliveryAssignment, DeliveryDriver
                    from commerce.delivery_services import update_delivery_status

                    assignment = DeliveryAssignment.raw_objects.select_for_update().get(
                        business=request.business, pk=data.get("assignment")
                    )
                    if assignment.updated_at.isoformat() != data.get("updated_at"):
                        raise ValidationError(
                            "This delivery changed after preparation. Review the current dispatch assignment."
                        )
                    driver = DeliveryDriver.raw_objects.get(
                        business=request.business, pk=data.get("driver"), active=True
                    )
                    update_delivery_status(
                        assignment=assignment,
                        status=DeliveryAssignment.STATUS_ASSIGNED,
                        actor=request.user,
                        driver=driver,
                        note="Assigned offline; confirmed on sync",
                    )
                    result = {"assignment": assignment.pk, "driver": driver.pk}
                elif kind in {"expiry_flag", "stock_note"}:
                    if not user_has_permission(
                        request.user, request.business, "inventory", "edit"
                    ):
                        raise PermissionDenied
                    if kind == "expiry_flag":
                        lot = MarketStockLot.raw_objects.get(
                            business=request.business, pk=data.get("lot")
                        )
                        result = {
                            "lot": lot.pk,
                            "note": str(data.get("note") or "Flagged for review")[:500],
                        }
                    else:
                        result = {"note": str(data.get("note") or "")[:500]}
                    if not result.get("note"):
                        raise ValidationError("Enter a review note.")
                else:
                    raise ValidationError("This offline operation is not supported.")
                entry = OfflineEntry.raw_objects.create(
                    business=request.business,
                    user=request.user,
                    device=device,
                    branch=branch,
                    public_id=key,
                    kind=kind,
                    payload=data,
                    result=result,
                    created_by=request.user,
                )
                audit(
                    request.business,
                    request.user,
                    "offline_sync",
                    entry,
                    "Offline entry confirmed and synced",
                    {"kind": kind, "branch": branch.pk},
                )
                results.append({"id": str(key), "ok": True, "result": result})
        except (
            ValidationError,
            ValueError,
            TypeError,
            KeyError,
            InvalidOperation,
            ObjectDoesNotExist,
            PermissionDenied,
        ) as exc:
            results.append(
                {
                    "id": str(row.get("id", "")),
                    "ok": False,
                    "error": (
                        " ".join(exc.messages)
                        if isinstance(exc, ValidationError)
                        else "Entry could not be accepted. Check permissions, product and stock."
                    ),
                }
            )
    return JsonResponse({"results": results})
