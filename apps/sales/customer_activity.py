"""Bounded, read-only activity for one tenant-owned customer."""
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.core.paginator import Paginator
from django.db.models import F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from accounts.services import user_has_permission
from core.accounting import _sale_expressions
from .forms import INPUT_CLS
from .models import CustomerPayment, Sale, SaleItem


class ActivityDates(forms.Form):
    start = forms.DateField(label='From', widget=forms.DateInput(attrs={'type':'date','class':INPUT_CLS}))
    end = forms.DateField(label='To', widget=forms.DateInput(attrs={'type':'date','class':INPUT_CLS}))

    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['start'] > data['end']:
            raise forms.ValidationError('The end date must be on or after the start date.')
        return data


def customer_activity_context(request, customer):
    day = timezone.localdate()
    values = {'start': request.GET.get('start',day.replace(day=1).isoformat()),
              'end': request.GET.get('end',day.isoformat())}
    form = ActivityDates(values)
    result = {'customer':customer,'date_form':form,'period_links':[
        ('This week', (day-timedelta(days=day.weekday())).isoformat(),day.isoformat()),
        ('This month', day.replace(day=1).isoformat(),day.isoformat()),
        ('This year', day.replace(month=1,day=1).isoformat(),day.isoformat()),
    ]}
    prices = customer.product_prices.filter(business=request.business,finished_good__business=request.business).select_related('finished_good')
    result['prices'] = prices
    if not form.is_valid():
        return result
    start,end = form.cleaned_data['start'],form.cleaned_data['end']
    all_sales = Sale.objects.filter(business=request.business).filter(
        Q(customer_master=customer) | Q(customer_master__isnull=True,customer=customer.name))
    sales = all_sales.filter(date__range=(start,end)).select_related('linked_order').prefetch_related('items__finished_good')
    items = SaleItem.objects.filter(sale__in=sales)
    money_field,line,_cost,units = _sale_expressions(prefix='')
    # Aggregate open balances in SQL rather than loading every historic line or
    # payment into a small customer modal. Payments include later ledger periods.
    invoice_value = SaleItem.objects.filter(sale_id=OuterRef('pk')).values('sale_id').annotate(value=Sum(line)).values('value')[:1]
    paid_value = CustomerPayment.raw_objects.filter(business=request.business,sale_id=OuterRef('pk')).values('sale_id').annotate(value=Sum('amount')).values('value')[:1]
    zero = Value(Decimal('0'),output_field=money_field)
    open_sales = all_sales.filter(source__in=('distribution_order','online_order'),transaction_type__in=('unpaid','partial')).exclude(linked_order__status='reversed')
    result['outstanding'] = open_sales.annotate(
        invoice_value=Coalesce(Subquery(invoice_value,output_field=money_field),zero),
        paid_value=Coalesce(Subquery(paid_value,output_field=money_field),zero),
    ).aggregate(value=Sum(Greatest(F('invoice_value')-F('paid_value'),zero,output_field=money_field)))['value'] or Decimal('0')
    result.update(start=start,end=end,sales=Paginator(sales.order_by('-date','-pk'),25).get_page(request.GET.get('sales_page')),
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
