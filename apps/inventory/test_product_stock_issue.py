from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from core.accounting import profit_and_loss
from core.brief_views import _report
from core.models import Business, FinancialTransaction, AuditLog
from sales.models import Sale, CustomerPayment
from .models import FinishedGood, RawMaterial, RecipeItem, StockAdjustment, StockMovement, MarketStockLot, MarketStockMovement
from .services import issue_product_stock


class ProductStockIssueTests(TestCase):
    def setUp(self):
        self.day = timezone.localdate()
        self.business = Business.objects.create(name='Product issues', slug='product-issues')
        roles = seed_business_roles(self.business)
        self.user = CustomUser.objects.create_user(username='issue-admin', password='test')
        UserBusiness.objects.create(user=self.user, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.good = FinishedGood.objects.create(business=self.business, name='Bread', unit='loaf', stock=10, units_per_batch=1)
        material = RawMaterial.objects.create(business=self.business, name='Flour', usage_unit='kg', cost_per_unit=2)
        RecipeItem.objects.create(finished_good=self.good, raw_material=material, qty_per_batch=1)
        self.lot = self.make_lot(5, 2, expiry=self.day+timedelta(days=1))
        self.other_lot = self.make_lot(10, 3)
        self.client.force_login(self.user)

    def make_lot(self, quantity, cost, expiry=None):
        lot = MarketStockLot.objects.create(business=self.business, finished_good=self.good,
            received_date=self.day, quantity_received=quantity, quantity_available=quantity, unit_cost=cost, expiry_date=expiry)
        MarketStockMovement.objects.create(business=self.business, lot=lot, date=self.day,
            movement_type=MarketStockMovement.PRODUCTION_IN, quantity=quantity, balance_after=quantity, unit_value=cost)
        return lot

    def issue(self, **changes):
        data = dict(business=self.business, good=self.good, quantity=Decimal('7'), date=self.day,
            stock_source='market_stock', reason='staff', recipient='Bakery team', description='Lunch', user=self.user)
        data.update(changes)
        return issue_product_stock(**data)

    def test_market_issue_uses_fefo_and_records_cost_once_without_cash_or_sale(self):
        issue = self.issue()
        self.lot.refresh_from_db(); self.other_lot.refresh_from_db(); self.good.refresh_from_db()
        self.assertEqual((self.lot.quantity_available, self.other_lot.quantity_available, self.good.stock), (0, 8, 10))
        self.assertEqual(issue.quantity, -7)
        self.assertAlmostEqual(-issue.value, Decimal('16'), places=5)
        self.assertEqual(MarketStockMovement.objects.filter(movement_type='non_stock').count(), 2)
        self.assertEqual(StockMovement.objects.filter(reference=f'ADJ-{issue.pk}', affects_stock=False).count(), 2)
        pl = profit_and_loss(self.business, self.day, self.day)
        self.assertEqual((pl['write_offs'], pl['product_use'], pl['total_costs']), (16, 16, 16))
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(CustomerPayment.objects.exists())
        self.assertFalse(FinancialTransaction.objects.exists())
        self.assertTrue(AuditLog.objects.filter(business=self.business, action='issue_product_stock').exists())

    def test_physical_issue_uses_shelf_stock_and_preserves_market_stock(self):
        self.good.transferred_market_stock = 5
        self.good.save(update_fields=['transferred_market_stock'])
        issue = self.issue(stock_source='physical_store', quantity=3, reason='charity', recipient='Community kitchen')
        self.good.refresh_from_db(); self.lot.refresh_from_db()
        self.assertEqual((self.good.stock, self.good.transferred_market_stock, self.lot.quantity_available), (7, 2, 5))
        self.assertEqual(-issue.value, 6)
        self.assertTrue(StockMovement.objects.filter(reference=f'ADJ-{issue.pk}', affects_stock=True, quantity=-3).exists())

    def test_over_issue_and_expired_stock_are_rejected_without_partial_records(self):
        self.other_lot.expiry_date = self.day-timedelta(days=1)
        self.other_lot.save(update_fields=['expiry_date'])
        for source, quantity in [('market_stock', 6), ('physical_store', 11)]:
            with self.assertRaises(ValidationError):
                self.issue(stock_source=source, quantity=quantity)
        self.assertFalse(StockAdjustment.objects.exists())
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_available, 5)

    def test_tenant_and_validation_guards(self):
        foreign = Business.objects.create(name='Other', slug='other-issue')
        other_good = FinishedGood.objects.create(business=foreign, name='Private', stock=100)
        for data in [{'good': other_good}, {'recipient': ''}, {'quantity': Decimal('0.001')},
                     {'date': self.day+timedelta(days=1)}, {'stock_source': 'invalid'}]:
            with self.assertRaises(ValidationError):
                self.issue(**data)
        self.assertFalse(StockAdjustment.objects.exists())

    def test_form_is_scoped_validates_and_redirects_after_issue(self):
        url = reverse('market_stock_issue')
        page = self.client.get(url)
        self.assertContains(page, '15 loaves available')
        response = self.client.post(url, {'date': self.day, 'finished_good': self.good.pk,
            'quantity': '99', 'reason': 'staff', 'recipient': 'Team', 'description': ''})
        self.assertContains(response, 'Only 15 loaves')
        response = self.client.post(url, {'date': self.day, 'finished_good': self.good.pk,
            'quantity': '3', 'reason': 'staff', 'recipient': 'Team', 'description': ''})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StockAdjustment.objects.get().quantity, -3)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_shared_brief_and_distribution_include_recipient_cost_and_closing(self):
        from .distribution_summary import distribution_summaries
        self.issue()
        page = self.client.get(reverse('market_stock'), {'date': self.day})
        self.assertContains(page, 'Bakery team')
        self.assertContains(page, 'Staff / gifting inventory cost')
        data = distribution_summaries(page.wsgi_request)
        row = data['distribution_rows'][0]
        self.assertEqual((row['received'], row['issued'], row['closing']), (15, 7, 8))
        self.assertEqual(row['other_change'], 0)
        self.assertIn('Staff issue · Bakery team', data['distribution_share_text'])
        self.assertIn('7 loaves', data['distribution_share_text'])
        for weekly in [False, True]:
            report = _report(self.business, {'date': str(self.day), 'view': 'weekly' if weekly else 'daily'}, user=self.user)
            self.assertIn('Staff and gifting', report['text'])
            self.assertIn('Bakery team', report['text'])
            self.assertIn('Inventory cost: ₦16.00', report['text'])
            self.assertIn('Staff and gifting', [row['label'] for row in report['brief']['snapshot_highlights']])
        with patch('inventory.distribution_summary.render_distribution_snapshot', return_value=b'png') as renderer:
            self.client.get(reverse('market_stock_snapshot', args=['stock']), {'date': self.day})
        self.assertIn('Staff / gifting cost', [card['label'] for card in renderer.call_args.kwargs['cards']])

    def test_issue_cost_is_separately_identified_in_finance(self):
        self.issue(stock_source='physical_store', quantity=3)
        response = self.client.get(reverse('finance_profit_loss'), {'start': self.day, 'end': self.day})
        self.assertContains(response, 'Staff / gifting inventory cost')

    def test_failure_mid_issue_rolls_back_stock_and_adjustment(self):
        with patch('inventory.services.record_finished_good_movement', side_effect=RuntimeError('Interrupted')):
            with self.assertRaises(RuntimeError):
                self.issue()
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_available, 5)
        self.assertFalse(StockAdjustment.objects.exists())
        self.assertFalse(MarketStockMovement.objects.filter(movement_type='non_stock').exists())

    def test_books_record_inventory_cost_once_and_balance(self):
        from core.books import rebuild_books
        from core.models import JournalEntry
        issue = self.issue(stock_source='physical_store', quantity=3)
        rebuild_books(self.business)
        entry = JournalEntry.raw_objects.get(business=self.business, source_type='adjustment', source_id=str(issue.pk))
        lines = list(entry.lines.select_related('account'))
        self.assertEqual(sum(line.debit for line in lines), 6)
        self.assertEqual(sum(line.credit for line in lines), 6)
        self.assertEqual({line.account.system_key for line in lines}, {'inventory', 'adjustments'})

    def test_inventory_viewer_cannot_record_issue(self):
        from accounts.models import UserModulePermission
        from django.core.cache import cache
        member = UserBusiness.objects.get(user=self.user, business=self.business)
        UserModulePermission.objects.create(membership=member, module='inventory', can_view=True, can_edit=False)
        cache.clear()
        self.assertEqual(self.client.post(reverse('market_stock_issue'), {
            'date': self.day, 'finished_good': self.good.pk, 'quantity': 1,
            'reason': 'staff', 'recipient': 'Team'}).status_code, 403)
        self.assertFalse(StockAdjustment.objects.exists())

    def test_historical_issue_does_not_reduce_live_lot_or_shelf_balances(self):
        from core.context import set_ledger_context
        from core.models import LedgerCutover
        yesterday = self.day - timedelta(days=1)
        LedgerCutover.raw_objects.create(business=self.business, cutover_date=self.day)
        MarketStockLot.raw_objects.filter(business=self.business).update(received_date=yesterday)
        MarketStockMovement.raw_objects.filter(business=self.business).update(date=yesterday)
        try:
            set_ledger_context('backlog', self.day, 'open')
            self.issue(date=yesterday)
        finally:
            set_ledger_context()
        self.lot.refresh_from_db(); self.other_lot.refresh_from_db(); self.good.refresh_from_db()
        self.assertEqual((self.lot.quantity_available, self.other_lot.quantity_available, self.good.stock), (5, 10, 10))
        self.assertEqual(StockAdjustment.raw_objects.get().date, yesterday)


    def test_inventory_lists_issue_quantities_and_keeps_action_outside_rows(self):
        self.issue(quantity=2)
        self.issue(stock_source='physical_store', quantity=3, reason='charity')
        reversed_issue = self.issue(quantity=1)
        reversed_issue.reversed = True
        reversed_issue.save(update_fields=['reversed'])
        response = self.client.get(reverse('inventory'))
        self.assertEqual(response.status_code, 200)
        good = next(g for g in response.context['finished_goods'] if g.pk == self.good.pk)
        self.assertEqual(good.staff_gifting_quantity, 5)
        self.assertContains(response, reverse('physical_stock_issue'), count=1)
        self.assertNotContains(response, f'?product={self.good.pk}')
