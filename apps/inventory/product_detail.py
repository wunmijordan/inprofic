"""Read-only product workspace, using canonical costing and sale expressions."""
from decimal import Decimal
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.utils import timezone
from django.urls import reverse
from accounts.services import user_has_permission
from core.accounting import _sale_expressions
from sales.customer_activity import ActivityDates
from sales.models import SaleItem, return_prefetch
from .models import MarketStockMovement

ZERO=Decimal('0')


def product_context(request, good):
    from production.models import ProductionBatch, OrderItem
    from procurement.models import PurchaseOrderItem
    day=timezone.localdate()
    form=ActivityDates({'start':request.GET.get('start',day.replace(day=1).isoformat()),'end':request.GET.get('end',day.isoformat())})
    permits={name:user_has_permission(request.user,request.business,name,'view') for name in ('sales','finance','production','procurement')}
    data={'good':good,'date_form':form,'permits':permits,'history_url':reverse('stock_history',args=['finished',good.pk])}
    if not form.is_valid():
        return data
    start,end=form.cleaned_data['start'],form.cleaned_data['end']
    data.update(start=start,end=end)
    data['history_url'] += f'?start={start.isoformat()}&end={end.isoformat()}'
    from core.quantities import product_portion
    selling_unit, factor = product_portion(good)
    data['selling_unit'] = selling_unit
    data['estimated_cost']=good.est_cost * factor
    estimated_cost = data['estimated_cost']
    prices=[{'name':'Default','channel':'All customers','price':good.selling_price}]
    prices += [{'name':row.get_channel_display(),'channel':'All customers','price':row.price} for row in good.channel_prices.all()]
    if permits['sales']:
        prices += [{'name':row.customer.name,'customer_id':row.customer_id,'channel':row.get_channel_display(),'price':row.price}
            for row in good.customer_prices.filter(business=request.business,customer__business=request.business).select_related('customer')]
    for row in prices:
        row['contribution']=row['price']-estimated_cost
        row['margin']=row['contribution']/row['price']*100 if row['price'] else None
    data['prices']=prices
    items=SaleItem.objects.filter(sale__business=request.business,finished_good=good,sale__date__range=(start,end)).exclude(sale__linked_order__status='reversed')
    _money, line, cost, units = _sale_expressions(prefix='')
    if permits['sales'] or permits['finance']:
        totals=items.aggregate(value=Sum(line),units=Sum(units),sales=Count('sale_id',distinct=True))
        data['sales_totals']={key:value or ZERO for key,value in totals.items()}
    if permits['finance']:
        totals=items.filter(sale__transaction_type='paid').aggregate(revenue=Sum(line),cost=Sum(cost),units=Sum(units))
        totals={key:value or ZERO for key,value in totals.items()}
        totals['profit']=totals['revenue']-totals['cost']
        totals['margin']=totals['profit']/totals['revenue']*100 if totals['revenue'] else None
        totals['unit_cost']=totals['cost']/totals['units'] * factor if totals['units'] else None
        totals['unit_price']=totals['revenue']/totals['units'] * factor if totals['units'] else None
        totals['uncosted']=items.filter(sale__transaction_type='paid',unit_cost__isnull=True).count()
        data['realized']=totals
    if permits['sales']:
        data['sales']=Paginator(items.annotate(recorded_cost=cost).select_related('sale','finished_good').prefetch_related(return_prefetch()).order_by('-sale__date','-pk'),25).get_page(request.GET.get('sales_page'))
    if permits['production'] and request.business.uses_production:
        batches=ProductionBatch.objects.filter(business=request.business,finished_good=good,production_date__range=(start,end),is_reversed=False)
        data['production_totals']=batches.aggregate(made=Sum('produced_units'),waste=Sum('wastage_units'),cost=Sum('total_cost'))
        data['batches']=Paginator(batches.select_related('order').order_by('-production_date','-pk'),20).get_page(request.GET.get('batches_page'))
        data['orders']=Paginator(OrderItem.objects.filter(order__business=request.business,finished_good=good,order__date__range=(start,end)).select_related('order').order_by('-order__date','-pk'),20).get_page(request.GET.get('orders_page'))
    if permits['procurement']:
        purchases=PurchaseOrderItem.objects.filter(finished_good=good,purchase_order__business=request.business,purchase_order__status='received',purchase_order__received_date__range=(start,end)).select_related('purchase_order')
        data['purchases']=Paginator(purchases.order_by('-purchase_order__received_date','-pk'),20).get_page(request.GET.get('purchases_page'))
    movements=MarketStockMovement.objects.filter(business=request.business,lot__finished_good=good,date__range=(start,end)).select_related('customer','sale')
    data['market_movements']=Paginator(movements.order_by('-date','-pk'),25).get_page(request.GET.get('market_page'))
    return data
