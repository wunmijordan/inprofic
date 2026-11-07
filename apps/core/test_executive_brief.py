"""Executive reports keep rich text complete, images bounded and access scoped."""
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.urls import reverse

from accounts.models import UserBusiness, UserModulePermission
from inventory.models import RawMaterial, FinishedGood
from sales.models import CustomerPayment, Sale, SaleItem
from .accounting import profit_and_loss
from .brief_views import _report
from .models import Business
from .test_daily_brief import BriefBase, TODAY, YESTERDAY


class ExecutiveReportTests(BriefBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def report(self, weekly=False):
        return _report(self.business, {'view': 'weekly' if weekly else 'daily', 'date': str(YESTERDAY)}, user=self.admin)

    def test_daily_and_weekly_include_complete_material_and_product_positions(self):
        for i in range(24):
            RawMaterial.objects.create(business=self.business, name=f'Material {i:02}', stock=Decimal('2'),
                                       usage_unit='kg', reorder_level=Decimal('5'))
        private = Business.objects.create(name='PRIVATE TENANT', slug='private-brief')
        FinishedGood.objects.create(business=private, name='PRIVATE PRODUCT', stock=99)
        for weekly in [False, True]:
            report = self.report(weekly)
            text = report['text']
            self.assertIn('Material 23: 2 kg', text)
            self.assertIn('Bread: 5 loaves', text)
            self.assertNotIn('reorder at', text)
            self.assertNotIn('PRIVATE PRODUCT', text)
            self.assertIn('Stock and outstanding balances as of', text)
            self.assertIn('current position', text)
            self.assertLessEqual(len(report['brief']['snapshot_highlights']), 8)

    def test_daily_and_weekly_profit_matches_finance_and_separates_sales_from_paid_revenue(self):
        self.sell(10, 5)
        self.sell(4, 5, paid=False, customer='Credit customer')
        for weekly in [False, True]:
            report = self.report(weekly)
            brief = report['brief']
            expected = profit_and_loss(self.business, brief['report_start'], brief['report_through'])
            self.assertEqual(brief['executive_pl'], expected)
            self.assertIn('Sales: ' + self.business.currency_symbol + '70.00', report['text'])
            self.assertIn('Paid-sales revenue: ' + self.business.currency_symbol + '50.00', report['text'])
            self.assertIn('Credit customer', report['text'])
            self.assertIn('Cost of goods:', report['text'])
            self.assertIn('Net profit / loss:', report['text'])

    def test_complete_text_does_not_inherit_finance_screen_detail_cap(self):
        for i in range(32):
            self.sell(1, 5, paid=False, customer=f'Debtor {i:02}')
        text = self.report()['text']
        self.assertEqual(text.count('Customer balance · Debtor'), 32)
        from .finance_views import _finance_open_items
        capped = _finance_open_items(self.business)
        complete = _finance_open_items(self.business, detail_limit=None)
        self.assertEqual(len(capped['outstanding_sales']), 30)
        self.assertEqual(len(complete['outstanding_sales']), 32)
        self.assertEqual(capped['receivables'], complete['receivables'])

    def test_received_purchases_group_products_under_vendor_totals(self):
        from procurement.models import PurchaseOrder, PurchaseOrderItem
        flour = RawMaterial.objects.create(business=self.business, name='Flour',
            purchase_unit='bag', usage_unit='kg')
        for vendor, quantity, cost in [('Vendor A', 1, 100), ('Vendor A', 2, 100), ('Vendor B', 4, 80)]:
            po = PurchaseOrder.objects.create(business=self.business, date=YESTERDAY,
                supplier=vendor, status='received', received_date=YESTERDAY)
            PurchaseOrderItem.objects.create(purchase_order=po, raw_material=flour, qty=quantity, unit_cost=cost)
        symbol = self.business.currency_symbol
        for weekly in [False, True]:
            text = self.report(weekly)['text']
            self.assertIn(f'Received purchases: {symbol}620.00', text)
            self.assertIn(f'• Received from Vendor A: {symbol}300.00\n  ◦ Flour: 3 bags · {symbol}300.00', text)
            self.assertIn(f'• Received from Vendor B: {symbol}320.00\n  ◦ Flour: 4 bags · {symbol}320.00', text)

    def test_customer_sales_show_current_payment_status_in_daily_and_weekly_text(self):
        self.sell(10, 5, customer='Paid customer')
        self.sell(10, 5, paid=False, customer='Partial customer')
        self.sell(10, 5, paid=False, customer='Unpaid customer')
        partial = Sale.objects.get(customer='Partial customer')
        # A later receipt must count even when the stored invoice status is stale.
        CustomerPayment.objects.create(business=self.business, sale=partial,
            customer=partial.customer, date=TODAY, amount=20)
        symbol = self.business.currency_symbol
        for weekly in [False, True]:
            text = self.report(weekly)['text']
            self.assertIn(f'Customer · Paid customer: 1 sale · {symbol}50.00 · Fully paid', text)
            self.assertIn(f'• Customer · Partial customer: 1 sale · {symbol}50.00 · Partially paid · Balance: {symbol}30.00', text)
            self.assertIn(f'• Customer · Unpaid customer: 1 sale · {symbol}50.00 · Unpaid · Balance: {symbol}50.00', text)
            self.assertNotIn('Distribution · ', text)
            self.assertIn(f'Unpaid · Balance: {symbol}50.00\n  ◦ Bread: 10 loaves · {symbol}50.00', text)

    def test_customer_status_combines_channels_and_counts_return_credit(self):
        from inventory.models import DistributionReturn

        self.sell(10, 5, customer='Ada')
        self.sell(10, 5, paid=False, customer='Ada')
        sale = Sale.objects.get(source='distribution_order')
        CustomerPayment.objects.create(business=self.business, sale=sale,
            customer='Ada', date=TODAY, amount=20)
        symbol = self.business.currency_symbol
        text = self.report()['text']
        self.assertIn(f'Customer · Ada: 2 sales · {symbol}100.00 · Partially paid · Balance: {symbol}30.00', text)
        self.assertNotIn('Distribution · Ada', text)
        self.assertIn(f'  ◦ Bread: 20 loaves · {symbol}100.00', text)
        DistributionReturn.raw_objects.create(business=self.business, sale_item=sale.items.get(),
            date=TODAY, quantity=6, condition=DistributionReturn.REDISTRIBUTABLE, unit_value=1)
        text = self.report()['text']
        self.assertIn(f'Customer · Ada: 2 sales · {symbol}70.00 · Fully paid', text)
        self.assertIn(f'  ◦ Bread: 14 loaves · {symbol}70.00', text)
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sale).amount, 20)

    def test_customer_settlement_uses_lifetime_tenant_scoped_receipts(self):
        from . import ledger
        from .models import LedgerCutover

        self.sell(10, 5, paid=False, customer='Ada')
        sale = Sale.objects.get(customer='Ada')
        LedgerCutover.raw_objects.create(business=self.business, cutover_date=TODAY)
        CustomerPayment.raw_objects.create(business=self.business, sale=sale,
            customer='Ada', date=TODAY, amount=15)
        private = Business.objects.create(name='Other business', slug='other-payment-brief')
        CustomerPayment.raw_objects.create(business=private, sale=sale,
            customer='Ada', date=TODAY, amount=35)
        with patch.object(ledger, 'current_scope', return_value='backlog'):
            text = self.report()['text']
        self.assertIn(f'Customer · Ada: 1 sale · {self.business.currency_symbol}50.00 · Partially paid · Balance: {self.business.currency_symbol}35.00', text)

    def test_denied_sections_are_absent_from_page_text_and_image(self):
        member = UserBusiness.objects.get(user=self.admin, business=self.business)
        for module in ['finance', 'inventory', 'production', 'procurement']:
            UserModulePermission.objects.create(membership=member, module=module, can_view=False, can_edit=False)
        cache.clear()
        for weekly in [False, True]:
            report = self.report(weekly)
            text = report['text']
            self.assertNotIn('Cash on hand:', text)
            self.assertNotIn('Net profit', text)
            self.assertNotIn('Bread: 5', text)
            self.assertNotIn('awaiting approval', text)
            self.assertNotIn('Material stock', text)
            labels = [r['label'] for r in report['brief']['snapshot_highlights']]
            self.assertNotIn('Payables', labels)
            self.assertNotIn('Product stock alerts', labels)
        page = self.client.get(reverse('daily_brief'), {'view': 'weekly'})
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'brief-kpi-value">')

    def test_snapshot_is_editorial_summary_while_shared_payload_is_full_text(self):
        for weekly in [False, True]:
            params = {'view': 'weekly' if weekly else 'daily', 'date': str(YESTERDAY)}
            page = self.client.get(reverse('daily_brief'), params)
            self.assertEqual(page.status_code, 200)
            text = page.context['share_text']
            for marker in ['Product stock']:
                self.assertIn(marker, text)
            self.assertIn('Open Executive dashboard', text)
            from .snapshot_images import render_report_snapshot
            with patch('core.snapshot_views.render_report_snapshot', wraps=render_report_snapshot) as render:
                response = self.client.get(reverse('daily_brief_snapshot'), params)
            self.assertEqual(response.status_code, 200)
            args = render.call_args.kwargs
            self.assertLessEqual(len(args['rows']), 8)
            self.assertEqual(args['max_rows'], 8)
            self.assertEqual(args['scope'], '')
            import io
            from PIL import Image
            image = Image.open(io.BytesIO(response.content))
            self.assertEqual(image.width, 1080)
            self.assertLessEqual(image.height, 1800)

    def test_purchased_stock_vertical_omits_production_and_market_pool(self):
        self.business.vertical = Business.VERTICAL_WHOLESALE
        self.business.save()
        for weekly in [False, True]:
            text = self.report(weekly)['text']
            self.assertIn('Product stock', text)
            self.assertNotIn('Production · selected period', text)
            self.assertNotIn('Market Stock shelf life', text)
            self.assertNotIn('recipe this month', text)

    def test_stock_badges_precede_items_and_unconfigured_thresholds_are_omitted(self):
        for name, stock, reorder in [('OK material', 20, 10), ('Warning material', 12, 10),
                                      ('Low material', 5, 10), ('Empty material', 0, 10),
                                      ('No threshold', 30, 0)]:
            RawMaterial.objects.create(business=self.business, name=name, stock=stock, reorder_level=reorder, usage_unit='kg')
        text = self.report()['text']
        for badge, name in [('🟢 OK', 'OK material'), ('🟡 WARNING', 'Warning material'),
                            ('🔴 LOW', 'Low material'), ('⚪ OUT OF STOCK', 'Empty material')]:
            self.assertIn(f'{badge} · {name}:', text)
        self.assertIn('🟢 OK · No threshold: 30 kg', text)
        self.assertNotIn('not configured', text)
        self.assertNotIn('Ledger scope:', text)
        self.assertNotIn('Ledger controls', text)

    def test_configured_ledger_scope_remains_explicit_in_shared_reports(self):
        from .models import LedgerCutover
        from . import ledger
        LedgerCutover.raw_objects.create(business=self.business, cutover_date=TODAY)
        with patch.object(ledger, 'current_scope', return_value='all'):
            text = self.report()['text']
        self.assertIn('Combined ledger', text)
