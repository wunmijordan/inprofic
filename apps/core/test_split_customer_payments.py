from decimal import Decimal
from django.urls import reverse
from django.utils import timezone
from assistant.test_quick_facts import ToolBase
from sales.models import Sale, SaleItem, CustomerPayment
from core.models import CashAccount, FinancialTransaction


class SplitCustomerPaymentTests(ToolBase):
    def setUp(self):
        super().setUp()
        self.sale = self.owed(self.biz, 'Ada', 10, 10)
        self.cash = CashAccount.raw_objects.create(business=self.biz, name='Till')
        self.bank = CashAccount.raw_objects.create(business=self.biz, name='Bank')
        self.foreign = CashAccount.raw_objects.create(business=self.other, name='PRIVATE BANK')

    def payload(self, cash='30', card='20', transfer='50'):
        data = {'date': str(timezone.localdate()), 'customer': 'Ada', 'sale': self.sale.pk,
                'notes': 'Split receipt', 'payments-TOTAL_FORMS': '3', 'payments-INITIAL_FORMS': '3'}
        for i, (method, amount, account) in enumerate([('Cash',cash,self.cash), ('Card',card,self.bank), ('Transfer',transfer,self.bank)]):
            data.update({f'payments-{i}-payment_method':method, f'payments-{i}-amount':amount,
                         f'payments-{i}-account':str(account.pk), f'payments-{i}-reference':f'TEST-{method}'})
        return data

    def test_one_receipt_posts_all_methods_exactly_once_and_settles_invoice(self):
        response = self.client.post(reverse('customer_payment_add'), self.payload())
        self.assertEqual(response.status_code, 302)
        rows = CustomerPayment.raw_objects.filter(business=self.biz, sale=self.sale)
        self.assertEqual(dict(rows.values_list('payment_method','amount')), {'Cash':30,'Card':20,'Transfer':50})
        cash = FinancialTransaction.raw_objects.filter(business=self.biz, category='Customer payment')
        self.assertEqual(cash.count(), 3)
        self.assertEqual(sum(cash.values_list('amount',flat=True)), 100)
        self.assertEqual(sum(cash.filter(account=self.cash).values_list('amount',flat=True)), 30)
        self.sale.refresh_from_db(); self.assertEqual(self.sale.transaction_type, 'paid')
        # A repeated submission cannot post another receipt against the settled invoice.
        self.client.post(reverse('customer_payment_add'), self.payload())
        self.assertEqual(CustomerPayment.raw_objects.filter(business=self.biz,sale=self.sale).count(), 3)

    def test_unused_methods_create_no_rows_and_partial_collection_keeps_balance(self):
        response = self.client.post(reverse('customer_payment_add'), self.payload(cash='10', card='0', transfer='15'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CustomerPayment.raw_objects.filter(business=self.biz,sale=self.sale).count(), 2)
        self.sale.refresh_from_db(); self.assertEqual(self.sale.transaction_type, 'partial')
        response = self.client.post(reverse('customer_payment_add'), self.payload(cash='76',card='0',transfer='0'))
        self.assertContains(response, 'combined payment exceeds')
        self.assertEqual(CustomerPayment.raw_objects.filter(business=self.biz,sale=self.sale).count(), 2)

    def test_foreign_accounts_missing_accounts_and_overpayments_are_atomic_errors(self):
        data = self.payload(cash='50',card='30',transfer='30')
        response = self.client.post(reverse('customer_payment_add'), data)
        self.assertContains(response, 'combined payment exceeds')
        for account in ('', str(self.foreign.pk)):
            data = self.payload(); data['payments-0-account'] = account
            response = self.client.post(reverse('customer_payment_add'), data)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['payment_parts'].errors)
        self.assertFalse(CustomerPayment.raw_objects.filter(business=self.biz,sale=self.sale).exists())
        self.assertFalse(FinancialTransaction.raw_objects.filter(business=self.biz,category='Customer payment').exists())

    def test_all_payment_methods_are_available_and_zero_total_is_rejected(self):
        response = self.client.get(reverse('customer_payment_add'))
        self.assertEqual(len(response.context['payment_parts'].forms), 3)
        for form in response.context['payment_parts'].forms:
            self.assertEqual(list(form.fields['payment_method'].choices), Sale.PAYMENT_CHOICES)
            self.assertNotIn(self.foreign.pk, list(form.fields['account'].queryset.values_list('pk',flat=True)))
        self.assertContains(self.client.post(reverse('customer_payment_add'), self.payload('0','0','0')), 'at least one payment method')

    def test_later_receipt_clears_the_remaining_finance_receivable(self):
        from core.finance_views import _finance_open_items
        self.client.post(reverse('customer_payment_add'), self.payload(cash='10',card='0',transfer='15'))
        self.assertEqual(_finance_open_items(self.biz)['receivables'], 75)
        selected = self.client.get(reverse('customer_payment_add'), {'sale':self.sale.pk})
        self.assertEqual(selected.context['form'].initial['sale'], self.sale.pk)
        self.assertEqual(selected.context['form'].initial['customer'], 'Ada')
        response = self.client.post(reverse('customer_payment_add'), self.payload(cash='20',card='0',transfer='55'))
        self.assertEqual(response.status_code,302)
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.transaction_type,'paid')
        self.assertEqual(_finance_open_items(self.biz)['receivables'],0)
        receipts = CustomerPayment.raw_objects.filter(business=self.biz,sale=self.sale)
        self.assertEqual(receipts.count(),4)
        self.assertEqual(sum(receipts.values_list('amount',flat=True)),100)
        self.assertEqual(FinancialTransaction.raw_objects.filter(business=self.biz,category='Customer payment').count(),4)
