"""Correct the pricing unit of legacy operational lines; never change cash."""
from decimal import Decimal
from django.db import migrations

ZERO = Decimal('0')


def correct_prices(apps, schema_editor):
    db = schema_editor.connection.alias
    Profile = apps.get_model('inventory', 'ProductPortionProfile')
    Sale = apps.get_model('sales', 'Sale')
    Item = apps.get_model('sales', 'SaleItem')
    Payment = apps.get_model('sales', 'CustomerPayment')
    Return = apps.get_model('inventory', 'DistributionReturn')
    Audit = apps.get_model('core', 'AuditLog')
    Invoice = apps.get_model('core', 'EInvoice')
    Cash = apps.get_model('core', 'FinancialTransaction')
    Allocation = apps.get_model('production', 'OrderAllocation')
    Order = apps.get_model('production', 'Order')
    affected = set()

    def units(item):
        factor = getattr(item, 'units_per_batch_snapshot', None) or item.finished_good.units_per_batch or Decimal('1')
        return item.batch_qty * factor + item.piece_qty

    def total(item):
        if item.commercial_quantity is not None and item.commercial_unit_price is not None:
            return item.commercial_quantity * item.commercial_unit_price
        return units(item) / item.price_unit_quantity * (item.price - item.discount)

    # Preserve paid-at-creation settlement before changing invoice values.
    profiles = Profile.objects.using(db).filter(active=True, base_quantity__gt=0).exclude(customer_unit='')
    for profile in profiles.iterator():
        if profile.base_quantity == 1:
            continue
        business = profile.business_id
        items = Item.objects.using(db).filter(sale__business_id=business, finished_good_id=profile.finished_good_id,
            price_unit='', commercial_quantity__isnull=True).select_related('sale', 'finished_good')
        for item in items.iterator():
            sale = item.sale
            if sale.pk not in affected and sale.transaction_type == 'paid' and not Payment.objects.using(db).filter(business_id=business,sale_id=sale.pk).exists():
                old = sum((total(line) for line in Item.objects.using(db).filter(sale_id=sale.pk).select_related('finished_good')), ZERO)
                receipts = Cash.objects.using(db).filter(business_id=business,reference=f'SALE-{sale.pk}',transaction_type='income',reversed=False,reversal_of__isnull=True)
                actual = sum(receipts.values_list('amount',flat=True), ZERO) if receipts.exists() else old
                if actual > 0:
                    Payment.objects.using(db).create(business_id=business,sale_id=sale.pk,date=sale.date,
                        customer=sale.customer,customer_master_id=sale.customer_master_id,amount=actual,
                        payment_method=sale.payment_method or 'Cash',account_id=sale.account_id,
                        reference=f'PACK-CORRECTION-{sale.pk}',
                        notes='Original paid-at-creation amount preserved during selling-portion correction; no new cash received.')
            before = total(item)
            Item.objects.using(db).filter(pk=item.pk).update(price_unit=profile.customer_unit,price_unit_quantity=profile.base_quantity)
            affected.add(sale.pk)
            Audit.objects.using(db).create(business_id=business,action='reprice',model_name='SaleItem',object_id=str(item.pk),
                description='Corrected legacy selling price from base-unit to configured selling portion.',
                metadata={'sale':sale.pk,'price':str(item.price),'old_factor':'1','new_factor':str(profile.base_quantity),
                          'old_total':str(before),'new_total':str(before/profile.base_quantity),'cash_changed':False})
        for name,parent in [('OrderItem','order'),('OrderAllocationLine','allocation__order')]:
            Model = apps.get_model('production',name)
            lookup = 'finished_good_id' if name == 'OrderItem' else 'order_item__finished_good_id'
            filters = {f'{parent}__business_id':business,lookup:profile.finished_good_id,'price_unit':''}
            if name == 'OrderItem':
                filters['commercial_quantity__isnull'] = True
            for line in Model.objects.using(db).filter(**filters).iterator():
                Model.objects.using(db).filter(pk=line.pk).update(price_unit=profile.customer_unit,price_unit_quantity=profile.base_quantity)
                Audit.objects.using(db).create(business_id=business,action='reprice',model_name=name,object_id=str(line.pk),
                    description='Corrected legacy selling-portion pricing unit.',
                    metadata={'price':str(line.price),'old_factor':'1','new_factor':str(profile.base_quantity),'cash_changed':False})
    for sale in Sale.objects.using(db).filter(pk__in=affected).iterator():
        net = ZERO
        for item in Item.objects.using(db).filter(sale_id=sale.pk).select_related('finished_good'):
            quantity = units(item)
            returned = sum(Return.objects.using(db).filter(business_id=sale.business_id,sale_item_id=item.pk).values_list('quantity',flat=True),ZERO)
            net += total(item) * max(ZERO,quantity-returned) / quantity if quantity else ZERO
        paid = sum(Payment.objects.using(db).filter(business_id=sale.business_id,sale_id=sale.pk).values_list('amount',flat=True),ZERO)
        state = 'paid' if paid >= net else ('partial' if paid else 'unpaid')
        Sale.objects.using(db).filter(pk=sale.pk).update(transaction_type=state)
        Allocation.objects.using(db).filter(sale_id=sale.pk,order__business_id=sale.business_id).update(payment_status='paid' if state=='paid' else 'unpaid')
        if sale.linked_order_id:
            Order.objects.using(db).filter(pk=sale.linked_order_id,business_id=sale.business_id,order_type__in=['distribution','online']).update(customer_payment_status='paid' if state=='paid' else 'unpaid')
        for invoice in Invoice.objects.using(db).filter(sale_id=sale.pk,business_id=sale.business_id):
            if invoice.status in ('submitted','cleared') or invoice.submitted_at or invoice.irn:
                Invoice.objects.using(db).filter(pk=invoice.pk).update(error='Selling-portion values corrected; review a credit/debit adjustment for the submitted invoice.')
            else:
                Invoice.objects.using(db).filter(pk=invoice.pk).update(status='draft',payload={},error='Selling-portion values corrected; prepare this invoice again.')


class Migration(migrations.Migration):
    dependencies = [
        ('sales', '0010_saleitem_price_unit_saleitem_price_unit_quantity'),
        ('production', '0024_orderallocationline_price_unit_and_more'),
        ('inventory', '0023_stockadjustment_recipient_and_more'),
        ('core', '0018_daily_brief_report_sections'),
    ]
    operations = [migrations.RunPython(correct_prices)]
