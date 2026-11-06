from datetime import date
from decimal import Decimal
from django.test import TestCase
from accounts.models import CustomUser
from core.models import Business
from core.quantities import format_quantity,material_stock_label,plural_unit
from inventory.models import RawMaterial
from expenses.models import Expense
from procurement.models import PurchaseOrder,PurchaseOrderItem
from procurement.share import summarize,share_text,render_snapshot
from .daily_brief import build_brief
from .executive_brief import enrich,full_text


class SummaryReadabilityTests(TestCase):
    def setUp(self):
        self.business=Business.objects.create(name='Readable',slug='readable')
        self.user=CustomUser.objects.create_superuser(username='readable-admin',password='test-password')
        self.material=RawMaterial.raw_objects.create(business=self.business,name='Flour',stock=70,
            purchase_unit='bag',package_qty=50,package_unit='kg',usage_unit='kg',cost_per_unit=1)

    def test_global_quantity_and_package_breakdown(self):
        self.assertEqual(format_quantity(2,'loaf'),'2 loaves')
        self.assertEqual(format_quantity(1,'bun'),'1 bun')
        self.assertEqual(format_quantity(3,'plate'),'3 plates')
        self.assertEqual(format_quantity(20,'kg'),'20 kg')
        self.assertEqual(material_stock_label(self.material),'1 bag, 20 kg')

    def test_brief_omits_distribution_section_reorder_and_zero_money_fields(self):
        day=date(2026,11,6)
        Expense.raw_objects.create(business=self.business,date=day,description='Transport to market',amount=200,category='transport')
        brief=enrich(build_brief(self.business,day),day,day,user=self.user)
        text=full_text(brief)
        self.assertNotIn('Distribution ·',text)
        self.assertNotIn('reorder',text.lower())
        self.assertIn('1 bag, 20 kg',text)
        self.assertIn('Miscellaneous expenses:',text)
        self.assertIn('Transport to market',text)
        self.assertNotIn('₦0.00',text)
        self.assertFalse(any(s['key']=='distribution' for s in brief['executive_sections']))

    def test_procurement_expenses_are_scoped_by_business_and_request_date(self):
        day=date(2026,11,6)
        po=PurchaseOrder.raw_objects.create(business=self.business,date=day,supplier='Vendor',status='draft')
        PurchaseOrderItem.objects.create(purchase_order=po,raw_material=self.material,qty=1,unit_cost=100)
        Expense.raw_objects.create(business=self.business,date=day,description='Delivery charge',amount=20)
        Expense.raw_objects.create(business=self.business,date=date(2026,11,5),description='Different day',amount=99)
        other=Business.objects.create(name='Other',slug='other-readable')
        Expense.raw_objects.create(business=other,date=day,description='PRIVATE EXPENSE',amount=888)
        summary=summarize(po,self.business,user=self.user)
        text=share_text(summary)
        self.assertEqual(summary['total'],100)
        self.assertEqual(summary['miscellaneous_total'],20)
        self.assertIn('Delivery charge',text)
        self.assertNotIn('Different day',text)
        self.assertNotIn('PRIVATE EXPENSE',text)
        self.assertTrue(render_snapshot(summary).startswith(b'\x89PNG'))

    def test_expenses_are_not_disclosed_without_expenses_or_finance_access(self):
        from accounts.models import UserBusiness, UserModulePermission
        from accounts.services import seed_business_roles
        from django.core.cache import cache
        roles=seed_business_roles(self.business)
        user=CustomUser.objects.create_user(username='procurement-only',password='test-password')
        membership=UserBusiness.objects.create(business=self.business,user=user,role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        for module in ('expenses','finance'):
            UserModulePermission.objects.create(membership=membership,module=module,can_view=False,can_edit=False)
        cache.clear()
        day=date(2026,11,6)
        po=PurchaseOrder.raw_objects.create(business=self.business,date=day,status='draft')
        Expense.raw_objects.create(business=self.business,date=day,description='Private expense',amount=200)
        self.assertEqual(summarize(po,self.business,user=user)['miscellaneous'],[])

    def test_quiet_sections_remain_selectable_and_distribution_preferences_move_to_sales(self):
        day=date(2026,11,6)
        brief=enrich(build_brief(self.business,day),day,day,user=self.user,selected_sections=['distribution'])
        options={row['key']:row for row in brief['report_options']}
        self.assertTrue(options['sales']['checked'])
        self.assertIn('production',options)
        self.assertIn('purchasing',options)
        self.assertNotIn('distribution',options)
        self.assertEqual(brief['selected_sections'],['sales'])

    def test_market_balances_remain_in_product_stock_without_a_distribution_section(self):
        from inventory.models import FinishedGood,MarketStockLot
        from django.utils import timezone
        from datetime import timedelta
        day=timezone.localdate()
        good=FinishedGood.raw_objects.create(business=self.business,name='Market Bread',unit='loaf',stock=None)
        for expiry,quantity in [(None,12),(day-timedelta(days=1),3)]:
            MarketStockLot.raw_objects.create(business=self.business,finished_good=good,received_date=day-timedelta(days=2),
                expiry_date=expiry,quantity_received=quantity,quantity_available=quantity)
        brief=enrich(build_brief(self.business,day),day,day,user=self.user)
        text=full_text(brief)
        self.assertIn('Market Bread · Market Stock: 12 loaves',text)
        self.assertIn('EXPIRED · Market Bread · Market Stock: 3 loaves',text)
        self.assertFalse(any(row['key']=='distribution' for row in brief['executive_sections']))
