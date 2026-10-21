from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.services import is_business_admin, user_has_permission
from .services import audit
from inventory.models import FinishedGood, MarketStockLot, RawMaterial, StockMovement
from .ledger import active_cutover, movement_balance, rebuild_live_stock
from .models import CashAccount, FinancialTransaction, LedgerCutover, OpeningPosition


def _admin(request):
    return bool(request.business and is_business_admin(request.user, request.business))


def _safe_next(request):
    destination = request.POST.get("next", "")
    if destination and url_has_allowed_host_and_scheme(destination, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return destination
    return "dashboard"


def _receivable_openings(business, cutover_date):
    from sales.models import Customer, CustomerPayment, Sale

    totals = {customer.pk: Decimal("0") for customer in Customer.objects.filter(business=business)}
    sales = Sale.raw_objects.filter(
        business=business, customer_master_id__in=totals,
        source__in=("distribution_order", "online_order"),
    ).filter(date__lt=cutover_date).prefetch_related("items__finished_good")
    for sale in sales:
        paid = sum((payment.amount for payment in CustomerPayment.raw_objects.filter(
            business=business, sale=sale, date__lt=cutover_date,
        )), Decimal("0"))
        totals[sale.customer_master_id] += max(Decimal("0"), sale.total - paid)
    return totals


def _payable_openings(business, cutover_date):
    from procurement.models import PurchaseOrder, Supplier, SupplierPayment

    totals = {supplier.pk: Decimal("0") for supplier in Supplier.objects.filter(business=business)}
    orders = PurchaseOrder.raw_objects.filter(
        business=business, supplier_master_id__in=totals, status="received",
    ).filter(received_date__lt=cutover_date).prefetch_related("items")
    for order in orders:
        paid = sum((payment.amount for payment in SupplierPayment.raw_objects.filter(
            business=business, purchase_order=order, date__lt=cutover_date,
        )), Decimal("0"))
        totals[order.supplier_master_id] += max(Decimal("0"), order.total - paid)
    return totals


def _opening_rows(business, cutover):
    rows = []
    for kind, model in (("raw", RawMaterial), ("fg", FinishedGood)):
        for item in model.objects.order_by("name"):
            if kind == "fg" and model.raw_objects.filter(pk=item.pk, business=business).values_list("stock", flat=True).first() is None:
                continue
            position = OpeningPosition.objects.filter(
                cutover=cutover, segment="live", kind=kind, object_id=item.pk,
            ).first()
            if not position:
                stored_stock = model.raw_objects.filter(pk=item.pk, business=business).values_list("stock", flat=True).first()
                posted_live = StockMovement.raw_objects.filter(
                    business=business, business_date__gte=cutover.cutover_date,
                    **({"raw_material": item} if kind == "raw" else {"finished_good": item}),
                )
                if kind == "fg":
                    posted_live = posted_live.filter(affects_stock=True)
                later_total = posted_live.aggregate(total=Sum("quantity"))["total"] or Decimal("0")
                opening = (stored_stock or Decimal("0")) - later_total
            else:
                opening = position.quantity or Decimal("0")
            rows.append({"kind": kind, "item": item, "quantity": opening, "step": "0.001" if kind == "raw" else "0.01", "source": position.source if position else "derived", "unit": item.usage_unit if kind == "raw" else item.unit})
    for account in CashAccount.objects.order_by("name"):
        position = OpeningPosition.objects.filter(
            cutover=cutover, segment="live", kind="cash", object_id=account.pk,
        ).first()
        if not position:
            # FinancialTransaction amounts are signed through transaction_type.
            later_net = sum((entry.signed_amount for entry in FinancialTransaction.raw_objects.filter(
                business=business, account=account, date__gte=cutover.cutover_date,
            )), Decimal("0"))
            opening = account.opening_balance + sum((entry.signed_amount for entry in FinancialTransaction.raw_objects.filter(
                business=business, account=account,
            )), Decimal("0")) - later_net
        else:
            opening = position.amount or Decimal("0")
        rows.append({"kind": "cash", "item": account, "quantity": opening, "step": "0.01", "source": position.source if position else "derived", "unit": business.currency_symbol})
    from sales.models import Customer
    from procurement.models import Supplier
    for kind, model, balances in (
        ("receivable", Customer, _receivable_openings(business, cutover.cutover_date)),
        ("payable", Supplier, _payable_openings(business, cutover.cutover_date)),
    ):
        for item in model.objects.filter(business=business).order_by("name"):
            position = OpeningPosition.objects.filter(
                cutover=cutover, segment="live", kind=kind, object_id=item.pk,
            ).first()
            amount = position.amount if position and position.amount is not None else balances.get(item.pk, Decimal("0"))
            rows.append({"kind": kind, "item": item, "quantity": amount, "step": "0.01",
                         "source": position.source if position else "derived", "unit": business.currency_symbol})
    return rows


def _merge_variances(business, cutover):
    variances = []
    for kind, model in (("raw", RawMaterial), ("fg", FinishedGood)):
        for item in model.objects.order_by("name"):
            if kind == "fg" and item.stock is None:
                continue
            live = OpeningPosition.objects.filter(cutover=cutover, segment="live", kind=kind, object_id=item.pk).first()
            live_opening = (live.quantity or Decimal("0")) if live else Decimal("0")
            backlog_close = movement_balance(business, item, kind, "backlog")
            variances.append({"item": item, "kind": kind, "live": live_opening, "backlog": backlog_close, "variance": backlog_close - live_opening})
    for account in CashAccount.objects.order_by("name"):
        live = OpeningPosition.objects.filter(cutover=cutover, segment="live", kind="cash", object_id=account.pk).first()
        live_opening = (live.amount or Decimal("0")) if live else account.opening_balance
        backlog_opening = OpeningPosition.raw_objects.filter(
            business=business, cutover=cutover, segment="backlog", kind="cash", object_id=account.pk,
        ).first()
        backlog_base = backlog_opening.amount if backlog_opening and backlog_opening.amount is not None else account.opening_balance
        backlog_net = sum((entry.signed_amount for entry in FinancialTransaction.raw_objects.filter(
            business=business, account=account, date__lt=cutover.cutover_date,
        )), Decimal("0"))
        backlog_close = backlog_base + backlog_net
        variances.append({"item": account, "kind": "cash", "live": live_opening, "backlog": backlog_close, "variance": backlog_close - live_opening})
    from sales.models import Customer
    from procurement.models import Supplier
    for kind, model, balances in (
        ("receivable", Customer, _receivable_openings(business, cutover.cutover_date)),
        ("payable", Supplier, _payable_openings(business, cutover.cutover_date)),
    ):
        for item in model.objects.filter(business=business).order_by("name"):
            live = OpeningPosition.objects.filter(cutover=cutover, segment="live", kind=kind, object_id=item.pk).first()
            live_amount = live.amount if live and live.amount is not None else Decimal("0")
            backlog_close = balances.get(item.pk, Decimal("0"))
            variances.append({"item": item, "kind": kind, "live": live_amount,
                              "backlog": backlog_close, "variance": backlog_close - live_amount})
    return variances


def _backlog_negative_dates(business, item, kind, cutover):
    opening = OpeningPosition.raw_objects.filter(
        business=business, cutover=cutover, segment="backlog", kind=kind, object_id=item.pk,
    ).first()
    balance = opening.quantity if opening and opening.quantity is not None else Decimal("0")
    movements = StockMovement.raw_objects.filter(
        business=business, business_date__lt=cutover.cutover_date,
        **({"raw_material": item} if kind == "raw" else {"finished_good": item}),
    )
    if kind == "fg":
        movements = movements.filter(affects_stock=True)
    negative = []
    for movement in movements.order_by("business_date", "id"):
        balance += movement.quantity
        if balance < 0 and (not negative or negative[-1] != movement.business_date):
            negative.append(movement.business_date)
    return negative


@login_required
def ledger_workspace(request):
    if not _admin(request):
        return render(request, "403.html", status=403)
    cutover = active_cutover(request.business)
    rows = _opening_rows(request.business, cutover) if cutover else []
    backlog_rows = []
    carryover_orders = []
    carryover_pos = []
    if cutover:
        for kind, model in (("raw", RawMaterial), ("fg", FinishedGood)):
            for item in model.objects.order_by("name"):
                if kind == "fg" and item.stock is None:
                    continue
                backlog_rows.append({"item": item, "kind": kind, "balance": movement_balance(request.business, item, kind, "backlog"), "negative_dates": _backlog_negative_dates(request.business, item, kind, cutover)})
        from production.models import Order
        from procurement.models import PurchaseOrder
        carryover_orders = list(Order.raw_objects.filter(business=request.business, date__lt=cutover.cutover_date).exclude(status__in=["completed", "cancelled", "reversed"]))
        carryover_pos = list(PurchaseOrder.raw_objects.filter(business=request.business, date__lt=cutover.cutover_date).exclude(status="received"))
    return render(request, "core/ledger_workspace.html", {
        "cutover": cutover, "opening_rows": rows, "backlog_rows": backlog_rows,
        "merge_variances": _merge_variances(request.business, cutover) if cutover and cutover.status == "complete" else [],
        "show_merge_preview": bool(request.session.get("ledger_merge_preview") == getattr(cutover, "pk", None)),
        "carryover_orders": carryover_orders, "carryover_pos": carryover_pos,
        "backlog_entry_allowed": user_has_permission(request.user, request.business, "ledger", "edit"),
        "is_business_admin": True,
    })


@login_required
@require_POST
@transaction.atomic
def ledger_action(request):
    if not _admin(request):
        return render(request, "403.html", status=403)
    business = request.business
    action = request.POST.get("action")
    cutover = active_cutover(business)
    try:
        if action == "start":
            if cutover:
                raise ValueError("An active ledger cut-over already exists.")
            cutover_date = datetime.strptime(request.POST.get("cutover_date", ""), "%Y-%m-%d").date()
            cutover = LedgerCutover.raw_objects.create(
                business=business, created_by=request.user, cutover_date=cutover_date,
                notes=(request.POST.get("notes") or "").strip(),
            )
            for row in _opening_rows(business, cutover):
                is_amount = row["kind"] in {"cash", "receivable", "payable"}
                OpeningPosition.raw_objects.update_or_create(
                    business=business, cutover=cutover, segment="live", kind=row["kind"], object_id=row["item"].pk,
                    defaults={"created_by": request.user, "quantity": None if is_amount else row["quantity"],
                              "amount": row["quantity"] if is_amount else None, "source": "derived"},
                )
            for kind, model in (("raw", RawMaterial), ("fg", FinishedGood)):
                for item in model.raw_objects.filter(business=business):
                    if kind == "fg" and item.stock is None:
                        continue
                    historical = StockMovement.raw_objects.filter(
                        business=business,
                        **({"raw_material": item} if kind == "raw" else {"finished_good": item}),
                    )
                    if kind == "fg":
                        historical = historical.filter(affects_stock=True)
                    movement_total = historical.aggregate(total=Sum("quantity"))["total"] or Decimal("0")
                    initial = (item.__dict__.get("stock") or Decimal("0")) - movement_total
                    OpeningPosition.raw_objects.update_or_create(
                        business=business, cutover=cutover, segment="backlog", kind=kind, object_id=item.pk,
                        defaults={"created_by": request.user, "quantity": initial, "amount": None,
                                  "source": "derived", "note": "Estimated from stored stock less dated movements."},
                    )
            for account in CashAccount.raw_objects.filter(business=business):
                OpeningPosition.raw_objects.update_or_create(
                    business=business, cutover=cutover, segment="backlog", kind="cash", object_id=account.pk,
                    defaults={"created_by": request.user, "quantity": None, "amount": account.opening_balance,
                              "source": "derived", "note": "Original account opening balance."},
                )
            messages.success(request, "Cut-over opened. Review the derived opening positions and carry-over checklist.")
        elif not cutover:
            raise ValueError("There is no active ledger cut-over.")
        elif action == "save_openings":
            for row in _opening_rows(business, cutover):
                key = f"pos_{row['kind']}_{row['item'].pk}"
                raw_value = request.POST.get(key, "").strip()
                value = Decimal(raw_value)
                is_amount = row["kind"] in {"cash", "receivable", "payable"}
                OpeningPosition.raw_objects.update_or_create(
                    business=business, cutover=cutover, segment="live", kind=row["kind"], object_id=row["item"].pk,
                    defaults={"created_by": request.user,
                              "quantity": None if is_amount else value,
                              "amount": value if is_amount else None,
                              "source": "counted", "note": request.POST.get(f"note_{row['kind']}_{row['item'].pk}", "")[:255]},
                )
            rebuild_live_stock(business)
            messages.success(request, "Opening positions saved and live stock rebuilt.")
        elif action == "carry_over":
            from production.models import Order
            from procurement.models import PurchaseOrder
            order_ids = request.POST.getlist("orders")
            po_ids = request.POST.getlist("purchase_orders")
            for order in Order.raw_objects.filter(business=business, pk__in=order_ids, date__lt=cutover.cutover_date).exclude(status__in=["completed", "cancelled", "reversed"]):
                Order.raw_objects.filter(pk=order.pk, business=business).update(carried_from_date=order.date, date=cutover.cutover_date)
            for po in PurchaseOrder.raw_objects.filter(business=business, pk__in=po_ids, date__lt=cutover.cutover_date).exclude(status="received"):
                PurchaseOrder.raw_objects.filter(pk=po.pk, business=business).update(carried_from_date=po.date, date=cutover.cutover_date)
            messages.success(request, "Selected open documents were carried to the cut-over date.")
        elif action == "complete":
            cutover.status = LedgerCutover.STATUS_COMPLETE
            cutover.backlog_complete_at = timezone.now()
            cutover.save(update_fields=["status", "backlog_complete_at", "updated_at"])
            messages.success(request, "Backlog marked complete. Merge and archive controls are now available.")
        elif action == "preview_merge":
            if cutover.status != LedgerCutover.STATUS_COMPLETE:
                raise ValueError("Complete the backlog before previewing a merge.")
            request.session["ledger_merge_preview"] = cutover.pk
            messages.info(request, "Merge preview calculated. No balances were changed.")
        elif action == "archive":
            if cutover.status not in {LedgerCutover.STATUS_COMPLETE, LedgerCutover.STATUS_MERGED}:
                raise ValueError("Complete the backlog before archiving it.")
            cutover.status = LedgerCutover.STATUS_ARCHIVED
            cutover.save(update_fields=["status", "updated_at"])
            request.session["ledger_mode"] = "live"
            messages.success(request, "Backlog archived.")
        elif action == "merge":
            if cutover.status != LedgerCutover.STATUS_COMPLETE:
                raise ValueError("Complete the backlog before merging it.")
            if request.session.get("ledger_merge_preview") != cutover.pk:
                raise ValueError("Review the merge preview before committing it.")
            snapshot = {"opening_positions": [], "stock": [], "costs": [], "market_lots": [], "variances": []}
            snapshot["variances"] = [
                {"kind": row["kind"], "object_id": row["item"].pk,
                 "live": str(row["live"]), "backlog": str(row["backlog"]),
                 "variance": str(row["variance"])}
                for row in _merge_variances(business, cutover)
            ]
            for position in OpeningPosition.raw_objects.filter(business=business, cutover=cutover, segment="live"):
                snapshot["opening_positions"].append({"pk": position.pk, "quantity": str(position.quantity) if position.quantity is not None else None,
                    "amount": str(position.amount) if position.amount is not None else None, "source": position.source, "note": position.note})
            for kind, model in (("raw", RawMaterial), ("fg", FinishedGood)):
                for item in model.raw_objects.filter(business=business):
                    if kind == "fg" and item.stock is None:
                        continue
                    if kind == "raw":
                        snapshot["costs"].append({"pk": item.pk, "cost_per_unit": str(item.cost_per_unit)})
                    snapshot["stock"].append({"kind": kind, "pk": item.pk, "stock": str(item.__dict__.get("stock")) if item.__dict__.get("stock") is not None else None})
                    backlog_close = movement_balance(business, item, kind, "backlog")
                    position, _ = OpeningPosition.raw_objects.update_or_create(
                        business=business, cutover=cutover, segment="live", kind=kind, object_id=item.pk,
                        defaults={"created_by": request.user, "quantity": backlog_close, "amount": None,
                                  "source": "merged", "note": "Backlog closing position carried into live opening."},
                    )
            from procurement.models import RawMaterialCostSnapshot
            from inventory.services import _market_lot_balance
            for lot in MarketStockLot.raw_objects.select_for_update().filter(
                business=business, received_date__lt=cutover.cutover_date,
            ):
                snapshot["market_lots"].append({
                    "pk": lot.pk, "quantity_available": str(lot.quantity_available),
                    "active": lot.active, "closed_reason": lot.closed_reason,
                })
                closing = _market_lot_balance(lot, cutover.cutover_date - timedelta(days=1))
                MarketStockLot.raw_objects.filter(pk=lot.pk, business=business).update(
                    quantity_available=closing, active=closing > 0,
                    closed_reason="" if closing > 0 else "merged_closed",
                )
            for material in RawMaterial.raw_objects.filter(business=business):
                latest_cost = RawMaterialCostSnapshot.raw_objects.filter(
                    business=business, raw_material=material,
                    effective_date__lt=cutover.cutover_date,
                ).order_by("-effective_date", "-id").first()
                if latest_cost:
                    RawMaterial.raw_objects.filter(pk=material.pk, business=business).update(
                        cost_per_unit=latest_cost.usage_unit_cost,
                    )
            for account in CashAccount.raw_objects.filter(business=business):
                backlog_opening = OpeningPosition.raw_objects.filter(
                    business=business, cutover=cutover, segment="backlog", kind="cash", object_id=account.pk,
                ).first()
                backlog_base = backlog_opening.amount if backlog_opening and backlog_opening.amount is not None else account.opening_balance
                backlog_net = sum((entry.signed_amount for entry in FinancialTransaction.raw_objects.filter(
                    business=business, account=account, date__lt=cutover.cutover_date,
                )), Decimal("0"))
                OpeningPosition.raw_objects.update_or_create(
                    business=business, cutover=cutover, segment="live", kind="cash", object_id=account.pk,
                    defaults={"created_by": request.user, "quantity": None, "amount": backlog_base + backlog_net,
                              "source": "merged", "note": "Backlog closing cash carried into live opening."},
                )
            from sales.models import Customer
            from procurement.models import Supplier
            for kind, model, balances, note in (
                ("receivable", Customer, _receivable_openings(business, cutover.cutover_date), "Backlog receivable carried into live opening."),
                ("payable", Supplier, _payable_openings(business, cutover.cutover_date), "Backlog payable carried into live opening."),
            ):
                for item in model.raw_objects.filter(business=business):
                    OpeningPosition.raw_objects.update_or_create(
                        business=business, cutover=cutover, segment="live", kind=kind, object_id=item.pk,
                        defaults={"created_by": request.user, "quantity": None,
                                  "amount": balances.get(item.pk, Decimal("0")),
                                  "source": "merged", "note": note},
                    )
            cutover.merge_snapshot = snapshot
            cutover.status = LedgerCutover.STATUS_MERGED
            cutover.merged_at = timezone.now()
            cutover.save(update_fields=["merge_snapshot", "status", "merged_at", "updated_at"])
            rebuild_live_stock(business)
            audit(business, request.user, "ledger_merge", cutover,
                  "Backlog merged into live opening positions",
                  {"cutover_id": cutover.pk, "opening_position_count": len(snapshot["opening_positions"]),
                   "variance_count": len(snapshot["variances"])})
            request.session.pop("ledger_merge_preview", None)
            request.session["ledger_mode"] = "live"
            messages.success(request, "Backlog merged into live openings.")
        elif action == "undo_merge":
            if cutover.status != LedgerCutover.STATUS_MERGED:
                raise ValueError("This cut-over has not been merged.")
            if cutover.merged_at and (
                StockMovement.raw_objects.filter(business=business, created_at__gt=cutover.merged_at).exists()
                or FinancialTransaction.raw_objects.filter(business=business, created_at__gt=cutover.merged_at).exists()
            ):
                raise ValueError("New stock or cash activity exists after the merge. Review it before undoing this merge.")
            snapshot = cutover.merge_snapshot or {}
            snapshot_ids = [row["pk"] for row in snapshot.get("opening_positions", [])]
            OpeningPosition.raw_objects.filter(
                business=business, cutover=cutover, segment="live",
            ).exclude(pk__in=snapshot_ids).delete()
            for row in snapshot.get("opening_positions", []):
                position = OpeningPosition.raw_objects.filter(business=business, cutover=cutover, pk=row["pk"]).first()
                if position:
                    position.quantity = Decimal(row["quantity"]) if row["quantity"] is not None else None
                    position.amount = Decimal(row["amount"]) if row["amount"] is not None else None
                    position.source = row["source"]
                    position.note = row["note"]
                    position.save(update_fields=["quantity", "amount", "source", "note", "updated_at"])
            for row in snapshot.get("costs", []):
                RawMaterial.raw_objects.filter(pk=row["pk"], business=business).update(
                    cost_per_unit=Decimal(row["cost_per_unit"]),
                )
            for row in snapshot.get("market_lots", []):
                MarketStockLot.raw_objects.filter(pk=row["pk"], business=business).update(
                    quantity_available=Decimal(row["quantity_available"]),
                    active=row["active"], closed_reason=row["closed_reason"],
                )
            cutover.status = LedgerCutover.STATUS_COMPLETE
            cutover.merged_at = None
            cutover.save(update_fields=["status", "merged_at", "updated_at"])
            rebuild_live_stock(business)
            for row in snapshot.get("stock", []):
                model = RawMaterial if row["kind"] == "raw" else FinishedGood
                value = Decimal(row["stock"]) if row["stock"] is not None else None
                model.raw_objects.filter(pk=row["pk"], business=business).update(stock=value)
            audit(business, request.user, "ledger_merge_undo", cutover,
                  "Backlog merge undone and previous openings restored",
                  {"cutover_id": cutover.pk, "opening_position_count": len(snapshot.get("opening_positions", []))})
            request.session.pop("ledger_merge_preview", None)
            messages.success(request, "Merge undone and live stock rebuilt from the restored openings.")
        else:
            raise ValueError("Unknown ledger action.")
    except (ValueError, ArithmeticError, TypeError) as exc:
        messages.error(request, str(exc))
    return redirect("ledger_workspace")


@login_required
@require_POST
def ledger_mode(request):
    mode = request.POST.get("mode", "live")
    cutover = active_cutover(request.business)
    if mode == "backlog":
        if not cutover or cutover.status not in {LedgerCutover.STATUS_OPEN, LedgerCutover.STATUS_COMPLETE}:
            messages.error(request, "There is no open backlog to enter.")
            return redirect(_safe_next(request))
        if not user_has_permission(request.user, request.business, "ledger", "edit"):
            return render(request, "403.html", status=403)
    elif mode not in {"live", "all"}:
        mode = "live"
    request.session["ledger_mode"] = mode
    return redirect(_safe_next(request))
