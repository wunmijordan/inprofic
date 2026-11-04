"""Books screens: trial balance, chart of accounts, journal, registers, statements."""
import csv
from datetime import date as date_cls

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import books, ledger
from .accounting import fiscal_period
from .models import BooksState, JournalEntry, LedgerAccount
from .services import audit

JOURNAL_PAGE = 200


def _date(raw, default):
    try:
        return date_cls.fromisoformat((raw or "").strip()) if raw else default
    except ValueError:
        return default


def _admin_only(request):
    return ledger.can_manage(request.user, request.business)


@login_required
@require_http_methods(["GET", "POST"])
def books_home(request):
    business = request.business
    if request.method == "POST":
        count = books.rebuild_books(business, request.user)
        audit(business, request.user, "books_rebuild", None, f"Books rebuilt: {count} entries", {"entries": count})
        messages.success(request, f"Books rebuilt from your records: {count} entries.")
        return redirect("books_home")
    state = BooksState.raw_objects.filter(business=business).first()
    as_of = _date(request.GET.get("as_of"), timezone.localdate())
    tb = books.trial_balance(business, as_of) if state else None
    if request.GET.get("format") == "csv" and tb:
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="trial-balance-{as_of}.csv"'
        writer = csv.writer(response)
        writer.writerow(["Code", "Account", "Type", "Debit", "Credit"])
        for r in tb["rows"]:
            writer.writerow([r["account"].code, r["account"].name, r["account"].account_type, r["debit"], r["credit"]])
        writer.writerow(["", "Total", "", tb["debit_total"], tb["credit_total"]])
        return response
    return render(request, "core/books_home.html", {
        "state": state, "tb": tb, "as_of": as_of, "tie": books.tie_out(business) if state else [],
        "can_edit": _admin_only(request),
    })


@login_required
@require_http_methods(["GET", "POST"])
def books_accounts(request):
    business = request.business
    books.ensure_chart(business)
    if request.method == "POST":
        if not _admin_only(request):
            return render(request, "403.html", status=403)
        action = request.POST.get("action")
        try:
            if action == "add":
                code, name = (request.POST.get("code") or "").strip(), (request.POST.get("name") or "").strip()
                kind = request.POST.get("account_type")
                if not code.isdigit() or not 3 <= len(code) <= 10 or not name or kind not in dict(LedgerAccount.TYPE_CHOICES):
                    raise ValueError("Enter a numeric code (3 to 10 digits), a name and a type.")
                if LedgerAccount.raw_objects.filter(business=business, code=code).exists():
                    raise ValueError(f"Code {code} is already used.")
                LedgerAccount.raw_objects.create(business=business, created_by=request.user, code=code, name=name[:120], account_type=kind)
                messages.success(request, "Account added.")
            elif action in {"rename", "toggle"}:
                account = get_object_or_404(LedgerAccount.raw_objects, pk=request.POST.get("account"), business=business)
                if action == "rename":
                    name = (request.POST.get("name") or "").strip()
                    if not name:
                        raise ValueError("Enter a name.")
                    account.name = name[:120]
                else:
                    if account.system_key and account.active:
                        raise ValueError("Accounts the books rely on cannot be switched off.")
                    account.active = not account.active
                account.save(update_fields=["name", "active", "updated_at"])
                messages.success(request, "Saved.")
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("books_accounts")
    totals = books.trial_balance(business)
    nets = {r["account"].pk: r["net"] for r in totals["rows"]}
    return render(request, "core/books_accounts.html", {
        "accounts": LedgerAccount.raw_objects.filter(business=business), "nets": nets,
        "types": LedgerAccount.TYPE_CHOICES, "can_edit": _admin_only(request),
    })


@login_required
@require_GET
def books_journal(request):
    business = request.business
    qs = JournalEntry.raw_objects.filter(business=business).prefetch_related("lines__account").order_by("-date", "-id")
    source = request.GET.get("source", "")
    if source:
        qs = qs.filter(source_type=source)
    start, end = _date(request.GET.get("start"), None), _date(request.GET.get("end"), None)
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="journal.csv"'
        writer = csv.writer(response)
        writer.writerow(["Entry", "Date", "Source", "Memo", "Account code", "Account", "Debit", "Credit"])
        for entry in qs[:50000]:
            for line in entry.lines.all():
                writer.writerow([entry.pk, entry.date, entry.source_type, entry.memo, line.account.code, line.account.name, line.debit, line.credit])
        return response
    entries = list(qs[:JOURNAL_PAGE])
    sources = JournalEntry.raw_objects.filter(business=business).order_by().values_list("source_type", flat=True).distinct()
    return render(request, "core/books_journal.html", {
        "entries": entries, "sources": sorted(sources), "source": source, "start": start, "end": end,
        "truncated": len(entries) == JOURNAL_PAGE, "can_edit": _admin_only(request),
    })


@login_required
@require_http_methods(["GET", "POST"])
def books_journal_new(request):
    if not _admin_only(request):
        return render(request, "403.html", status=403)
    business = request.business
    accounts = list(LedgerAccount.raw_objects.filter(business=business, active=True))
    if not accounts:
        books.ensure_chart(business)
        accounts = list(LedgerAccount.raw_objects.filter(business=business, active=True))
    if request.method == "POST":
        lines = []
        for i in range(8):
            account = request.POST.get(f"account_{i}")
            if account:
                lines.append((account, request.POST.get(f"debit_{i}") or 0, request.POST.get(f"credit_{i}") or 0, request.POST.get(f"memo_{i}")))
        try:
            entry = books.post_manual(business, request.user, _date(request.POST.get("date"), timezone.localdate()),
                                      request.POST.get("memo", ""), lines)
            audit(business, request.user, "journal_manual", entry, f"Manual journal #{entry.pk}: {entry.memo}")
            messages.success(request, f"Journal #{entry.pk} posted.")
            return redirect("books_journal")
        except (books.JournalError, ArithmeticError, ValueError) as exc:
            messages.error(request, str(exc) if isinstance(exc, books.JournalError) else "Check the amounts: use plain numbers.")
    return render(request, "core/books_journal_form.html", {"accounts": accounts, "rows": range(8), "today": timezone.localdate()})


@login_required
@require_POST
def books_journal_reverse(request, pk):
    if not _admin_only(request):
        return render(request, "403.html", status=403)
    entry = get_object_or_404(JournalEntry.raw_objects, pk=pk, business=request.business)
    try:
        reversal = books.reverse_manual(entry, request.user)
        audit(request.business, request.user, "journal_reverse", reversal, f"Journal #{entry.pk} reversed")
        messages.success(request, f"Reversed with journal #{reversal.pk}.")
    except books.JournalError as exc:
        messages.error(request, str(exc))
    return redirect("books_journal")


@login_required
@require_GET
def books_ledger(request, pk):
    account = get_object_or_404(LedgerAccount.raw_objects, pk=pk, business=request.business)
    start, end = _date(request.GET.get("start"), None), _date(request.GET.get("end"), None)
    register = books.account_register(request.business, account, start, end)
    return render(request, "core/books_ledger.html", {"account": account, "register": register, "start": start, "end": end})


@login_required
@require_GET
def books_statements(request):
    business = request.business
    period = fiscal_period(business)
    start = _date(request.GET.get("start"), period["start"])
    end = _date(request.GET.get("end"), min(period["end"], timezone.localdate()))
    if start > end:
        start, end = end, start
    return render(request, "core/books_statements.html", {
        "income": books.income_statement(business, start, end), "sheet": books.balance_sheet(business, end),
        "start": start, "end": end, "built": BooksState.raw_objects.filter(business=business).exists(),
    })
