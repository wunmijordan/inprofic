"""Executive reports keep rich text complete, images bounded and access scoped."""
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.urls import reverse

from accounts.models import UserBusiness, UserModulePermission
from inventory.models import RawMaterial, FinishedGood
from sales.models import Sale, SaleItem
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
            self.assertIn('Bread: 5 loaf', text)
            self.assertIn('reorder at 5 kg', text)
            self.assertNotIn('PRIVATE PRODUCT', text)
            self.assertIn('Current positions as of', text)
            self.assertIn('not historical closing balances', text)
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
            self.assertIn('Revenue (fully paid sales): ' + self.business.currency_symbol + '50.00', report['text'])
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
            for marker in ['Product stock', 'Profit and loss', 'Cash movement', 'Purchasing', 'Production']:
                self.assertIn(marker, text)
            self.assertIn('Open Executive dashboard', text)
            from .snapshot_images import render_report_snapshot
            with patch('core.snapshot_views.render_report_snapshot', wraps=render_report_snapshot) as render:
                response = self.client.get(reverse('daily_brief_snapshot'), params)
            self.assertEqual(response.status_code, 200)
            args = render.call_args.kwargs
            self.assertLessEqual(len(args['rows']), 8)
            self.assertEqual(args['max_rows'], 8)
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
