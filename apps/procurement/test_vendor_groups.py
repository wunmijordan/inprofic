from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Business, CashAccount, FinancialTransaction
from .models import PurchaseOrder, PurchaseOrderItem, SupplierPayment
from .share import render_snapshot, share_text, summarize
from .tests import DirectProductProcurementTests


class VendorGroupTests(TestCase):
    setUp = DirectProductProcurementTests.setUp

    def payload(self):
        data = {'date': str(timezone.localdate()), 'vendors-TOTAL_FORMS': '2', 'vendors-INITIAL_FORMS': '0',
                'items-TOTAL_FORMS': '3', 'items-INITIAL_FORMS': '0'}
        for index, name in enumerate(['Typed random vendor', '']):
            data.update({f'vendors-{index}-supplier': name, f'vendors-{index}-payment_status': 'unpaid',
                         f'vendors-{index}-payment_method': 'Transfer'})
        for index, group in enumerate(['0', '0', '1']):
            data.update({f'items-{index}-item': f'finished:{self.product.pk}', f'items-{index}-qty': '2',
                         f'items-{index}-unit_cost': '100', f'items-{index}-vendor_group': group})
        return data

    def test_groups_allow_an_unnamed_vendor_and_keep_distinct_payment_instructions(self):
        bank = CashAccount.raw_objects.create(business=self.business, name='Group bank')
        till = CashAccount.raw_objects.create(business=self.business, name='Group till')
        data = self.payload()
        data.update({'vendors-0-payment_status': 'partial', 'vendors-0-amount_paid': '50',
                     'vendors-0-account': str(bank.pk), 'vendors-1-payment_status': 'paid',
                     'vendors-1-payment_method': 'Cash', 'vendors-1-account': str(till.pk)})
        response = self.client.post(reverse('po_add'), data)
        self.assertEqual(response.status_code, 302)
        first, second = PurchaseOrder.raw_objects.filter(business=self.business).order_by('pk')
        self.assertEqual(first.request_id, second.request_id)
        self.assertEqual((first.total, second.total), (200, 100))
        self.assertEqual((first.payment_status, first.account_id), ('partial', bank.pk))
        self.assertEqual((second.supplier, second.payment_status, second.payment_method), ('', 'paid', 'Cash'))
        self.assertEqual(SupplierPayment.raw_objects.get(business=self.business).amount, 50)
        self.assertEqual(sum(FinancialTransaction.raw_objects.filter(business=self.business).values_list('amount', flat=True)), Decimal('150'))
        summary = summarize(first, self.business, 'https://example.com/procurement/')
        text = share_text(summary)
        self.assertIn('Partially Paid · Transfer', text)
        self.assertIn('Paid · Cash', text)
        self.assertIn('Group bank', text)
        self.assertIn('Group till', text)
        self.assertIn('vendor unnamed', text)
        self.assertTrue(render_snapshot(summary).startswith(b'\x89PNG'))
        self.assertEqual(self.client.get(reverse('po_approve', args=[first.pk])).status_code, 200)

    def test_invalid_group_payment_rolls_back_the_entire_request(self):
        other = Business.objects.create(name='Other group business', slug='other-group-business')
        foreign = CashAccount.raw_objects.create(business=other, name='Private account')
        data = self.payload()
        data.update({'vendors-1-payment_status': 'paid', 'vendors-1-account': str(foreign.pk)})
        response = self.client.post(reverse('po_add'), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Select a valid choice')
        self.assertEqual(PurchaseOrder.raw_objects.filter(business=self.business).count(), 0)
        self.assertEqual(FinancialTransaction.raw_objects.filter(business=self.business).count(), 0)

    def test_group_validation_preserves_rows_and_rejects_excess_advance(self):
        account = CashAccount.raw_objects.create(business=self.business, name='Bank')
        data = self.payload()
        data.update({'vendors-0-payment_status': 'partial', 'vendors-0-amount_paid': '201', 'vendors-0-account': str(account.pk)})
        response = self.client.post(reverse('po_add'), data)
        self.assertContains(response, 'advance cannot exceed')
        self.assertContains(response, 'name="items-2-vendor_group" value="1"')
        self.assertEqual(PurchaseOrder.raw_objects.filter(business=self.business).count(), 0)

    def test_edit_and_delete_a_group_preserves_request_url_and_remaining_lines(self):
        self.assertEqual(self.client.post(reverse('po_add'), self.payload()).status_code, 302)
        first, second = PurchaseOrder.raw_objects.filter(business=self.business).order_by('pk')
        response = self.client.get(reverse('po_edit', args=[first.pk]))
        self.assertContains(response, 'value="Typed random vendor"')
        data = self.payload()
        items = list(PurchaseOrderItem.objects.filter(purchase_order__in=[first, second]).order_by('pk'))
        data['items-INITIAL_FORMS'] = '3'
        for index, item in enumerate(items):
            data[f'items-{index}-id'] = str(item.pk)
        data.update({'vendors-0-DELETE': 'on', 'items-0-DELETE': 'on', 'items-1-DELETE': 'on'})
        response = self.client.post(reverse('po_edit', args=[first.pk]), data)
        self.assertEqual(response.status_code, 302)
        first.refresh_from_db()
        self.assertEqual(first.supplier, '')
        self.assertEqual(first.items.count(), 1)
        self.assertEqual(self.client.get(reverse('po_edit', args=[first.pk])).status_code, 200)

    def test_invalid_item_group_is_reported_without_partial_writes(self):
        data = self.payload()
        data['items-0-vendor_group'] = 'private-group'
        response = self.client.post(reverse('po_add'), data)
        self.assertContains(response, 'invalid purchase group')
        self.assertEqual(PurchaseOrder.raw_objects.filter(business=self.business).count(), 0)

    def paid_request(self, partial=False):
        account = CashAccount.raw_objects.create(business=self.business, name='Paid request bank', opening_balance=1000)
        data = self.payload()
        data.update({'vendors-0-payment_status': 'partial' if partial else 'paid',
                     'vendors-0-account': str(account.pk), 'vendors-0-amount_paid': '50' if partial else '',
                     'vendors-1-payment_status': 'paid', 'vendors-1-account': str(account.pk)})
        self.assertEqual(self.client.post(reverse('po_add'), data).status_code, 302)
        orders = list(PurchaseOrder.raw_objects.filter(business=self.business).order_by('pk'))
        return account, orders

    def edit_payload(self, orders):
        data = self.payload()
        data['items-INITIAL_FORMS'] = '3'
        for index, order in enumerate(orders):
            data[f'vendors-{index}-order_id'] = str(order.pk)
        for index, item in enumerate(PurchaseOrderItem.objects.filter(purchase_order__in=orders).order_by('pk')):
            data[f'items-{index}-id'] = str(item.pk)
        return data

    def test_paid_draft_edit_reconciles_settlement_without_reposting_cash(self):
        from core.models import AuditLog
        from .services import recorded_vendor_payments
        account, orders = self.paid_request()
        self.assertEqual(account.balance, 700)
        page = self.client.get(reverse('po_edit', args=[orders[0].pk]))
        self.assertContains(page, 'This request has recorded payments')
        data = self.edit_payload(orders)
        data['items-0-unit_cost'] = '150'
        for _ in range(2):
            response = self.client.post(reverse('po_edit', args=[orders[0].pk]), data)
            self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        first = PurchaseOrder.raw_objects.prefetch_related('payments').get(pk=orders[0].pk)
        self.assertEqual((first.total, first.payment_status), (250, 'partial'))
        self.assertEqual(recorded_vendor_payments([first])[0][first.pk], 200)
        self.assertEqual(SupplierPayment.raw_objects.filter(purchase_order=first).count(), 1)
        self.assertEqual(account.balance, 700)
        self.assertEqual(FinancialTransaction.raw_objects.filter(business=self.business).count(), 2)
        log = AuditLog.raw_objects.filter(business=self.business, model_name='PurchaseOrder', object_id=str(first.pk), action='update').latest('pk')
        self.assertEqual(Decimal(log.metadata['after']['total']), 250)
        self.assertIn('before', log.metadata)
        approval = {f'item-{item.pk}-qty': '2' for item in PurchaseOrderItem.objects.filter(purchase_order__in=orders)}
        self.assertEqual(self.client.post(reverse('po_approve', args=[first.pk]), approval).status_code, 302)
        first.refresh_from_db()
        self.assertEqual(first.status, 'approved')
        self.client.post(reverse('po_receive', args=[first.pk]))
        from core.finance_views import _finance_open_items
        self.assertEqual(_finance_open_items(self.business)['payables'], 50)
        settlement = self.client.post(reverse('supplier_payment_add'), {
            'date': str(timezone.localdate()), 'purchase_order': str(first.pk), 'supplier': first.supplier,
            'amount': '50', 'payment_method': 'Transfer', 'account': str(account.pk)})
        self.assertEqual(settlement.status_code, 302)
        self.assertEqual(_finance_open_items(self.business)['payables'], 0)
        self.assertEqual(account.balance, 650)

    def test_reduction_below_paid_total_rolls_back_without_touching_accounts(self):
        account, orders = self.paid_request()
        data = self.edit_payload(orders)
        data['items-0-unit_cost'] = '1'
        response = self.client.post(reverse('po_edit', args=[orders[0].pk]), data)
        self.assertContains(response, 'cannot be lower than payments already recorded')
        self.assertEqual(account.balance, 700)
        self.assertEqual(PurchaseOrder.raw_objects.get(pk=orders[0].pk).total, 200)
        self.assertFalse(SupplierPayment.raw_objects.filter(purchase_order__in=orders).exists())

    def test_delete_paid_and_partial_drafts_retains_money_and_audit_history(self):
        from core.models import AuditLog
        account, orders = self.paid_request(partial=True)
        original_ids = [order.pk for order in orders]
        response = self.client.get(reverse('procurement_list'))
        self.assertContains(response, 'data-confirm=')
        self.assertContains(response, 'Edit request')
        self.assertContains(response, '?request=1')
        self.assertEqual(account.balance, 850)
        response = self.client.post(reverse('po_delete', args=[orders[0].pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(PurchaseOrder.raw_objects.filter(pk__in=original_ids).exists())
        retained = SupplierPayment.raw_objects.filter(business=self.business, purchase_order__isnull=True)
        self.assertEqual(sum(retained.values_list('amount', flat=True)), 150)
        self.assertEqual(account.balance, 850)
        self.assertEqual(FinancialTransaction.raw_objects.filter(business=self.business).count(), 2)
        logs = AuditLog.raw_objects.filter(business=self.business, model_name='PurchaseOrder', action='delete')
        self.assertEqual(logs.count(), 2)
        self.assertTrue(all('before' in log.metadata and 'retained_payment_amount' in log.metadata for log in logs))

    def test_combined_pdf_lists_every_vendor_with_group_and_grand_totals(self):
        from unittest.mock import patch
        from core.invoice import _table
        self.client.post(reverse('po_add'), self.payload())
        first = PurchaseOrder.raw_objects.filter(business=self.business).earliest('pk')
        with patch('core.invoice._table', wraps=_table) as tables:
            response = self.client.get(reverse('po_invoice', args=[first.pk]), {'request': '1'})
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))
        self.assertEqual(len(tables.call_args_list), 2)
        self.assertEqual([call.args[4] for call in tables.call_args_list], [200, 100])
