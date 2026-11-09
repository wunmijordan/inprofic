"""Regression coverage for pre-commit findings and personal report contents."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserBusiness, UserModulePermission
from expenses.models import Expense
from procurement.models import PurchaseOrder, PurchaseOrderItem
from production.models import Order, OrderItem, ProductionBatch
from . import bank_reconciliation as recon
from .brief_views import _report
from .ledger_views import _carry_candidates
from .models import CashAccount, DailyBriefPreference, FinancialTransaction, LedgerCutover
from .test_daily_brief import BriefBase, YESTERDAY
from .test_reconciliation import ReconBase


class ReviewFixTests(BriefBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.client.force_login(self.admin)

    def test_admin_pulse_obeys_explicit_module_restrictions(self):
        membership = UserBusiness.objects.get(user=self.admin, business=self.business)
        for module in ['finance', 'inventory', 'production', 'audit']:
            UserModulePermission.objects.create(membership=membership, module=module, can_view=False, can_edit=False)
        cache.clear()
        CashAccount.objects.create(business=self.business, name='Private bank', opening_balance=987654)
        response = self.client.get(reverse('complete_dashboard_section', args=['pulse', 'indicators']))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['kpis'], [])
        self.assertEqual(response.json()['charts'], [])
        response = self.client.get(reverse('complete_dashboard_section', args=['pulse', 'attention']))
        self.assertEqual(response.json()['tables'], [])

    def test_approved_purchase_is_carried_and_reported(self):
        today = timezone.localdate()
        po = PurchaseOrder.objects.create(business=self.business, date=today-timedelta(days=2), status='approved', payment_status='unpaid')
        cutover = LedgerCutover.objects.create(business=self.business, cutover_date=today)
        self.assertIn(po, _carry_candidates(self.business, cutover)[1])
        report = _report(self.business, {}, user=self.admin)
        self.assertIn('Purchasing · selected period and current queue', report['text'])
        self.assertIn('Open purchase requests: 1', report['text'])

    def test_procurement_viewers_can_preview_but_cannot_edit_or_approve(self):
        membership = UserBusiness.objects.get(user=self.admin, business=self.business)
        UserModulePermission.objects.create(membership=membership, module='procurement', can_view=True, can_edit=False)
        cache.clear()
        po = PurchaseOrder.objects.create(business=self.business, date=YESTERDAY, payment_status='unpaid')
        self.assertEqual(self.client.get(reverse('po_edit', args=[po.pk])+'?share=1').status_code, 200)
        self.assertEqual(self.client.get(reverse('po_approve', args=[po.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('po_edit', args=[po.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('po_approve', args=[po.pk])).status_code, 403)

    def test_wastage_and_unit_cost_use_gross_and_saleable_denominators(self):
        order = Order.objects.create(business=self.business, date=YESTERDAY)
        item = OrderItem.objects.create(order=order, finished_good=self.bread)
        ProductionBatch.objects.create(business=self.business, order=order, order_item=item, finished_good=self.bread,
                                       production_date=YESTERDAY, batch_number='R1', produced_units=100, wastage_units=20, total_cost=100)
        response = self.client.get(reverse('complete_dashboard_section', args=['production', 'production_summary']), {'start': str(YESTERDAY), 'end': str(YESTERDAY)})
        data = {row['label']: row for row in response.json()['kpis']}
        self.assertEqual(data['Wastage']['hint'], '20.0% of output')
        self.assertIn('1.25 per saleable unit', data['Production cost']['hint'])

    @override_settings(SITE_URL='')
    def test_whatsapp_missing_public_url_returns_a_clear_reply(self):
        from whatsapp_storefront.assistant import build_reply
        from django.core.exceptions import ValidationError
        from whatsapp_storefront.assistant import _order_link
        with self.assertRaisesMessage(ValidationError, 'SITE_URL'):
            _order_link(SimpleNamespace(business=self.business))
        conversation = SimpleNamespace(connection=SimpleNamespace(business=self.business), business=self.business, state={})
        with patch('whatsapp_storefront.assistant._catalogue', return_value=[{'price': None}]):
            reply = build_reply(conversation, 'MENU')
        self.assertIn('SITE_URL', reply)

    def test_saved_report_contents_control_text_and_snapshot_and_are_mode_specific(self):
        self.sell(2, 5)
        response = self.client.post(reverse('daily_brief'), {'action': 'report_sections', 'section': ['products', 'sales']})
        self.assertEqual(response.status_code, 302)
        report = _report(self.business, {}, user=self.admin)
        self.assertIn('Product stock', report['text'])
        self.assertNotIn('Cash on hand:', report['text'])
        self.assertNotIn('Profit and loss', report['text'])
        self.assertNotIn('executive_pl', report['brief'])
        self.assertEqual({row['label'] for row in report['brief']['snapshot_highlights']}, {'Recorded sales', 'Product stock alerts'})
        weekly = _report(self.business, {'view': 'weekly'}, user=self.admin)
        self.assertIn('Profit and loss', weekly['text'])
        self.client.post(reverse('daily_brief'), {'action': 'report_sections'})
        empty = _report(self.business, {}, user=self.admin)
        self.assertEqual(empty['brief']['snapshot_highlights'], [])
        self.assertIn('No report sections selected', empty['text'])

    def test_current_payables_identify_the_expense_vendor(self):
        Expense.objects.create(business=self.business, date=YESTERDAY, category='utilities', description='Generator service', vendor='Ayo Services', amount=50, payment_status='unpaid')
        report = _report(self.business, {}, user=self.admin)
        self.assertIn('vendor Ayo Services', report['text'])
        self.assertIn('Payee · Ayo Services: ' + self.business.currency_symbol + '50.00', report['text'])


class BankReversalTests(ReconBase):
    def test_original_and_compensating_refund_match_their_own_statements(self):
        original = self.tx(5, FinancialTransaction.INCOME, '100', 'Deposit')
        original.reversed = True
        original.save(update_fields=['reversed'])
        refund = self.tx(9, FinancialTransaction.OUTFLOW, '100', 'Refund')
        refund.reversal_of = original
        refund.save(update_fields=['reversal_of'])
        first = self.statement([(6, 'Deposit', '100')], '1100')
        self.assertEqual(recon.auto_match(first), 1)
        self.assertEqual(recon.summarize(first)['difference'], Decimal('0'))
        second = self.statement([(9, 'Refund', '-100')], '1000')
        self.assertEqual(recon.auto_match(second), 1)
        self.assertEqual(recon.summarize(second)['difference'], Decimal('0'))
