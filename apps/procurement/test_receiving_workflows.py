from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from core.models import CashAccount, FinancialTransaction
from inventory.models import StockMovement
from .models import PurchaseOrder
from . import test_multi_vendor as vendor_tests


class ReceivingWorkflowTests(TestCase):
    setUp=vendor_tests.MultiVendorTests.setUp
    data=vendor_tests.MultiVendorTests.data
    create_request=vendor_tests.MultiVendorTests.create_request

    def test_one_confirmation_receives_all_vendors_once(self):
        first,second=self.create_request()
        PurchaseOrder.raw_objects.filter(pk__in=[first.pk,second.pk]).update(status='approved')
        before=self.product.stock
        data={'receive_group':'1','vendor_ids':[first.pk,second.pk],f'received_date_{first.pk}':str(timezone.localdate()),f'received_date_{second.pk}':str(timezone.localdate())}
        response=self.client.post(reverse('po_receive',args=[first.pk]),data)
        self.assertEqual(response.status_code,302)
        self.product.refresh_from_db();self.assertEqual(self.product.stock,before+4)
        self.assertEqual(PurchaseOrder.raw_objects.filter(status='received').count(),2)
        self.client.post(reverse('po_receive',args=[first.pk]),data)
        self.product.refresh_from_db();self.assertEqual(self.product.stock,before+4)

    def test_invalid_vendor_date_leaves_every_vendor_unreceived(self):
        first,second=self.create_request()
        PurchaseOrder.raw_objects.filter(pk__in=[first.pk,second.pk]).update(status='approved')
        before=self.product.stock
        data={'receive_group':'1','vendor_ids':[first.pk,second.pk],f'received_date_{first.pk}':str(timezone.localdate()),f'received_date_{second.pk}':str(timezone.localdate()+timedelta(days=1))}
        response=self.client.post(reverse('po_receive',args=[first.pk]),data)
        self.assertEqual(response.status_code,302)
        self.product.refresh_from_db();self.assertEqual(self.product.stock,before)
        self.assertEqual(PurchaseOrder.raw_objects.filter(status='approved').count(),2)
        self.assertFalse(StockMovement.raw_objects.exists())

    def test_direct_purchase_receives_stock_without_approval(self):
        before=self.product.stock
        response=self.client.post(reverse('po_direct_add'),self.data())
        self.assertEqual(response.status_code,302)
        orders=list(PurchaseOrder.raw_objects.filter(business=self.business))
        self.assertEqual(len(orders),2)
        self.assertTrue(all(po.status=='received' and po.entry_mode=='direct' and po.approved_by_id is None for po in orders))
        self.product.refresh_from_db();self.assertEqual(self.product.stock,before+4)
        self.assertFalse(FinancialTransaction.raw_objects.exists())

    def test_approval_can_increase_quantities_with_reason_and_receive_the_new_amount(self):
        first,second=self.create_request()
        items=list(first.items.all())+list(second.items.all())
        payload={f'item-{item.pk}-qty':'3' for item in items}
        payload.update({f'item-{item.pk}-reason':'Additional demand' for item in items})
        response=self.client.post(reverse('po_approve',args=[first.pk]),payload)
        self.assertEqual(response.status_code,302)
        for item in items:
            item.refresh_from_db();self.assertEqual((item.qty,item.requested_qty,item.approval_reason),(3,2,'Additional demand'))
        self.client.post(reverse('po_receive',args=[first.pk]),{'receive_group':'1','vendor_ids':[first.pk,second.pk],f'received_date_{first.pk}':str(timezone.localdate()),f'received_date_{second.pk}':str(timezone.localdate())})
        self.product.refresh_from_db();self.assertEqual(self.product.stock,6)
        from .share import summarize, share_text
        text=share_text(summarize(first,self.business))
        self.assertIn('requested 2',text)
        self.assertIn('Additional demand',text)

    def test_increased_paid_request_keeps_receipts_and_creates_only_the_balance_due(self):
        account=CashAccount.raw_objects.create(business=self.business,name='Paid purchase till')
        first,second=self.create_request(payment_status='paid',account=str(account.pk))
        before=list(FinancialTransaction.raw_objects.filter(business=self.business).values_list('pk','amount'))
        items=list(first.items.all())+list(second.items.all())
        data={f'item-{item.pk}-qty':'3' for item in items}
        data.update({f'item-{item.pk}-reason':'Additional quantity required' for item in items})
        self.assertEqual(self.client.post(reverse('po_approve',args=[first.pk]),data).status_code,302)
        for po in [first,second]:
            po.refresh_from_db();self.assertEqual(po.payment_status,'partial')
            self.client.post(reverse('po_receive',args=[po.pk]))
        self.assertEqual(list(FinancialTransaction.raw_objects.filter(business=self.business).values_list('pk','amount')),before)
        from core.finance_views import _finance_open_items
        self.assertEqual(_finance_open_items(self.business)['purchase_payables'],100)

    def test_other_requests_vendor_cannot_be_received_through_this_request(self):
        first,second=self.create_request()
        foreign=PurchaseOrder.raw_objects.create(business=self.business,date=timezone.localdate(),status='approved')
        self.client.post(reverse('po_receive',args=[first.pk]),{'receive_group':'1','vendor_ids':[foreign.pk],f'received_date_{foreign.pk}':str(timezone.localdate())})
        foreign.refresh_from_db();self.assertEqual(foreign.status,'approved')
