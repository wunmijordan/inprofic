"""The financial pack: one read-only statement set for lenders, investors and auditors.

It assembles figures the rest of the system already computes (profit and loss,
cash balances, invoice-level receivables and payables, stock) and presents a
**statement of financial position**: what the business holds, what it owes, and
the difference. It is derived from operational records, not a double-entry
general ledger, and the pack says so on its face.

Everything reads through scoped managers, so the ledger scope in force when it
is built (Live, Backlog or Combined) decides what it covers.
"""
import hashlib
import secrets
from datetime import timedelta
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.utils import timezone

from . import ledger
from .accounting import daily_components, profit_and_loss
from .models import CashAccount, FinancialPackShare

ZERO = Decimal("0")
MONEY = DecimalField(max_digits=24, decimal_places=6)
AGING_BUCKETS = (("Current (0–30 days)", 0, 30), ("31–60 days", 31, 60), ("61–90 days", 61, 90), ("Over 90 days", 91, 10**6))


def _aging(rows, as_of, date_of, amount_of):
    buckets = [{"label": label, "total": ZERO, "count": 0} for label, _lo, _hi in AGING_BUCKETS]
    for row in rows:
        age = max((as_of - date_of(row)).days, 0)
        for bucket, (_label, low, high) in zip(buckets, AGING_BUCKETS):
            if low <= age <= high:
                bucket["total"] += amount_of(row)
                bucket["count"] += 1
                break
    return buckets


def _finished_goods_at_cost(business):
    """Stocked products valued at cost per unit; returns (value, items_valued, truncated)."""
    from inventory.valuation import finished_stock_value
    total, count, errors = finished_stock_value(business)
    return total, count, bool(errors)


def build_pack(business, start, end, *, today=None):
    from inventory.models import RawMaterial
    from .finance_views import _finance_open_items
    today = today or timezone.localdate()
    components = daily_components(business, start, end)
    pnl = profit_and_loss(business, start, end, components)
    accounts = ledger.apply_cash_totals(CashAccount.objects.filter(business=business, active=True).order_by("name"))
    cash_total = sum((a._calculated_balance for a in accounts), ZERO)
    open_items = _finance_open_items(business)
    raw_value = RawMaterial.objects.filter(business=business).aggregate(
        v=Sum(ExpressionWrapper(F("stock") * F("cost_per_unit"), output_field=MONEY)))["v"] or ZERO
    fg_value, fg_count, fg_truncated = _finished_goods_at_cost(business)
    inventory_total = raw_value + fg_value
    assets = cash_total + open_items["receivables"] + inventory_total
    liabilities = open_items["payables"]
    cutover = ledger.latest_cutover(business)
    return {
        "business": business, "start": start, "end": end, "generated_on": today,
        "pnl": pnl,
        "accounts": accounts, "cash_total": cash_total,
        "receivables": open_items["receivables"], "payables": open_items["payables"],
        "payable_breakdown": {"suppliers": open_items["purchase_payables"], "expenses": open_items["expense_payables"]},
        "receivable_aging": _aging(open_items["outstanding_sales"], today, lambda r: r["sale"].date, lambda r: r["balance"]),
        "payable_aging": _aging(open_items["outstanding_pos"], today, lambda r: r["po"].date, lambda r: r["balance"]),
        "inventory": {"raw": raw_value, "finished": fg_value, "total": inventory_total, "finished_items": fg_count,
                      "finished_truncated": fg_truncated},
        "position": {"assets": assets, "liabilities": liabilities, "net": assets - liabilities},
        "ledger": {
            "split": bool(cutover and cutover.status != "reverted"),
            "status": cutover.get_status_display() if cutover else "",
            "cutover_date": cutover.cutover_date if cutover else None,
            "scope": ledger.current_scope() or "all",
        },
    }


# ----- share links -----------------------------------------------------------

def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_share(business, user, start, end, *, days=7, include_backlog=False, label=""):
    """Return (share, plaintext_token). The token is shown once and only its hash is stored."""
    days = max(1, min(int(days), 30))
    token = secrets.token_urlsafe(32)
    share = FinancialPackShare.raw_objects.create(
        business=business, created_by=user, token_hash=_hash(token), label=label[:120], period_start=start,
        period_end=end, include_backlog=include_backlog, expires_at=timezone.now() + timedelta(days=days),
    )
    return share, token


def resolve_share(token):
    """The active share for a token, or None (unknown, expired and revoked look the same)."""
    if not token or len(token) > 100:
        return None
    share = FinancialPackShare.raw_objects.select_related("business").filter(token_hash=_hash(token)).first()
    return share if share and share.is_active else None


def revoke_share(share):
    share.revoked_at = timezone.now()
    share.save(update_fields=["revoked_at", "updated_at"])
