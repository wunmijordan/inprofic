from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from accounts.models import CustomUser, UserBusiness, UserModulePermission
from accounts.services import seed_business_roles
from core.models import Business, CashAccount, FinancialTransaction
from expenses.models import Expense
from inventory.models import RawMaterial
from .models import PurchaseOrder, PurchaseOrderItem, PurchaseRequest, SupplierPayment
from .share import summarize, share_text
from .spending import period_totals, spending_cash


class SpendingWorkspaceTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name='Spending', slug='spending-workspace')
        self.admin = CustomUser.objects.create_superuser(username='spending-admin', password='test-password')
        self.client.force_login(self.admin)
        self.day = date(2026, 11, 6)
        self.material = RawMaterial.raw_objects.create(business=self.business, name='Flour', usage_unit='kg')
        self.account = CashAccount.raw_objects.create(business=self.business, name='Till')
        self.request = PurchaseRequest.raw_objects.create(business=self.business)
        self.po = self.order(100, 'Flour vendor')
        self.other_po = self.order(200, 'Second vendor')
        self.expense = Expense.raw_objects.create(business=self.business, date=self.day, amount=50,
            description='Transport', vendor='Driver', category='transport', payment_status='paid')
        self.url = reverse('procurement_list')
        self.period = {'start':'2026-11-06','end':'2026-11-06'}

    def order(self, value, vendor):
        po = PurchaseOrder.raw_objects.create(business=self.business, request=self.request, date=self.day,
            supplier=vendor, status='draft', payment_status='unpaid')
        PurchaseOrderItem.objects.create(purchase_order=po, raw_material=self.material, qty=1, unit_cost=value)
        return po

    def cash(self, amount, category='Procurement', reference=None, kind='outflow', day=None, reversal=None):
        return FinancialTransaction.raw_objects.create(business=self.business, date=day or self.day, amount=amount,
            category=category, reference=reference or f'PO-{self.po.pk}', transaction_type=kind,
            account=self.account, reversal_of=reversal)

    def test_grouped_requests_and_expenses_count_once_with_net_actual_cash_separate(self):
        self.cash(100)
        SupplierPayment.raw_objects.create(business=self.business, date=self.day, purchase_order=self.po,
            supplier='Flour vendor', amount=20, payment_method='Cash')
        self.cash(20, category='Supplier payment', reference='MANUAL-BANK-REF')
        self.cash(50, category='Transport / Delivery', reference=f'EXP-{self.expense.pk}')
        totals = period_totals(self.business, self.day, self.day)
        self.assertEqual(totals, {'purchases':Decimal(300), 'expenses':Decimal(50), 'grand_total':Decimal(350), 'money_out':Decimal(170)})
        response = self.client.get(self.url, self.period)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['orders']), 1)
        self.assertEqual(len(response.context['spending_activity']), 2)
        self.assertContains(response, 'General-use expense')
        self.assertContains(response, 'Actual money paid out')
        self.assertContains(response, '350.00')
        text = response.context['spending_share_text']
        self.assertEqual(text.count('• Transport:'), 1)
        self.assertIn('Grand total:', text)
        self.assertIn('350.00', text)
        self.assertIn('170.00', text)

    def test_unpaid_entries_do_not_create_cash_and_later_payments_use_payment_date(self):
        before = date(2026,11,5)
        Expense.raw_objects.create(business=self.business, date=self.day, amount=40, description='Unpaid repair', payment_status='unpaid')
        self.cash(75, day=before)
        self.cash(20, day=self.day, category='Supplier payment', reference='SUPPAY-older-invoice')
        totals = period_totals(self.business, self.day, self.day)
        self.assertEqual(totals['grand_total'], 390)
        self.assertEqual(totals['money_out'], 20)

    def test_compensating_entries_are_netted_by_their_actual_dates(self):
        original = self.cash(100)
        original.reversed = True; original.save()
        self.cash(100, category='Reversal: Procurement', reference='REV-PO', kind='income', reversal=original)
        self.cash(120)
        self.assertEqual(spending_cash(self.business, self.day, self.day), 120)
        self.assertEqual(FinancialTransaction.raw_objects.count(), 3)

    def test_shared_period_image_and_request_share_use_the_combined_grand_total(self):
        summary = summarize(self.po, self.business, user=self.admin)
        self.assertEqual(summary['total'], 300)
        self.assertEqual(summary['grand_total'], 350)
        self.assertIn('Grand total:', share_text(summary))
        with patch('procurement.spending_views.render_report_snapshot', return_value=b'png') as render:
            response = self.client.get(reverse('spending_snapshot'), self.period)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(render.call_args.kwargs['total'], f'{self.business.currency_symbol}350.00')
            self.assertIn('start=2026-11-06', render.call_args.kwargs['url'])
        response = self.client.get(reverse('spending_csv'), self.period)
        self.assertContains(response, 'Flour vendor')
        self.assertContains(response, 'General-use expense')
        self.assertContains(response, 'Grand total,350')

    def test_legacy_expenses_return_to_the_hub_and_invalid_dates_do_not_leak_rows(self):
        self.assertRedirects(self.client.get(reverse('expenses_list')), self.url)
        invalid = {'start':'2026-11-07','end':'2026-11-06'}
        response = self.client.get(self.url, invalid)
        self.assertContains(response, 'end date must be on or after')
        self.assertNotContains(response, 'Expense #')
        self.assertEqual(self.client.get(reverse('spending_snapshot'), invalid).status_code, 400)
        self.assertEqual(self.client.get(reverse('spending_csv'), invalid).status_code, 400)

    def test_expense_only_user_sees_no_procurement_data_in_hub_snapshot_or_exports(self):
        roles = seed_business_roles(self.business)
        user = CustomUser.objects.create_user(username='spending-expenses-only', password='test-password')
        member = UserBusiness.objects.create(business=self.business, user=user, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        UserModulePermission.objects.create(membership=member, module='procurement', can_view=False, can_edit=False)
        cache.clear(); self.client.force_login(user)
        response = self.client.get(self.url, self.period)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['spending_totals']['grand_total'], 50)
        self.assertNotContains(response, 'Flour vendor')
        self.assertNotContains(response, 'Purchase request</a>')
        self.assertContains(response, '+ Record expenses')
        export = self.client.get(reverse('spending_csv'), self.period)
        self.assertNotContains(export, 'Flour vendor')
        self.assertEqual(self.client.get(reverse('po_edit', args=[self.po.pk])).status_code, 403)
        UserModulePermission.objects.create(membership=member, module='expenses', can_view=False, can_edit=False)
        cache.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(reverse('spending_snapshot')).status_code, 403)

    def test_business_and_period_scoping_match_the_combined_export(self):
        foreign = Business.objects.create(name='Private',slug='private-spending-workspace')
        Expense.raw_objects.create(business=foreign, date=self.day, amount=999, description='PRIVATE SPENDING')
        Expense.raw_objects.create(business=self.business, date=date(2026,10,1), amount=700, description='OLD SPENDING')
        for name in ('procurement_list','spending_csv'):
            response = self.client.get(reverse(name), self.period)
            self.assertNotContains(response, 'PRIVATE SPENDING')
            self.assertNotContains(response, 'OLD SPENDING')

    def test_expense_payments_return_to_the_hub_and_do_not_add_the_invoice_twice(self):
        expense = Expense.raw_objects.create(business=self.business, date=self.day, amount=100,
            description='Installment repair', payment_status='unpaid')
        url = reverse('expense_payment_add') + f'?expense={expense.pk}&return_to=procurement'
        get_response = self.client.get(url)
        self.assertEqual(str(get_response.context['form'].initial['expense']), str(expense.pk))
        data = {'date':str(self.day), 'expense':expense.pk, 'amount':'25', 'payment_method':'Cash', 'account':self.account.pk}
        response = self.client.post(url, data)
        self.assertRedirects(response, self.url)
        totals = period_totals(self.business, self.day, self.day)
        self.assertEqual(totals['grand_total'], 450)
        self.assertEqual(totals['money_out'], 25)
        response = self.client.get(self.url, self.period)
        self.assertContains(response, 'Partially paid')
        self.assertContains(response, '75.00')
        data['amount'] = '75'
        self.assertRedirects(self.client.post(url, data), self.url)
        expense.refresh_from_db()
        self.assertEqual(expense.payment_status, 'paid')
        self.assertEqual(period_totals(self.business, self.day, self.day)['grand_total'], 450)
        self.assertEqual(period_totals(self.business, self.day, self.day)['money_out'], 100)

    def test_disabled_procurement_entitlement_keeps_expense_workspace_usable(self):
        from accounts.models import BusinessModuleAccess
        BusinessModuleAccess.objects.create(business=self.business, module='procurement', enabled=False)
        cache.clear()
        response = self.client.get(self.url, self.period)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['spending_totals']['grand_total'], 50)
        self.assertNotContains(response, 'Flour vendor')
        self.assertEqual(self.client.get(reverse('spending_snapshot'), self.period).status_code, 200)
        self.assertEqual(self.client.get(reverse('po_add')).status_code, 403)

    def test_combined_excel_keeps_numeric_amounts_and_plain_text_vendor_names(self):
        import io
        from openpyxl import load_workbook
        self.expense.vendor = '=1+1'; self.expense.save()
        response = self.client.get(reverse('spending_xlsx'), self.period)
        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(io.BytesIO(response.content))
        sheet = workbook.active
        self.assertTrue(all(sheet.cell(row, 5).data_type == 'n' for row in range(2, sheet.max_row + 1)))
        vendor = next(cell for row in sheet for cell in row if cell.value == '=1+1')
        self.assertEqual(vendor.data_type, 's')
