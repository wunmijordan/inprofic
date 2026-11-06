from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase, RequestFactory
from django.urls import reverse
from accounts.models import CustomUser
from core.models import Business, CashAccount, FinancialTransaction
from sales.models import Sale
from .models import FinishedGood, FinishedGoodChannelPrice, MarketStockLot, MarketStockMovement
from .services import sell_market_stock_items
from .distribution_summary import distribution_summaries


class MarketPhysicalSaleTests(TestCase):
    def setUp(self):
        self.business=Business.objects.create(name='Market sales',slug='market-sales')
        self.user=CustomUser.objects.create_superuser(username='market-physical-admin',password='test-password')
        self.account=CashAccount.raw_objects.create(business=self.business,name='Till')
        self.good=FinishedGood.raw_objects.create(business=self.business,name='Bread',unit='loaf',stock=20,selling_price=100)
        FinishedGoodChannelPrice.objects.create(finished_good=self.good,channel='distribution',price=50)
        self.lot=MarketStockLot.raw_objects.create(business=self.business,finished_good=self.good,
            received_date=date(2026,11,1),quantity_received=10,quantity_available=10,unit_cost=40)
        MarketStockMovement.raw_objects.create(business=self.business,lot=self.lot,date=date(2026,11,1),
            movement_type=MarketStockMovement.PRODUCTION_IN,quantity=10,balance_after=10)

    def sell(self,quantity=3,account=None):
        return sell_market_stock_items(business=self.business,items=[{'finished_good':self.good,'quantity':quantity}],
            date=date(2026,11,6),payment_method='Cash',account=account or self.account,user=self.user)

    def test_direct_sale_uses_store_price_cost_and_one_payment_without_shelf_transfer(self):
        sale=self.sell()
        self.assertEqual((sale.source,sale.transaction_type,sale.total),('walkin','paid',Decimal('300')))
        self.assertEqual(sale.items.get().unit_cost,40)
        self.good.refresh_from_db();self.lot.refresh_from_db()
        self.assertEqual(self.good.stock,20)
        self.assertEqual(self.good.total_delivered_to_customers,0)
        self.assertEqual(self.lot.quantity_available,7)
        self.assertFalse(MarketStockMovement.raw_objects.filter(movement_type='transfer_physical').exists())
        self.assertEqual(FinancialTransaction.raw_objects.get(reference=f'SALE-{sale.pk}').amount,300)
        request=RequestFactory().get('/inventory/market-stock/',{'date':'2026-11-06'})
        request.user,request.business=self.user,self.business
        summary=distribution_summaries(request)
        row=summary['distribution_rows'][0]
        self.assertEqual((row['opening'],row['physical_sold'],row['distributed'],row['closing']),(10,3,0,7))
        self.assertEqual(summary['physical_sales_value'],300)
        self.assertIn('Sold in Physical Store: 3 loaves',summary['distribution_share_text'])
        self.assertNotIn('Reading this report',summary['distribution_share_text'])
        self.assertNotIn('Written off:',summary['distribution_share_text'])

    def test_insufficient_stock_and_foreign_account_roll_back(self):
        with self.assertRaises(ValidationError):self.sell(11)
        other=Business.objects.create(name='Private',slug='private-market')
        account=CashAccount.raw_objects.create(business=other,name='Private account')
        with self.assertRaises(ValidationError):self.sell(account=account)
        self.assertFalse(Sale.raw_objects.filter(business=self.business).exists())
        self.assertFalse(FinancialTransaction.raw_objects.filter(business=self.business).exists())
        self.lot.refresh_from_db();self.assertEqual(self.lot.quantity_available,10)

    def test_sale_page_posts_multiple_product_formset_and_respects_access(self):
        self.client.force_login(self.user)
        response=self.client.post(reverse('market_stock_sell'),{'date':'2026-11-06','payment_method':'Cash',
            'account':self.account.pk,'customer':'Shop customer','items-TOTAL_FORMS':'1','items-INITIAL_FORMS':'0',
            'items-0-finished_good':self.good.pk,'items-0-quantity':'2'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(Sale.raw_objects.get(business=self.business).customer,'Shop customer')
        self.business.vertical=Business.VERTICAL_WHOLESALE;self.business.save(update_fields=['vertical'])
        self.assertEqual(self.client.get(reverse('market_stock_sell')).status_code,403)

    def test_customer_price_is_frozen_in_receivable_profit_and_distribution_detail(self):
        from sales.models import Customer, CustomerProductPrice, CustomerPayment
        from inventory.services import release_market_stock_items
        from core.accounting import profit_and_loss
        from core.views import _finished_good_search_detail
        customer=Customer.raw_objects.create(business=self.business,name='Price agreement')
        agreement=CustomerProductPrice.raw_objects.create(business=self.business,customer=customer,
            finished_good=self.good,channel='distribution',price=80)
        sale=release_market_stock_items(business=self.business,items=[{'finished_good':self.good,'quantity':3}],
            customer=customer,date=date(2026,11,6),payment_status='unpaid',payment_method='Cash',account=None,user=self.user)
        self.assertEqual(sale.items.get().price,80)
        self.assertEqual(sale.total,240)
        self.client.force_login(self.user)
        response=self.client.post(reverse('customer_payment_add'),{'sale':sale.pk,'customer':customer.name,
            'date':'2026-11-06','amount':'100','payment_method':'Cash','account':self.account.pk})
        self.assertEqual(response.status_code,302)
        agreement.price=90;agreement.save()
        sale.refresh_from_db()
        self.assertEqual(sale.total,240)
        request=RequestFactory().get('/inventory/market-stock/',{'date':'2026-11-06'})
        request.user,request.business=self.user,self.business
        summary=distribution_summaries(request)
        invoice=summary['distribution_settlement']['rows'][0]
        self.assertEqual((invoice['gross'],invoice['paid'],invoice['net_due']),(240,100,140))
        self.assertIn('3 loaves × ₦80.00',summary['distribution_share_text'])
        detail=_finished_good_search_detail(self.good)
        self.assertTrue(any(row['customer']==customer.name and row['price']==90 for row in detail['pricing']))
        self.assertFalse(any(row['customer']==customer.name for row in _finished_good_search_detail(self.good,show_customer_prices=False)['pricing']))
        # Accounting uses the invoice snapshot, never today's price agreement.
        pl=profit_and_loss(self.business,date(2026,11,6),date(2026,11,6))
        self.assertEqual(pl['unpaid_sales'],240)

        response=self.client.post(reverse('customer_payment_add'),{'sale':sale.pk,'customer':customer.name,
            'date':'2026-11-06','amount':'140','payment_method':'Transfer','account':self.account.pk})
        self.assertEqual(response.status_code,302)
        sale.refresh_from_db()
        self.assertEqual(sale.transaction_type,'paid')
        pl=profit_and_loss(self.business,date(2026,11,6),date(2026,11,6))
        self.assertEqual((pl['revenue'],pl['cogs'],pl['gross_profit']),(240,120,120))
