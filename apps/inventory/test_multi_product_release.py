from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from accounts.models import CustomUser
from core.models import Business, FinancialTransaction, CashAccount
from sales.models import Customer, Sale
from .forms import MarketStockReleaseItemFormSet
from .models import FinishedGood, FinishedGoodChannelPrice, MarketStockLot, MarketStockMovement
from .services import release_market_stock_items


class MultiProductReleaseTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='Release bakery',slug='release-bakery')
        self.user = CustomUser.objects.create_superuser(username='release-admin',password='test-password')
        self.customer = Customer.raw_objects.create(business=self.business,name='One customer')
        self.account = CashAccount.raw_objects.create(business=self.business,name='Release bank',account_type='bank')
        self.goods=[]
        for name, price in [('Bread','100'),('Cake','200')]:
            good=FinishedGood.raw_objects.create(business=self.business,name=name,unit='piece',stock=None)
            FinishedGoodChannelPrice.objects.create(finished_good=good,channel='distribution',price=price)
            MarketStockLot.raw_objects.create(business=self.business,finished_good=good,received_date=date(2026,1,1),quantity_received=10,quantity_available=10,unit_cost=10)
            self.goods.append(good)

    def release(self, paid=False, second_quantity=3):
        return release_market_stock_items(business=self.business,items=[
            {'finished_good':self.goods[0],'quantity':2},
            {'finished_good':self.goods[1],'quantity':second_quantity},
        ],customer=self.customer,date=date(2026,11,5),payment_status='paid' if paid else 'unpaid',
        payment_method='Transfer',account=self.account if paid else None,user=self.user)

    def test_mixed_expiry_lots_release_no_expiry_stock_without_cross_batch_blocking(self):
        from .services import market_stock_available, release_market_stock
        good = self.goods[0]
        dated = MarketStockLot.raw_objects.create(business=self.business, finished_good=good,
            received_date=date(2026,11,1), expiry_date=date(2026,11,10),
            quantity_received=6, quantity_available=6, unit_cost=10)
        expired = MarketStockLot.raw_objects.create(business=self.business, finished_good=good,
            received_date=date(2026,10,1), expiry_date=date(2026,11,1),
            quantity_received=5, quantity_available=5, unit_cost=10)
        self.assertEqual(market_stock_available(good, date(2026,11,5)), Decimal('16'))
        self.client.force_login(self.user)
        with patch('inventory.views.timezone.localdate', return_value=date(2026,11,5)):
            response = self.client.get(reverse('market_stock_release'))
        self.assertEqual(Decimal(response.context['available_by_product'][str(good.pk)]['available']), Decimal('16'))
        sale = release_market_stock(business=self.business, good=good, customer=self.customer,
            quantity=12, date=date(2026,11,5), payment_status='unpaid', payment_method='Transfer',
            account=None, user=self.user)
        self.assertEqual(sale.items.count(), 2)
        dated.refresh_from_db()
        expired.refresh_from_db()
        self.assertEqual(dated.quantity_available, 0)
        self.assertEqual(expired.quantity_available, 5)

    def test_form_availability_uses_selected_release_date(self):
        good = self.goods[0]
        MarketStockLot.raw_objects.create(business=self.business, finished_good=good,
            received_date=date(2026,11,6), quantity_received=7, quantity_available=7, unit_cost=10)
        self.client.force_login(self.user)
        for day, expected in [('2026-11-05', '10'), ('2026-11-06', '17')]:
            response = self.client.get(reverse('market_stock_release'), {'availability':'1','stock_date':day})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['date'], day)
            self.assertEqual(Decimal(response.json()['products'][str(good.pk)]['available']), Decimal(expected))

    def test_multi_product_invoice_has_one_receivable_and_all_stock_movements(self):
        sale=self.release()
        self.assertEqual(Sale.raw_objects.filter(business=self.business).count(),1)
        self.assertEqual(sale.total,Decimal('800'))
        self.assertEqual(sale.items.count(),2)
        self.assertEqual(sale.transaction_type,'unpaid')
        self.assertEqual(MarketStockMovement.raw_objects.filter(sale=sale).count(),2)
        self.assertEqual(list(MarketStockLot.raw_objects.order_by('finished_good_id').values_list('quantity_available',flat=True)),[Decimal('8'),Decimal('7')])
        self.assertFalse(FinancialTransaction.raw_objects.filter(business=self.business).exists())

    def test_paid_multi_product_invoice_records_combined_payment_once(self):
        sale=self.release(paid=True)
        transactions=FinancialTransaction.raw_objects.filter(business=self.business,reference=f'SALE-{sale.pk}')
        self.assertEqual(transactions.count(),1)
        self.assertEqual(transactions.get().amount,Decimal('800'))

    def test_shared_summary_includes_all_release_products_and_one_invoice_balance(self):
        from .distribution_summary import distribution_summaries
        sale = self.release()
        request = RequestFactory().get('/inventory/market-stock/', {'date':'2026-11-05'})
        request.user, request.business = self.user, self.business
        summary = distribution_summaries(request)
        allocations = summary['distribution_destinations']
        self.assertEqual({row['product']:row['quantity'] for row in allocations},
                         {'Bread':Decimal('2'),'Cake':Decimal('3')})
        self.assertTrue(all(row['destination'] == self.customer.name for row in allocations))
        self.assertEqual(len(summary['distribution_settlement']['rows']), 1)
        self.assertEqual(summary['distribution_settlement']['rows'][0]['sale'], sale.pk)
        self.assertEqual(summary['distribution_settlement']['totals']['net_due'], Decimal('800'))
        for name in ['Bread', 'Cake', self.customer.name]:
            self.assertIn(name, summary['distribution_share_text'])

    def test_failed_later_product_rolls_back_entire_release(self):
        with self.assertRaises(ValidationError):
            self.release(paid=True,second_quantity=20)
        self.assertFalse(Sale.raw_objects.filter(business=self.business).exists())
        self.assertFalse(MarketStockMovement.raw_objects.filter(business=self.business).exists())
        self.assertFalse(FinancialTransaction.raw_objects.filter(business=self.business).exists())
        self.assertEqual(list(MarketStockLot.raw_objects.values_list('quantity_available',flat=True)),[Decimal('10'),Decimal('10')])
        self.goods[0].refresh_from_db()
        self.assertEqual(self.goods[0].total_delivered_to_customers,0)

    def test_formset_rejects_duplicate_products_and_missing_management_form(self):
        payload={'items-TOTAL_FORMS':'2','items-INITIAL_FORMS':'0'}
        for i in range(2):
            payload.update({f'items-{i}-finished_good':self.goods[0].pk,f'items-{i}-quantity':'1'})
        formset=MarketStockReleaseItemFormSet(payload,prefix='items',form_kwargs={'business':self.business})
        self.assertFalse(formset.is_valid())
        self.assertIn('one row per product',str(formset.non_form_errors()))
        self.assertFalse(MarketStockReleaseItemFormSet({},prefix='items',form_kwargs={'business':self.business}).is_valid())

    def test_release_page_submits_multiple_product_rows(self):
        self.client.force_login(self.user)
        payload={'date':'2026-11-05','customer':self.customer.pk,'payment_status':'unpaid',
                 'items-TOTAL_FORMS':'2','items-INITIAL_FORMS':'0'}
        for i,good in enumerate(self.goods):
            payload.update({f'items-{i}-finished_good':good.pk,f'items-{i}-quantity':'2'})
        response=self.client.post(reverse('market_stock_release'),payload)
        self.assertEqual(response.status_code,302)
        self.assertEqual(Sale.raw_objects.get(business=self.business).items.count(),2)

    def test_backlog_expiry_is_historical_but_live_expiry_uses_today(self):
        good=self.goods[0]
        lot=MarketStockLot.raw_objects.get(finished_good=good)
        lot.expiry_date=date(2026,11,1)
        lot.is_backlog_entry=True
        lot.save(update_fields=['expiry_date','is_backlog_entry'])
        with patch('inventory.models.timezone.localdate',return_value=date(2026,11,5)):
            self.assertTrue(lot.is_expired)
            self.assertFalse(lot.current_expiry_alert)
            self.assertEqual(good.expired_market_stock,0)
            lot.is_backlog_entry=False
            lot.save(update_fields=['is_backlog_entry'])
            self.assertTrue(lot.current_expiry_alert)
            self.assertEqual(good.expired_market_stock,10)
        lot.refresh_from_db()
        self.assertEqual(lot.expiry_date,date(2026,11,1))
