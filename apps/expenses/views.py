from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction
from django.utils import timezone

from .forms import ExpenseForm, ExpenseFormSet
from .models import Expense
from .invoice import build_expense_invoice
from core.services import record_cash, audit
from core.models import FinancialTransaction
from django.http import HttpResponse


def today():
    return timezone.localdate()


@login_required
def expenses_list(request):
    expenses = Expense.objects.select_related("created_by")
    total = sum((e.amount for e in expenses), Decimal("0"))
    return render(request, "expenses/expenses_list.html", {"expenses": expenses, "total_expenses": total})


@login_required
def expense_form(request, pk=None):
    obj = get_object_or_404(Expense, pk=pk) if pk else None

    if obj is not None:
        if request.method == "POST":
            form = ExpenseForm(request.POST, instance=obj, business=request.business)
            if form.is_valid():
                expense = form.save(commit=False)
                expense.business = request.business
                expense.save()
                audit(expense.business, request.user, "update", expense, f"Expense {expense.pk} saved", {"payment_status": expense.payment_status})
                messages.success(request, "Expense saved.")
                return redirect("expenses_list")
        else:
            form = ExpenseForm(instance=obj, business=request.business)
        return render(request, "expenses/expense_form.html", {"form": form, "obj": obj})

    if request.method == "POST":
        formset = ExpenseFormSet(
            request.POST,
            prefix="expenses",
            form_kwargs={"business": request.business},
        )
        if formset.is_valid():
            saved = []
            with transaction.atomic():
                for row_form in formset.forms:
                    if (
                        not row_form.cleaned_data
                        or row_form.cleaned_data.get("DELETE")
                        or not row_form.has_changed()
                    ):
                        continue
                    expense = row_form.save(commit=False)
                    expense.business = request.business
                    expense.created_by = request.user
                    expense.save()
                    if expense.payment_status == "paid":
                        record_cash(
                            expense.business, request.user, date=expense.date, amount=expense.amount,
                            transaction_type=FinancialTransaction.OUTFLOW,
                            category=expense.get_category_display(), description=expense.description,
                            payment_method=expense.payment_method, reference=f"EXP-{expense.pk}",
                            account=expense.account,
                        )
                    audit(
                        expense.business, request.user, "create", expense,
                        f"Expense {expense.pk} saved", {"payment_status": expense.payment_status},
                    )
                    saved.append(expense)
            messages.success(request, f"{len(saved)} expense{'s' if len(saved) != 1 else ''} saved.")
            if "save_add_new" in request.POST:
                return redirect("expense_add")
            return redirect("expenses_list")
    else:
        formset = ExpenseFormSet(prefix="expenses", form_kwargs={"business": request.business})

    return render(request, "expenses/expense_form.html", {"formset": formset, "obj": None})


@login_required
def expense_delete(request, pk):
    obj = get_object_or_404(Expense, pk=pk)
    if request.method == "POST":
        obj.delete()
        messages.success(request, "Expense removed.")
    return redirect("expenses_list")


@login_required
def expense_invoice(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    pdf = build_expense_invoice(expense, request.business)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="expense-{expense.pk}.pdf"'
    return response


@login_required
def expense_payment_form(request):
    from .forms import ExpensePaymentForm
    form = ExpensePaymentForm(request.POST or None, initial={"date": today()})
    if request.method == "POST" and form.is_valid():
        from django.db import transaction
        with transaction.atomic():
            x = form.save(commit=False)
            x.business = request.business
            x.created_by = request.user
            x.save()
            expense = x.expense
            paid_before = sum((p.amount for p in expense.payments.exclude(pk=x.pk)), Decimal("0"))
            new_paid = paid_before + x.amount
            expense.payment_status = "paid" if new_paid >= expense.amount else "unpaid"
            expense.save(update_fields=["payment_status", "updated_at"])
            record_cash(request.business, request.user, date=x.date, amount=x.amount,
                        transaction_type=FinancialTransaction.OUTFLOW, category="Expense payment",
                        description=f"Payment for Expense #{expense.pk} — {expense.description}",
                        payment_method=x.payment_method, reference=x.reference or f"EXPPAY-{x.pk}",
                        account=x.account)
            audit(request.business, request.user, "create", x, "Expense payment recorded",
                  {"amount": str(x.amount), "expense": expense.pk})
        messages.success(request, "Expense payment recorded and payable updated.")
        return redirect("finance_dashboard")
    return render(request, "core/payment_form.html", {"form": form, "title": "Expense Payment",
        "help_text": "Settle an unpaid expense. The expense was already recorded; this payment only moves actual money out of the selected cash/bank account."})
