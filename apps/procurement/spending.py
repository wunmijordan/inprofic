"""Shared purchasing/expense workspace and period cash-out definitions."""
from decimal import Decimal
from django import forms
from django.core.exceptions import PermissionDenied
from django.db.models import Case, DecimalField, F, Q, Sum, When
from django.utils import timezone
from accounts.services import user_has_permission
from core.models import FinancialTransaction
from expenses.models import Expense
from .models import PurchaseOrder, PurchaseOrderItem

ZERO = Decimal('0')


class SpendingDates(forms.Form):
    start = forms.DateField(required=False, label='From', widget=forms.DateInput(attrs={'type': 'date', 'class': 'workspace-input'}))
    end = forms.DateField(required=False, label='To', widget=forms.DateInput(attrs={'type': 'date', 'class': 'workspace-input'}))

    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['start'] > data['end']:
            raise forms.ValidationError('The end date must be on or after the start date.')
        return data


def access(user, business):
    allowed = {name: user_has_permission(user, business, name, 'view') for name in ('procurement', 'expenses')}
    if not any(allowed.values()):
        raise PermissionDenied('Purchasing or Expenses view access is required.')
    return allowed


def in_period(queryset, start, end, field='date'):
    if start:
        queryset = queryset.filter(**{f'{field}__gte': start})
    if end:
        queryset = queryset.filter(**{f'{field}__lte': end})
    return queryset


def spending_cash(business, start, end, *, purchases=True, expenses=True):
    """Net actual payments, including later settlements and compensating entries.

    Count cash ledger entries once; never add invoice status or payment rows on
    top of the same receipt. Reversals follow the original spending category.
    """
    categories, prefixes = [], []
    if purchases:
        categories += ['Procurement', 'Supplier payment']
        prefixes += ['PO-', 'SUPPAY-']
    if expenses:
        categories += ['Expense payment']
        prefixes += ['EXP-', 'EXPPAY-']
    if not categories:
        return ZERO
    match = Q(category__in=categories) | Q(reversal_of__category__in=categories)
    for prefix in prefixes:
        match |= Q(reference__startswith=prefix) | Q(reversal_of__reference__startswith=prefix)
    transactions = in_period(FinancialTransaction.objects.filter(business=business).filter(match), start, end)
    money = DecimalField(max_digits=24, decimal_places=2)
    return transactions.aggregate(value=Sum(Case(
        When(transaction_type=FinancialTransaction.OUTFLOW, then=F('amount')),
        default=-F('amount'), output_field=money)))['value'] or ZERO


def period_totals(business, start, end, *, purchases=True, expenses=True):
    purchase_value = expense_value = ZERO
    if purchases:
        lines = in_period(PurchaseOrderItem.objects.filter(purchase_order__in=PurchaseOrder.objects.filter(business=business)), start, end, 'purchase_order__date')
        purchase_value = lines.aggregate(value=Sum(F('qty') * F('unit_cost')))['value'] or ZERO
    if expenses:
        expense_value = in_period(Expense.objects.filter(business=business), start, end).aggregate(value=Sum('amount'))['value'] or ZERO
    return {'purchases': purchase_value, 'expenses': expense_value, 'grand_total': purchase_value + expense_value,
        'money_out': spending_cash(business, start, end, purchases=purchases, expenses=expenses)}


def workspace_period(request):
    allowed = access(request.user, request.business)
    form = SpendingDates(request.GET)
    result = {'spending_dates': form, 'spending_access': allowed, 'period_valid': form.is_valid(),
        'month_start': timezone.localdate().replace(day=1), 'today': timezone.localdate()}
    if not result['period_valid']:
        return result
    start, end = form.cleaned_data['start'], form.cleaned_data['end']
    result.update(period_start=start, period_end=end,
        spending_totals=period_totals(request.business, start, end,
            purchases=allowed['procurement'], expenses=allowed['expenses']))
    return result


def workspace_records(request):
    from django.db.models import Prefetch
    from expenses.services import with_outstanding
    from .models import SupplierPayment
    from .services import describe_request
    context = workspace_period(request)
    allowed = context['spending_access']
    orders, expenses = [], []
    if context['period_valid']:
        start, end = context['period_start'], context['period_end']
        if allowed['procurement']:
            query = in_period(PurchaseOrder.objects.filter(business=request.business), start, end).select_related('created_by').prefetch_related(
                Prefetch('payments', queryset=SupplierPayment.raw_objects.filter(business=request.business)),
                Prefetch('items', queryset=PurchaseOrderItem.objects.select_related('raw_material', 'finished_good')))
            groups = {}
            for po in query:
                groups.setdefault(('request', po.request_id) if po.request_id else ('po', po.pk), []).append(po)
            orders = [describe_request(sorted(group, key=lambda po: po.pk)) for group in groups.values()]
        if allowed['expenses']:
            expenses = list(with_outstanding(in_period(Expense.objects.filter(business=request.business), start, end))
                .select_related('created_by').prefetch_related('payments'))
    for expense in expenses:
        expense.remaining_balance = ZERO if expense.payment_status == 'paid' else expense._remaining_balance
        expense.settlement_label = 'Paid' if expense.payment_status == 'paid' else ('Partially paid' if expense._paid_receipts else 'Unpaid')
    def purchase_use(po):
        items = po.request_items
        if any(item.finished_good_id for item in items):
            return 'Stock product purchase' if all(item.finished_good_id for item in items) else 'Materials / stock purchase'
        if items and all(item.raw_material.category == 'operational_supply' for item in items):
            return 'General-use supply purchase'
        return 'Production material purchase' if request.business.uses_production else 'Supporting supply purchase'

    activity = [{'date': po.date, 'kind': 'purchase', 'order': po, 'amount': po.request_total,
                 'vendor': po.request_supplier or 'Vendor not named', 'description': ', '.join(item.item_name for item in po.request_items),
                 'payment': dict(PurchaseOrder.PAYMENT_STATUS_CHOICES).get(po.request_payment, po.request_payment),
                 'usage': purchase_use(po)} for po in orders]
    activity += [{'date': expense.date, 'kind': 'expense', 'expense': expense, 'amount': expense.amount,
                  'vendor': expense.vendor or 'Vendor not named', 'description': expense.description,
                  'payment': expense.settlement_label} for expense in expenses]
    activity.sort(key=lambda row: (row['date'], row.get('order', row.get('expense')).pk), reverse=True)
    from expenses.models import ExpenseRequest
    context["expense_requests"] = list(in_period(ExpenseRequest.objects.filter(business=request.business), context.get("period_start"), context.get("period_end")).prefetch_related("lines")) if allowed["expenses"] and context["period_valid"] else []
    context.update(orders=orders, spending_activity=activity)
    return context
