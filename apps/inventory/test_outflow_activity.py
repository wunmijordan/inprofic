from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from core.models import Business, FinancialTransaction, AuditLog
from sales.models import CustomerPayment
from .models import StockAdjustment, MarketStockMovement, FinishedGoodChannelPrice
from .services import issue_product_stock, sell_market_stock_items, sell_market_stock_online, transfer_market_stock_to_physical
from .outflow_corrections import correct_product_issue
from .release_corrections import correct_market_release
from . import test_release_corrections as release_tests


class OutflowActivityTests(TestCase):
    setUp = release_tests.ReleaseCorrectionTests.setUp
    release = release_tests.ReleaseCorrectionTests.release

    def document(self, kind, **query):
        return self.client.get(reverse('market_outflow_document', args=[kind]), {
            'start': self.day, 'end': self.day, **query})

    def issue(self, quantity=5):
        return issue_product_stock(business=self.business, good=self.good, quantity=quantity, date=self.day,
            stock_source='market_stock', reason='staff', recipient='Bakery team', description='Lunch', user=self.user)

    def correct_issue(self, issue, quantity=3, **changes):
        data = dict(business=self.business, issue_id=issue.pk, revision=str(issue.updated_at),
            reason='Correct the quantity', good=self.good, quantity=quantity, purpose='charity',
            recipient='Community kitchen', description='Donation', user=self.user)
        data.update(changes)
        return correct_product_issue(**data)

    def test_customer_document_matches_period_and_excludes_later_receipts(self):
        sale = self.release(quantity=2)
        CustomerPayment.raw_objects.create(business=self.business, sale=sale, date=self.day,
            customer=self.customer.name, customer_master=self.customer, amount=50, payment_method='Cash')
        CustomerPayment.raw_objects.create(business=self.business, sale=sale, date=self.day+timedelta(days=1),
            customer=self.customer.name, customer_master=self.customer, amount=150, payment_method='Cash')
        sale.transaction_type = 'paid'
        sale.save()
        url = reverse('customer_activity_invoice', args=[self.customer.pk])
        page = self.client.get(url, {'start': self.day, 'end': self.day})
        self.assertEqual(page.status_code, 200)
        doc = page.context['activity_document']
        self.assertEqual((doc['total'], doc['paid'], doc['due'], doc['status']), (200, 50, 150, 'Partially paid'))
        self.assertContains(page, 'receipt-card')
        self.assertContains(page, 'Download / print invoice')
        self.assertContains(page, self.business.name)
        self.assertNotContains(page, 'Payment confirmed')
        self.assertIn('no-store', page['Cache-Control'])
        empty = self.client.get(url, {'start': self.day-timedelta(days=2), 'end': self.day-timedelta(days=1)})
        self.assertEqual(empty.context['activity_document']['lines'], [])
        invalid = self.client.get(url, {'start': self.day, 'end': 'invalid'})
        self.assertEqual(invalid.status_code, 400)

    def test_invoice_groups_batch_lines_by_product_within_each_sale(self):
        from sales.models import SaleItem
        from .models import DistributionReturn
        sale = self.release(quantity=2)
        original = sale.items.get()
        SaleItem.objects.create(sale=sale, finished_good=self.good, piece_qty=3, price=100)
        DistributionReturn.raw_objects.create(business=self.business, date=self.day,
            sale_item=original, quantity=1, condition='redistributable', reason='Returned')
        self.release(quantity=2)
        page = self.client.get(reverse('customer_activity_invoice', args=[self.customer.pk]), {'start': self.day, 'end': self.day})
        document = page.context['activity_document']
        self.assertEqual(len(document['lines']), 2)
        self.assertEqual([(line['quantity'], line['line_total']) for line in document['lines']], [('5 loaves', 500), ('2 loaves', 200)])
        self.assertEqual((document['credit'], document['total'], document['due']), (100, 600, 600))
        self.assertNotContains(page, f'Sale #{sale.pk}')
        self.assertNotIn('detail', document['lines'][0])

    def test_grouped_product_preserves_different_saved_prices(self):
        from sales.models import SaleItem
        sale = self.release(quantity=2)
        SaleItem.objects.create(sale=sale, finished_good=self.good, piece_qty=3, price=120)
        page = self.client.get(reverse('customer_activity_invoice', args=[self.customer.pk]), {'start': self.day, 'end': self.day})
        document = page.context['activity_document']
        self.assertEqual(len(document['lines']), 1)
        line = document['lines'][0]
        self.assertEqual((line['quantity'], line['line_total']), ('5 loaves', 560))
        self.assertEqual([rate['unit_price'] for rate in line['price_breakdown']], [100, 120])
        self.assertEqual(document['total'], 560)

    def test_invoice_period_uses_full_weekday_dates_and_one_date_for_a_day(self):
        from django.utils.formats import date_format
        self.release(quantity=2)
        url = reverse('customer_activity_invoice', args=[self.customer.pk])
        full_day = date_format(self.day, 'l, j F Y')
        page = self.client.get(url, {'start': self.day, 'end': self.day})
        self.assertContains(page, full_day, count=1)
        self.assertNotContains(page, ' – ')
        start = self.day-timedelta(days=1)
        period = self.client.get(url, {'start': start, 'end': self.day})
        self.assertContains(period, date_format(start, 'l, j F Y') + ' – ' + full_day)

    def test_later_returns_do_not_change_period_invoice_or_activity_status(self):
        from .models import DistributionReturn
        sale = self.release(quantity=2)
        DistributionReturn.raw_objects.create(business=self.business, date=self.day+timedelta(days=1),
            sale_item=sale.items.get(), quantity=1, condition='redistributable', reason='Returned next day')
        query = {'start': self.day, 'end': self.day}
        doc = self.client.get(reverse('customer_activity_invoice', args=[self.customer.pk]), query).context['activity_document']
        self.assertEqual((doc['credit'], doc['total'], doc['due']), (0, 200, 200))
        activity = self.client.get(reverse('customer_activity', args=[self.customer.pk]), query)
        self.assertEqual((activity.context['invoiced'], activity.context['outstanding']), (200, 200))
        row = list(activity.context['sales'])[0]
        self.assertEqual((row.activity_net_total, row.activity_payment_status), (200, 'Unpaid'))
        later = self.client.get(reverse('customer_activity_invoice', args=[self.customer.pk]),
            {**query, 'end': self.day+timedelta(days=1)}).context['activity_document']
        self.assertEqual((later['credit'], later['total']), (100, 100))

    def test_earlier_outstanding_is_only_brought_forward_when_checked(self):
        from sales.models import Sale, SaleItem
        old = Sale.raw_objects.create(business=self.business, date=self.day-timedelta(days=5),
            customer_master=self.customer, customer=self.customer.name, source='distribution_order', transaction_type='partial')
        SaleItem.objects.create(sale=old, finished_good=self.good, piece_qty=5, price=100)
        CustomerPayment.raw_objects.create(business=self.business, sale=old, date=self.day-timedelta(days=2),
            customer=self.customer.name, amount=100, payment_method='Cash')
        CustomerPayment.raw_objects.create(business=self.business, sale=old, date=self.day,
            customer=self.customer.name, amount=50, payment_method='Cash')
        self.release(quantity=2)
        url = reverse('customer_activity_invoice', args=[self.customer.pk])
        query = {'start': self.day, 'end': self.day}
        default = self.client.get(url, query).context['activity_document']
        self.assertEqual((default['total'], default['paid'], default['due'], default['brought_forward']), (200, 0, 200, 0))
        included = self.client.get(url, {**query, 'include_outstanding': '1'}).context['activity_document']
        self.assertEqual((included['total'], included['paid'], included['due'], included['brought_forward']), (600, 50, 550, 400))
        self.assertEqual(len(included['settlements']), 2)
        activity = self.client.get(reverse('customer_activity', args=[self.customer.pk]), query)
        self.assertContains(activity, 'Include earlier outstanding balances')
        self.assertNotContains(activity, 'name="include_outstanding" value="1" checked')

    def test_period_document_includes_all_sales_and_omits_reversed_orders(self):
        from sales.models import Sale, SaleItem
        from production.models import Order
        for index in range(26):
            sale = Sale.raw_objects.create(business=self.business, date=self.day, customer_master=self.customer,
                customer=self.customer.name, source='distribution_order', transaction_type='unpaid')
            SaleItem.objects.create(sale=sale, finished_good=self.good, piece_qty=1, price=100)
        order = Order.raw_objects.create(business=self.business, date=self.day, status='reversed')
        reversed_sale = Sale.raw_objects.create(business=self.business, date=self.day, customer_master=self.customer,
            customer=self.customer.name, source='distribution_order', transaction_type='unpaid', linked_order=order)
        SaleItem.objects.create(sale=reversed_sale, finished_good=self.good, piece_qty=1, price=999)
        page = self.client.get(reverse('customer_activity_invoice', args=[self.customer.pk]), {'start': self.day, 'end': self.day, 'sales_page': 2})
        self.assertEqual(len(page.context['activity_document']['settlements']), 26)
        self.assertEqual(page.context['activity_document']['total'], 2600)

    def test_other_destinations_are_clickable_and_use_non_sale_document_labels(self):
        issue = self.issue()
        transfer_market_stock_to_physical(business=self.business, good=self.good, quantity=2, date=self.day, user=self.user, reason='Shop')
        summary = self.client.get(reverse('market_stock'), {'date': self.day})
        self.assertContains(summary, reverse('market_outflow_activity', args=['non_stock']))
        self.assertContains(summary, reverse('market_outflow_activity', args=['store']))
        self.assertContains(summary, reverse('market_outflow_edit', args=[issue.pk]))
        for kind, title in [('non_stock', 'Stock issue'), ('store', 'Stock transfer')]:
            with self.subTest(kind=kind):
                page = self.document(kind)
                self.assertEqual(page.status_code, 200)
                self.assertEqual(page.context['activity_document']['title'], title)
                self.assertContains(page, 'Not applicable')
                self.assertNotContains(page, 'Invoice total')
                self.assertNotContains(page, 'Amount received')
        activity = self.client.get(reverse('market_outflow_activity', args=['non_stock']), {'start': self.day, 'end': self.day})
        self.assertEqual(activity.status_code, 200)
        self.assertContains(activity, 'Bakery team')

    def test_outflow_pages_are_bounded_but_documents_cover_the_full_period(self):
        for _ in range(26):
            self.issue(quantity=Decimal('.01'))
        query = {'start': self.day, 'end': self.day, 'name': 'Bakery team', 'purpose': 'staff', 'page': 2}
        response = self.client.get(reverse('market_outflow_activity', args=['non_stock']), query)
        self.assertEqual(len(response.context['outflows']), 1)
        self.assertEqual(response.context['activity_page'].number, 2)
        self.assertContains(response, 'name=Bakery+team')
        document = self.document('non_stock', name='Bakery team', purpose='staff', page=2)
        self.assertEqual(len(document.context['activity_document']['lines']), 26)

    def test_issue_correction_compensates_stock_preserves_history_and_cost_once(self):
        from core.accounting import profit_and_loss
        issue = self.issue()
        replacement = self.correct_issue(issue)
        issue.refresh_from_db(); self.lot.refresh_from_db()
        self.assertTrue(issue.reversed)
        self.assertEqual(self.lot.quantity_available, 17)
        self.assertEqual(replacement.quantity, -3)
        self.assertEqual(StockAdjustment.raw_objects.count(), 2)
        self.assertEqual(MarketStockMovement.raw_objects.filter(movement_type='non_stock').count(), 3)
        self.assertEqual(profit_and_loss(self.business, self.day, self.day)['product_use'], 90)
        self.assertFalse(FinancialTransaction.raw_objects.exists())
        self.assertTrue(AuditLog.raw_objects.filter(action='correct_product_issue').exists())
        page = self.document('non_stock', name='Community kitchen', purpose='charity')
        self.assertEqual(len(page.context['activity_document']['lines']), 1)
        self.assertEqual(page.context['activity_document']['lines'][0]['quantity'], '3 loaves')

    def test_failed_and_stale_corrections_do_not_restore_or_duplicate_stock(self):
        issue = self.issue()
        with self.assertRaises(ValidationError):
            self.correct_issue(issue, quantity=100)
        issue.refresh_from_db(); self.lot.refresh_from_db()
        self.assertFalse(issue.reversed)
        self.assertEqual(self.lot.quantity_available, 15)
        self.assertEqual(StockAdjustment.raw_objects.count(), 1)
        with self.assertRaises(ValidationError):
            self.correct_issue(issue, revision='stale')
        self.lot.active, self.lot.closed_reason = False, 'expired'
        self.lot.save()
        with self.assertRaises(ValidationError):
            self.correct_issue(issue)

    def test_issue_edit_page_saves_recipient_and_quantity_with_required_reason(self):
        issue = self.issue()
        url = reverse('market_outflow_edit', args=[issue.pk])
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        data = {'finished_good': self.good.pk, 'quantity': 4, 'reason': 'staff', 'recipient': 'New team',
            'description': 'Correct issue', 'revision': str(issue.updated_at), 'correction_reason': ''}
        invalid = self.client.post(url, data)
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, 'This field is required')
        data['correction_reason'] = 'Wrong quantity'
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_available, 16)

    def test_online_and_direct_sale_corrections_preserve_receipts_and_prices(self):
        self.good.selling_price = 100
        self.good.save()
        for kind in ('online', 'physical_sale'):
            with self.subTest(kind=kind):
                if kind == 'online':
                    sale = sell_market_stock_online(business=self.business, items=[{'finished_good': self.good, 'quantity': 2}],
                        customer=None, customer_name='Guest', date=self.day, payment_status='paid', payment_method='Cash', account=self.account, user=self.user)
                else:
                    sale = sell_market_stock_items(business=self.business, items=[{'finished_good': self.good, 'quantity': 2}],
                        customer_name='Walk-in', date=self.day, payment_method='Cash', account=self.account, user=self.user)
                before = FinancialTransaction.raw_objects.count()
                correct_market_release(business=self.business, sale_id=sale.pk, items=[{'finished_good': self.good, 'quantity': 1}],
                    customer=None, customer_name=sale.customer, reason='Quantity correction', revision=str(sale.updated_at), user=self.user)
                self.assertEqual(FinancialTransaction.raw_objects.count(), before)
                activity = self.client.get(reverse('market_outflow_activity', args=[kind]), {'start': self.day, 'end': self.day})
                self.assertEqual(activity.status_code, 200)
                self.assertEqual(len(activity.context['settlements']), 1)
                page = self.document(kind)
                doc = page.context['activity_document']
                self.assertEqual((doc['total'], doc['paid'], doc['overpaid']), (100, 200, 100))
                self.assertNotContains(page, 'Payment confirmed')
                self.assertEqual(self.client.get(reverse('market_stock_release_edit', args=[sale.pk])).status_code, 200)

    def test_cross_business_and_permission_boundaries(self):
        issue = self.issue()
        other = Business.objects.create(name='Other', slug='other-documents')
        from sales.models import Customer
        customer = Customer.raw_objects.create(business=other, name='Private customer')
        self.assertEqual(self.client.get(reverse('customer_activity_invoice', args=[customer.pk])).status_code, 404)
        self.assertEqual(self.document('unknown').status_code, 404)
        with patch('inventory.outflow_activity.user_has_permission', return_value=False):
            self.assertEqual(self.document('non_stock').status_code, 403)
            self.assertEqual(self.client.get(reverse('market_outflow_edit', args=[issue.pk])).status_code, 403)
