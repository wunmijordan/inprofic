"""Regression coverage for the system interconnection audit findings."""
from decimal import Decimal as D
from django.test import TestCase, RequestFactory
from django.contrib.messages.storage.fallback import FallbackStorage
from django.utils import timezone
from core.context import set_current_business, set_ledger_context
from core.models import Business, CashAccount, FinancialTransaction
from core.services import record_cash, tenant_backup_querysets
from accounts.models import CustomUser
from expenses.models import Expense, ExpensePayment
from expenses.forms import ExpensePaymentForm
from expenses.views import expense_form, expense_delete
from sales.models import Sale, SaleItem
from sales.views import sale_delete
from procurement.models import PurchaseOrder, PurchaseOrderItem
from procurement.views import po_delete
from inventory.models import FinishedGood, RawMaterial, StockMovement
from inventory.services import record_finished_good_movement, record_raw_material_movement

class IntegrityRegressionTests(TestCase):

    def setUp(self):
        self.business = Business.objects.create(name='Audit', slug='audit')
        set_current_business(self.business)
        set_ledger_context()
        self.user = CustomUser.objects.create_user(username='audit')
        self.account = CashAccount.raw_objects.create(business=self.business, name='Till')
        self.day = timezone.localdate()

    def tearDown(self):
        set_current_business(None)
        set_ledger_context()

    def request(self):
        r = RequestFactory().post('/')
        r.user = self.user
        r.business = self.business
        r.session = {}
        r._messages = FallbackStorage(r)
        return r

    def test_expense_correction_reverses_and_reposts_cash(self):
        e = Expense.raw_objects.create(business=self.business, date=self.day, description='Power', amount=100, account=self.account, payment_status='paid')
        record_cash(self.business, self.user, date=self.day, amount=100, transaction_type='outflow', category='Utilities', description='Power', reference=f'EXP-{e.pk}', account=self.account)
        r = self.request()
        r.POST = {'date': str(self.day), 'category': 'utilities', 'description': 'Power', 'amount': '200', 'payment_status': 'paid', 'payment_method': 'Transfer', 'account': str(self.account.pk), 'vendor': '', 'notes': ''}
        response = expense_form(r, e.pk)
        e.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(e.amount, D('200'))
        self.assertEqual(sum((t.signed_amount for t in FinancialTransaction.raw_objects.filter(business=self.business))), D('-200'))
        self.assertEqual(FinancialTransaction.raw_objects.filter(business=self.business, reversal_of__isnull=False).count(), 1)

    def test_paid_expense_delete_is_blocked(self):
        e = Expense.raw_objects.create(business=self.business, date=self.day, description='Power', amount=100, account=self.account)
        record_cash(self.business, self.user, date=self.day, amount=100, transaction_type='outflow', category='Utilities', description='Power', reference=f'EXP-{e.pk}', account=self.account)
        expense_delete(self.request(), e.pk)
        self.assertTrue(Expense.raw_objects.filter(pk=e.pk).exists())
        self.assertEqual(FinancialTransaction.raw_objects.filter(business=self.business).count(), 1)

    def test_direct_sale_delete_is_blocked(self):
        g = FinishedGood.raw_objects.create(business=self.business, name='Goods', unit='piece', stock=10)
        s = Sale.raw_objects.create(business=self.business, date=self.day, customer='Walk-in', transaction_type='paid')
        SaleItem.objects.create(sale=s, finished_good=g, piece_qty=2, price=50)
        record_finished_good_movement(g, -D('2'), StockMovement.FG_SALE, reference=f'SALE-{s.pk}', on_date=self.day)
        record_cash(self.business, self.user, date=self.day, amount=100, transaction_type='income', category='Sales revenue', description='Sale', reference=f'SALE-{s.pk}', account=self.account)
        sale_delete(self.request(), s.pk)
        g.refresh_from_db()
        self.assertTrue(Sale.raw_objects.filter(pk=s.pk).exists())
        self.assertEqual(g.stock, D('8'))
        self.assertEqual(FinancialTransaction.raw_objects.filter(business=self.business).count(), 1)

    def test_received_po_delete_is_blocked(self):
        m = RawMaterial.raw_objects.create(business=self.business, name='Flour', usage_unit='kg', stock=0)
        p = PurchaseOrder.raw_objects.create(business=self.business, date=self.day, status='received', received_date=self.day)
        PurchaseOrderItem.objects.create(purchase_order=p, raw_material=m, qty=5, unit_cost=2)
        record_raw_material_movement(m, D('5'), StockMovement.RAW_PURCHASE, reference=f'PO-{p.pk}', on_date=self.day)
        po_delete(self.request(), p.pk)
        m.refresh_from_db()
        self.assertTrue(PurchaseOrder.raw_objects.filter(pk=p.pk).exists())
        self.assertEqual(m.stock, D('5'))

    def test_expense_payment_uses_remaining_balance(self):
        e = Expense.raw_objects.create(business=self.business, date=self.day, description='Power', amount=100, payment_status='unpaid')
        ExpensePayment.raw_objects.create(business=self.business, date=self.day, expense=e, amount=60, account=self.account)
        form = ExpensePaymentForm({'date': str(self.day), 'expense': e.pk, 'amount': '60', 'payment_method': 'Transfer', 'account': self.account.pk, 'reference': '', 'notes': ''})
        self.assertFalse(form.is_valid())
        from core.finance_views import _finance_open_items
        self.assertEqual(_finance_open_items(self.business)['expense_payables'], D('40'))

    def test_stale_material_instances_preserve_both_updates(self):
        m = RawMaterial.raw_objects.create(business=self.business, name='Flour', usage_unit='kg', stock=10)
        first = RawMaterial.raw_objects.get(pk=m.pk)
        second = RawMaterial.raw_objects.get(pk=m.pk)
        record_raw_material_movement(first, -D('3'), StockMovement.RAW_CONSUMPTION, on_date=self.day)
        record_raw_material_movement(second, -D('4'), StockMovement.RAW_CONSUMPTION, on_date=self.day)
        m.refresh_from_db()
        self.assertEqual(m.stock, D('3'))
        self.assertEqual(sum(StockMovement.raw_objects.filter(raw_material=m).values_list('quantity', flat=True)), D('-7'))

    def test_historical_sale_ignores_later_product_yield(self):
        g = FinishedGood.raw_objects.create(business=self.business, name='Batch item', unit='piece', units_per_batch=100)
        s = Sale.raw_objects.create(business=self.business, date=self.day, customer='Buyer', transaction_type='unpaid', source='distribution_order')
        SaleItem.objects.create(sale=s, finished_good=g, batch_qty=1, piece_qty=0, price=2, unit_cost=1)
        self.assertEqual(s.total, D('200'))
        g.units_per_batch = D('120')
        g.save(update_fields=['units_per_batch'])
        s = Sale.raw_objects.get(pk=s.pk)
        self.assertEqual(s.total, D('200'))

    def test_backup_model_coverage(self):
        names = {q.model._meta.label_lower for q in tenant_backup_querysets(self.business)}
        required = {'core.journalentry', 'core.taxsettings', 'commerce.commerceintake', 'accounts.payrollrun'}
        self.assertTrue(required.issubset(names))

    def test_partial_payment_view_does_not_post_a_second_overpayment(self):
        from expenses.views import expense_payment_form
        expense = Expense.raw_objects.create(business=self.business, date=self.day, description='Power', amount=100, payment_status='unpaid')
        for amount, expected_status in [('60', 302), ('60', 200), ('40', 302)]:
            request = self.request()
            request.POST = {'date': str(self.day), 'expense': str(expense.pk), 'amount': amount,
                            'payment_method': 'Transfer', 'account': str(self.account.pk), 'reference': '', 'notes': ''}
            response = expense_payment_form(request)
            self.assertEqual(response.status_code, expected_status)
        expense.refresh_from_db()
        self.assertEqual(expense.payment_status, 'paid')
        self.assertEqual(expense.payments.count(), 2)
        self.assertEqual(sum(t.amount for t in FinancialTransaction.raw_objects.filter(business=self.business)), D('100'))

    def test_snapshot_also_drives_reporting_expressions(self):
        from django.db.models import Sum
        from .accounting import _sale_expressions
        good = FinishedGood.raw_objects.create(business=self.business, name='Batch', units_per_batch=100)
        sale = Sale.raw_objects.create(business=self.business, date=self.day, customer='Buyer')
        SaleItem.objects.create(sale=sale, finished_good=good, batch_qty=1, price=2)
        good.units_per_batch = 120
        good.save(update_fields=['units_per_batch'])
        money, total, _, _ = _sale_expressions(prefix='')
        amount = SaleItem.objects.filter(sale=sale).aggregate(total=Sum(total, output_field=money))['total']
        self.assertEqual(amount, D('200'))

    def test_finance_receivables_and_dashboard_ignore_later_product_yield(self):
        """Finance open items and the dashboard ledger must use the sale line's frozen yield."""
        from core.finance_views import _finance_open_items
        from core.views import _financial_daily_ledgers
        good = FinishedGood.raw_objects.create(business=self.business, name='Yield', unit='piece', units_per_batch=100)
        unpaid = Sale.raw_objects.create(business=self.business, date=self.day, customer='Buyer',
                                         transaction_type='unpaid', source='distribution_order')
        SaleItem.objects.create(sale=unpaid, finished_good=good, batch_qty=1, piece_qty=0, price=2, unit_cost=1)
        paid = Sale.raw_objects.create(business=self.business, date=self.day, customer='Cash', transaction_type='paid')
        SaleItem.objects.create(sale=paid, finished_good=good, batch_qty=1, piece_qty=0, price=2, unit_cost=1)
        before = _finance_open_items(self.business)['receivables']
        self.assertEqual(before, D('200'))
        good.units_per_batch = D('120')
        good.save(update_fields=['units_per_batch'])
        self.assertEqual(_finance_open_items(self.business)['receivables'], before)
        ledgers = _financial_daily_ledgers(self.day, self.day)
        self.assertEqual(ledgers['sales_by_date'][self.day], D('200'))
        self.assertEqual(ledgers['cogs_by_date'][self.day], D('100'))

    def test_raising_amount_of_payment_settled_expense_reopens_the_balance(self):
        expense = Expense.raw_objects.create(business=self.business, date=self.day, description='Power', amount=100,
                                             payment_status='unpaid', account=self.account)
        ExpensePayment.raw_objects.create(business=self.business, date=self.day, expense=expense, amount=100, account=self.account)
        expense.payment_status = 'paid'
        expense.save(update_fields=['payment_status'])
        request = self.request()
        request.POST = {'date': str(self.day), 'category': 'utilities', 'description': 'Power', 'amount': '150',
                        'payment_status': 'paid', 'payment_method': 'Transfer', 'account': str(self.account.pk)}
        self.assertEqual(expense_form(request, expense.pk).status_code, 302)
        expense.refresh_from_db()
        self.assertEqual((expense.amount, expense.payment_status), (D('150'), 'unpaid'))
        from core.finance_views import _finance_open_items
        self.assertEqual(_finance_open_items(self.business)['expense_payables'], D('50'))
        # Explicitly flipping an unpaid expense to paid without matching payments stays refused.
        other = Expense.raw_objects.create(business=self.business, date=self.day, description='Rent', amount=100, payment_status='unpaid')
        ExpensePayment.raw_objects.create(business=self.business, date=self.day, expense=other, amount=40, account=self.account)
        request = self.request()
        request.POST = {'date': str(self.day), 'category': 'rent', 'description': 'Rent', 'amount': '100',
                        'payment_status': 'paid', 'payment_method': 'Transfer', 'account': str(self.account.pk)}
        self.assertEqual(expense_form(request, other.pk).status_code, 200)
        other.refresh_from_db()
        self.assertEqual(other.payment_status, 'unpaid')

    def test_more_than_one_thousand_products_are_valued(self):
        from inventory.valuation import finished_stock_value
        goods = FinishedGood.raw_objects.bulk_create([
            FinishedGood(business=self.business, name=f'Purchased {index}', stock=1, source_type='purchased_for_resale')
            for index in range(1001)
        ])
        StockMovement.raw_objects.bulk_create([
            StockMovement(business=self.business, finished_good=good, movement_type=StockMovement.FG_PURCHASE,
                          quantity=1, unit_value=2, business_date=self.day)
            for good in goods
        ])
        total, count, errors = finished_stock_value(self.business, all_periods=True)
        self.assertEqual(total, D('2002'))
        self.assertEqual(count, 1001)
        self.assertEqual(errors, [])
        from . import books
        books.rebuild_books(self.business)
        inventory = next(row for row in books.tie_out(self.business) if row['name'] == 'Inventory')
        self.assertEqual(inventory['operational'], D('2002'))

    def test_version_two_backup_restores_new_modules_and_opening_ids(self):
        import json
        from django.core import serializers
        from django.core.files.uploadedfile import SimpleUploadedFile
        from accounts.backup_restore import restore_backup_fixture
        from accounts.models import PayrollStaffProfile
        from .models import LedgerCutover, OpeningPosition, TaxSettings, LedgerAccount, JournalEntry, JournalLine
        from .services import tenant_backup_objects
        TaxSettings.raw_objects.create(business=self.business, tin='123456789')
        PayrollStaffProfile.raw_objects.create(business=self.business, full_name='Audit Staff', base_pay=200)
        cutover = LedgerCutover.raw_objects.create(business=self.business, cutover_date=self.day)
        OpeningPosition.raw_objects.create(business=self.business, cutover=cutover, segment='live', kind='cash', object_id=self.account.pk, amount=50)
        debit = LedgerAccount.raw_objects.create(business=self.business, code='1100', name='Asset', account_type='asset')
        credit = LedgerAccount.raw_objects.create(business=self.business, code='3000', name='Equity', account_type='equity')
        entry = JournalEntry.raw_objects.create(business=self.business, date=self.day, memo='Manual opening', source_type='manual', is_system=False)
        JournalLine.objects.create(entry=entry, account=debit, debit=50)
        JournalLine.objects.create(entry=entry, account=credit, credit=50)
        records = json.loads(serializers.serialize('json', tenant_backup_objects(self.business)))
        payload = json.dumps({'format': 'inprofic-tenant-backup', 'version': 2, 'records': records}).encode()
        target = Business.objects.create(name='Restore', slug='restore')
        upload = SimpleUploadedFile('backup.json', payload, content_type='application/json')
        restore_backup_fixture(upload, target, self.business.pk, actor=self.user)
        restored_cash = CashAccount.raw_objects.get(business=target, name='Till')
        self.assertNotEqual(restored_cash.pk, self.account.pk)
        position = OpeningPosition.raw_objects.get(business=target)
        self.assertEqual(position.object_id, restored_cash.pk)
        self.assertEqual(TaxSettings.raw_objects.get(business=target).tin, '123456789')
        self.assertEqual(PayrollStaffProfile.raw_objects.get(business=target).full_name, 'Audit Staff')
        self.assertEqual(JournalEntry.raw_objects.filter(business=target, is_system=False).count(), 1)
        self.assertEqual(JournalLine.objects.filter(entry__business=target, entry__is_system=False).count(), 2)
