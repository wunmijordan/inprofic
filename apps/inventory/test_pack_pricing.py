"""Pack price accounting, historical correction and presentation parity."""
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.db import connection
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from accounts.models import CustomUser
from core.accounting import _sale_expressions
from core.models import Business, CashAccount, FinancialTransaction, AuditLog, EInvoice
from core.quantities import product_price_fields, set_product_price
from inventory.models import FinishedGood, FinishedGoodChannelPrice, ProductPortionProfile, MarketStockLot, DistributionReturn
from production.forms import OrderItemForm
from production.models import Order, OrderItem, OrderAllocation, OrderAllocationLine
from sales.models import Customer, Sale, SaleItem, CustomerPayment


class PackPricingTests(TestCase):
    def setUp(self):
        self.day=timezone.localdate()
        self.business=Business.objects.create(name='Pack prices',slug='pack-prices')
        self.user=CustomUser.objects.create_superuser(username='pack-admin',password='test')
        self.client.force_login(self.user)
        self.good=FinishedGood.raw_objects.create(business=self.business,name='Buns',unit='bun',stock=80,reorder_level=4,units_per_batch=20,selling_price=1500)
        ProductPortionProfile.raw_objects.create(business=self.business,finished_good=self.good,active=True,customer_unit='pack',base_quantity=4)
        FinishedGoodChannelPrice.objects.create(finished_good=self.good,channel='distribution',price=1100)
        FinishedGoodChannelPrice.objects.create(finished_good=self.good,channel='online',price=1500)
        self.customer=Customer.raw_objects.create(business=self.business,name='Ada')
        self.account=CashAccount.raw_objects.create(business=self.business,name='Till')
        self.lot=MarketStockLot.raw_objects.create(business=self.business,finished_good=self.good,received_date=self.day,quantity_received=80,quantity_available=80,unit_cost=20)

    def release(self):
        response=self.client.post(reverse('market_stock_release'),{'date':str(self.day),'customer':self.customer.pk,
            'payment_status':'paid','payment_method':'Cash','account':self.account.pk,
            'items-TOTAL_FORMS':1,'items-INITIAL_FORMS':0,'items-MIN_NUM_FORMS':1,'items-MAX_NUM_FORMS':100,
            'items-0-finished_good':self.good.pk,'items-0-quantity':10})
        self.assertEqual(response.status_code,302)
        return Sale.raw_objects.get(business=self.business)

    def test_release_prices_packs_and_sql_matches_invoice_and_returns(self):
        sale=self.release(); item=sale.items.get()
        self.assertEqual((item.total_units,item.price,item.price_unit_quantity,sale.total),(40,1100,4,11000))
        self.assertEqual(FinancialTransaction.raw_objects.get(business=self.business).amount,11000)
        self.lot.refresh_from_db(); self.assertEqual(self.lot.quantity_available,40)
        _,line,cost,_=_sale_expressions('')
        totals=SaleItem.objects.filter(sale=sale).aggregate(revenue=Sum(line),cost=Sum(cost))
        self.assertEqual(totals,{'revenue':11000,'cost':800})
        DistributionReturn.raw_objects.create(business=self.business,sale_item=item,date=self.day,quantity=4,condition='damaged',reason='Damaged')
        self.assertEqual(sale.return_credit,1100)
        self.assertEqual(SaleItem.objects.filter(sale=sale).aggregate(value=Sum(line))['value'],9900)
        text=self.client.get(reverse('market_stock')).context['distribution_share_text']
        self.assertIn('10 packs × ₦1,100.00 per pack',text)

    def test_different_channels_and_production_use_same_pack_convention(self):
        order=Order.raw_objects.create(business=self.business,date=self.day,order_type='online')
        item=OrderItem(order=order,finished_good=self.good,piece_qty=40)
        set_product_price(item,'online'); item.save()
        self.assertEqual(item.line_total,15000)
        form=OrderItemForm(instance=item)
        self.assertNotIn('quantity_unit',form.fields)
        self.assertEqual(form.initial['piece_qty'],10)
        bound=OrderItemForm({'finished_good':self.good.pk,'batch_qty':0,'piece_qty':10,'discount':100})
        self.assertTrue(bound.is_valid(),bound.errors)
        self.assertEqual(bound.cleaned_data['piece_qty'],40)
        self.assertEqual(bound.cleaned_data['discount'],100)
        allocation=OrderAllocation.objects.create(order=order,channel='distribution')
        line=OrderAllocationLine.objects.create(allocation=allocation,order_item=item,piece_qty=40,price=1100,**product_price_fields(self.good))
        self.assertEqual(line.line_total,11000)

    def test_fractional_pack_return_uses_cents_in_sql_and_invoice(self):
        profile=self.good.portion_profile
        profile.base_quantity=3; profile.save()
        self.good.refresh_from_db()
        sale=Sale.raw_objects.create(business=self.business,date=self.day)
        item=SaleItem.objects.create(sale=sale,finished_good=self.good,piece_qty=3,price=1100,**product_price_fields(self.good))
        DistributionReturn.raw_objects.create(business=self.business,sale_item=item,date=self.day,quantity=1,condition='damaged',reason='Damaged')
        _,line,_,_=_sale_expressions('')
        self.assertEqual(item.net_line_total,Decimal('733.33'))
        self.assertEqual(SaleItem.objects.filter(pk=item.pk).aggregate(value=Sum(line))['value'],Decimal('733.33'))

    def test_historical_correction_handles_returns_partial_payments_and_submitted_invoice(self):
        sale=Sale.raw_objects.create(business=self.business,date=self.day,customer='Ada',transaction_type='partial')
        item=SaleItem.objects.create(sale=sale,finished_good=self.good,piece_qty=40,price=1100)
        CustomerPayment.raw_objects.create(business=self.business,sale=sale,date=self.day,customer='Ada',amount=2000)
        DistributionReturn.raw_objects.create(business=self.business,sale_item=item,date=self.day,quantity=4,condition='damaged',reason='Damaged')
        invoice=EInvoice.raw_objects.create(business=self.business,sale=sale,invoice_number='ISSUED-1',status='cleared',irn='original-irn',payload={'original':True})
        foreign=Business.objects.create(name='Private',slug='private-pricing')
        other=FinishedGood.raw_objects.create(business=foreign,name='Plain',unit='piece')
        private_sale=Sale.raw_objects.create(business=foreign,date=self.day)
        private=SaleItem.objects.create(sale=private_sale,finished_good=other,piece_qty=2,price=100)
        import_module('sales.migrations.0011_correct_selling_portion_prices').correct_prices(apps,SimpleNamespace(connection=connection))
        sale.refresh_from_db(); invoice.refresh_from_db(); private.refresh_from_db()
        self.assertEqual((sale.net_total,sale.transaction_type),(9900,'partial'))
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sale).amount,2000)
        self.assertEqual((invoice.status,invoice.irn,invoice.payload),('cleared','original-irn',{'original':True}))
        self.assertIn('adjustment',invoice.error)
        self.assertEqual((private.line_total,private.price_unit_quantity),(200,1))
        self.assertFalse(AuditLog.raw_objects.filter(business=foreign).exists())

    def test_historical_correction_is_audited_idempotent_and_preserves_cash(self):
        sale=Sale.raw_objects.create(business=self.business,date=self.day,customer='Ada',transaction_type='paid',account=self.account)
        item=SaleItem.objects.create(sale=sale,finished_good=self.good,piece_qty=40,price=1100)
        tx=FinancialTransaction.raw_objects.create(business=self.business,date=self.day,amount=44000,transaction_type='income',category='Sales revenue',reference=f'SALE-{sale.pk}',account=self.account)
        invoice=EInvoice.raw_objects.create(business=self.business,sale=sale,invoice_number='PACK-1',payload={'old':'value'})
        commercial=SaleItem.objects.create(sale=sale,finished_good=self.good,piece_qty=4,price=1500,commercial_quantity=1,commercial_unit='pack',commercial_unit_price=1500)
        correction=import_module('sales.migrations.0011_correct_selling_portion_prices').correct_prices
        correction(apps,SimpleNamespace(connection=connection))
        item.refresh_from_db(); commercial.refresh_from_db(); tx.refresh_from_db(); invoice.refresh_from_db()
        self.assertEqual((item.line_total,item.price_unit_quantity),(11000,4))
        self.assertEqual(commercial.line_total,1500)
        self.assertEqual(tx.amount,44000)
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sale).amount,44000)
        self.assertEqual((invoice.status,invoice.payload),('draft',{}))
        count=AuditLog.raw_objects.filter(business=self.business,action='reprice').count()
        self.assertGreater(count,0)
        correction(apps,SimpleNamespace(connection=connection))
        self.assertEqual(AuditLog.raw_objects.filter(business=self.business,action='reprice').count(),count)
