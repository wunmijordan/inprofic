"""Bounded, read-only activity for one tenant-owned customer."""
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from accounts.services import user_has_permission
from core.accounting import _sale_expressions
from .forms import INPUT_CLS
from .models import CustomerPayment, Sale, SaleItem, return_prefetch


class ActivityDates(forms.Form):
    start = forms.DateField(label='From', widget=forms.DateInput(attrs={'type':'date','class':INPUT_CLS}))
    end = forms.DateField(label='To', widget=forms.DateInput(attrs={'type':'date','class':INPUT_CLS}))

    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['start'] > data['end']:
            raise forms.ValidationError('The end date must be on or after the start date.')
        return data


def customer_sales(business, customer):
    return Sale.objects.filter(business=business).filter(
        Q(customer_master=customer) | Q(customer_master__isnull=True, customer=customer.name)
    ).exclude(linked_order__status='reversed')


def activity_dates(request):
    day = timezone.localdate()
    values = {'start': request.GET.get('start',day.replace(day=1).isoformat()),
              'end': request.GET.get('end',day.isoformat())}
    return ActivityDates(values)


def customer_activity_context(request, customer):
    day = timezone.localdate()
    form = activity_dates(request)
    result = {'customer':customer,'date_form':form,'period_links':[
        ('This week', (day-timedelta(days=day.weekday())).isoformat(),day.isoformat()),
        ('This month', day.replace(day=1).isoformat(),day.isoformat()),
        ('This year', day.replace(month=1,day=1).isoformat(),day.isoformat()),
    ]}
    prices = customer.product_prices.filter(business=request.business,finished_good__business=request.business).select_related('finished_good__portion_profile')
    result['prices'] = prices
    if not form.is_valid():
        return result
    start,end = form.cleaned_data['start'],form.cleaned_data['end']
    all_sales = customer_sales(request.business, customer)
    sales = all_sales.filter(date__range=(start,end)).select_related('linked_order').prefetch_related('items__finished_good__portion_profile', return_prefetch('items__distribution_returns'))
    items = SaleItem.objects.filter(sale__in=sales)
    money_field,line,_cost,units = _sale_expressions(prefix='', returns_through=end)
    # Aggregate each invoice in SQL without expanding its lines into the UI.
    # Lifetime receipt existence distinguishes a genuinely paid-at-creation sale
    # from one whose status changed after this period ended.
    invoice_value = SaleItem.objects.filter(sale_id=OuterRef('pk')).values('sale_id').annotate(value=Sum(line)).values('value')[:1]
    all_payments = CustomerPayment.raw_objects.filter(business=request.business, sale_id=OuterRef('pk'))
    paid_value = all_payments.filter(date__lte=end).values('sale_id').annotate(value=Sum('amount')).values('value')[:1]
    zero = Value(Decimal('0'), output_field=money_field)
    period_balances = sales.prefetch_related(None).select_related(None).annotate(
        invoice_value=Coalesce(Subquery(invoice_value, output_field=money_field), zero),
        has_receipts=Exists(all_payments),
        paid_value=Coalesce(Subquery(paid_value, output_field=money_field), zero),
    ).values('invoice_value', 'has_receipts', 'paid_value', 'transaction_type')
    # Combine the small aggregate rows in Python. Repeating the nested pricing
    # and return subquery inside CASE/MAX exceeds SQLite's parser depth.
    result['outstanding'] = sum((
        max(row['invoice_value'] - (row['invoice_value'] if row['transaction_type'] == 'paid' and not row['has_receipts']
                                   else row['paid_value']), Decimal('0'))
        for row in period_balances.iterator()
    ), Decimal('0'))
    page = Paginator(sales.order_by('-date','-pk'),25).get_page(request.GET.get('sales_page'))
    from .activity_documents import sale_document
    page.object_list = list(page.object_list)
    settlement = sale_document(request.business, Sale.objects.filter(pk__in=[sale.pk for sale in page.object_list]), start=start, end=end)
    by_id = {row['sale']: row for row in settlement['settlements']}
    for sale in page.object_list:
        sale.activity_payment_status = by_id[sale.pk]['status']
        sale.activity_net_total = by_id[sale.pk]['total']
    result.update(start=start,end=end,sales=page,
        sale_count=sales.count(),invoiced=items.aggregate(value=Sum(line))['value'] or Decimal('0'),
        product_totals=items.values('finished_good__name','finished_good__unit').annotate(quantity=Sum(units),value=Sum(line)).order_by('finished_good__name'))
    if user_has_permission(request.user,request.business,'production','view') and request.business.uses_production:
        from production.models import Order
        orders = Order.objects.filter(business=request.business,date__range=(start,end)).filter(
            Q(customer=customer) | Q(customer__isnull=True,customer_name=customer.name))
        result['orders'] = Paginator(orders.order_by('-date','-pk'),15).get_page(request.GET.get('orders_page'))
    if user_has_permission(request.user,request.business,'inventory','view'):
        from inventory.models import DistributionReturn
        returns = DistributionReturn.objects.filter(business=request.business,date__range=(start,end),
            sale_item__sale__in=all_sales).select_related('sale_item__finished_good')
        result['returns'] = Paginator(returns.order_by('-date','-pk'),15).get_page(request.GET.get('returns_page'))
    # Payment dates describe recorded receipts, separately from invoice dates.
    payments = CustomerPayment.objects.filter(business=request.business,date__range=(start,end)).filter(
        Q(sale__in=all_sales) | Q(sale__isnull=True,customer_master=customer) |
        Q(sale__isnull=True,customer_master__isnull=True,customer=customer.name))
    result['payments'] = Paginator(payments.order_by('-date','-pk'),15).get_page(request.GET.get('payments_page'))
    result['recorded_payments'] = payments.aggregate(value=Sum('amount'))['value'] or Decimal('0')
    return result
