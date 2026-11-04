"""Expense settlement and auditable corrections, shared by forms and reports."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, F, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from core.models import FinancialTransaction
from core.services import record_cash
from .models import Expense, ExpensePayment

ZERO = Decimal("0")


def with_outstanding(queryset):
    money = DecimalField(max_digits=20, decimal_places=2)
    payments = (ExpensePayment.raw_objects.filter(expense_id=OuterRef("pk"), business_id=OuterRef("business_id"))
                .order_by().values("expense_id").annotate(total=Sum("amount")).values("total"))
    return queryset.annotate(_paid_receipts=Coalesce(Subquery(payments), Value(ZERO), output_field=money)).annotate(
        _remaining_balance=Greatest(F("amount") - F("_paid_receipts"), Value(ZERO), output_field=money))


def outstanding_amount(expense):
    if expense.payment_status == "paid":
        return ZERO
    if hasattr(expense, "_remaining_balance"):
        return expense._remaining_balance
    paid = ExpensePayment.raw_objects.filter(business_id=expense.business_id, expense=expense).aggregate(total=Sum("amount"))["total"] or ZERO
    return max(ZERO, expense.amount - paid)


@transaction.atomic
def save_correction(expense, user):
    previous = Expense.raw_objects.select_for_update().get(pk=expense.pk, business_id=expense.business_id)
    payments = ExpensePayment.raw_objects.filter(business_id=expense.business_id, expense=expense)
    if payments.exists():
        paid = payments.aggregate(total=Sum("amount"))["total"] or ZERO
        if expense.amount < paid:
            raise ValidationError("The expense cannot be reduced below its recorded payments.")
        if previous.payment_status != "paid" and expense.payment_status == "paid" and paid < expense.amount:
            raise ValidationError("Record the remaining payment through Finance before marking this expense paid.")
        # Otherwise settlement state is derived from the recorded payments, so
        # raising the amount of a fully-paid expense reopens its balance.
        expense.payment_status = "paid" if paid >= expense.amount else "unpaid"
    else:
        fields = ("date", "amount", "account_id", "payment_status", "payment_method", "category")
        changed = any(getattr(previous, field) != getattr(expense, field) for field in fields)
        if changed:
            originals = FinancialTransaction.raw_objects.select_for_update().filter(
                business_id=expense.business_id, reference=f"EXP-{expense.pk}",
                transaction_type=FinancialTransaction.OUTFLOW, reversed=False, reversal_of__isnull=True,
            )
            for original in originals:
                FinancialTransaction.raw_objects.create(
                    business=expense.business, created_by=user, date=timezone.localdate(),
                    transaction_type=FinancialTransaction.INCOME, amount=original.amount,
                    category=f"Reversal: {original.category}"[:80], description=f"Expense #{expense.pk} correction",
                    reference=f"REV-EXP-{expense.pk}", payment_method=original.payment_method,
                    account=original.account, reversal_of=original,
                )
                original.reversed = True
                original.save(update_fields=["reversed", "updated_at"])
            if expense.payment_status == "paid":
                record_cash(expense.business, user, date=expense.date, amount=expense.amount,
                            transaction_type=FinancialTransaction.OUTFLOW, category=expense.get_category_display(),
                            description=expense.description, payment_method=expense.payment_method,
                            reference=f"EXP-{expense.pk}", account=expense.account)
    expense.save()
    return expense
