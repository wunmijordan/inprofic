from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from core.models import AuditLog, Business, CashAccount, EInvoice, FinancialTransaction
from inventory.models import FinishedGood
from .models import Customer, CustomerPayment, CustomerProductPrice, Sale, SaleItem
from .repricing import apply_price_review, price_review, receipt_review


class DistributionPriceCorrectionTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='Price correction', slug='price-correction')
        self.user = CustomUser.objects.create_superuser(username='price-admin', password='test-password')
        self.customer = Customer.raw_objects.create(business=self.business, name='Ada')
        self.good = FinishedGood.raw_objects.create(business=self.business, name='Bread', unit='loaf', units_per_batch=1, stock=50)
        self.agreement = CustomerProductPrice.raw_objects.create(business=self.business, customer=self.customer,
            finished_good=self.good, channel='distribution', price=80)
        self.account = CashAccount.raw_objects.create(business=self.business, name='Till')
        self.day = date(2026, 11, 1)

    def sale(self, state='paid', day=None):
        sale = Sale.raw_objects.create(business=self.business, date=day or self.day, customer_master=self.customer,
            customer=self.customer.name, source='distribution_order', transaction_type=state, account=self.account, payment_method='Cash')
        SaleItem.objects.create(sale=sale, finished_good=self.good, piece_qty=10, price=100, unit_cost=40)
        return sale

    def cash(self, amount, reference, category='Sales revenue', description='Sale'):
        return FinancialTransaction.raw_objects.create(business=self.business, date=self.day, amount=amount,
            reference=reference, account=self.account, payment_method='Cash', category=category,
            description=description, transaction_type='income')

    def payment(self, sale, amount):
        payment = CustomerPayment.raw_objects.create(business=self.business, date=self.day, sale=sale, amount=amount,
            customer=self.customer.name, customer_master=self.customer, account=self.account, payment_method='Cash')
        tx = self.cash(amount, f'CUSTPAY-{payment.pk}', 'Customer payment', f'Payment from Ada — Sale #{sale.pk}')
        return payment, tx

    def review(self):
        return price_review(business=self.business, customer=self.customer, start=self.day, end=self.day, user=self.user)

    def apply(self, review, corrections=None):
        return apply_price_review(business=self.business, customer=self.customer, start=self.day, end=self.day,
            user=self.user, fingerprint=review['fingerprint'], receipt_corrections=corrections)

    def test_invoice_only_preserves_cash_and_full_original_settlement(self):
        sale = self.sale()
        tx = self.cash(1000, f'SALE-{sale.pk}')
        self.apply(self.review())
        sale.refresh_from_db(); tx.refresh_from_db(); self.good.refresh_from_db()
        self.assertEqual(sale.total, 800)
        self.assertEqual(sale.transaction_type, 'paid')
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sale).amount, 1000)
        self.assertEqual(FinancialTransaction.raw_objects.count(), 1)
        self.assertEqual(tx.amount, 1000)
        self.assertEqual(self.good.stock, 50)
        self.assertEqual(sale.items.get().unit_cost, 40)
        self.assertEqual(self.review()['count'], 0)

    def test_paid_at_creation_correction_compensates_cash_and_preserves_original_entry(self):
        sale = self.sale()
        tx = self.cash(1000, f'SALE-{sale.pk}')
        review = self.review()
        receipts = receipt_review(self.business, review)
        self.assertEqual(receipts['errors'], [])
        self.apply(review, receipts['amounts'])
        sale.refresh_from_db(); tx.refresh_from_db()
        self.assertTrue(tx.reversed)
        self.assertEqual(tx.amount, 1000)
        self.assertEqual(sale.total, 800)
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sale).amount, 800)
        self.assertEqual(sum(t.signed_amount for t in FinancialTransaction.raw_objects.filter(account=self.account)), 800)
        audit = AuditLog.raw_objects.get(action='reprice')
        self.assertEqual(audit.metadata['recorded_paid_before'], '1000.00')
        self.assertEqual(Decimal(audit.metadata['recorded_paid_after']), 800)
        with self.assertRaises(ValidationError):
            self.apply(review, receipts['amounts'])
        self.assertEqual(FinancialTransaction.raw_objects.count(), 3)

    def test_partial_receipts_need_actual_amount_and_keep_balance_due(self):
        sale = self.sale('partial')
        payment, tx = self.payment(sale, 500)
        review = self.review()
        initial = receipt_review(self.business, review)
        self.assertEqual(Decimal(initial['amounts'][f'payment-{payment.pk}']), 500)
        self.apply(review, {f'payment-{payment.pk}': '400.00'})
        payment.refresh_from_db(); sale.refresh_from_db()
        self.assertEqual(payment.amount, 400)
        self.assertEqual(sale.transaction_type, 'partial')
        self.assertEqual(sale.total - payment.amount, 400)
        self.assertEqual(sum(t.signed_amount for t in FinancialTransaction.raw_objects.all()), 400)

    def test_split_payments_keep_accounts_and_sum_to_corrected_total(self):
        sale = self.sale()
        first, _ = self.payment(sale, 600)
        second, tx = self.payment(sale, 400)
        bank = CashAccount.raw_objects.create(business=self.business, name='Bank')
        second.account, tx.account = bank, bank
        second.save(); tx.save()
        review = self.review(); receipts = receipt_review(self.business, review)
        self.assertEqual(receipts['errors'], [])
        self.apply(review, receipts['amounts'])
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual(first.amount, 480)
        self.assertEqual(second.amount, 320)
        self.assertEqual(second.account_id, bank.pk)
        self.assertEqual(sum(t.signed_amount for t in FinancialTransaction.raw_objects.filter(account=bank)), 320)

    def test_stale_cash_and_payment_previews_are_rejected_atomically(self):
        sale = self.sale('partial'); payment, tx = self.payment(sale, 500)
        review = self.review(); receipts = receipt_review(self.business, review)
        tx.description = 'Updated'; tx.save()
        with self.assertRaises(ValidationError): self.apply(review, receipts['amounts'])
        self.assertEqual(sale.items.get().price, 100)
        self.assertEqual(FinancialTransaction.raw_objects.count(), 1)

    def test_missing_or_ambiguous_cash_is_blocked_without_any_changes(self):
        sale = self.sale(); review = self.review(); receipts = receipt_review(self.business, review)
        self.assertTrue(receipts['errors'])
        with self.assertRaises(ValidationError): self.apply(review, receipts['amounts'])
        self.assertEqual(sale.items.get().price, 100)
        self.assertFalse(AuditLog.raw_objects.filter(action='reprice').exists())

    def test_other_dates_customers_businesses_and_walkin_prices_stay_unchanged(self):
        selected = self.sale('unpaid')
        before = self.sale('unpaid', date(2026, 10, 31))
        other = Customer.raw_objects.create(business=self.business, name='Other')
        excluded = self.sale('unpaid'); excluded.customer_master = other; excluded.customer = other.name; excluded.save()
        walkin = self.sale('unpaid'); walkin.source = 'walkin'; walkin.save()
        self.apply(self.review())
        self.assertEqual(selected.items.get().price, 80)
        for sale in (before, excluded, walkin): self.assertEqual(sale.items.get().price, 100)
        foreign = Business.objects.create(name='Other business', slug='other-price-correction')
        with self.assertRaises(PermissionDenied):
            price_review(business=foreign, customer=self.customer, start=self.day, end=self.day, user=self.user)

    def test_submitted_einvoice_and_commercial_unit_are_blocked(self):
        sale = self.sale()
        EInvoice.raw_objects.create(business=self.business, sale=sale, invoice_number='TEST-1', status='cleared')
        review = self.review()
        self.assertTrue(review['errors'])
        with self.assertRaises(ValidationError): self.apply(review)
        self.assertEqual(sale.items.get().price, 100)
        EInvoice.raw_objects.all().delete()
        item = sale.items.get(); item.commercial_quantity = 2; item.commercial_unit_price = 500; item.save()
        self.assertTrue(self.review()['errors'])

    def test_review_screen_requires_confirmed_signed_scope_and_corrects_receipts(self):
        sale = self.sale(); self.cash(1000, f'SALE-{sale.pk}')
        self.client.force_login(self.user)
        url = reverse('customer_reprice', args=[self.customer.pk])
        response = self.client.get(url, {'start': str(self.day), 'end': str(self.day), 'receipts': 'correct'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Actual receipts to record')
        token = response.context['review_token']
        response = self.client.post(url, {'start': str(self.day), 'end': str(self.day), 'action':'apply',
            'review_token':token, 'receipt_mode':'correct', 'confirm':'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(sale.items.get().price, 80)
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sale).amount, 800)

    def test_linked_order_prices_and_receivable_state_follow_invoice_without_stock_changes(self):
        from production.models import Order, OrderItem
        order = Order.raw_objects.create(business=self.business, date=self.day, order_type='distribution', customer=self.customer, customer_name=self.customer.name, status='completed')
        item = OrderItem.objects.create(order=order, finished_good=self.good, piece_qty=10, price=100)
        sale = self.sale(); sale.linked_order = order; sale.save()
        self.agreement.price = 120; self.agreement.save()
        self.apply(self.review())
        order.refresh_from_db(); item.refresh_from_db(); sale.refresh_from_db()
        self.assertEqual(item.price, 120)
        self.assertEqual(order.customer_payment_status, 'unpaid')
        self.assertEqual(sale.transaction_type, 'partial')
        self.assertEqual(sale.total, 1200)
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sale).amount, 1000)
        self.assertEqual(item.total_units, 10)

    def test_multi_customer_allocations_reprice_only_the_selected_customer(self):
        from production.models import Order, OrderItem, OrderAllocation, OrderAllocationLine
        order = Order.raw_objects.create(business=self.business, date=self.day, order_type='multi', status='completed')
        item = OrderItem.objects.create(order=order, finished_good=self.good, piece_qty=20, price=100)
        selected = self.sale('unpaid'); selected.linked_order = order; selected.save()
        allocation = OrderAllocation.objects.create(order=order, channel='distribution', customer=self.customer, sale=selected)
        line = OrderAllocationLine.objects.create(allocation=allocation, order_item=item, piece_qty=10, price=100)
        other = Customer.raw_objects.create(business=self.business, name='Another customer')
        allocation2 = OrderAllocation.objects.create(order=order, channel='distribution', customer=other)
        line2 = OrderAllocationLine.objects.create(allocation=allocation2, order_item=item, piece_qty=10, price=90)
        self.apply(self.review())
        line.refresh_from_db(); line2.refresh_from_db(); item.refresh_from_db()
        self.assertEqual(line.price, 80)
        self.assertEqual(line2.price, 90)
        self.assertEqual(item.price, 100)

    def test_archived_period_is_blocked_and_rejected_apply_is_atomic(self):
        from core.models import LedgerCutover
        sale = self.sale('unpaid')
        LedgerCutover.raw_objects.create(business=self.business, cutover_date=date(2026,11,2), status='archived')
        review = self.review()
        self.assertTrue(review['errors'])
        with self.assertRaises(ValidationError): self.apply(review)
        self.assertEqual(sale.items.get().price, 100)

    def test_corrected_receipts_and_invoice_value_reach_customer_activity(self):
        sale = self.sale(); self.cash(1000, f'SALE-{sale.pk}')
        review = self.review(); receipts = receipt_review(self.business, review)
        self.apply(review, receipts['amounts'])
        self.client.force_login(self.user)
        response = self.client.get(reverse('customer_activity', args=[self.customer.pk]), {'start':str(self.day), 'end':str(self.day)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['recorded_payments'], 800)
        self.assertEqual(response.context['invoiced'], 800)
        self.assertEqual(response.context['outstanding'], 0)

    def test_tampered_preview_dates_and_missing_confirmation_do_not_change_invoices(self):
        sale = self.sale('unpaid')
        self.client.force_login(self.user)
        url = reverse('customer_reprice', args=[self.customer.pk])
        response = self.client.get(url, {'start':str(self.day),'end':str(self.day)})
        token = response.context['review_token']
        response = self.client.post(url, {'start':str(self.day),'end':'2026-11-02', 'action':'apply','review_token':token,'confirm':'yes'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'review expired or is invalid')
        self.assertEqual(sale.items.get().price, 100)
        response = self.client.post(url, {'start':str(self.day),'end':str(self.day),'action':'apply','review_token':token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sale.items.get().price, 100)

    def test_receipt_review_pages_preserve_edits_and_apply_the_whole_selected_period(self):
        sales = []
        for _ in range(51):
            sale = self.sale(); self.cash(1000, f'SALE-{sale.pk}'); sales.append(sale)
        self.client.force_login(self.user)
        url = reverse('customer_reprice', args=[self.customer.pk])
        response = self.client.get(url, {'start':str(self.day), 'end':str(self.day), 'receipts':'correct'})
        self.assertEqual(len(response.context['review_rows']), 50)
        token = response.context['review_token']
        response = self.client.post(url, {'start':str(self.day), 'end':str(self.day), 'action':'preview_receipts',
            'receipt_mode':'correct', 'review_token':token, 'page':'2', f'receipt_direct-{sales[0].pk}':'700.00'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['review_rows']), 1)
        self.assertFalse(response.context['date_form'].errors)
        response = self.client.post(url, {'start':str(self.day), 'end':str(self.day), 'action':'apply',
            'receipt_mode':'correct', 'confirm':'yes', 'review_token':response.context['review_token']})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sales[0]).amount, 700)
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sales[-1]).amount, 800)
        self.assertEqual(AuditLog.raw_objects.filter(action='reprice').count(), 51)
        self.assertEqual(sum(t.signed_amount for t in FinancialTransaction.raw_objects.all()), 40700)

    def test_mistaken_receipt_can_be_corrected_after_an_invoice_only_correction(self):
        sale = self.sale(); self.cash(1000, f'SALE-{sale.pk}')
        self.apply(self.review())
        self.assertEqual(sale.items.get().price, 80)
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sale).amount, 1000)
        review = price_review(business=self.business, customer=self.customer, start=self.day, end=self.day,
            user=self.user, include_receipts=True)
        self.assertEqual(review['count'], 1)
        self.assertEqual(review['rows'][0]['changes'], [])
        receipts = receipt_review(self.business, review)
        self.apply(review, receipts['amounts'])
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sale).amount, 800)
        self.assertEqual(sum(t.signed_amount for t in FinancialTransaction.raw_objects.all()), 800)
