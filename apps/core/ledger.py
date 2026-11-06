"""Ledger cut-over: Backlog vs Live segments.

A business admin fixes a cut-over date D. Records dated before D are the
*Backlog* (hidden by default, enterable in Backlog entry mode); records dated
on or after D are *Live* and restart from opening positions at D. Nothing is
copied or deleted: a record's segment is a pure function of its business date.

* Live stock  = live opening at D + sum(live movements). Items that have a live
  opening position present that derived figure (see ``item_balance``); items
  created after the cut-over keep their stored stock.
* Backlog stock is derived (backlog opening + backlog movements) and is only
  shown while a user is in Backlog mode; the stored number is never touched by
  a Backlog-dated movement.
* Cash: live scope uses the live opening position, backlog scope the original
  opening balance, combined/merged scopes opening_balance + everything.
* Receivables and payables stay invoice-level across segments by design
  (Finance open items use ``raw_objects``), so they need no opening positions.

Every lookup here is cached for the request (``get_request_cache``) so stock
reads inside loops do not become per-item queries.
"""
from contextlib import contextmanager
from datetime import date as date_cls, timedelta
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from .context import (
    get_ledger_cutover_date, get_ledger_scope, get_ledger_status, get_request_cache,
    ledger_bypass_active, set_ledger_bypass,
)

SEGMENT_LIVE = "live"
SEGMENT_BACKLOG = "backlog"
STOCK_KINDS = ("raw", "fg")
LOCKED_MESSAGE = "The backlog is locked. Ask a business admin to reopen it before adding records."


class LedgerDateError(ValidationError):
    """A date is on the wrong side of the cut-over (or otherwise not allowed)."""

    def __init__(self, scope=None, cutover_date=None, message=None, *, status=None):
        self.scope = scope
        self.cutover_date = cutover_date
        self.status = status
        # Only a still-open backlog can be entered, so only then offer the switch.
        self.suggest_backlog = scope == SEGMENT_LIVE and status in (None, "open")
        if message is None:
            if scope == SEGMENT_LIVE and status in (None, "open"):
                message = (
                    f"This date is before the live start ({cutover_date:%d %b %Y}). Records before it belong to the "
                    "Backlog; use Backlog entry mode to record them."
                )
            elif scope == SEGMENT_LIVE:
                message = (
                    f"This date is before {cutover_date:%d %b %Y}. That earlier period is sealed, so records cannot be "
                    "added to it."
                )
            else:
                message = (
                    f"Backlog entry mode only accepts dates before {cutover_date:%d %b %Y}. "
                    "Switch back to Live entry for this date."
                )
        super().__init__(message)


def today():
    return timezone.localdate()


# --------------------------------------------------------------------------
# Cached cut-over lookup
# --------------------------------------------------------------------------

def _business_id(business):
    return getattr(business, "pk", business)


def _cached(key, loader):
    cache = get_request_cache()
    if cache is None:
        return loader()
    if key not in cache:
        cache[key] = loader()
    return cache[key]


def latest_cutover(business):
    """The business's newest LedgerCutover row (any status) or None."""
    from .models import LedgerCutover
    bid = _business_id(business)
    if not bid:
        return None
    return _cached(
        ("ledger", "latest", bid),
        lambda: LedgerCutover.raw_objects.filter(business_id=bid).order_by("-id").first(),
    )


def report_scope_label(business):
    """Only explain ledger scope on exports when this business uses cut-over."""
    from .models import LedgerCutover
    cutover = latest_cutover(business)
    if not cutover or cutover.status == LedgerCutover.STATUS_REVERTED:
        return ''
    label = {'live': 'Live', 'backlog': 'Backlog', 'all': 'Combined'}.get(current_scope() or 'live', 'Live')
    return f'{label} ledger'


def active_cutover(business):
    """The cut-over that is still being worked (not archived / reverted)."""
    from .models import LedgerCutover
    cutover = latest_cutover(business)
    if cutover and cutover.status in {LedgerCutover.STATUS_ARCHIVED, LedgerCutover.STATUS_REVERTED}:
        return None
    return cutover


def invalidate(business):
    """Drop every cached ledger lookup for the business (call after changes)."""
    cache = get_request_cache()
    if cache is None:
        return
    bid = _business_id(business)
    for key in [k for k in cache if isinstance(k, tuple) and k[:1] == ("ledger",) and bid in k[1:4]]:
        cache.pop(key, None)


def invalidate_balances(business):
    cache = get_request_cache()
    if cache is None:
        return
    bid = _business_id(business)
    for key in [k for k in cache if isinstance(k, tuple) and k[:2] == ("ledger", "balances") and k[2] == bid]:
        cache.pop(key, None)


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------

def can_manage(user, business):
    """Cut-over, merge, undo, archive: business admin only."""
    from accounts.services import is_business_admin
    return bool(business and is_business_admin(user, business))


def can_view_backlog(user, business):
    """May see Backlog / Combined data (read access to the 'ledger' module)."""
    from accounts.services import user_has_permission
    return bool(business and (can_manage(user, business) or user_has_permission(user, business, "ledger", "view")))


def can_enter_backlog(user, business):
    """May write in Backlog entry mode (edit access to the 'ledger' module)."""
    from accounts.services import user_has_permission
    return bool(business and (can_manage(user, business) or user_has_permission(user, business, "ledger", "edit")))


# --------------------------------------------------------------------------
# Entry guard
# --------------------------------------------------------------------------

@contextmanager
def bypass():
    """Run admin / merge code without the entry guard (nests safely)."""
    previous = ledger_bypass_active()
    set_ledger_bypass(True)
    try:
        yield
    finally:
        set_ledger_bypass(previous)


def check_scope_date(value):
    """Raise LedgerDateError if ``value`` may not be written in the current scope."""
    if ledger_bypass_active() or value is None:
        return
    scope, cutover, status = get_ledger_scope(), get_ledger_cutover_date(), get_ledger_status()
    if not scope or not cutover:
        return
    if status == "merged":
        if value < cutover:
            raise LedgerDateError(
                message=(
                    f"This date is before the merged cut-over of {cutover:%d %b %Y}. History before that date is "
                    "sealed; a business admin can undo the merge to add records there."
                )
            )
        return
    # Combined is a read scope: writes follow the Live rules.
    write_scope = SEGMENT_LIVE if scope == "all" else scope
    if write_scope == SEGMENT_LIVE:
        if value < cutover:
            raise LedgerDateError(SEGMENT_LIVE, cutover, status=status)
        return
    if value >= cutover:
        raise LedgerDateError(SEGMENT_BACKLOG, cutover, status=status)
    if status != "open":
        raise LedgerDateError(message=LOCKED_MESSAGE, status=status)


_UNSET = object()


def remember_ledger_date(sender, instance, **kwargs):
    """post_init: remember the date a loaded record had, so only a *changed* date is guarded."""
    instance._ledger_original_date = instance.__dict__.get(sender.ledger_date_field, _UNSET)


def guard_ledger_date(sender, instance, **kwargs):
    """pre_save guard for every model that declares ``ledger_date_field``."""
    field = getattr(sender, "ledger_date_field", None)
    if not field or not getattr(instance, "business_id", None):
        return
    # AuditLog.created_at records when an action happened, not a business date.
    if sender._meta.label_lower == "core.auditlog":
        return
    if not get_ledger_scope() or not get_ledger_cutover_date():
        return
    value = instance.__dict__.get(field)
    if value is None:
        return
    if hasattr(value, "date") and callable(value.date):
        value = timezone.localtime(value).date()
    # Updating an existing record (for example a Live payment marking a Backlog
    # invoice paid) is not a new entry on the wrong side of the cut-over.
    if not instance._state.adding and getattr(instance, "_ledger_original_date", _UNSET) == instance.__dict__.get(field):
        return
    check_scope_date(value)


def validate_entry_date(business, on_date, *, scope=None):
    """Compatibility wrapper: the segment ``on_date`` belongs to (raises if sealed)."""
    return segment_for(business, on_date)


def segment_for(business, on_date):
    """'backlog' when ``on_date`` is before an unmerged cut-over, else 'live'."""
    from .models import LedgerCutover
    latest = latest_cutover(business)
    if not latest or on_date is None or latest.status in {LedgerCutover.STATUS_MERGED, LedgerCutover.STATUS_REVERTED}:
        return SEGMENT_LIVE
    if on_date >= latest.cutover_date:
        return SEGMENT_LIVE
    if latest.status == LedgerCutover.STATUS_ARCHIVED:
        raise LedgerDateError(
            message=f"Records before {latest.cutover_date:%d %b %Y} are sealed because that period was archived."
        )
    return SEGMENT_BACKLOG


def current_scope():
    return get_ledger_scope()


def is_backlog_scope():
    return get_ledger_scope() == SEGMENT_BACKLOG


def default_entry_date():
    """Initial date for entry forms: today, or the day before the cut-over in Backlog mode."""
    cutover = get_ledger_cutover_date()
    if is_backlog_scope() and cutover:
        return cutover - timedelta(days=1)
    return today()


def resolve_event_date(request, document_date=None, *, required=False):
    """Date for a workflow event (approve / complete / receive).

    Callers can require an explicit date for production approval/completion.
    Other workflows may pass an optional ``event_date`` to record the day
    it really happened. Without it the date is today, or the document date in
    Backlog mode where today is not a valid date. The date may not be in the
    future nor earlier than the document, and it must be valid for the scope.
    """
    raw = (request.POST.get("event_date") or "").strip() if request.method == "POST" else ""
    if required and request.method == "POST" and not raw:
        raise LedgerDateError(message="Select the event date before saving.")
    if raw:
        try:
            chosen = date_cls.fromisoformat(raw)
        except ValueError:
            raise LedgerDateError(message="Enter the date as YYYY-MM-DD.")
    elif is_backlog_scope():
        chosen = document_date or today()
    else:
        chosen = today()
    if chosen > today():
        raise LedgerDateError(message="The date cannot be in the future.")
    if document_date and chosen < document_date:
        raise LedgerDateError(
            message=f"The date cannot be earlier than the document date ({document_date:%d %b %Y})."
        )
    check_scope_date(chosen)
    return chosen


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

def registered_ledger_models():
    """Models whose primary business date participates in automatic scoping."""
    return tuple(model for model in apps.get_models() if getattr(model, "ledger_date_field", None))


def validate_registry():
    """Fail loudly if an opted-in model's configured date field is missing."""
    errors = []
    for model in registered_ledger_models():
        name = model.ledger_date_field
        try:
            field = model._meta.get_field(name)
        except Exception:
            errors.append(f"{model._meta.label}.{name}")
            continue
        if field.get_internal_type() not in {"DateField", "DateTimeField"}:
            errors.append(f"{model._meta.label}.{name} is not a date field")
    if errors:
        raise AssertionError("Invalid ledger date registry: " + ", ".join(errors))
    return True


# --------------------------------------------------------------------------
# Opening positions and derived balances
# --------------------------------------------------------------------------

def positions_map(business, segment):
    """{(kind, object_id): OpeningPosition} for the business's latest cut-over."""
    from .models import OpeningPosition
    bid = _business_id(business)
    cutover = latest_cutover(business)
    if not cutover:
        return {}

    def load():
        return {
            (p.kind, p.object_id): p
            for p in OpeningPosition.raw_objects.filter(business_id=bid, cutover=cutover, segment=segment)
        }
    return _cached(("ledger", "positions", bid, segment, cutover.pk), load)


def _movement_qs(business):
    from inventory.models import StockMovement
    return StockMovement.raw_objects.filter(business_id=_business_id(business)).filter(
        Q(raw_material__isnull=False) | Q(finished_good__isnull=False, affects_stock=True)
    ).annotate(_d=Coalesce("business_date", TruncDate("occurred_at")))


def _item_key(row):
    if row["raw_material_id"]:
        return ("raw", row["raw_material_id"])
    if row["finished_good_id"]:
        return ("fg", row["finished_good_id"])
    return None


def movement_sums(business, segment):
    """{(kind, id): sum of movements} for a segment (grouped, one query)."""
    cutover = latest_cutover(business)
    if not cutover:
        return {}
    qs = _movement_qs(business)
    qs = qs.filter(_d__lt=cutover.cutover_date) if segment == SEGMENT_BACKLOG else qs.filter(_d__gte=cutover.cutover_date)
    out = {}
    for row in qs.values("raw_material_id", "finished_good_id").annotate(total=Sum("quantity")):
        key = _item_key(row)
        if key:
            out[key] = row["total"] or Decimal("0")
    return out


def segment_balances(business, segment):
    """{(kind, id): derived balance}: items with an opening position (live) or any
    history (backlog). Cached per request; call invalidate_balances after writes."""
    bid = _business_id(business)
    cutover = latest_cutover(business)
    if not cutover:
        return {}

    def load():
        positions = positions_map(business, segment)
        sums = movement_sums(business, segment)
        balances = {}
        for key, pos in positions.items():
            if key[0] in STOCK_KINDS:
                balances[key] = (pos.quantity or Decimal("0")) + sums.get(key, Decimal("0"))
        if segment == SEGMENT_BACKLOG:
            for key, total in sums.items():
                balances.setdefault(key, total)
        return balances
    return _cached(("ledger", "balances", bid, segment, cutover.pk), load)


def item_balance(business_id, kind, pk, segment):
    """Derived balance for one item, or None when the stored stock should be used."""
    balances = segment_balances(business_id, segment)
    if segment == SEGMENT_BACKLOG:
        return balances.get((kind, pk), Decimal("0"))
    return balances.get((kind, pk))


def movement_balance(business, item, kind, segment):
    """Derived balance, falling back to stored stock."""
    value = item_balance(_business_id(business), kind, item.pk, segment)
    if value is None:
        stored = type(item).raw_objects.filter(pk=item.pk).values_list("stock", flat=True).first()
        return stored if stored is not None else Decimal("0")
    return value


def stored_stock_map(business):
    """{(kind, id): stored stock} read straight from the database."""
    from inventory.models import FinishedGood, RawMaterial
    bid = _business_id(business)
    out = {}
    for pk, stock in RawMaterial.raw_objects.filter(business_id=bid).values_list("pk", "stock"):
        out[("raw", pk)] = stock
    for pk, stock in FinishedGood.raw_objects.filter(business_id=bid, stock__isnull=False).values_list("pk", "stock"):
        out[("fg", pk)] = stock
    return out


@transaction.atomic
def rebuild_live_stock(business):
    """Stored stock = live opening + live movements, for items that have a live
    opening position. Also re-stamps ``balance_after`` chronologically. Idempotent."""
    from inventory.models import FinishedGood, RawMaterial, StockMovement
    invalidate(business)
    cutover = latest_cutover(business)
    if not cutover or cutover.status == "reverted":
        return {"raw": 0, "fg": 0}
    bid = _business_id(business)
    positions = {k: v for k, v in positions_map(business, SEGMENT_LIVE).items() if k[0] in STOCK_KINDS}
    sums = movement_sums(business, SEGMENT_LIVE)
    stored = stored_stock_map(business)
    counts = {"raw": 0, "fg": 0}
    with bypass():
        for key, pos in positions.items():
            if key not in stored:
                continue
            target = ((pos.quantity or Decimal("0")) + sums.get(key, Decimal("0"))).quantize(
                Decimal("0.001") if key[0] == "raw" else Decimal("0.01")
            )
            if stored[key] == target:
                continue
            model = RawMaterial if key[0] == "raw" else FinishedGood
            model.raw_objects.filter(pk=key[1], business_id=bid).update(stock=target)
            counts[key[0]] += 1
        running, pending = {}, []
        stream = (
            _movement_qs(business).filter(_d__gte=cutover.cutover_date)
            .order_by("raw_material_id", "finished_good_id", "_d", "occurred_at", "id")
            .only("id", "raw_material_id", "finished_good_id", "quantity", "balance_after")
        )
        for mv in stream.iterator(chunk_size=1000):
            key = ("raw", mv.raw_material_id) if mv.raw_material_id else ("fg", mv.finished_good_id)
            if key not in positions:
                continue
            running[key] = running.get(key, positions[key].quantity or Decimal("0")) + mv.quantity
            if mv.balance_after != running[key]:
                mv.balance_after = running[key]
                pending.append(mv)
            if len(pending) >= 500:
                StockMovement.raw_objects.bulk_update(pending, ["balance_after"])
                pending = []
        if pending:
            StockMovement.raw_objects.bulk_update(pending, ["balance_after"])
    invalidate_balances(business)
    return counts


def backlog_negative_dates(business):
    """{(kind, id): [dates the chronological backlog balance is below zero]}."""
    cutover = latest_cutover(business)
    if not cutover:
        return {}
    opening = positions_map(business, SEGMENT_BACKLOG)
    per_item = {}
    rows = (
        _movement_qs(business).filter(_d__lt=cutover.cutover_date)
        .values("raw_material_id", "finished_good_id", "_d").annotate(total=Sum("quantity")).order_by("_d")
    )
    for row in rows:
        key = _item_key(row)
        if key:
            per_item.setdefault(key, []).append((row["_d"], row["total"] or Decimal("0")))
    result = {}
    for key, days in per_item.items():
        running = (opening[key].quantity or Decimal("0")) if key in opening else Decimal("0")
        negative = []
        for day, total in days:
            running += total
            if running < 0:
                negative.append(day)
        if negative:
            result[key] = negative
    return result


def integrity_report(business):
    """Stored live stock vs live opening + live movements (items with positions)."""
    cutover = latest_cutover(business)
    if not cutover or cutover.status == "reverted":
        return []
    positions = {k: v for k, v in positions_map(business, SEGMENT_LIVE).items() if k[0] in STOCK_KINDS}
    sums = movement_sums(business, SEGMENT_LIVE)
    stored = stored_stock_map(business)
    rows = []
    for key, pos in positions.items():
        if key not in stored:
            continue
        expected = (pos.quantity or Decimal("0")) + sums.get(key, Decimal("0"))
        if (stored[key] or Decimal("0")) != expected:
            rows.append({"kind": key[0], "id": key[1], "stored": stored[key], "expected": expected,
                         "drift": (stored[key] or Decimal("0")) - expected})
    return rows


# --------------------------------------------------------------------------
# Cash
# --------------------------------------------------------------------------

def cash_scope(business):
    """(opening segment or None, earliest transaction date or None) for cash maths.

    Merged: the live opening already carries the backlog closing, so only
    transactions from the cut-over count. Live / Backlog: that segment's opening
    (the manager already filters transactions). Otherwise: the account's own
    opening balance plus everything.
    """
    from .models import LedgerCutover
    cutover = latest_cutover(business)
    if cutover and cutover.status == LedgerCutover.STATUS_MERGED:
        return SEGMENT_LIVE, cutover.cutover_date
    scope = get_ledger_scope()
    if cutover and cutover.status != LedgerCutover.STATUS_REVERTED and scope in {SEGMENT_LIVE, SEGMENT_BACKLOG}:
        return scope, None
    return None, None


def cash_opening(account):
    segment, _floor = cash_scope(account.business_id)
    if segment:
        position = positions_map(account.business_id, segment).get(("cash", account.pk))
        if position and position.amount is not None:
            return position.amount
    return account.opening_balance


def apply_cash_totals(accounts):
    """Fill ``_calculated_balance`` for a list of CashAccounts in one grouped query."""
    from .models import FinancialTransaction
    accounts = list(accounts)
    if not accounts:
        return accounts
    business_id = accounts[0].business_id
    _segment, floor = cash_scope(business_id)
    transactions = FinancialTransaction.objects.filter(account__in=[a.pk for a in accounts])
    if floor:
        transactions = transactions.filter(date__gte=floor)
    totals = {}
    for row in transactions.values("account_id", "transaction_type").annotate(total=Sum("amount")):
        totals[(row["account_id"], row["transaction_type"])] = row["total"] or Decimal("0")
    for account in accounts:
        money_in = totals.get((account.pk, FinancialTransaction.INCOME), Decimal("0"))
        money_out = totals.get((account.pk, FinancialTransaction.OUTFLOW), Decimal("0"))
        account._money_in, account._money_out = money_in, money_out
        account._calculated_balance = cash_opening(account) + money_in - money_out
    return accounts
