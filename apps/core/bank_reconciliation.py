"""Bank reconciliation: load a statement, match it to the cash ledger, prove the balance.

Statements span both ledger periods, so everything here reads through
``raw_objects`` (never the Live/Backlog scoped managers). Matching is by signed
amount and date proximity, and a transaction can be matched to only one line.

The proof is the standard adjusted-balance reconciliation::

    adjusted bank = statement closing balance + (book entries not yet on the statement)
    adjusted book = book balance at statement end + (statement lines not yet in the books)

The two are equal when every difference is explained.
"""
import csv
import io
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import BankStatement, BankStatementLine, FinancialTransaction

ZERO = Decimal("0")
CENT = Decimal("0.01")
MATCH_WINDOW_DAYS = 3
MAX_ROWS = 5000

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d %B %Y", "%m/%d/%Y")
_DATE_HEADS = ("date", "transaction date", "value date", "posting date", "txn date")
_TEXT_HEADS = ("description", "narration", "details", "particulars", "remarks", "reference", "transaction details")
_AMOUNT_HEADS = ("amount", "transaction amount")
_IN_HEADS = ("credit", "money in", "deposit", "deposits", "credits", "paid in")
_OUT_HEADS = ("debit", "money out", "withdrawal", "withdrawals", "debits", "paid out")


class StatementError(ValueError):
    """The uploaded statement could not be read."""


def _money(raw):
    text = (raw or "").strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")") or text.startswith("-")
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    if not cleaned:
        raise StatementError(f"'{raw}' is not a valid amount.")
    try:
        value = Decimal(cleaned).quantize(CENT)
    except InvalidOperation:
        raise StatementError(f"'{raw}' is not a valid amount.")
    return -value if negative else value


def _date(raw):
    text = (raw or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise StatementError(f"'{raw}' is not a recognised date. Use YYYY-MM-DD or DD/MM/YYYY.")


def _find(headers, wanted):
    for index, head in enumerate(headers):
        if head in wanted:
            return index
    return None


def parse_statement_csv(content):
    """Read CSV bytes/text into ``[(date, description, signed_amount)]``.

    Accepts one signed ``amount`` column, or separate debit/credit (or
    money in/out) columns. Credits and deposits are money in; debits and
    withdrawals are money out.
    """
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(content))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if len(rows) < 2:
        raise StatementError("The file needs a header row and at least one transaction.")
    headers = [cell.strip().lower() for cell in rows[0]]
    date_i, text_i = _find(headers, _DATE_HEADS), _find(headers, _TEXT_HEADS)
    amount_i, in_i, out_i = _find(headers, _AMOUNT_HEADS), _find(headers, _IN_HEADS), _find(headers, _OUT_HEADS)
    if date_i is None:
        raise StatementError("No date column found. Add a header such as 'Date'.")
    if amount_i is None and in_i is None and out_i is None:
        raise StatementError("No amount column found. Use 'Amount', or 'Debit' and 'Credit'.")
    if len(rows) - 1 > MAX_ROWS:
        raise StatementError(f"Statements are limited to {MAX_ROWS} lines.")
    parsed = []
    for number, row in enumerate(rows[1:], start=2):
        def cell(index):
            return row[index] if index is not None and index < len(row) else ""
        try:
            when = _date(cell(date_i))
            if amount_i is not None:
                amount = _money(cell(amount_i))
            else:
                money_in, money_out = _money(cell(in_i)), _money(cell(out_i))
                amount = (abs(money_in) if money_in else ZERO) - (abs(money_out) if money_out else ZERO)
        except StatementError as exc:
            raise StatementError(f"Line {number}: {exc}")
        if amount is None or amount == 0:
            continue
        parsed.append((when, cell(text_i).strip()[:255], amount))
    if not parsed:
        raise StatementError("No transactions with an amount were found.")
    return parsed


def _signed(tx):
    return tx.amount if tx.transaction_type == FinancialTransaction.INCOME else -tx.amount


@transaction.atomic
def create_statement(business, user, account, closing_balance, rows, label=""):
    start, end = min(r[0] for r in rows), max(r[0] for r in rows)
    statement = BankStatement.raw_objects.create(
        business=business, created_by=user, account=account, label=label[:120], period_start=start,
        period_end=end, closing_balance=Decimal(closing_balance).quantize(CENT),
    )
    BankStatementLine.objects.bulk_create(
        [BankStatementLine(statement=statement, date=d, description=text, amount=amount) for d, text, amount in rows]
    )
    return statement


def _book_candidates(statement):
    """Transactions on the account near the statement period that are still free to match."""
    taken = BankStatementLine.objects.filter(matched_transaction__isnull=False).values_list("matched_transaction_id", flat=True)
    return (
        FinancialTransaction.raw_objects.filter(
            business=statement.business, account=statement.account,
            date__range=(statement.period_start - timedelta(days=MATCH_WINDOW_DAYS * 3), statement.period_end + timedelta(days=MATCH_WINDOW_DAYS)),
        ).filter(Q(reversed=False) | Q(reversal_entries__isnull=False)).exclude(pk__in=taken).distinct().order_by("date", "id")
    )


@transaction.atomic
def auto_match(statement):
    """Greedy match by exact signed amount, nearest date within the window."""
    if statement.status == BankStatement.STATUS_RECONCILED:
        return 0
    free = [(tx, _signed(tx)) for tx in _book_candidates(statement)]
    matched = 0
    for line in statement.lines.filter(matched_transaction__isnull=True).order_by("date", "id"):
        best, best_gap = None, None
        for tx, amount in free:
            if amount != line.amount:
                continue
            gap = abs((tx.date - line.date).days)
            if gap <= MATCH_WINDOW_DAYS and (best_gap is None or gap < best_gap):
                best, best_gap = tx, gap
        if best is not None:
            line.matched_transaction, line.matched_at, line.matched_automatically = best, timezone.now(), True
            line.save(update_fields=["matched_transaction", "matched_at", "matched_automatically", "updated_at"])
            free = [(tx, a) for tx, a in free if tx.pk != best.pk]
            matched += 1
    return matched


@transaction.atomic
def match_line(statement, line_id, transaction_id):
    if statement.status == BankStatement.STATUS_RECONCILED:
        raise StatementError("This statement is already reconciled.")
    line = statement.lines.get(pk=line_id)
    tx = _book_candidates(statement).get(pk=transaction_id)
    if _signed(tx) != line.amount:
        raise StatementError("The amounts differ, so these cannot be matched.")
    line.matched_transaction, line.matched_at, line.matched_automatically = tx, timezone.now(), False
    line.save(update_fields=["matched_transaction", "matched_at", "matched_automatically", "updated_at"])


@transaction.atomic
def unmatch_line(statement, line_id):
    if statement.status == BankStatement.STATUS_RECONCILED:
        raise StatementError("This statement is already reconciled.")
    statement.lines.filter(pk=line_id).update(matched_transaction=None, matched_at=None, matched_automatically=False)


def book_balance_at(account, on_date):
    """The account's book balance at the end of ``on_date`` (all ledger periods)."""
    total = ZERO
    for tx in FinancialTransaction.raw_objects.filter(account=account, date__lte=on_date):
        total += _signed(tx)
    return account.opening_balance + total


def summarize(statement):
    """Everything the reconciliation screen shows, including the proof."""
    lines = list(statement.lines.select_related("matched_transaction"))
    unmatched_lines = [l for l in lines if l.matched_transaction_id is None]
    candidates = [{"tx": tx, "amount": _signed(tx)} for tx in _book_candidates(statement)]
    book_only = [c for c in candidates if c["tx"].date <= statement.period_end]
    book_only_total = sum((c["amount"] for c in book_only), ZERO)
    bank_only_total = sum((l.amount for l in unmatched_lines), ZERO)
    book_balance = book_balance_at(statement.account, statement.period_end)
    adjusted_bank = statement.closing_balance + book_only_total
    adjusted_book = book_balance + bank_only_total
    difference = (adjusted_bank - adjusted_book).quantize(CENT)
    suggestions = {line.pk: [c for c in candidates if c["amount"] == line.amount][:5] for line in unmatched_lines}
    return {
        "lines": lines, "unmatched_lines": unmatched_lines, "matched_count": len(lines) - len(unmatched_lines),
        "book_only": book_only, "book_only_total": book_only_total, "bank_only_total": bank_only_total,
        "book_balance": book_balance, "adjusted_bank": adjusted_bank, "adjusted_book": adjusted_book,
        "difference": difference, "balanced": difference == 0, "suggestions": suggestions,
    }


@transaction.atomic
def finish(statement):
    if summarize(statement)["difference"] != 0:
        raise StatementError("The adjusted balances still differ, so this cannot be marked reconciled yet.")
    statement.status = BankStatement.STATUS_RECONCILED
    statement.reconciled_at = timezone.now()
    statement.save(update_fields=["status", "reconciled_at", "updated_at"])
