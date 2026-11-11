from datetime import date
from decimal import Decimal
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from accounts.models import CustomUser,UserBusiness,UserModulePermission
from accounts.services import seed_business_roles
from core.models import Business
from inventory.models import FinishedGood
from sales.models import Customer,CustomerProductPrice,CustomerPayment,Sale,SaleItem


class CustomerActivityTests(TestCase):
    def setUp(self):
        self.business=Business.objects.create(name='Customer activity',slug='customer-activity')
        self.admin=CustomUser.objects.create_superuser(username='activity-admin',password='test-password')
        self.customer=Customer.raw_objects.create(business=self.business,name='Ada',region='Lagos',customer_group='Trade')
        self.good=FinishedGood.raw_objects.create(business=self.business,name='Bread',unit='loaf',units_per_batch=1)
        CustomerProductPrice.raw_objects.create(business=self.business,customer=self.customer,finished_good=self.good,channel='distribution',price=80)
        self.client.force_login(self.admin)
        self.url=reverse('customer_activity',args=[self.customer.pk])
        self.dates={'start':'2026-10-01','end':'2026-10-31'}

    def sale(self,day,*,master=True,name='Ada',price=50):
        sale=Sale.raw_objects.create(business=self.business,date=day,customer_master=self.customer if master else None,
            customer=name,transaction_type='unpaid',source='distribution_order')
        SaleItem.objects.create(sale=sale,finished_good=self.good,piece_qty=2,price=price)
        return sale

    def test_period_activity_uses_frozen_prices_and_includes_linked_renamed_and_legacy_records(self):
        sale=self.sale(date(2026,10,2),name='Old customer name')
        self.sale(date(2026,10,3),master=False)
        self.sale(date(2026,9,30))
        CustomerPayment.raw_objects.create(business=self.business,sale=sale,date=date(2026,10,5),amount=20,customer='Old customer name')
        response=self.client.get(self.url,self.dates)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['sale_count'],2)
        self.assertEqual(response.context['invoiced'],200)
        self.assertEqual(response.context['recorded_payments'],20)
        self.assertContains(response,'80.00 / loaf')
        self.assertContains(response,'2 loaves')
        self.assertEqual(response.context['outstanding'],280)
        detail=self.client.get(reverse('dashboard_search_detail'),{'type':'customer','channel':'distribution','value':self.customer.name})
        self.assertEqual(detail.status_code,200)
        self.assertEqual(detail.json()['activity_url'],self.url)

    def test_customer_scope_access_and_invalid_dates(self):
        other=Business.objects.create(name='Private',slug='private-customer-activity')
        private=Customer.raw_objects.create(business=other,name='PRIVATE CUSTOMER')
        self.assertEqual(self.client.get(reverse('customer_activity',args=[private.pk])).status_code,404)
        response=self.client.get(self.url,{'start':'2026-10-31','end':'2026-10-01'})
        self.assertContains(response,'end date must be on or after')
        self.assertNotIn('sales',response.context)
        roles=seed_business_roles(self.business)
        user=CustomUser.objects.create_user(username='activity-denied',password='test-password')
        member=UserBusiness.objects.create(business=self.business,user=user,role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        UserModulePermission.objects.create(membership=member,module='sales',can_view=False,can_edit=False)
        cache.clear();self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code,403)

    def test_activity_is_paginated_and_other_customers_are_excluded(self):
        for _ in range(26):self.sale(date(2026,10,1))
        self.sale(date(2026,10,1),master=False,name='Someone else',price=999)
        response=self.client.get(self.url,self.dates)
        self.assertEqual(response.context['sale_count'],26)
        self.assertEqual(len(response.context['sales']),25)
        self.assertEqual(response.context['invoiced'],2600)
        self.assertEqual(len(self.client.get(self.url,{**self.dates,'sales_page':'2'}).context['sales']),1)
        self.assertEqual(self.client.post(self.url,{}).status_code,405)

    def test_production_and_returns_sections_follow_their_module_permissions(self):
        from production.models import Order
        from inventory.models import DistributionReturn
        sale=self.sale(date(2026,10,2))
        Order.raw_objects.create(business=self.business,date=date(2026,10,2),customer=self.customer,
            customer_name=self.customer.name,order_type='distribution',status='pending')
        DistributionReturn.raw_objects.create(business=self.business,date=date(2026,10,3),sale_item=sale.items.get(),
            quantity=1,condition=DistributionReturn.REDISTRIBUTABLE,reason='Customer return')
        page=self.client.get(self.url,self.dates)
        self.assertEqual(len(page.context['orders']),1)
        self.assertEqual(len(page.context['returns']),1)
        roles=seed_business_roles(self.business)
        user=CustomUser.objects.create_user(username='activity-sales-only',password='test-password')
        member=UserBusiness.objects.create(business=self.business,user=user,role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        for module in ('production','inventory'):
            UserModulePermission.objects.create(membership=member,module=module,can_view=False,can_edit=False)
        cache.clear();self.client.force_login(user)
        page=self.client.get(self.url,self.dates)
        self.assertEqual(page.status_code,200)
        self.assertNotIn('orders',page.context)
        self.assertNotIn('returns',page.context)
