"""Bank reconciliation screens (finance module)."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from . import bank_reconciliation as recon
from .models import BankStatement, CashAccount
from .services import audit


@login_required
@require_http_methods(["GET", "POST"])
def reconcile_home(request):
    business = request.business
    accounts = CashAccount.objects.filter(business=business, active=True).order_by("name")
    if request.method == "POST":
        try:
            account = accounts.get(pk=int(request.POST.get("account") or 0))
            upload = request.FILES.get("statement")
            if not upload:
                raise recon.StatementError("Choose the statement CSV file.")
            if upload.size > 2_000_000:
                raise recon.StatementError("The file is too large. Statements are limited to 2 MB.")
            try:
                closing = Decimal((request.POST.get("closing_balance") or "").replace(",", "").strip())
            except InvalidOperation:
                raise recon.StatementError("Enter the statement's closing balance.")
            rows = recon.parse_statement_csv(upload.read())
            statement = recon.create_statement(business, request.user, account, closing, rows,
                                               label=(request.POST.get("label") or upload.name))
            matched = recon.auto_match(statement)
            audit(business, request.user, "reconcile_import", statement,
                  f"Statement loaded for {account.name}: {len(rows)} lines, {matched} matched automatically",
                  {"lines": len(rows), "matched": matched})
            messages.success(request, f"{len(rows)} lines loaded; {matched} matched automatically.")
            return redirect("reconcile_detail", pk=statement.pk)
        except (recon.StatementError, CashAccount.DoesNotExist, ValueError) as exc:
            messages.error(request, str(exc) or "Choose an account for this statement.")
    statements = BankStatement.objects.filter(business=business).select_related("account")[:50]
    return render(request, "core/reconcile_list.html", {"accounts": accounts, "statements": statements})


@login_required
@require_http_methods(["GET", "POST"])
def reconcile_detail(request, pk):
    statement = get_object_or_404(BankStatement.objects.select_related("account"), pk=pk, business=request.business)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "auto":
                count = recon.auto_match(statement)
                messages.success(request, f"{count} more line(s) matched.")
            elif action == "match":
                recon.match_line(statement, int(request.POST["line"]), int(request.POST["transaction"]))
                messages.success(request, "Matched.")
            elif action == "unmatch":
                recon.unmatch_line(statement, int(request.POST["line"]))
                messages.success(request, "Match removed.")
            elif action == "finish":
                recon.finish(statement)
                audit(request.business, request.user, "reconcile_finish", statement,
                      f"{statement.account.name} reconciled to {statement.period_end}")
                messages.success(request, "Reconciled. The statement is now locked.")
            elif action == "delete" and statement.status != BankStatement.STATUS_RECONCILED:
                statement.delete()
                messages.success(request, "Statement removed. Your cash records were not changed.")
                return redirect("reconcile_home")
            else:
                raise recon.StatementError("Unknown action.")
        except (recon.StatementError, KeyError, ValueError) as exc:
            messages.error(request, str(exc) or "That could not be done.")
        except Exception as exc:  # a stale candidate or line id
            messages.error(request, "That line or transaction is no longer available.")
        return redirect("reconcile_detail", pk=statement.pk)
    return render(request, "core/reconcile_detail.html", {"statement": statement, "r": recon.summarize(statement)})
