from decimal import Decimal
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from accounts.models import CustomUser, UserBusiness, UserModulePermission
from accounts.services import seed_business_roles
from core.models import Business, CashAccount, FinancialTransaction, AuditLog
from expenses.models import Expense, ExpenseRequest, ExpenseRequestLine
from procurement.spending import period_totals


class ExpenseRequestTests(TestCase):
    def setUp(self):
        self.business=Business.objects.create(name='Daily requests',slug='daily-requests')
        self.admin=CustomUser.objects.create_superuser(username='request-admin',password='test')
        self.client.force_login(self.admin)
        self.day=timezone.localdate()
        self.account=CashAccount.raw_objects.create(business=self.business,name='Till')

    def payload(self, entries=None, action='save'):
        entries=entries or [{'description':'Transport','amount':100},{'description':'Repairs','amount':200}]
        data={'date':str(self.day),'action':action,'notes':'Daily needs','lines-TOTAL_FORMS':len(entries),
            'lines-INITIAL_FORMS':sum(bool(row.get('id')) for row in entries)}
        for i,row in enumerate(entries):
            values={'category':'other','payment_status':'unpaid','payment_method':'Transfer',**row}
            data.update({f'lines-{i}-{key}':str(value) for key,value in values.items()})
        return data

    def create(self):
        response=self.client.post(reverse('expense_request_add'),self.payload())
        self.assertEqual(response.status_code,302)
        return ExpenseRequest.raw_objects.get(business=self.business)

    def test_daily_requests_merge_and_do_not_create_spending_before_approval(self):
        obj=self.create()
        response=self.client.post(reverse('expense_request_add'),self.payload([{'description':'Power','amount':50}]))
        self.assertEqual(response.status_code,302)
        self.assertEqual(ExpenseRequest.raw_objects.count(),1)
        self.assertEqual(obj.total,350)
        self.assertFalse(Expense.raw_objects.exists())
        self.assertFalse(FinancialTransaction.raw_objects.exists())
        self.assertEqual(period_totals(self.business,self.day,self.day)['grand_total'],0)
        page=self.client.get(reverse('procurement_list'))
        self.assertContains(page,'General-use expense requests')
        self.assertContains(page,reverse('expense_request_detail',args=[obj.pk]))
        self.assertEqual(self.client.get(reverse('expense_request_image',args=[obj.pk])).status_code,200)

    def test_approval_edits_and_removes_items_and_records_paid_spending_once(self):
        obj=self.create(); first,second=list(obj.lines.order_by('pk'))
        response=self.client.post(reverse('expense_request_detail',args=[obj.pk]),self.payload([
            {'id':first.pk,'description':first.description,'amount':150,'payment_status':'paid','account':self.account.pk,'approval_reason':'Updated transport quote'},
            {'id':second.pk,'description':second.description,'amount':200,'DELETE':'on','approval_reason':'Repair postponed'},
        ],'approve'))
        self.assertEqual(response.status_code,302,getattr(response,'context',None))
        obj.refresh_from_db(); second.refresh_from_db()
        self.assertEqual(obj.status,'approved');self.assertTrue(second.removed)
        expense=Expense.raw_objects.get(business=self.business)
        self.assertEqual((expense.amount,expense.payment_status),(150,'paid'))
        self.assertEqual(FinancialTransaction.raw_objects.get(business=self.business).amount,150)
        self.assertEqual(period_totals(self.business,self.day,self.day)['grand_total'],150)
        self.assertTrue(AuditLog.raw_objects.filter(business=self.business,action='approve').exists())
        response=self.client.post(reverse('expense_request_detail',args=[obj.pk]),self.payload(action='approve'))
        self.assertEqual(response.status_code,403)
        self.assertEqual(Expense.raw_objects.count(),1)
        self.assertEqual(FinancialTransaction.raw_objects.count(),1)

    def test_approval_requires_reasons_for_increases_and_removals(self):
        obj=self.create(); first,second=list(obj.lines.order_by('pk'))
        response=self.client.post(reverse('expense_request_detail',args=[obj.pk]),self.payload([
            {'id':first.pk,'description':first.description,'amount':150},
            {'id':second.pk,'description':second.description,'amount':200,'DELETE':'on'},
        ],'approve'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Give a reason')
        self.assertFalse(Expense.raw_objects.exists())
        obj.refresh_from_db();self.assertEqual(obj.status,'pending')

    def test_unauthorized_approval_and_foreign_requests_are_blocked(self):
        obj=self.create()
        roles=seed_business_roles(self.business)
        user=CustomUser.objects.create_user(username='expense-staff',password='test')
        member=UserBusiness.objects.create(business=self.business,user=user,role=roles[CustomUser.ROLE_MANAGER])
        UserModulePermission.objects.update_or_create(membership=member,module='expenses',defaults={'can_view':True,'can_edit':True})
        cache.clear();self.client.force_login(user)
        self.assertEqual(self.client.post(reverse('expense_request_detail',args=[obj.pk]),self.payload(action='approve')).status_code,403)
        foreign=Business.objects.create(name='Private',slug='private-requests')
        private=ExpenseRequest.raw_objects.create(business=foreign,date=self.day)
        self.assertEqual(self.client.get(reverse('expense_request_detail',args=[private.pk])).status_code,404)
        self.assertFalse(Expense.raw_objects.exists())

    def test_removal_keeps_audit_without_creating_financial_records(self):
        obj=self.create()
        response=self.client.post(reverse('expense_request_detail',args=[obj.pk]),{'action':'decline'})
        self.assertEqual(response.status_code,302)
        obj.refresh_from_db();self.assertEqual(obj.status,'declined')
        self.assertFalse(Expense.raw_objects.exists())
        self.assertEqual(obj.lines.count(),2)
        self.assertTrue(AuditLog.raw_objects.filter(business=self.business,action='decline').exists())

    def test_foreign_line_ids_cannot_be_submitted_as_request_edits(self):
        obj=self.create()
        other=ExpenseRequest.raw_objects.create(business=self.business,date=self.day.replace(day=1))
        foreign_line=ExpenseRequestLine.objects.create(request=other,description='Other day',amount=75,requested_amount=75)
        response=self.client.post(reverse('expense_request_detail',args=[obj.pk]),self.payload([{'id':foreign_line.pk,'description':'Forged','amount':999}]))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'request items changed')
        foreign_line.refresh_from_db();self.assertEqual(foreign_line.amount,75)
        self.assertEqual(obj.lines.count(),2)

    def test_foreign_cash_account_cannot_be_approved(self):
        obj=self.create(); line=obj.lines.first()
        foreign=Business.objects.create(name='Foreign account',slug='foreign-account-request')
        account=CashAccount.raw_objects.create(business=foreign,name='Private till')
        page=self.client.post(reverse('expense_request_detail',args=[obj.pk]),self.payload([{'id':line.pk,'description':line.description,'amount':100,'payment_status':'paid','account':account.pk}],'approve'))
        self.assertEqual(page.status_code,200)
        self.assertContains(page,'Select a valid choice')
        self.assertFalse(Expense.raw_objects.exists())
