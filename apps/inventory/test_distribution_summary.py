from datetime import timedelta
from decimal import Decimal
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from assistant.test_quick_facts import ToolBase
from core.models import Business
from inventory.distribution_summary import distribution_summaries
from inventory.models import DistributionReturn, MarketStockLot, MarketStockMovement
from sales.models import Sale, SaleItem


class DistributionSummaryTests(ToolBase):
    def request(self):
        request = RequestFactory().get(reverse('market_stock'))
        request.business, request.user = self.biz, self.admin
        return request

    def test_only_recorded_returns_are_shared_and_other_tenants_are_excluded(self):
        sale = Sale.objects.create(business=self.biz, date=timezone.localdate(), source='distribution_order', customer='Ada')
        item = SaleItem.objects.create(sale=sale, finished_good=self.good, piece_qty=10, price=5)
        DistributionReturn.raw_objects.create(business=self.biz, sale_item=item, date=timezone.localdate(), quantity=3,
                                              condition=DistributionReturn.DAMAGED, reason='Damaged')
        self.owed(self.other, 'PRIVATE CUSTOMER', 20, 50)
        data = distribution_summaries(self.request())
        self.assertEqual(data['recorded_return_rows'][0]['quantity'], Decimal('3'))
        self.assertNotIn('PRIVATE CUSTOMER', data['returns_share_text'])
        self.assertIn('#distribution-returns', data['returns_share_text'])
        self.assertIn('#distribution-summary', data['distribution_share_text'])
        self.assertNotIn('up to', data['returns_share_text'].lower())
        self.assertNotIn('possible', data['returns_share_text'].lower())
        self.assertIn('Damaged', data['returns_share_text'])

    def test_snapshot_covers_received_distributed_destinations_and_remaining_stock(self):
        today = timezone.localdate()
        lot = MarketStockLot.objects.create(business=self.biz, finished_good=self.good, received_date=today,
                                           quantity_received=20, quantity_available=8)
        for kind, quantity, balance in [(MarketStockMovement.PRODUCTION_IN, 20, 20),
                                        (MarketStockMovement.RELEASE, -7, 13),
                                        (MarketStockMovement.TRANSFER_PHYSICAL, -5, 8)]:
            sale = Sale.objects.create(business=self.biz, date=today, source='distribution_order', customer='Ada') if kind == MarketStockMovement.RELEASE else None
            MarketStockMovement.objects.create(business=self.biz, lot=lot, date=today, movement_type=kind,
                                               quantity=quantity, balance_after=balance, sale=sale)
        data = distribution_summaries(self.request())
        row = data['distribution_rows'][0]
        self.assertEqual((row['received'], row['distributed'], row['physical'], row['available']),
                         (Decimal('20'), Decimal('7'), Decimal('5'), Decimal('8')))
        self.assertEqual({r['destination']: r['quantity'] for r in data['distribution_destinations']},
                         {'Ada': Decimal('7'), 'Physical Store': Decimal('5')})
        self.assertIn('Ada', data['distribution_share_text'])
        self.assertIn('Physical Store', data['distribution_share_text'])
        from unittest.mock import patch
        with patch('inventory.distribution_summary.render_distribution_snapshot', return_value=b'png') as render:
            self.client.get(reverse('market_stock_snapshot', args=['stock']))
        shared_products = render.call_args.kwargs['products']
        self.assertEqual(shared_products[0]['available'], 8)
        self.assertEqual(shared_products[0]['made'], 20)
        self.assertEqual({r['destination']:r['quantity'] for r in shared_products[0]['customers']}, {'Ada':7, 'Physical Store':5})

    def test_stock_only_vertical_uses_registered_stock_without_a_production_pool(self):
        self.biz.vertical = Business.VERTICAL_WHOLESALE
        self.biz.save()
        data = distribution_summaries(self.request())
        self.assertIn('Wholesale', data['distribution_title'])
        self.assertNotIn('Market Stock', data['distribution_share_text'])
        self.assertEqual(data['distribution_rows'][0]['available'], self.good.stock)
        self.assertIsNone(data['distribution_rows'][0]['expired'])

    def test_purchased_distribution_uses_sales_and_stock_in_registered_units(self):
        self.biz.vertical = Business.VERTICAL_RETAIL
        self.biz.save()
        sale = Sale.objects.create(business=self.biz, date=timezone.localdate(), source='distribution_order', customer='Retail partner')
        SaleItem.objects.create(sale=sale, finished_good=self.good, piece_qty=4, price=5)
        data = distribution_summaries(self.request())
        row = data['distribution_rows'][0]
        self.assertEqual(row['distributed'], Decimal('4'))
        self.assertEqual(row['available'], self.good.stock)
        self.assertEqual(data['distribution_destinations'][0]['destination'], 'Retail partner')
        self.assertNotIn('Physical Store', data['distribution_share_text'])
        self.assertNotIn('Market Stock', data['distribution_share_text'])

    def test_market_stock_page_renders_for_production_and_purchased_stock(self):
        for vertical in (Business.VERTICAL_BAKERY, Business.VERTICAL_WHOLESALE, Business.VERTICAL_RETAIL):
            self.biz.vertical = vertical
            self.biz.save()
            response = self.client.get(reverse('market_stock'))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'id="distribution-summary"')
            self.assertContains(response, 'id="distribution-returns"')
            self.assertNotContains(response, 'Possible returns')
            self.assertContains(response, 'data-ui-snapshot-menu', count=2)

    def test_market_stock_page_enforces_inventory_permission(self):
        from accounts.models import UserBusiness, UserModulePermission
        from django.core.cache import cache
        membership = UserBusiness.objects.get(user=self.admin, business=self.biz)
        UserModulePermission.objects.create(membership=membership, module='inventory', can_view=False, can_edit=False)
        cache.clear()
        self.assertEqual(self.client.get(reverse('market_stock')).status_code, 403)

    def test_stock_and_return_snapshots_are_private_paper_exports(self):
        import io
        from PIL import Image
        from core.snapshot_images import PAPER, ACCENT
        for kind in ['stock','returns']:
            response = self.client.get(reverse('market_stock_snapshot', args=[kind]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'image/png')
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            image = Image.open(io.BytesIO(response.content))
            self.assertEqual(image.getpixel((5,30)), PAPER)
            self.assertEqual(image.getpixel((5,5)), ACCENT)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('market_stock_snapshot',args=['stock'])).status_code, 302)

    def test_settlement_uses_selling_values_returns_and_payments_without_changing_invoices(self):
        from sales.models import CustomerPayment
        self.biz.vertical = Business.VERTICAL_WHOLESALE
        self.biz.save()
        today = timezone.localdate()
        sale = Sale.objects.create(business=self.biz, date=today, source='distribution_order',
                                   customer='Ada', transaction_type='partial')
        item = SaleItem.objects.create(sale=sale, finished_good=self.good, piece_qty=10, price=12, discount=2)
        DistributionReturn.raw_objects.create(business=self.biz, sale_item=item, date=today, quantity=3,
                                              condition=DistributionReturn.DAMAGED, unit_value=1)
        CustomerPayment.raw_objects.create(business=self.biz, sale=sale, date=today, customer='Ada', amount=50)
        data = distribution_summaries(self.request())
        totals = data['distribution_settlement']['totals']
        self.assertEqual(totals, dict(gross=100, returned=30, net=70, paid=50,
                                     outstanding=50, net_due=20, refund_review=0))
        self.assertIn('Net distribution value: ', data['distribution_share_text'])
        self.assertIn('Ada', data['distribution_share_text'])
        sale.refresh_from_db()
        self.assertEqual(sale.total, 100)
        self.assertEqual(sale.transaction_type, 'partial')
        sale.transaction_type = 'paid'
        sale.save()
        totals = distribution_summaries(self.request())['distribution_settlement']['totals']
        self.assertEqual((totals['paid'], totals['outstanding'], totals['refund_review']), (100, 0, 30))

    def test_production_review_only_includes_market_pool_releases_once(self):
        today = timezone.localdate()
        sale = Sale.objects.create(business=self.biz, date=today, source='distribution_order', customer='Ada')
        SaleItem.objects.create(sale=sale, finished_good=self.good, piece_qty=10, price=5)
        # Direct production distributions are outside this stock pool.
        self.assertEqual(distribution_summaries(self.request())['distribution_settlement']['totals']['gross'], 0)
        lot = MarketStockLot.objects.create(business=self.biz, finished_good=self.good, received_date=today,
                                           quantity_received=10, quantity_available=0)
        for qty in [-4, -6]:
            MarketStockMovement.objects.create(business=self.biz, lot=lot, sale=sale, date=today,
                                               movement_type=MarketStockMovement.RELEASE, quantity=qty, balance_after=0)
        self.assertEqual(distribution_summaries(self.request())['distribution_settlement']['totals']['gross'], 50)

    def test_finance_denial_hides_settlement_from_page_text_and_snapshot(self):
        from accounts.models import UserBusiness, UserModulePermission
        from django.core.cache import cache
        from unittest.mock import patch
        membership = UserBusiness.objects.get(user=self.admin, business=self.biz)
        UserModulePermission.objects.create(membership=membership, module='finance', can_view=False, can_edit=False)
        cache.clear()
        data = distribution_summaries(self.request())
        self.assertIsNone(data['distribution_settlement'])
        self.assertNotIn('*Money summary*', data['distribution_share_text'])
        self.assertNotIn('Invoice outstanding:', data['distribution_share_text'])
        response = self.client.get(reverse('market_stock'))
        self.assertNotContains(response, 'id="distribution-settlement-heading"')
        with patch('inventory.distribution_summary.render_distribution_snapshot', return_value=b'png') as render:
            self.client.get(reverse('market_stock_snapshot', args=['stock']))
        self.assertEqual(render.call_args.kwargs['cards'], [])

    def test_backlog_invoice_includes_later_returns_and_payments_at_commercial_price(self):
        from core.test_ledger import scoped, BACKLOG_DAY, LIVE_DAY
        from sales.models import CustomerPayment
        self.biz.vertical = Business.VERTICAL_WHOLESALE
        self.biz.save()
        sale = Sale.raw_objects.create(business=self.biz, date=BACKLOG_DAY, source='distribution_order',
                                       customer='Ada', transaction_type='partial')
        item = SaleItem.objects.create(sale=sale, finished_good=self.good, piece_qty=10, price=100,
                                       commercial_quantity=2, commercial_unit_price=30)
        DistributionReturn.raw_objects.create(business=self.biz, sale_item=item, date=LIVE_DAY, quantity=2,
                                              condition=DistributionReturn.DAMAGED)
        CustomerPayment.raw_objects.create(business=self.biz, sale=sale, date=LIVE_DAY, customer='Ada', amount=40)
        with scoped(self.biz, 'backlog'):
            request = self.request()
            request.GET = request.GET.copy()
            request.GET['date'] = str(BACKLOG_DAY)
            totals = distribution_summaries(request)['distribution_settlement']['totals']
            self.assertEqual((totals['gross'], totals['returned'], totals['paid'], totals['net_due']), (60, 12, 40, 8))
        with scoped(self.biz, 'live'):
            self.assertEqual(distribution_summaries(self.request())['distribution_settlement']['rows'], [])

    def test_selected_date_has_multiple_customers_and_excludes_other_dates(self):
        today = timezone.localdate()
        lot = MarketStockLot.objects.create(business=self.biz, finished_good=self.good, received_date=today,
                                           quantity_received=100, quantity_available=70)
        MarketStockMovement.objects.create(business=self.biz, lot=lot, date=today,
            movement_type=MarketStockMovement.PRODUCTION_IN, quantity=100, balance_after=100)
        for customer, amount, when in [('Ada',10,today), ('Bola',15,today), ('Earlier customer',5,today-timedelta(days=1))]:
            sale = Sale.objects.create(business=self.biz, date=when, customer=customer, source='distribution_order')
            SaleItem.objects.create(sale=sale,finished_good=self.good,piece_qty=amount,price=10)
            MarketStockMovement.objects.create(business=self.biz,lot=lot,date=when,sale=sale,
                movement_type=MarketStockMovement.RELEASE,quantity=-amount,balance_after=70)
        data = distribution_summaries(self.request())
        self.assertEqual(data['distribution_rows'][0]['made'], 100)
        self.assertEqual(data['distribution_rows'][0]['distributed'], 25)
        self.assertEqual({row['destination'] for row in data['distribution_destinations']}, {'Ada','Bola'})
        self.assertNotIn('Earlier customer', data['distribution_share_text'])
        self.assertEqual(data['distribution_settlement']['totals']['gross'], 250)
        self.assertIn('date='+str(today),data['distribution_image_url'])
        self.assertEqual(self.client.get(reverse('market_stock'),{'date':'not-a-date'}).status_code,404)

    def test_snapshot_stays_bounded_with_many_products_and_customers(self):
        import io
        from PIL import Image
        from core.snapshot_images import render_distribution_snapshot
        products = [{'product':f'Product {i}', 'unit':'kg', 'made':Decimal('100'), 'distributed':Decimal('50'),
                     'available':Decimal('50'), 'customers':[{'destination':f'Customer {j}','quantity':Decimal('1')} for j in range(30)]}
                    for i in range(30)]
        image = Image.open(io.BytesIO(render_distribution_snapshot(business=self.biz,title='Distribution',
            day=timezone.localdate(),products=products,cards=[],url='http://testserver/inventory/market-stock/')))
        self.assertEqual(image.width,1080)
        self.assertLess(image.height,2200)

    def test_shared_text_separates_totals_products_and_customer_invoice_details(self):
        from sales.models import CustomerPayment
        self.biz.vertical = Business.VERTICAL_WHOLESALE
        self.biz.save()
        today = timezone.localdate()
        for customer, quantity in [('Ada',10),('Bola',5)]:
            sale = Sale.objects.create(business=self.biz,date=today,customer=customer,source='distribution_order',transaction_type='partial')
            SaleItem.objects.create(sale=sale,finished_good=self.good,piece_qty=quantity,price=10)
            CustomerPayment.raw_objects.create(business=self.biz,sale=sale,date=today,customer=customer,
                                               amount=10,payment_method='Transfer')
        text = distribution_summaries(self.request())['distribution_share_text']
        sections = ['*Money summary*','*Stock by product*','*Customer breakdown*','*Reading this report*','*Open complete distribution summary*']
        offsets = [text.index(section) for section in sections]
        self.assertEqual(offsets,sorted(offsets))
        self.assertIn('• Distributed invoice value: '+self.biz.currency_symbol+'150.00\n',text)
        self.assertIn('• Invoice outstanding: '+self.biz.currency_symbol+'130.00\n',text)
        self.assertIn('*Ada*\n• Bread: 10 loaf\n\nInvoice #',text)
        self.assertIn('*Bola*\n• Bread: 5 loaf\n\nInvoice #',text)
        self.assertEqual(text.count('*Ada*'),1)
        self.assertIn('Payment methods:\n• Transfer: '+self.biz.currency_symbol+'10.00\n',text)
        self.assertTrue(text.splitlines()[-1].startswith('http://testserver/inventory/market-stock/?date='))
        self.assertTrue(text.endswith('#distribution-summary'))
