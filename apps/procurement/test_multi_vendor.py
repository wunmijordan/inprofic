from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from core.models import CashAccount, FinancialTransaction
from core.finance_views import _finance_open_items
from .models import PurchaseOrder, PurchaseOrderItem, PurchaseRequest, SupplierPayment
from .tests import DirectProductProcurementTests


class MultiVendorTests(DirectProductProcurementTests):
    def data(self, names=('Vendor A', 'Vendor B'), **overrides):
        data = {'date': str(timezone.localdate()), 'supplier': '', 'payment_status': 'unpaid',
                'payment_method': 'Transfer', 'amount_paid': '', 'account': '',
                'items-TOTAL_FORMS': str(len(names)), 'items-INITIAL_FORMS': '0'}
        for i, name in enumerate(names):
            data.update({f'items-{i}-item': f'finished:{self.product.pk}', f'items-{i}-supplier': name,
                         f'items-{i}-qty': '2', f'items-{i}-unit_cost': '100'})
        data.update(overrides)
        return data

    def create_request(self, **overrides):
        response = self.client.post(reverse('po_add'), self.data(**overrides))
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        orders = list(PurchaseOrder.raw_objects.filter(business=self.business).order_by('pk'))
        self.assertEqual(len(orders), 2)
        return orders

    def test_one_request_approves_both_vendors_and_receives_them_independently(self):
        first, second = self.create_request()
        self.assertIsNotNone(first.request_id)
        self.assertEqual(first.request_id, second.request_id)
        self.assertEqual(PurchaseRequest.raw_objects.filter(business=self.business).count(), 1)
        share = self.client.get(reverse('po_edit', args=[first.pk]), {'share': 1})
        self.assertContains(share, 'Vendor A')
        self.assertContains(share, 'Vendor B')
        self.assertEqual(share.context['summary']['total'], 200)
        listing = self.client.get(reverse('procurement_list'))
        self.assertEqual(len(listing.context['orders']), 1)
        approval = {f'item-{i.pk}-qty': '2' for i in PurchaseOrderItem.objects.filter(purchase_order__in=[first, second])}
        self.client.post(reverse('po_approve', args=[first.pk]), approval)
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((first.status, second.status), ('approved', 'approved'))
        self.client.post(reverse('po_receive', args=[second.pk]))
        first.refresh_from_db(); second.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual((first.status, second.status, self.product.stock), ('approved', 'received', 2))
        self.client.post(reverse('po_receive', args=[second.pk]))
        self.product.refresh_from_db(); self.assertEqual(self.product.stock, 2)
        self.client.post(reverse('po_receive', args=[first.pk]))
        balances = _finance_open_items(self.business)
        self.assertEqual(balances['payables'], 200)
        self.assertEqual({row['po'].supplier for row in balances['outstanding_pos']}, {'Vendor A', 'Vendor B'})

    def test_advance_is_attributed_only_to_selected_vendor_and_cannot_be_reduced_below_it(self):
        account = CashAccount.raw_objects.create(business=self.business, name='Bank')
        first, second = self.create_request(payment_status='partial', payment_supplier='Vendor B', amount_paid='60', account=str(account.pk))
        self.assertEqual((first.payment_status, second.payment_status), ('unpaid', 'partial'))
        payment = SupplierPayment.raw_objects.get(business=self.business)
        self.assertEqual((payment.purchase_order_id, payment.supplier, payment.amount), (second.pk, 'Vendor B', 60))
        approval = {f'item-{i.pk}-qty': ('0.5' if i.purchase_order_id == second.pk else '2')
                    for i in PurchaseOrderItem.objects.filter(purchase_order__in=[first,second])}
        approval.update({f'item-{i.pk}-reason': 'Reduce' for i in second.items.all()})
        response = self.client.post(reverse('po_approve', args=[first.pk]), approval)
        self.assertContains(response, 'cannot be lower than payments')
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((first.status, second.status), ('draft', 'draft'))
        self.assertEqual(self.client.get(reverse('po_edit', args=[first.pk])).status_code, 200)

    def test_paid_multi_vendor_request_records_exactly_one_outflow_per_vendor(self):
        account = CashAccount.raw_objects.create(business=self.business, name='Bank')
        first, second = self.create_request(payment_status='paid', account=str(account.pk))
        entries = FinancialTransaction.raw_objects.filter(business=self.business, category='Procurement')
        self.assertEqual(entries.count(), 2)
        self.assertEqual(sum(entries.values_list('amount', flat=True)), 200)
        self.assertEqual(set(entries.values_list('reference', flat=True)), {f'PO-{first.pk}', f'PO-{second.pk}'})

    def test_edit_can_regroup_vendors_but_rejects_foreign_item_ids(self):
        first, second = self.create_request()
        items = list(PurchaseOrderItem.objects.filter(purchase_order__in=[first,second]).order_by('pk'))
        data = self.data(names=('Vendor C', 'Vendor C'), **{'items-INITIAL_FORMS':'2'})
        for index, item in enumerate(items): data[f'items-{index}-id'] = str(item.pk)
        response = self.client.post(reverse('po_edit', args=[first.pk]), data)
        self.assertEqual(response.status_code, 302)
        orders = list(PurchaseOrder.raw_objects.filter(business=self.business))
        self.assertEqual(len(orders), 1)
        self.assertEqual((orders[0].supplier, orders[0].total), ('Vendor C', 200))
        foreign = PurchaseOrder.raw_objects.create(business=self.business, date=timezone.localdate(), payment_status='unpaid')
        foreign_item = PurchaseOrderItem.objects.create(purchase_order=foreign, finished_good=self.product, qty=1, unit_cost=50)
        data['items-0-id'] = str(foreign_item.pk)
        response = self.client.post(reverse('po_edit', args=[orders[0].pk]), data)
        self.assertEqual(response.status_code, 200)
        foreign_item.refresh_from_db(); self.assertEqual(foreign_item.purchase_order_id, foreign.pk)
        self.assertTrue(response.context['formset'].errors)

    def test_missing_vendor_and_ambiguous_advance_fail_without_creating_anything(self):
        account = CashAccount.raw_objects.create(business=self.business, name='Bank')
        data = self.data(names=('Vendor A', ''), payment_status='partial', account=str(account.pk), amount_paid='10')
        response = self.client.post(reverse('po_add'), data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        self.assertFalse(PurchaseOrder.raw_objects.filter(business=self.business).exists())

    def test_backup_preserves_request_envelope_and_vendor_relationships(self):
        from core.services import tenant_backup_objects
        first, second = self.create_request()
        objects = list(tenant_backup_objects(self.business))
        envelopes = [obj for obj in objects if isinstance(obj, PurchaseRequest)]
        self.assertEqual([obj.pk for obj in envelopes],[first.request_id])
        vendor_orders = [obj for obj in objects if isinstance(obj, PurchaseOrder)]
        self.assertEqual({obj.request_id for obj in vendor_orders},{first.request_id})
        self.assertEqual({obj.pk for obj in vendor_orders},{first.pk,second.pk})

    def test_vendor_payment_entry_preserves_independent_advances_and_balances(self):
        first, second = self.create_request()
        account = CashAccount.raw_objects.create(business=self.business,name='Bank')
        response = self.client.post(reverse('supplier_payment_add'), {
            'date':str(timezone.localdate()), 'supplier':second.supplier, 'purchase_order':str(second.pk),
            'amount':'25', 'payment_method':'Transfer', 'account':str(account.pk)})
        self.assertEqual(response.status_code,302)
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((first.payment_status,second.payment_status),('unpaid','partial'))
        self.assertEqual(SupplierPayment.raw_objects.get(business=self.business).purchase_order_id,second.pk)
        response = self.client.get(reverse('supplier_payment_add'),{'purchase_order':second.pk})
        self.assertEqual(response.context['form'].initial['purchase_order'],second.pk)
        self.assertEqual(response.context['form'].supplier_payload[str(second.pk)]['outstanding'],75)

    def test_assistant_counts_one_request_across_both_vendors(self):
        from assistant.tools import run_tool
        first, second = self.create_request()
        result, _ = run_tool('get_pending_approvals', {}, self.user, self.business)
        self.assertEqual(result.data['purchase_requests_waiting'],1)
        self.assertEqual(len(result.data['purchase_requests']),1)
        self.assertIn('Vendor A',result.text)
        self.assertIn('Vendor B',result.text)

    def test_removing_original_vendor_keeps_the_shared_request_url(self):
        first, second = self.create_request()
        items = list(PurchaseOrderItem.objects.filter(purchase_order__in=[first,second]).order_by('pk'))
        data = self.data(**{'items-INITIAL_FORMS':'2', 'items-0-DELETE':'on'})
        for index,item in enumerate(items): data[f'items-{index}-id'] = str(item.pk)
        response = self.client.post(reverse('po_edit',args=[first.pk]),data)
        self.assertEqual(response.status_code,302)
        self.assertTrue(PurchaseOrder.raw_objects.filter(pk=first.pk,business=self.business).exists())
        first.refresh_from_db()
        self.assertEqual(first.supplier,'Vendor B')
        share = self.client.get(reverse('po_edit',args=[first.pk]),{'share':'1'})
        self.assertEqual(share.status_code,200)
        self.assertEqual(share.context['summary']['total'],100)
