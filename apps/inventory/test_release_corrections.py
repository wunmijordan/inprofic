from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser
from core.models import AuditLog, Business, CashAccount, FinancialTransaction, EInvoice
from sales.models import Customer, CustomerPayment
from .distribution_summary import distribution_summaries
from .models import FinishedGood, FinishedGoodChannelPrice, MarketStockLot, MarketStockMovement, DistributionReturn
from .services import release_market_stock, record_distribution_return
from .release_corrections import correct_market_release


class ReleaseCorrectionTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='Corrections', slug='release-corrections')
        self.user = CustomUser.objects.create_superuser(username='release-editor', password='test')
        self.customer = Customer.raw_objects.create(business=self.business, name='Ada')
        self.account = CashAccount.raw_objects.create(business=self.business, name='Bank')
        self.day = timezone.localdate()
        self.good = FinishedGood.raw_objects.create(business=self.business, name='Bread', unit='loaf', stock=0)
        FinishedGoodChannelPrice.objects.create(finished_good=self.good, channel='distribution', price=100)
        self.lot = MarketStockLot.raw_objects.create(business=self.business, finished_good=self.good,
            source='production', received_date=self.day, quantity_received=20, quantity_available=20, unit_cost=30)
        MarketStockMovement.raw_objects.create(business=self.business, lot=self.lot, date=self.day,
            movement_type='production_in', quantity=20, balance_after=20)
        self.client.force_login(self.user)

    def release(self, *, paid=False, quantity=5):
        return release_market_stock(business=self.business, good=self.good, customer=self.customer,
            quantity=quantity, date=self.day, payment_status='paid' if paid else 'unpaid', payment_method='Cash',
            account=self.account if paid else None, user=self.user)

    def correct(self, sale, quantity, **changes):
        sale.refresh_from_db()
        data = dict(business=self.business, sale_id=sale.pk,
            items=[{'finished_good': self.good, 'quantity': Decimal(quantity)}], customer=self.customer,
            reason='Correct quantity entered', revision=str(sale.updated_at), user=self.user)
        data.update(changes)
        return correct_market_release(**data)

    def test_increase_then_decrease_reconciles_stock_delivery_totals_and_period_summary(self):
        sale = self.release()
        self.correct(sale, 8)
        self.lot.refresh_from_db(); self.good.refresh_from_db(); sale.refresh_from_db()
        self.assertEqual((self.lot.quantity_available, self.good.total_delivered_to_customers, sale.total), (12, 8, 800))
        self.correct(sale, 3)
        self.lot.refresh_from_db(); self.good.refresh_from_db(); sale.refresh_from_db()
        self.assertEqual((self.lot.quantity_available, self.good.total_delivered_to_customers, sale.total), (17, 3, 300))
        request = RequestFactory().get('/inventory/market-stock/', {'date': str(self.day)})
        request.user, request.business = self.user, self.business
        summary = distribution_summaries(request)
        row = summary['distribution_rows'][0]
        self.assertEqual((row['distributed'], row['closing']), (3, 17))
        self.assertEqual(summary['distribution_destinations'][0]['release_ids'], [sale.pk])
        self.assertEqual(AuditLog.raw_objects.filter(action='correct_market_release').count(), 2)
        self.assertFalse(FinancialTransaction.raw_objects.exists())

    def test_paid_invoice_increase_keeps_original_cash_and_exposes_balance(self):
        sale = self.release(paid=True)
        cash_before = list(FinancialTransaction.raw_objects.values_list('id', 'amount', 'account_id'))
        self.correct(sale, 8)
        sale.refresh_from_db()
        self.assertEqual((sale.total, sale.transaction_type), (800, 'partial'))
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sale).amount, 500)
        self.assertEqual(list(FinancialTransaction.raw_objects.values_list('id', 'amount', 'account_id')), cash_before)
        self.correct(sale, 3)
        sale.refresh_from_db()
        self.assertEqual((sale.total, sale.transaction_type), (300, 'paid'))
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sale).amount, 500)
        self.assertEqual(list(FinancialTransaction.raw_objects.values_list('id', 'amount', 'account_id')), cash_before)

    def test_failure_rolls_back_all_compensation_and_payments(self):
        sale = self.release(paid=True)
        count = MarketStockMovement.raw_objects.count()
        with self.assertRaises(ValidationError):
            self.correct(sale, 25)
        self.lot.refresh_from_db(); sale.refresh_from_db()
        self.assertEqual((self.lot.quantity_available, sale.total), (15, 500))
        self.assertEqual(MarketStockMovement.raw_objects.count(), count)
        self.assertFalse(CustomerPayment.raw_objects.exists())
        self.assertFalse(AuditLog.raw_objects.filter(action='correct_market_release').exists())

    def test_original_prices_are_preserved_and_customer_correction_uses_customer_price(self):
        from sales.models import CustomerProductPrice
        sale = self.release(paid=True)
        FinishedGoodChannelPrice.objects.filter(finished_good=self.good).update(price=200)
        self.correct(sale, 6)
        self.assertEqual(sale.items.get().price, 100)
        other = Customer.raw_objects.create(business=self.business, name='Ben')
        CustomerProductPrice.raw_objects.create(business=self.business, customer=other,
            finished_good=self.good, channel='distribution', price=80)
        self.correct(sale, 6, customer=other)
        sale.refresh_from_db()
        self.assertEqual((sale.customer_master_id, sale.total), (other.pk, 480))
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sale).customer_master_id, other.pk)

    def test_recorded_returns_block_correction_without_losing_original_links(self):
        sale = self.release()
        item = sale.items.get()
        record_distribution_return(business=self.business, sale_item=item, quantity=1, date=self.day,
            condition=DistributionReturn.DAMAGED, reason='Damaged', user=self.user)
        with self.assertRaisesMessage(ValidationError, 'recorded returns'):
            self.correct(sale, 4)
        self.assertTrue(sale.items.filter(pk=item.pk).exists())

    def test_stale_or_unauthorized_or_cross_business_edits_are_rejected(self):
        sale = self.release()
        with self.assertRaisesMessage(ValidationError, 'changed after'):
            self.correct(sale, 6, revision='stale')
        staff = CustomUser.objects.create_user(username='no-access')
        with self.assertRaises(PermissionDenied):
            self.correct(sale, 6, user=staff)
        foreign = Business.objects.create(name='Foreign', slug='foreign-release')
        customer = Customer.raw_objects.create(business=foreign, name='Private')
        with self.assertRaises(ValidationError):
            self.correct(sale, 6, customer=customer)
        self.assertEqual(self.client.get(reverse('market_stock_release_edit', args=[99999])).status_code, 404)

    def test_edit_form_keeps_exhausted_product_and_requires_a_reason(self):
        sale = self.release(quantity=20)
        url = reverse('market_stock_release_edit', args=[sale.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Edit customer release')
        self.assertEqual(response.context['formset'].initial[0]['quantity'], 20)
        data = {'customer': self.customer.pk, 'reason': '', 'revision': str(sale.updated_at),
            'items-TOTAL_FORMS': 1, 'items-INITIAL_FORMS': 1,
            'items-0-finished_good': self.good.pk, 'items-0-quantity': '18'}
        invalid = self.client.post(url, data)
        self.assertEqual(invalid.status_code, 200)
        self.assertIn('reason', invalid.context['form'].errors)
        data['reason'] = 'Two entered in error'
        saved = self.client.post(url, data)
        self.assertEqual(saved.status_code, 302)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_available, 2)
        sale.refresh_from_db()
        listing = self.client.get(reverse('market_stock'), {'date': str(self.day)})
        self.assertContains(listing, reverse('market_stock_release_edit', args=[sale.pk]))

    def test_submitted_invoice_is_protected_and_draft_is_invalidated(self):
        sale = self.release()
        invoice = EInvoice.raw_objects.create(business=self.business, sale=sale,
            status=EInvoice.SUBMITTED, payload={'original': True})
        with self.assertRaisesMessage(ValidationError, 'submitted e-invoice'):
            self.correct(sale, 6)
        invoice.status = EInvoice.READY
        invoice.save(update_fields=['status'])
        self.correct(sale, 6)
        invoice.refresh_from_db()
        self.assertEqual((invoice.status, invoice.payload), (EInvoice.DRAFT, {}))

    def test_partial_receipts_and_other_product_changes_preserve_cash_and_pack_math(self):
        from core.services import record_cash
        from .models import ProductPortionProfile
        sale = self.release()
        CustomerPayment.raw_objects.create(business=self.business, sale=sale, date=self.day,
            customer=self.customer.name, customer_master=self.customer, amount=200, account=self.account,
            payment_method='Card')
        record_cash(self.business, self.user, date=self.day, amount=200,
            transaction_type=FinancialTransaction.INCOME, category='Sales', description='Part payment',
            payment_method='Card', account=self.account, reference=f'PAYMENT-{sale.pk}')
        other = FinishedGood.raw_objects.create(business=self.business, name='Buns', unit='bun', stock=0)
        ProductPortionProfile.raw_objects.create(business=self.business, finished_good=other,
            active=True, customer_unit='pack', base_quantity=4)
        FinishedGoodChannelPrice.objects.create(finished_good=other, channel='distribution', price=50)
        other_lot = MarketStockLot.raw_objects.create(business=self.business, finished_good=other,
            source='production', received_date=self.day, quantity_received=12, quantity_available=12, unit_cost=3)
        self.correct(sale, 0, items=[{'finished_good': other, 'quantity': Decimal('8')}])
        sale.refresh_from_db(); self.lot.refresh_from_db(); other_lot.refresh_from_db()
        self.assertEqual((self.lot.quantity_available, other_lot.quantity_available, sale.total), (20, 4, 100))
        self.assertEqual(sale.transaction_type, 'paid')
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sale).amount, 200)
        self.assertEqual(FinancialTransaction.raw_objects.count(), 1)
        self.assertEqual(FinancialTransaction.raw_objects.get().amount, 200)
        request = RequestFactory().get('/inventory/market-stock/', {'date': str(self.day)})
        request.user, request.business = self.user, self.business
        summary = distribution_summaries(request)
        destinations = summary['distribution_destinations']
        self.assertEqual(len(destinations), 1)
        self.assertEqual(destinations[0]['quantity_label'], '2 packs')
        form = self.client.get(reverse('market_stock_release_edit', args=[sale.pk]))
        self.assertEqual(form.context['formset'].initial[0]['quantity'], 2)

    def test_reviewed_receipt_correction_reverses_wrong_cash_and_updates_original_payment(self):
        from .release_payments import market_payment_review, apply_market_payment_review
        sale = self.release(paid=True)
        self.correct(sale, 3)
        payment = CustomerPayment.raw_objects.get(sale=sale)
        original = FinancialTransaction.raw_objects.get(reference=f'SALE-{sale.pk}')
        amounts = {f'payment-{payment.pk}': '300.00'}
        review = market_payment_review(business=self.business, sale_id=sale.pk, user=self.user, amounts=amounts)
        self.assertFalse(review['errors'])
        self.assertEqual(review['row']['corrected_due'], 0)
        apply_market_payment_review(business=self.business, sale_id=sale.pk, user=self.user, amounts=amounts,
            fingerprint=review['fingerprint'], reason='Customer paid the correct lower amount')
        original.refresh_from_db(); payment.refresh_from_db(); self.lot.refresh_from_db(); sale.refresh_from_db()
        self.assertTrue(original.reversed)
        self.assertEqual(original.amount, 500)
        self.assertEqual(payment.amount, 300)
        self.assertEqual(sale.total, 300)
        self.assertEqual(self.lot.quantity_available, 17)
        reversal = FinancialTransaction.raw_objects.get(reversal_of=original)
        self.assertEqual((reversal.amount, reversal.transaction_type), (500, FinancialTransaction.OUTFLOW))
        self.assertTrue(FinancialTransaction.raw_objects.filter(reference=f'SALE-{sale.pk}', amount=300, reversed=False).exists())
        self.assertTrue(AuditLog.raw_objects.filter(action='correct_market_payment').exists())
        # A second review resolves the replacement receipt, never the reversed entry.
        again = market_payment_review(business=self.business, sale_id=sale.pk, user=self.user)
        self.assertFalse(again['errors'])
        self.assertEqual(again['row']['paid'], 300)

    def test_receipt_review_rejects_stale_amounts_ambiguous_cash_and_foreign_keys(self):
        from .release_payments import market_payment_review, apply_market_payment_review
        sale = self.release(paid=True)
        review = market_payment_review(business=self.business, sale_id=sale.pk, user=self.user,
            amounts={f'direct-{sale.pk}': '400'})
        with self.assertRaisesMessage(ValidationError, 'changed'):
            apply_market_payment_review(business=self.business, sale_id=sale.pk, user=self.user,
                amounts=review['amounts'], fingerprint='stale', reason='Wrong receipt')
        invalid = market_payment_review(business=self.business, sale_id=sale.pk, user=self.user, amounts={'payment-9999': '400'})
        self.assertTrue(invalid['errors'])
        tx = FinancialTransaction.raw_objects.get()
        tx.pk = None
        tx.save()
        ambiguous = market_payment_review(business=self.business, sale_id=sale.pk, user=self.user,
            amounts={f'direct-{sale.pk}': '400'})
        self.assertTrue(ambiguous['errors'])
        self.assertFalse(FinancialTransaction.raw_objects.filter(reversed=True).exists())

    def test_payment_review_requires_signed_confirmation_and_preserves_stock(self):
        sale = self.release(paid=True)
        self.correct(sale, 3)
        url = reverse('market_stock_payments', args=[sale.pk])
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context['formset'].initial[0]['amount'], 500)
        preview = self.client.post(url, {'action': 'preview', 'reason': 'Actual receipt was 300',
            'receipts-TOTAL_FORMS': 1, 'receipts-INITIAL_FORMS': 1, 'receipts-0-amount': '300'})
        self.assertEqual(preview.status_code, 200)
        token = preview.context['review_token']
        self.assertTrue(token)
        denied = self.client.post(url, {'action': 'apply', 'review_token': token})
        self.assertEqual(denied.status_code, 200)
        self.assertFalse(FinancialTransaction.raw_objects.filter(reversed=True).exists())
        saved = self.client.post(url, {'action': 'apply', 'review_token': token, 'confirm': 'on'})
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(CustomerPayment.raw_objects.get(sale=sale).amount, 300)

    def test_split_method_receipt_correction_balances_each_account_without_new_stock_changes(self):
        from core.services import record_cash
        from .release_payments import market_payment_review, apply_market_payment_review
        sale = self.release()
        card = CashAccount.raw_objects.create(business=self.business, name='Card account')
        payments = []
        for amount, method, account in [(200, 'Cash', self.account), (300, 'Card', card)]:
            payment = CustomerPayment.raw_objects.create(business=self.business, sale=sale, date=self.day,
                customer=self.customer.name, customer_master=self.customer, amount=amount,
                payment_method=method, account=account)
            record_cash(self.business, self.user, date=self.day, amount=amount,
                transaction_type=FinancialTransaction.INCOME, category='Customer payment',
                description=f'Payment received for Sale #{sale.pk}', payment_method=method,
                reference=f'CUSTPAY-{payment.pk}', account=account)
            payments.append(payment)
        sale.transaction_type = 'paid'
        sale.save(update_fields=['transaction_type'])
        amounts = {f'payment-{payments[0].pk}': '100', f'payment-{payments[1].pk}': '250'}
        review = market_payment_review(business=self.business, sale_id=sale.pk, user=self.user, amounts=amounts)
        self.assertFalse(review['errors'])
        apply_market_payment_review(business=self.business, sale_id=sale.pk, user=self.user,
            amounts=amounts, fingerprint=review['fingerprint'], reason='Entered both payment amounts incorrectly')
        self.account.refresh_from_db(); card.refresh_from_db(); self.lot.refresh_from_db(); sale.refresh_from_db()
        self.assertEqual((self.account.balance, card.balance), (100, 250))
        self.assertEqual((sale.transaction_type, self.lot.quantity_available), ('partial', 15))
