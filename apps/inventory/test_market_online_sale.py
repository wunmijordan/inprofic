from decimal import Decimal
from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.utils import timezone
from accounts.models import CustomUser
from core.models import Business, CashAccount, FinancialTransaction
from sales.models import Sale, Customer, CustomerProductPrice
from .models import FinishedGood, FinishedGoodChannelPrice, MarketStockLot, MarketStockMovement, ProductPortionProfile
from .services import sell_market_stock_online
from .distribution_summary import distribution_summaries


class MarketOnlineSaleTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='Online Market Stock', slug='online-market')
        self.user = CustomUser.objects.create_superuser(username='online-market-admin', password='test')
        self.day = timezone.localdate()
        self.account = CashAccount.raw_objects.create(business=self.business, name='Bank')
        self.good = FinishedGood.raw_objects.create(business=self.business, name='Buns', unit='bun', stock=20, selling_price=100)
        ProductPortionProfile.raw_objects.create(business=self.business, finished_good=self.good,
            active=True, customer_unit='pack', base_quantity=4)
        FinishedGoodChannelPrice.objects.create(finished_good=self.good, channel='online', price=1500)
        FinishedGoodChannelPrice.objects.create(finished_good=self.good, channel='distribution', price=1100)
        self.lot = MarketStockLot.raw_objects.create(business=self.business, finished_good=self.good,
            source='production', received_date=self.day, quantity_received=40, quantity_available=40, unit_cost=50)
        MarketStockMovement.raw_objects.create(business=self.business, lot=self.lot, date=self.day,
            movement_type='production_in', quantity=40, balance_after=40)
        self.client.force_login(self.user)

    def sell(self, **changes):
        values = dict(business=self.business, items=[{'finished_good': self.good, 'quantity': Decimal('8')}],
            date=self.day, payment_status='paid', payment_method='Transfer', account=self.account,
            user=self.user, customer_name='Online Ada')
        values.update(changes)
        return sell_market_stock_online(**values)

    def test_online_sale_uses_online_pack_price_and_records_stock_and_cash_once(self):
        sale = self.sell()
        self.lot.refresh_from_db(); self.good.refresh_from_db()
        self.assertEqual((sale.source, sale.total, self.lot.quantity_available, self.good.stock), ('online_order', 3000, 32, 20))
        self.assertEqual(sale.items.get().price_unit_quantity, 4)
        self.assertEqual(FinancialTransaction.raw_objects.get().amount, 3000)
        request = RequestFactory().get('/inventory/market-stock/', {'date': str(self.day)})
        request.user, request.business = self.user, self.business
        summary = distribution_summaries(request)
        row = summary['distribution_rows'][0]
        self.assertEqual((row['online_sold'], row['distributed'], row['closing']), (8, 0, 32))
        self.assertIn('Online sales', summary['distribution_columns'])
        self.assertIn('Sold online: 2 packs', summary['distribution_share_text'])
        self.assertIn('Online', summary['distribution_share_text'])
        self.assertEqual(summary['distribution_settlement']['totals']['paid'], 3000)

    def test_unpaid_saved_customer_uses_online_customer_price_without_cash(self):
        customer = Customer.raw_objects.create(business=self.business, name='Ada')
        CustomerProductPrice.raw_objects.create(business=self.business, customer=customer,
            finished_good=self.good, channel='online', price=1300)
        sale = self.sell(customer=customer, payment_status='unpaid', account=None)
        self.assertEqual((sale.total, sale.transaction_type, sale.customer_master_id), (2600, 'unpaid', customer.pk))
        self.assertFalse(FinancialTransaction.raw_objects.exists())

    def test_online_form_converts_pack_quantity_and_is_reachable(self):
        url = reverse('market_stock_online_sale')
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Record Online sale')
        response = self.client.post(url, {'date': str(self.day), 'customer_name': 'Online buyer',
            'payment_status': 'unpaid', 'items-TOTAL_FORMS': 1, 'items-INITIAL_FORMS': 0,
            'items-0-finished_good': self.good.pk, 'items-0-quantity': '2'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Sale.raw_objects.get().total, 3000)
        self.assertContains(self.client.get(reverse('market_stock')), 'Sell online')
