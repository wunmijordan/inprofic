from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.urls import reverse

from accounts.models import UserBusiness, UserModulePermission
from inventory.models import DistributionReturn, FinishedGood
from sales.models import Sale, SaleItem, Customer, CustomerPayment
from .accounting import profit_and_loss
from .dashboard_metrics import metric_choices, selected_period
from .models import Business, LedgerCutover
from .test_daily_brief import BriefBase, TODAY, YESTERDAY


class DashboardMetricTests(BriefBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def metric(self, kind='sales', **params):
        return self.client.get(reverse('dashboard_metric'), {'metric':kind, 'date':str(YESTERDAY), **params})

    def test_sales_card_matches_received_value_with_partial_receipts_and_returns(self):
        self.sell(10, 100, customer='Paid')
        self.sell(10, 100, paid=False, customer='Partial')
        partial = Sale.objects.get(customer='Partial')
        partial.transaction_type = 'partial'
        partial.save(update_fields=['transaction_type'])
        CustomerPayment.objects.create(business=self.business, sale=partial, customer='Partial', date=TODAY, amount=300)
        DistributionReturn.objects.create(business=self.business, sale_item=partial.items.get(), date=TODAY,
            quantity=2, condition='redistributable', unit_value=1)
        body = self.metric().json()
        self.assertEqual(Decimal(body['value']), 1300)
        self.assertEqual(body['count'], 2)
        self.assertEqual({row['label'] for row in body['channels']}, {'Physical Store','Distribution Order'})

    def test_revenue_product_and_customer_follow_saved_sale_prices_and_finance(self):
        customer = Customer.objects.create(business=self.business,name='Ada')
        self.sell(10, 20, customer='Ada')
        paid = Sale.objects.get(customer='Ada')
        paid.customer_master = customer
        paid.save(update_fields=['customer_master'])
        self.sell(5, 10, paid=False, customer='Ada')
        self.bread.selling_price = 9999
        self.bread.save(update_fields=['selling_price'])
        for kind, subject in [('revenue',''), ('product',str(self.bread.pk)), ('customer',str(customer.pk))]:
            body = self.metric(kind,subject=subject).json()
            self.assertEqual(Decimal(body['value']),200)
            self.assertEqual(Decimal(body['recorded']),250)
        expected = profit_and_loss(self.business,YESTERDAY,YESTERDAY)
        body = self.metric('profit').json()
        self.assertEqual(Decimal(body['value']),expected['net_profit'])
        self.assertEqual(Decimal(body['costs']),expected['total_costs'])

    def test_historical_periods_and_subjects_exclude_other_tenants(self):
        self.sell(2, 5)
        foreign = Business.objects.create(name='Private',slug='private-metric')
        product = FinishedGood.objects.create(business=foreign,name='Private product')
        customer = Customer.objects.create(business=foreign,name='Private customer')
        sale = Sale.objects.create(business=foreign,date=YESTERDAY)
        SaleItem.objects.create(sale=sale,finished_good=product,piece_qty=999,price=999)
        self.assertEqual(Decimal(self.metric('revenue').json()['value']),10)
        self.assertEqual(self.metric('product',subject=product.pk).status_code,404)
        self.assertEqual(self.metric('customer',subject=customer.pk).status_code,404)
        products, customers = metric_choices(self.business)
        self.assertNotIn('Private product',str(products))
        self.assertNotIn('Private customer',str(customers))

    def test_product_and_customer_revenue_are_cumulative_through_today(self):
        customer = Customer.objects.create(business=self.business,name='Ada')
        for day, quantity in [(TODAY-timedelta(days=400),2),(YESTERDAY,3),(TODAY+timedelta(days=1),100)]:
            sale = Sale.raw_objects.create(business=self.business,date=day,customer='Ada',
                customer_master=customer,transaction_type='paid')
            SaleItem.objects.create(sale=sale,finished_good=self.bread,piece_qty=quantity,price=10)
        for kind, subject in [('product',self.bread.pk),('customer',customer.pk)]:
            body = self.metric(kind,subject=subject).json()
            self.assertEqual(body['period'],'all')
            self.assertEqual(Decimal(body['value']),50)
            self.assertEqual(body['count'],2)
        LedgerCutover.raw_objects.create(business=self.business,cutover_date=TODAY)
        self.assertEqual(Decimal(self.metric('product',subject=self.bread.pk).json()['value']),0)

    def test_validation_and_permissions(self):
        for params in [{'period':'unknown'}, {'date':'invalid'}, {'date':str(TODAY+timedelta(days=1))},
                       {'metric':'product','subject':'not-an-id'}, {'metric':'customer','subject':'9'*30}]:
            self.assertEqual(self.metric(**params).status_code,400)
        member = UserBusiness.objects.get(user=self.admin,business=self.business)
        UserModulePermission.objects.create(membership=member,module='finance',can_view=False,can_edit=False)
        cache.clear()
        self.assertEqual(self.metric('profit').status_code,403)
        self.assertEqual(self.metric('sales').status_code,200)
        self.client.logout()
        self.assertEqual(self.metric().status_code,302)

    def test_metric_periods_follow_calendar_boundaries_and_clamp_current_period(self):
        today = date(2026,11,10)
        self.assertEqual(selected_period({'period':'week','date':'2026-11-10'},today)['start'],date(2026,11,9))
        self.assertEqual(selected_period({'period':'quarter','date':'2026-11-10'},today)['end'],today)
        self.assertEqual(selected_period({'period':'month','date':'2024-02-10'},today)['end'],date(2024,2,29))
        week = selected_period({'period':'week','date':'2021-01-01'},today)
        self.assertEqual((week['start'],week['end']),(date(2020,12,28),date(2021,1,3)))
        self.assertEqual(selected_period({'period':'year','date':'2024-05-01'},today)['end'],date(2024,12,31))

    def test_metric_period_respects_active_ledger_segment(self):
        self.sell(2, 5)
        LedgerCutover.raw_objects.create(business=self.business,cutover_date=TODAY)
        self.assertEqual(Decimal(self.metric().json()['value']),0)
        self.client.post(reverse('ledger_mode'),{'mode':'backlog'})
        body = self.metric().json()
        self.assertEqual(Decimal(body['value']),10)
        self.assertIn('Backlog',body['scope'])

    def test_cards_are_present_and_subject_choices_do_not_duplicate_registered_customer(self):
        Customer.objects.create(business=self.business,name='Ada')
        self.sell(1,5,customer='Ada')
        _products, customers = metric_choices(self.business)
        self.assertEqual(sum(row['label']=='Ada' for row in customers),1)
        page = self.client.get(reverse('dashboard'), {'view':'simple'})
        for kind in ['sales','revenue','profit','product','customer']:
            self.assertContains(page,f'data-dashboard-metric="{kind}"')
