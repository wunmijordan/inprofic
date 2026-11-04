"""Ledger cut-over admin: start, openings, carry-over, merge, archive, revert.

All state changes are business-admin only, atomic, audited and run under the
ledger guard bypass. Stock and cash figures come from grouped, cached maps in
``core.ledger`` so the page costs a handful of queries however many items exist.
"""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import TruncDate
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import ledger
from .models import CashAccount, FinancialTransaction, LedgerCutover, OpeningPosition
from .services import audit

LEDGER_REVERT_GRACE = timedelta(days=7)
OPEN_ORDER_STATUSES = ("pending", "approved")
OPEN_PO_STATUSES = ("draft", "ordered", "approved")


def _admin(request):
    return bool(request.business and ledger.can_manage(request.user, request.business))


def _safe_next(request):
    destination = request.POST.get("next", "")
    if destination and url_has_allowed_host_and_scheme(
        destination, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return destination
    return "dashboard"


def _revert_deadline(cutover):
    return cutover.created_at + LEDGER_REVERT_GRACE


def _can_revert(cutover, *, now=None):
    return bool(
        cutover
        and cutover.status in {LedgerCutover.STATUS_OPEN, LedgerCutover.STATUS_COMPLETE}
        and (now or timezone.now()) <= _revert_deadline(cutover)
    )


# --------------------------------------------------------------------------
# Data assembly (grouped queries, no per-item lookups)
# --------------------------------------------------------------------------

def _stock_items(business):
    """[{kind, id, name, unit, stored}] for raw materials and stocked products."""
    from inventory.models import FinishedGood, RawMaterial
    items = []
    for pk, name, unit, stock in RawMaterial.raw_objects.filter(business=business).order_by("name").values_list(
        "pk", "name", "usage_unit", "stock"
    ):
        items.append({"kind": "raw", "id": pk, "name": name, "unit": unit, "stored": stock or Decimal("0")})
    for pk, name, unit, stock in FinishedGood.raw_objects.filter(
        business=business, stock__isnull=False
    ).order_by("name").values_list("pk", "name", "unit", "stock"):
        items.append({"kind": "fg", "id": pk, "name": name, "unit": unit, "stored": stock})
    return items


def _cash_backlog_net(business, cutover_date):
    """{account_id: net backlog cash movement}."""
    net = {}
    rows = (
        FinancialTransaction.raw_objects.filter(business=business, date__lt=cutover_date)
        .values("account_id", "transaction_type").annotate(total=Sum("amount"))
    )
    for row in rows:
        sign = 1 if row["transaction_type"] == FinancialTransaction.INCOME else -1
        net[row["account_id"]] = net.get(row["account_id"], Decimal("0")) + sign * (row["total"] or Decimal("0"))
    return net


def _opening_rows(business, cutover):
    live = ledger.positions_map(business, "live")
    later = ledger.movement_sums(business, "live")
    rows = []
    for item in _stock_items(business):
        key = (item["kind"], item["id"])
        pos = live.get(key)
        quantity = pos.quantity if pos and pos.quantity is not None else item["stored"] - later.get(key, Decimal("0"))
        rows.append({
            **item, "quantity": quantity, "has_position": bool(pos),
            "source": pos.source if pos else "derived", "step": "0.001" if item["kind"] == "raw" else "0.01",
            "note": pos.note if pos else "",
        })
    net = _cash_backlog_net(business, cutover.cutover_date)
    for account in CashAccount.raw_objects.filter(business=business).order_by("name"):
        pos = live.get(("cash", account.pk))
        amount = pos.amount if pos and pos.amount is not None else account.opening_balance + net.get(account.pk, Decimal("0"))
        rows.append({
            "kind": "cash", "id": account.pk, "name": account.name, "unit": business.currency_symbol,
            "quantity": amount, "has_position": bool(pos), "source": pos.source if pos else "derived",
            "step": "0.01", "note": pos.note if pos else "",
        })
    return rows


def _backlog_rows(business, cutover):
    sums = ledger.movement_sums(business, "backlog")
    balances = ledger.segment_balances(business, "backlog")
    negatives = ledger.backlog_negative_dates(business)
    rows = []
    for item in _stock_items(business):
        key = (item["kind"], item["id"])
        if key not in sums and key not in negatives and not balances.get(key):
            continue
        dates = negatives.get(key, [])
        rows.append({**item, "balance": balances.get(key, Decimal("0")), "negative_dates": dates[:6],
                     "negative_count": len(dates)})
    rows.sort(key=lambda r: (-r["negative_count"], r["name"]))
    return rows


def _merge_variances(business, cutover):
    live = ledger.positions_map(business, "live")
    balances = ledger.segment_balances(business, "backlog")
    stock = []
    for item in _stock_items(business):
        key = (item["kind"], item["id"])
        pos = live.get(key)
        if not pos:
            continue
        closing = balances.get(key, Decimal("0"))
        opening = pos.quantity or Decimal("0")
        stock.append({**item, "live": opening, "backlog": closing, "variance": closing - opening})
    stock.sort(key=lambda r: (-abs(r["variance"]), r["name"]))
    backlog_cash = ledger.positions_map(business, "backlog")
    net = _cash_backlog_net(business, cutover.cutover_date)
    cash = []
    for account in CashAccount.raw_objects.filter(business=business).order_by("name"):
        pos = live.get(("cash", account.pk))
        if not pos:
            continue
        base_pos = backlog_cash.get(("cash", account.pk))
        base = base_pos.amount if base_pos and base_pos.amount is not None else account.opening_balance
        closing = base + net.get(account.pk, Decimal("0"))
        opening = pos.amount or Decimal("0")
        cash.append({"kind": "cash", "id": account.pk, "name": account.name, "live": opening,
                     "backlog": closing, "variance": closing - opening})
    return stock, cash


def _carry_candidates(business, cutover):
    from procurement.models import PurchaseOrder
    from production.models import Order
    orders = list(Order.raw_objects.filter(
        business=business, date__lt=cutover.cutover_date, status__in=OPEN_ORDER_STATUSES
    ).order_by("date", "id")[:200])
    pos = list(PurchaseOrder.raw_objects.filter(
        business=business, date__lt=cutover.cutover_date, status__in=OPEN_PO_STATUSES
    ).order_by("date", "id")[:200])
    return orders, pos


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------

@login_required
def ledger_workspace(request):
    if not _admin(request):
        return render(request, "403.html", status=403)
    business = request.business
    cutover = ledger.active_cutover(business)
    context = {
        "cutover": cutover, "backlog_entry_allowed": ledger.can_enter_backlog(request.user, business),
        "is_business_admin": True, "can_revert_ledger": _can_revert(cutover),
        "revert_deadline": _revert_deadline(cutover) if cutover else None,
        "opening_stock_rows": [], "opening_cash_rows": [], "backlog_rows": [],
        "merge_stock": [], "merge_cash": [], "carryover_orders": [], "carryover_pos": [],
        "show_merge_preview": False, "drift_rows": [],
    }
    if cutover:
        rows = _opening_rows(business, cutover)
        context["opening_stock_rows"] = [r for r in rows if r["kind"] in ledger.STOCK_KINDS]
        context["opening_cash_rows"] = [r for r in rows if r["kind"] == "cash"]
        context["backlog_rows"] = _backlog_rows(business, cutover)
        context["unhealthy_count"] = sum(1 for r in context["backlog_rows"] if r["negative_count"])
        if cutover.status != LedgerCutover.STATUS_MERGED:
            context["carryover_orders"], context["carryover_pos"] = _carry_candidates(business, cutover)
            context["drift_rows"] = ledger.integrity_report(business)
        if cutover.status == LedgerCutover.STATUS_COMPLETE:
            context["merge_stock"], context["merge_cash"] = _merge_variances(business, cutover)
            context["show_merge_preview"] = request.session.get("ledger_merge_preview") == cutover.pk
            context["merge_changed"] = (
                sum(1 for r in context["merge_stock"] if r["variance"])
                + sum(1 for r in context["merge_cash"] if r["variance"])
            )
    return render(request, "core/ledger_workspace.html", context)


def _parse_decimal(raw):
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError):
        raise ValueError("Enter every opening figure as a plain number.")


@transaction.atomic
def _start(request, business):
    from inventory.models import StockMovement
    if ledger.active_cutover(business):
        raise ValueError("An active ledger cut-over already exists.")
    try:
        cutover_date = datetime.strptime(request.POST.get("cutover_date", ""), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("Choose the date Live recording starts.")
    if cutover_date > timezone.localdate():
        raise ValueError("The Live start date cannot be in the future.")
    with ledger.bypass():
        # Movements recorded before business_date existed use their entry date.
        StockMovement.raw_objects.filter(business=business, business_date__isnull=True).update(
            business_date=TruncDate("occurred_at")
        )
        cutover = LedgerCutover.raw_objects.create(
            business=business, created_by=request.user, cutover_date=cutover_date,
            notes=(request.POST.get("notes") or "").strip(),
        )
        ledger.invalidate(business)
        before = ledger.movement_sums(business, "backlog")
        after = ledger.movement_sums(business, "live")
        rows = []
        for item in _stock_items(business):
            key = (item["kind"], item["id"])
            # Residual = stock no recorded movement explains (initial stock, imports),
            # so backlog + live together reproduce today's stored stock exactly.
            residual = item["stored"] - before.get(key, Decimal("0")) - after.get(key, Decimal("0"))
            rows.append(OpeningPosition(
                business=business, created_by=request.user, cutover=cutover, segment="backlog",
                kind=item["kind"], object_id=item["id"], quantity=residual, source="derived",
                note="Stock not explained by recorded movements",
            ))
            rows.append(OpeningPosition(
                business=business, created_by=request.user, cutover=cutover, segment="live",
                kind=item["kind"], object_id=item["id"], quantity=item["stored"] - after.get(key, Decimal("0")),
                source="derived", note="Provisional: confirm with a count",
            ))
        net = _cash_backlog_net(business, cutover_date)
        for account in CashAccount.raw_objects.filter(business=business):
            rows.append(OpeningPosition(
                business=business, created_by=request.user, cutover=cutover, segment="backlog", kind="cash",
                object_id=account.pk, amount=account.opening_balance, source="derived",
                note="Original account opening balance",
            ))
            rows.append(OpeningPosition(
                business=business, created_by=request.user, cutover=cutover, segment="live", kind="cash",
                object_id=account.pk, amount=account.opening_balance + net.get(account.pk, Decimal("0")),
                source="derived", note="Provisional: confirm with a count",
            ))
        OpeningPosition.raw_objects.bulk_create(rows)
    ledger.invalidate(business)
    audit(business, request.user, "ledger_start", cutover, f"Ledger cut-over started for {cutover_date:%d %b %Y}",
          {"cutover_date": cutover_date.isoformat(), "positions": len(rows)})
    messages.success(request, "Cut-over opened. Confirm the opening positions and review the carry-over checklist.")


def _apply_openings(request, business, cutover, entries, *, confirm=False):
    """Save live opening positions from ``[(kind, id, value, note-or-None)]``.

    The workspace form posts every row, so only rows that differ are saved (and
    marked counted). Count sheets and the phone count page post only the rows
    somebody actually counted, so with ``confirm=True`` every entry is saved as
    counted, even when the count equals the provisional figure.
    """
    if cutover.status == LedgerCutover.STATUS_MERGED:
        raise ValueError("Openings cannot be edited after a merge. Undo the merge first.")
    rows = {(r["kind"], r["id"]): r for r in _opening_rows(business, cutover)}
    changed = 0
    with ledger.bypass():
        for kind, object_id, value, posted_note in entries:
            row = rows.get((kind, object_id))
            if row is None:
                raise ValueError(f"Unknown item: {kind} #{object_id}.")
            note = row["note"] or "" if posted_note is None else posted_note[:255]
            if not confirm:
                unchanged = row["has_position"] and value == row["quantity"] and note == (row["note"] or "")
                if unchanged or (not row["has_position"] and value == row["quantity"]):
                    continue
            is_cash = kind == "cash"
            OpeningPosition.raw_objects.update_or_create(
                business=business, cutover=cutover, segment="live", kind=kind, object_id=object_id,
                defaults={"created_by": request.user, "quantity": None if is_cash else value,
                          "amount": value if is_cash else None, "source": "counted", "note": note},
            )
            changed += 1
    ledger.invalidate(business)
    counts = ledger.rebuild_live_stock(business)
    audit(business, request.user, "ledger_openings", cutover, "Live opening positions saved",
          {"rows_changed": changed, "stock_rebuilt": counts, "confirmed_counts": confirm})
    messages.success(request, f"{changed} opening position(s) saved; live stock rebuilt.")
    return changed


@transaction.atomic
def _save_openings(request, business, cutover):
    entries = []
    for row in _opening_rows(business, cutover):
        posted = (request.POST.get(f"pos_{row['kind']}_{row['id']}") or "").strip()
        if posted == "":
            continue
        entries.append((row["kind"], row["id"], _parse_decimal(posted), request.POST.get(f"note_{row['kind']}_{row['id']}")))
    _apply_openings(request, business, cutover, entries, confirm=request.POST.get("confirm_counts") == "1")


@transaction.atomic
def _carry_over(request, business, cutover):
    from procurement.models import PurchaseOrder
    from production.models import Order
    if cutover.status == LedgerCutover.STATUS_MERGED:
        raise ValueError("Carry-over is only available before a merge.")
    moved = 0
    for model, field, statuses, label in (
        (Order, "orders", OPEN_ORDER_STATUSES, "Order"),
        (PurchaseOrder, "purchase_orders", OPEN_PO_STATUSES, "Purchase order"),
    ):
        ids = [int(v) for v in request.POST.getlist(field) if v.isdigit()]
        for doc in model.raw_objects.filter(business=business, pk__in=ids, date__lt=cutover.cutover_date, status__in=statuses):
            original = doc.date
            model.raw_objects.filter(pk=doc.pk, business=business).update(
                carried_from_date=original, date=cutover.cutover_date)
            audit(business, request.user, "ledger_carry", doc, f"{label} #{doc.pk} carried over from {original:%d %b %Y}",
                  {"original_date": original.isoformat(), "new_date": cutover.cutover_date.isoformat()})
            moved += 1
    messages.success(request, f"{moved} open document(s) carried to {cutover.cutover_date:%d %b %Y}.")


def _set_status(request, business, cutover, allowed, new_status, action, message, description):
    if cutover.status not in allowed:
        raise ValueError(f"This action is not available while the cut-over is '{cutover.get_status_display()}'.")
    cutover.status = new_status
    fields = ["status", "updated_at"]
    if new_status == LedgerCutover.STATUS_COMPLETE:
        cutover.backlog_complete_at = timezone.now()
        fields.append("backlog_complete_at")
    cutover.save(update_fields=fields)
    ledger.invalidate(business)
    request.session["ledger_mode"] = "live"
    request.session.pop("ledger_merge_preview", None)
    audit(business, request.user, action, cutover, description, {"cutover_id": cutover.pk})
    messages.success(request, message)


@transaction.atomic
def _merge(request, business, cutover):
    from inventory.models import MarketStockLot, MarketStockMovement, RawMaterial
    from procurement.models import RawMaterialCostSnapshot
    if cutover.status != LedgerCutover.STATUS_COMPLETE:
        raise ValueError("Mark the backlog complete before merging it.")
    if request.session.get("ledger_merge_preview") != cutover.pk:
        raise ValueError("Review the merge preview before committing it.")
    stock, cash = _merge_variances(business, cutover)
    live = ledger.positions_map(business, "live")
    snapshot = {"positions": [], "costs": [], "market_lots": [], "variances": []}
    with ledger.bypass():
        for row in stock + cash:
            pos = live[(row["kind"], row["id"])]
            snapshot["positions"].append({
                "pk": pos.pk, "quantity": str(pos.quantity) if pos.quantity is not None else None,
                "amount": str(pos.amount) if pos.amount is not None else None,
                "source": pos.source, "note": pos.note,
            })
            snapshot["variances"].append({"kind": row["kind"], "id": row["id"], "name": row["name"],
                                          "live": str(row["live"]), "backlog": str(row["backlog"]),
                                          "variance": str(row["variance"])})
            if row["kind"] == "cash":
                pos.amount = row["backlog"]
            else:
                pos.quantity = row["backlog"]
            pos.source = "merged"
            pos.note = "Backlog closing position carried into Live opening."
        OpeningPosition.raw_objects.bulk_update([live[(r["kind"], r["id"])] for r in stock + cash],
                                                ["quantity", "amount", "source", "note"])
        # Cost: the latest dated receipt overall wins (backlog receipts never overwrote it,
        # but entries made before this feature might have).
        for material in RawMaterial.raw_objects.filter(business=business):
            latest = RawMaterialCostSnapshot.raw_objects.filter(
                business=business, raw_material=material).order_by("-effective_date", "-id").first()
            if latest and latest.usage_unit_cost != material.cost_per_unit:
                snapshot["costs"].append({"pk": material.pk, "previous": str(material.cost_per_unit),
                                          "merged": str(latest.usage_unit_cost)})
                RawMaterial.raw_objects.filter(pk=material.pk, business=business).update(
                    cost_per_unit=latest.usage_unit_cost)
        # Market lots: once merged, a lot's available quantity is the combined truth
        # (every recorded movement, backlog and live) and backlog-entry lots go live.
        totals = {
            row["lot_id"]: row["total"] for row in MarketStockMovement.raw_objects.filter(business=business)
            .values("lot_id").annotate(total=Sum("quantity"))
        }
        for lot in MarketStockLot.raw_objects.select_for_update().filter(
            business=business, received_date__lt=cutover.cutover_date
        ):
            combined = totals.get(lot.pk)
            if combined is None and not lot.is_backlog_entry:
                continue  # no movement history to rebuild from: keep the live figure
            combined = Decimal(combined if combined is not None else lot.quantity_received).quantize(Decimal("0.01"))
            snapshot["market_lots"].append({
                "pk": lot.pk, "quantity_available": str(lot.quantity_available), "active": lot.active,
                "closed_reason": lot.closed_reason, "is_backlog_entry": lot.is_backlog_entry,
                "merged_quantity": str(combined),
            })
            MarketStockLot.raw_objects.filter(pk=lot.pk, business=business).update(
                quantity_available=combined, active=combined > 0, is_backlog_entry=False,
                closed_reason="" if combined > 0 else (lot.closed_reason or "merged_closed"))
        cutover.merge_snapshot = snapshot
        cutover.status = LedgerCutover.STATUS_MERGED
        cutover.merged_at = timezone.now()
        cutover.save(update_fields=["merge_snapshot", "status", "merged_at", "updated_at"])
        ledger.invalidate(business)
        ledger.rebuild_live_stock(business)
    audit(business, request.user, "ledger_merge", cutover, "Backlog merged into Live opening positions",
          {"cutover_id": cutover.pk, "positions": len(snapshot["positions"]),
           "with_variance": sum(1 for v in snapshot["variances"] if Decimal(v["variance"]) != 0)})
    request.session.pop("ledger_merge_preview", None)
    request.session["ledger_mode"] = "live"
    messages.success(request, "Backlog merged. Live now continues from the backlog's closing position.")


@transaction.atomic
def _undo_merge(request, business, cutover):
    from inventory.models import MarketStockLot, RawMaterial
    if cutover.status != LedgerCutover.STATUS_MERGED:
        raise ValueError("This cut-over has not been merged.")
    snapshot = cutover.merge_snapshot or {}
    skipped = 0
    with ledger.bypass():
        for row in snapshot.get("positions", []):
            OpeningPosition.raw_objects.filter(business=business, cutover=cutover, pk=row["pk"]).update(
                quantity=Decimal(row["quantity"]) if row["quantity"] is not None else None,
                amount=Decimal(row["amount"]) if row["amount"] is not None else None,
                source=row["source"], note=row["note"])
        # Live stock is recomputed from the restored openings plus every live movement,
        # so activity recorded after the merge is kept. Costs and market lots are only
        # restored where nothing has changed them since the merge.
        for row in snapshot.get("costs", []):
            restored = RawMaterial.raw_objects.filter(
                pk=row["pk"], business=business, cost_per_unit=Decimal(row["merged"])
            ).update(cost_per_unit=Decimal(row["previous"]))
            skipped += 0 if restored else 1
        for row in snapshot.get("market_lots", []):
            restored = MarketStockLot.raw_objects.filter(
                pk=row["pk"], business=business, quantity_available=Decimal(row["merged_quantity"])
            ).update(quantity_available=Decimal(row["quantity_available"]), active=row["active"],
                     closed_reason=row["closed_reason"], is_backlog_entry=row.get("is_backlog_entry", False))
            skipped += 0 if restored else 1
        cutover.status = LedgerCutover.STATUS_COMPLETE
        cutover.merged_at = None
        cutover.merge_snapshot = {}
        cutover.save(update_fields=["status", "merged_at", "merge_snapshot", "updated_at"])
        ledger.invalidate(business)
        ledger.rebuild_live_stock(business)
    audit(business, request.user, "ledger_merge_undo", cutover, "Backlog merge undone; openings restored",
          {"cutover_id": cutover.pk, "left_unchanged_because_edited_since": skipped})
    request.session["ledger_mode"] = "live"
    note = f" {skipped} cost/lot value(s) were edited after the merge and were left as they are." if skipped else ""
    messages.success(request, "Merge undone and live stock rebuilt from the restored openings." + note)


@login_required
@require_POST
def ledger_action(request):
    if not _admin(request):
        return render(request, "403.html", status=403)
    business = request.business
    action = request.POST.get("action")
    cutover = ledger.active_cutover(business)
    try:
        with transaction.atomic():
            if action == "start":
                _start(request, business)
            elif not cutover:
                raise ValueError("There is no active ledger cut-over.")
            elif action == "save_openings":
                _save_openings(request, business, cutover)
            elif action == "carry_over":
                _carry_over(request, business, cutover)
            elif action == "complete":
                _set_status(request, business, cutover, {"open"}, "complete", "ledger_complete",
                            "Backlog marked complete. Merge review is now available.", "Backlog declared complete")
            elif action == "reopen":
                _set_status(request, business, cutover, {"complete"}, "open", "ledger_reopen",
                            "Backlog reopened for entry.", "Backlog reopened for entry")
            elif action == "preview_merge":
                if cutover.status != LedgerCutover.STATUS_COMPLETE:
                    raise ValueError("Complete the backlog before previewing a merge.")
                request.session["ledger_merge_preview"] = cutover.pk
                messages.info(request, "Merge preview calculated. No balances were changed.")
            elif action == "merge":
                _merge(request, business, cutover)
            elif action == "undo_merge":
                _undo_merge(request, business, cutover)
            elif action == "archive":
                _set_status(request, business, cutover, {"open", "complete"}, "archived", "ledger_archive",
                            "Backlog archived and kept separate. It is sealed and viewable in Backlog mode.",
                            "Backlog archived without merging")
            elif action == "revert_split":
                if not _can_revert(cutover):
                    raise ValueError("The split can only be reverted during its first seven days, before it is merged or archived.")
                _set_status(request, business, cutover, {"open", "complete"}, "reverted", "ledger_revert",
                            "Ledger split reverted. All dated records are available together and live stock counts were kept.",
                            "Ledger split reverted to combined mode")
            elif action == "dissolve":
                _set_status(request, business, cutover, {"merged"}, "reverted", "ledger_dissolve",
                            "The split was removed. The business is a single ledger again.",
                            "Merged ledger split removed")
            else:
                raise ValueError("Unknown ledger action.")
    except (ValueError, ArithmeticError, TypeError) as exc:
        messages.error(request, str(exc))
    return redirect("ledger_workspace")


@login_required
@require_POST
def ledger_mode(request):
    mode = request.POST.get("mode", "live")
    business = request.business
    cutover = ledger.latest_cutover(business) if business else None
    if mode not in {"live", "backlog", "all"} or not cutover:
        mode = "live"
    elif mode == "backlog":
        if cutover.status == LedgerCutover.STATUS_ARCHIVED:
            allowed = ledger.can_view_backlog(request.user, business)
        elif cutover.status in {LedgerCutover.STATUS_OPEN, LedgerCutover.STATUS_COMPLETE}:
            allowed = ledger.can_enter_backlog(request.user, business)
        else:
            allowed = False
        if not allowed:
            return render(request, "403.html", status=403)
    elif mode == "all" and not ledger.can_view_backlog(request.user, business):
        return render(request, "403.html", status=403)
    request.session["ledger_mode"] = mode
    return redirect(_safe_next(request))


# --------------------------------------------------------------------------
# Count tools: CSV sheet, CSV import, phone count page
# --------------------------------------------------------------------------

@login_required
def ledger_count_sheet(request):
    """A CSV to print or fill in: one row per stock item and cash account."""
    import csv
    from django.http import HttpResponse
    if not _admin(request):
        return render(request, "403.html", status=403)
    cutover = ledger.active_cutover(request.business)
    if not cutover:
        messages.error(request, "Start a cut-over first.")
        return redirect("ledger_workspace")
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="count-sheet-{cutover.cutover_date}.csv"'
    writer = csv.writer(response)
    writer.writerow(["type", "id", "name", "unit", "current_opening", "counted"])
    for row in _opening_rows(request.business, cutover):
        writer.writerow([row["kind"], row["id"], row["name"], row["unit"], row["quantity"], ""])
    return response


@login_required
@require_POST
def ledger_count_import(request):
    """Upload a filled count sheet. Only rows with a value in 'counted' are applied."""
    import csv
    import io
    if not _admin(request):
        return render(request, "403.html", status=403)
    business = request.business
    cutover = ledger.active_cutover(business)
    upload = request.FILES.get("counts")
    try:
        if not cutover:
            raise ValueError("Start a cut-over first.")
        if not upload:
            raise ValueError("Choose the filled count sheet.")
        if upload.size > 2_000_000:
            raise ValueError("The file is too large. Count sheets are limited to 2 MB.")
        reader = csv.DictReader(io.StringIO(upload.read().decode("utf-8-sig", errors="replace")))
        needed = {"type", "id", "counted"}
        if not reader.fieldnames or not needed <= {f.strip().lower() for f in reader.fieldnames}:
            raise ValueError("This does not look like a count sheet. Download one and fill the 'counted' column.")
        entries = []
        for number, raw in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            if row.get("counted", "") == "":
                continue
            if row.get("type") not in {"raw", "fg", "cash"} or not row.get("id", "").isdigit():
                raise ValueError(f"Line {number}: the type or id is not recognised. Leave those two columns as downloaded.")
            try:
                value = Decimal(row["counted"].replace(",", ""))
            except InvalidOperation:
                raise ValueError(f"Line {number}: '{row['counted']}' is not a number.")
            entries.append((row["type"], int(row["id"]), value, None))
        if not entries:
            raise ValueError("No counted values were found. Fill the 'counted' column for the items you counted.")
        with transaction.atomic():
            _apply_openings(request, business, cutover, entries, confirm=True)
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("ledger_workspace")


@login_required
def ledger_count(request):
    """Phone-friendly count screen: search, type what you counted, save once."""
    if not _admin(request):
        return render(request, "403.html", status=403)
    cutover = ledger.active_cutover(request.business)
    if not cutover or cutover.status == LedgerCutover.STATUS_MERGED:
        messages.error(request, "Start a cut-over (and keep it unmerged) to count stock.")
        return redirect("ledger_workspace")
    rows = _opening_rows(request.business, cutover)
    return render(request, "core/ledger_count.html", {
        "cutover": cutover, "rows": rows, "pending": sum(1 for r in rows if r["source"] != "counted"),
    })
