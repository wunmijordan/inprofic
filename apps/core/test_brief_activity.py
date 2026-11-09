from django.urls import reverse

from inventory.models import FinishedGoodChannelPrice, StockMovement
from inventory.services import record_distribution_return, release_market_stock_items, sell_market_stock_items, issue_product_stock
from sales.models import Customer, Sale
from .brief_views import _report
from .models import CashAccount, DailyBriefPreference
from .test_daily_brief import BriefBase, YESTERDAY


class BriefActivityTests(BriefBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.bread.selling_price = 10
        self.bread.save(update_fields=['selling_price'])
        FinishedGoodChannelPrice.objects.create(finished_good=self.bread, channel='distribution', price=10)
        self.account = CashAccount.objects.create(business=self.business, name='Till')

    def reports(self):
        for weekly in [False, True]:
            yield _report(self.business, {'date': str(YESTERDAY), 'view': 'weekly' if weekly else 'daily'}, user=self.admin)['text']

    def test_returns_are_nested_under_original_customer_and_show_all_recorded_destinations(self):
        self.sell(10, 10, paid=False, customer='Ada')
        original = Sale.objects.get(customer='Ada')
        record_distribution_return(business=self.business, sale_item=original.items.get(), quantity=6,
            date=YESTERDAY, condition='redistributable', reason='Unsold', user=self.admin)
        bola = Customer.objects.create(business=self.business, name='Bola')
        release_market_stock_items(business=self.business, items=[{'finished_good': self.bread, 'quantity': 3}],
            customer=bola, date=YESTERDAY, payment_status='unpaid', payment_method='', account=None, user=self.admin)
        sell_market_stock_items(business=self.business, items=[{'finished_good': self.bread, 'quantity': 2}],
            date=YESTERDAY, payment_method='Cash', account=self.account, user=self.admin)
        issue_product_stock(business=self.business, good=self.bread, quantity=1, date=YESTERDAY,
            stock_source='market_stock', reason='staff', recipient='Team', description='', user=self.admin)
        for text in self.reports():
            start = text.index('• Customer · Ada:')
            following = text.find('\n• Customer · ', start+1)
            ada = text[start:following if following >= 0 else text.index('\n\n', start)]
            self.assertIn('  ◦ Returned · Bread: 6 loaves', ada)
            self.assertIn('    ▪ Redistributed to Bola: 3 loaves', ada)
            self.assertIn('    ▪ Sold in Physical Store: 2 loaves', ada)
            self.assertIn('    ▪ Staff issue — Team: 1 loaf', ada)
            self.assertNotIn('\n• Returned · ', text)
            self.assertIn('· Physical Store', text)
            self.assertIn('· Distribution Order', text)
            self.assertIn('Staff and gifting', text)

    def test_online_and_store_sales_for_one_customer_keep_both_channels(self):
        self.sell(2, 10, customer='Ada')
        self.sell(3, 10, customer='Ada')
        sale = Sale.objects.order_by('-pk').first()
        sale.source = 'online_order'
        sale.save(update_fields=['source'])
        for text in self.reports():
            self.assertEqual(text.count('• Customer · Ada:'), 1)
            self.assertIn('Bread: 2 loaves · ₦20.00 · Physical Store', text)
            self.assertIn('Bread: 3 loaves · ₦30.00 · Online Order', text)

    def test_non_stock_production_and_excess_are_reported_and_reversed_batches_are_omitted(self):
        from production.models import Order, OrderItem, ProductionBatch
        order = Order.objects.create(business=self.business, date=YESTERDAY, status='completed',
            order_type='physical_store', production_destination='non_stock', non_stock_purpose='Staff meals')
        item = OrderItem.objects.create(order=order, finished_good=self.bread, piece_qty=5)
        batch = ProductionBatch.objects.create(business=self.business, order=order, order_item=item,
            finished_good=self.bread, production_date=YESTERDAY, batch_number='NON-SALES', produced_units=7, total_cost=7)
        for quantity, note in [(5,'Non-stock production (Staff meals) — batch NON-SALES'),
                                (2,'Excess production for Charity — batch NON-SALES')]:
            StockMovement.objects.create(business=self.business, finished_good=self.bread,
                business_date=YESTERDAY, quantity=quantity, unit_value=1, affects_stock=False,
                movement_type=StockMovement.FG_PRODUCTION, reference=batch.batch_number, note=note)
        for text in self.reports():
            self.assertIn('Non-sales production · Staff meals', text)
            self.assertIn('Bread: 5 loaves · Production cost: ₦5.00', text)
            self.assertIn('Non-sales production · Charity', text)
        batch.is_reversed = True
        batch.save(update_fields=['is_reversed'])
        for text in self.reports():
            self.assertNotIn('Non-sales production · Staff meals', text)

    def test_legacy_sales_selection_includes_new_non_sales_section_until_explicitly_saved(self):
        StockMovement.objects.create(business=self.business, finished_good=self.bread,
            business_date=YESTERDAY, quantity=-1, movement_type=StockMovement.ADJUSTMENT, note='Test')
        from inventory.models import StockAdjustment
        StockAdjustment.objects.create(business=self.business, finished_good=self.bread, date=YESTERDAY,
            quantity=-1, reason='staff', recipient='Team', description='Lunch')
        pref = DailyBriefPreference.objects.create(business=self.business, user=self.admin,
            report_sections={'daily': ['sales'], 'weekly': ['sales']})
        for text in self.reports():
            self.assertIn('Staff issue · Team', text)
        self.client.post(reverse('daily_brief'), {'action': 'report_sections', 'section': ['sales']})
        pref.refresh_from_db()
        self.assertIn('product_use', pref.report_sections['known_sections']['daily'])
        daily = _report(self.business, {'date': str(YESTERDAY)}, user=self.admin)['text']
        self.assertNotIn('Staff issue · Team', daily)
