"""Double-entry books derived from the operational records.

Inprofic records each sale, purchase, expense and cash movement in its own
module. This module turns those records into balanced journal entries and a chart
of accounts, so accountants get a trial balance, income statement, balance sheet
and account registers, and so the books can be proven against operational figures.

Design rules:

* **Every entry balances by construction.** Each posting rule is self-contained:
  its lines sum to zero, and the manual-journal path rejects anything else.
* **System entries are rebuilt, never edited.** ``rebuild_books`` deletes the
  system entries and recreates them from the records, so the books follow
  corrections to the source records. Manual entries (an accountant's opening
  balances and adjustments) are kept and can only be reversed, never edited.
* **Cash is posted once.** Documents post the accrual side (receivable, payable,
  accrued expense); every cash movement then settles one of those, chosen from
  the movement's reference and category. Anything unrecognised goes to a
  *Suspense* account so cash always ties and nothing is silently misclassified.
* **The books span both ledger periods.** They read ``raw_objects`` so the
  Live/Backlog split never hides history from an accountant.

Known simplifications (shown on the tie-out screen, not hidden): stock written in
before the books began has no opening entry until an accountant posts one;
production conversion costs beyond materials are not capitalised into inventory;
sales are recognised when recorded, not when delivered.
"""
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Min, Sum
from django.utils import timezone

from .accounting import _sale_expressions
from .models import (
    BooksState, CashAccount, FinancialTransaction, InputVatEntry, JournalEntry, JournalLine, LedgerAccount, TaxSettings,
)

ZERO = Decimal("0")
CENT = Decimal("0.01")
MONEY = DecimalField(max_digits=24, decimal_places=6)
BATCH = 500

SYSTEM_ACCOUNTS = [
    ("1100", "Accounts receivable", LedgerAccount.ASSET, "ar"),
    ("1200", "Inventory", LedgerAccount.ASSET, "inventory"),
    ("1300", "VAT receivable (input VAT)", LedgerAccount.ASSET, "vat_input"),
    ("1900", "Suspense (unclassified cash)", LedgerAccount.ASSET, "suspense"),
    ("2000", "Accounts payable (suppliers)", LedgerAccount.LIABILITY, "ap"),
    ("2100", "Accrued expenses (unpaid bills)", LedgerAccount.LIABILITY, "accrued"),
    ("2200", "VAT payable (output VAT)", LedgerAccount.LIABILITY, "vat_output"),
    ("3000", "Opening balance equity", LedgerAccount.EQUITY, "opening_equity"),
    ("4000", "Sales revenue", LedgerAccount.INCOME, "revenue"),
    ("5000", "Cost of goods sold", LedgerAccount.EXPENSE, "cogs"),
    ("5100", "Inventory write-offs and adjustments", LedgerAccount.EXPENSE, "adjustments"),
    ("6100", "Salaries and wages", LedgerAccount.EXPENSE, "payroll"),
    ("6900", "Other operating expenses", LedgerAccount.EXPENSE, "expense_other"),
]
RECEIPT_CATEGORIES = {"Customer payment", "Customer order payment", "Sales revenue", "Commerce customer payment",
                      "Distribution Market Stock sale"}


def q(value):
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------
# Chart of accounts
# --------------------------------------------------------------------------

def _free_code(taken, start, stop):
    for number in range(start, stop):
        if str(number) not in taken:
            return str(number)
    raise ValueError("The chart of accounts is full in this range.")


def ensure_chart(business):
    """Create the system accounts, one account per cash account, and one per expense category."""
    from expenses.models import Expense
    accounts = {a.system_key: a for a in LedgerAccount.raw_objects.filter(business=business).exclude(system_key="")}
    taken = set(LedgerAccount.raw_objects.filter(business=business).values_list("code", flat=True))
    for code, name, kind, key in SYSTEM_ACCOUNTS:
        if key not in accounts:
            use = code if code not in taken else _free_code(taken, int(code) + 1, int(code) + 99)
            accounts[key] = LedgerAccount.raw_objects.create(business=business, code=use, name=name, account_type=kind, system_key=key)
            taken.add(use)
    for cash in CashAccount.raw_objects.filter(business=business).order_by("id"):
        key = f"cash_{cash.pk}"
        if key not in accounts:
            code = _free_code(taken, 1000, 1100)
            accounts[key] = LedgerAccount.raw_objects.create(
                business=business, code=code, name=cash.name, account_type=LedgerAccount.ASSET, system_key=key, cash_account=cash)
            taken.add(code)
    labels = dict(Expense._meta.get_field("category").choices)
    for category in Expense.raw_objects.filter(business=business).values_list("category", flat=True).distinct():
        key = f"expense_{category}"
        if category and key not in accounts:
            code = _free_code(taken, 6200, 6900)
            accounts[key] = LedgerAccount.raw_objects.create(
                business=business, code=code, name=str(labels.get(category, category)).title(),
                account_type=LedgerAccount.EXPENSE, system_key=key)
            taken.add(code)
    return accounts


# --------------------------------------------------------------------------
# Posting rules (each returns entries: (date, memo, source_type, source_id, [(account_key, net)]))
# net = debit minus credit, so every entry's nets must sum to zero.
# --------------------------------------------------------------------------

def _vat_settings(business):
    return TaxSettings.raw_objects.filter(business=business).first()


def _sale_entries(business):
    from sales.models import Sale, SaleItem
    settings = _vat_settings(business)
    registered = bool(settings and settings.vat_registered)
    rate = (settings.vat_rate / 100) if settings else ZERO
    reg_date = settings.vat_registration_date if settings else None
    _m, line_total, cogs, _u = _sale_expressions(prefix="")
    sales = {s["id"]: s for s in Sale.raw_objects.filter(business=business).values("id", "date", "customer")}
    totals = defaultdict(lambda: {"gross": ZERO, "vat": ZERO, "cost": ZERO})
    rows = (SaleItem.objects.filter(sale__business=business)
            .values("sale_id", "finished_good__vat_treatment")
            .annotate(gross=Sum(line_total, output_field=MONEY), cost=Sum(cogs, output_field=MONEY)))
    for row in rows:
        sale = sales.get(row["sale_id"])
        if sale is None:
            continue
        bucket = totals[row["sale_id"]]
        gross = q(row["gross"])
        bucket["gross"] += gross
        bucket["cost"] += q(row["cost"])
        if registered and (reg_date is None or sale["date"] >= reg_date) and (row["finished_good__vat_treatment"] or "standard") == "standard":
            bucket["vat"] += q(gross * rate / (1 + rate))
    for sale_id, t in totals.items():
        sale = sales[sale_id]
        legs = []
        if t["gross"]:
            legs += [("ar", t["gross"]), ("revenue", -(t["gross"] - t["vat"]))]
            if t["vat"]:
                legs.append(("vat_output", -t["vat"]))
        if t["cost"]:
            legs += [("cogs", t["cost"]), ("inventory", -t["cost"])]
        if legs:
            yield (sale["date"], f"Sale #{sale_id} {sale['customer'] or ''}".strip(), "sale", str(sale_id), legs)


def _purchase_entries(business):
    from procurement.models import PurchaseOrder, PurchaseOrderItem
    value = ExpressionWrapper(F("qty") * F("unit_cost"), output_field=MONEY)
    orders = {p["id"]: p for p in PurchaseOrder.raw_objects.filter(business=business, status="received")
              .values("id", "date", "received_date", "supplier")}
    for row in (PurchaseOrderItem.objects.filter(purchase_order_id__in=list(orders))
                .values("purchase_order_id").annotate(total=Sum(value))):
        po, total = orders[row["purchase_order_id"]], q(row["total"])
        if total:
            yield (po["received_date"] or po["date"], f"Purchase order #{po['id']} {po['supplier'] or ''}".strip(),
                   "purchase", str(po["id"]), [("inventory", total), ("ap", -total)])


def _expense_entries(business, accounts):
    from expenses.models import Expense
    for e in Expense.raw_objects.filter(business=business).values("id", "date", "category", "description", "amount"):
        amount = q(e["amount"])
        if amount:
            key = f"expense_{e['category']}" if f"expense_{e['category']}" in accounts else "expense_other"
            yield (e["date"], f"Expense: {e['description']}"[:250], "expense", str(e["id"]), [(key, amount), ("accrued", -amount)])


def _adjustment_entries(business):
    from inventory.models import StockAdjustment
    value = ExpressionWrapper(F("quantity") * F("unit_value"), output_field=MONEY)
    for a in StockAdjustment.raw_objects.filter(business=business, reversed=False).annotate(_v=value).values("id", "date", "reason", "_v"):
        amount = q(a["_v"])
        if amount:
            yield (a["date"], f"Stock adjustment: {a['reason']}"[:250], "adjustment", str(a["id"]), [("inventory", amount), ("adjustments", -amount)])


def _opening_entries(business):
    first_tx = {r["account_id"]: r["first"] for r in FinancialTransaction.raw_objects.filter(business=business)
                .values("account_id").annotate(first=Min("date"))}
    for cash in CashAccount.raw_objects.filter(business=business):
        if cash.opening_balance:
            day = min(filter(None, [cash.created_at.date(), first_tx.get(cash.pk)]))
            yield (day, f"Opening balance: {cash.name}", "opening", str(cash.pk),
                   [(f"cash_{cash.pk}", q(cash.opening_balance)), ("opening_equity", -q(cash.opening_balance))])


def classify_cash(tx, originals=None):
    """Which account a cash movement settles: ar, ap, accrued, payroll or suspense."""
    if tx.reversal_of_id and originals and tx.reversal_of_id in originals:
        return classify_cash(originals[tx.reversal_of_id])
    ref, cat = (tx.reference or "").upper(), tx.category or ""
    if tx.transaction_type == FinancialTransaction.INCOME:
        if ref.startswith(("SALE-", "ORDER-", "CUSTPAY-")) or cat in RECEIPT_CATEGORIES:
            return "ar"
        return "suspense"
    if cat == "Commerce payment reversal":
        return "ar"
    if ref.startswith(("SUPPAY-", "PO-")) or cat.startswith("Supplier payment") or cat == "Procurement":
        return "ap"
    if ref.startswith(("EXP-", "EXPPAY-")) or cat == "Expense payment":
        return "accrued"
    if cat.startswith("Payroll") or ref.startswith(("PAYRUN-", "PAYSLIP-")):
        return "payroll"
    return "suspense"


def _cash_entries(business):
    txs = list(FinancialTransaction.raw_objects.filter(business=business, account__isnull=False))
    originals = {t.pk: t for t in txs}
    for tx in txs:
        net = tx.amount if tx.transaction_type == FinancialTransaction.INCOME else -tx.amount
        yield (tx.date, f"{tx.category}: {tx.description}"[:250], "cash", str(tx.pk),
               [(f"cash_{tx.account_id}", net), (classify_cash(tx, originals), -net)])


def _input_vat_entries(business):
    for e in InputVatEntry.raw_objects.filter(business=business, claimable=True):
        vat = q(e.vat_amount)
        if vat:
            counter = "inventory" if e.kind == InputVatEntry.KIND_PURCHASE else "expense_other"
            yield (e.date, f"Input VAT: {e.supplier} {e.reference}".strip()[:250], "input_vat", str(e.pk), [("vat_input", vat), (counter, -vat)])


# --------------------------------------------------------------------------
# Rebuild
# --------------------------------------------------------------------------

@transaction.atomic
def rebuild_books(business, user=None):
    """Recreate every system entry from the records. Manual entries are untouched."""
    accounts = ensure_chart(business)
    JournalEntry.raw_objects.filter(business=business, is_system=True).delete()
    sources = (_opening_entries(business), _sale_entries(business), _purchase_entries(business),
               _expense_entries(business, accounts), _adjustment_entries(business), _input_vat_entries(business),
               _cash_entries(business))
    pending, count = [], 0

    def flush(items):
        entries = JournalEntry.raw_objects.bulk_create([
            JournalEntry(business=business, created_by=user, date=d, memo=m, source_type=st, source_id=sid, is_system=True)
            for d, m, st, sid, _legs in items])
        lines = []
        for entry, (_d, _m, _st, _sid, legs) in zip(entries, items):
            merged = defaultdict(lambda: ZERO)
            for key, net in legs:
                merged[key] += net
            for key, net in merged.items():
                if net:
                    lines.append(JournalLine(entry=entry, account=accounts[key], debit=net if net > 0 else ZERO,
                                             credit=-net if net < 0 else ZERO))
        JournalLine.objects.bulk_create(lines, batch_size=1000)

    for source in sources:
        for item in source:
            if abs(sum(net for _k, net in item[4])) > ZERO:
                raise AssertionError(f"Unbalanced {item[2]} entry {item[3]}")
            pending.append(item)
            count += 1
            if len(pending) >= BATCH:
                flush(pending)
                pending = []
    if pending:
        flush(pending)
    suspense = account_balance(business, accounts["suspense"])
    BooksState.raw_objects.update_or_create(
        business=business, defaults={"rebuilt_at": timezone.now(), "entry_count": count, "suspense_total": suspense,
                                     "created_by": user})
    return count


# --------------------------------------------------------------------------
# Manual journals
# --------------------------------------------------------------------------

class JournalError(ValueError):
    pass


@transaction.atomic
def post_manual(business, user, date, memo, lines):
    """lines: [(account_id, debit, credit, memo)]. Must balance and use this business's accounts."""
    cleaned = []
    for account_id, debit, credit, line_memo in lines:
        debit, credit = q(debit), q(credit)
        if debit < 0 or credit < 0:
            raise JournalError("Amounts cannot be negative.")
        if debit == 0 and credit == 0:
            continue
        if debit and credit:
            raise JournalError("A line is either a debit or a credit, not both.")
        cleaned.append((int(account_id), debit, credit, (line_memo or "")[:255]))
    if len(cleaned) < 2:
        raise JournalError("A journal needs at least two lines.")
    total_debit, total_credit = sum(l[1] for l in cleaned), sum(l[2] for l in cleaned)
    if total_debit != total_credit:
        raise JournalError(f"The journal does not balance: debits {total_debit} and credits {total_credit}.")
    accounts = {a.pk: a for a in LedgerAccount.raw_objects.filter(business=business, pk__in=[l[0] for l in cleaned], active=True)}
    if len(accounts) != len({l[0] for l in cleaned}):
        raise JournalError("One of the accounts is not available.")
    entry = JournalEntry.raw_objects.create(business=business, created_by=user, date=date, memo=(memo or "")[:255],
                                            source_type="manual", is_system=False)
    JournalLine.objects.bulk_create([JournalLine(entry=entry, account=accounts[a], debit=d, credit=c, memo=m) for a, d, c, m in cleaned])
    return entry


@transaction.atomic
def reverse_manual(entry, user, date=None):
    if entry.is_system:
        raise JournalError("System entries follow your records. Correct the record, then rebuild the books.")
    if hasattr(entry, "reversed_by") or entry.reverses_id:
        raise JournalError("This entry has already been reversed (or is itself a reversal).")
    reversal = JournalEntry.raw_objects.create(
        business=entry.business, created_by=user, date=date or timezone.localdate(), memo=f"Reversal of #{entry.pk}: {entry.memo}"[:255],
        source_type="manual", is_system=False, reverses=entry)
    JournalLine.objects.bulk_create([JournalLine(entry=reversal, account=l.account, debit=l.credit, credit=l.debit, memo=l.memo)
                                     for l in entry.lines.all()])
    return reversal


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------

def _lines(business):
    return JournalLine.objects.filter(entry__business=business)


def account_balance(business, account, as_of=None):
    """Net debit-minus-credit balance of one account."""
    qs = _lines(business).filter(account=account)
    if as_of:
        qs = qs.filter(entry__date__lte=as_of)
    agg = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or ZERO) - (agg["c"] or ZERO)


def _grouped(business, as_of=None, start=None):
    qs = _lines(business)
    if as_of:
        qs = qs.filter(entry__date__lte=as_of)
    if start:
        qs = qs.filter(entry__date__gte=start)
    return {r["account_id"]: (r["d"] or ZERO, r["c"] or ZERO) for r in qs.values("account_id").annotate(d=Sum("debit"), c=Sum("credit"))}


def trial_balance(business, as_of=None):
    totals = _grouped(business, as_of=as_of)
    rows, debit_total, credit_total = [], ZERO, ZERO
    for account in LedgerAccount.raw_objects.filter(business=business).order_by("code"):
        d, c = totals.get(account.pk, (ZERO, ZERO))
        net = d - c
        if not net and not d and not c and not account.active:
            continue
        rows.append({"account": account, "debit": net if net > 0 else ZERO, "credit": -net if net < 0 else ZERO, "net": net})
        debit_total += net if net > 0 else ZERO
        credit_total += -net if net < 0 else ZERO
    return {"rows": rows, "debit_total": debit_total, "credit_total": credit_total, "balanced": debit_total == credit_total}


def income_statement(business, start, end):
    totals = _grouped(business, as_of=end, start=start)
    income, expenses = [], []
    for account in LedgerAccount.raw_objects.filter(business=business, account_type__in=[LedgerAccount.INCOME, LedgerAccount.EXPENSE]).order_by("code"):
        d, c = totals.get(account.pk, (ZERO, ZERO))
        if account.account_type == LedgerAccount.INCOME and (d or c):
            income.append({"account": account, "amount": c - d})
        elif account.account_type == LedgerAccount.EXPENSE and (d or c):
            expenses.append({"account": account, "amount": d - c})
    total_income, total_expense = sum((r["amount"] for r in income), ZERO), sum((r["amount"] for r in expenses), ZERO)
    return {"income": income, "expenses": expenses, "total_income": total_income, "total_expenses": total_expense,
            "net_profit": total_income - total_expense}


def balance_sheet(business, as_of):
    totals = _grouped(business, as_of=as_of)
    sections = {LedgerAccount.ASSET: [], LedgerAccount.LIABILITY: [], LedgerAccount.EQUITY: []}
    earnings = ZERO
    for account in LedgerAccount.raw_objects.filter(business=business).order_by("code"):
        d, c = totals.get(account.pk, (ZERO, ZERO))
        net = d - c
        if account.account_type in (LedgerAccount.INCOME, LedgerAccount.EXPENSE):
            earnings += -net  # income is credit-normal, expense debit-normal
        elif net:
            sections[account.account_type].append({"account": account, "amount": net if account.normal_side == "debit" else -net})
    totals_by = {k: sum((r["amount"] for r in v), ZERO) for k, v in sections.items()}
    equity_total = totals_by[LedgerAccount.EQUITY] + earnings
    return {"assets": sections[LedgerAccount.ASSET], "liabilities": sections[LedgerAccount.LIABILITY],
            "equity": sections[LedgerAccount.EQUITY], "current_earnings": earnings,
            "total_assets": totals_by[LedgerAccount.ASSET], "total_liabilities": totals_by[LedgerAccount.LIABILITY],
            "total_equity": equity_total, "total_liabilities_and_equity": totals_by[LedgerAccount.LIABILITY] + equity_total,
            "balanced": totals_by[LedgerAccount.ASSET] == totals_by[LedgerAccount.LIABILITY] + equity_total}


def account_register(business, account, start=None, end=None):
    """Lines for one account with a running balance (in the account's normal direction)."""
    sign = 1 if account.normal_side == "debit" else -1
    before = ZERO
    if start:
        agg = _lines(business).filter(account=account, entry__date__lt=start).aggregate(d=Sum("debit"), c=Sum("credit"))
        before = ((agg["d"] or ZERO) - (agg["c"] or ZERO)) * sign
    qs = _lines(business).filter(account=account).select_related("entry").order_by("entry__date", "entry_id", "id")
    if start:
        qs = qs.filter(entry__date__gte=start)
    if end:
        qs = qs.filter(entry__date__lte=end)
    running, rows = before, []
    for line in qs:
        running += (line.debit - line.credit) * sign
        rows.append({"line": line, "balance": running})
    return {"opening": before, "rows": rows, "closing": running}


def tie_out(business):
    """Ledger balances beside the operational figures they should match."""
    from inventory.models import FinishedGood, RawMaterial
    from .finance_views import _finance_open_items
    accounts = {a.system_key: a for a in LedgerAccount.raw_objects.filter(business=business).exclude(system_key="")}
    if not accounts:
        return []

    def ledger(key, sign=1):
        return account_balance(business, accounts[key]) * sign if key in accounts else ZERO

    rows = []
    for cash in CashAccount.raw_objects.filter(business=business).order_by("name"):
        total = cash.opening_balance + sum((t.signed_amount for t in FinancialTransaction.raw_objects.filter(account=cash)), ZERO)
        rows.append(("Cash: " + cash.name, ledger(f"cash_{cash.pk}"), total,
                     "Should match exactly. A difference means books are out of date: rebuild."))
    open_items = _finance_open_items(business)
    rows.append(("Accounts receivable", ledger("ar"), open_items["receivables"],
                 "Operational figure counts unpaid customer orders; unpaid walk-in sales and opening balances explain differences."))
    rows.append(("Accounts payable (suppliers)", ledger("ap", -1), open_items["purchase_payables"],
                 "Opening supplier balances are not in the books until an accountant posts them."))
    rows.append(("Accrued expenses", ledger("accrued", -1), open_items["expense_payables"],
                 "Unpaid expenses."))
    raw = RawMaterial.raw_objects.filter(business=business).aggregate(
        v=Sum(ExpressionWrapper(F("stock") * F("cost_per_unit"), output_field=MONEY)))["v"] or ZERO
    from inventory.valuation import finished_stock_value
    finished, _count, errors = finished_stock_value(business, all_periods=True)
    rows.append(("Inventory", ledger("inventory"), q(raw + finished),
                 "Stock held before the books began and production conversion costs explain most differences. Post an opening-balance journal for the stock you already had."))
    result = [{"name": n, "ledger": q(l), "operational": q(o), "difference": q(l - o), "note": note,
               "ties": q(l - o) == 0} for n, l, o, note in rows]
    if errors:
        result[-1]["ties"] = False
        result[-1]["note"] = f"Valuation incomplete: {len(errors)} product(s) have invalid costs. Correct them before relying on this total."
    return result
