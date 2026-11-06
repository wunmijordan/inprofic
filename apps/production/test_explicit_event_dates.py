from datetime import date
from django.test import TestCase
from django.urls import reverse
from accounts.models import CustomUser
from core.models import Business
from inventory.models import FinishedGood,RawMaterial,RecipeItem,StockMovement
from production.models import Order,OrderItem,ProductionBatch,ProductionRun


class ExplicitProductionDateTests(TestCase):
    def setUp(self):
        self.business=Business.objects.create(name='Explicit dates',slug='explicit-dates')
        self.user=CustomUser.objects.create_superuser(username='date-admin',password='test-password')
        self.client.force_login(self.user)
        self.material=RawMaterial.raw_objects.create(business=self.business,name='Flour',stock=100,usage_unit='kg',cost_per_unit=5)
        self.good=FinishedGood.raw_objects.create(business=self.business,name='Bread',unit='loaf',units_per_batch=10,stock=0,reorder_level=1)
        RecipeItem.objects.create(finished_good=self.good,raw_material=self.material,qty_per_batch=2)
        self.order=Order.raw_objects.create(business=self.business,date=date(2026,11,1),order_type='physical_store',status='pending')
        self.item=OrderItem.objects.create(order=self.order,finished_good=self.good,batch_qty=1,price=20)

    def test_blank_approval_and_completion_do_not_mutate_records(self):
        page=self.client.get(reverse('order_detail',args=[self.order.pk]))
        self.assertContains(page,'name="event_date" required aria-required="true"')
        self.assertContains(page,'value=""')
        response=self.client.post(reverse('order_approve',args=[self.order.pk]),{})
        self.assertEqual(response.status_code,400)
        self.assertContains(response,'Select the event date',status_code=400)
        self.order.refresh_from_db();self.material.refresh_from_db()
        self.assertEqual((self.order.status,self.material.stock),('pending',100))
        self.assertFalse(StockMovement.raw_objects.filter(business=self.business).exists())
        response=self.client.post(reverse('order_approve',args=[self.order.pk]),{'event_date':'2026-11-02'})
        self.assertEqual(response.status_code,302)
        self.order.refresh_from_db();self.assertEqual(self.order.approved_date,date(2026,11,2))
        self.assertEqual(self.client.post(reverse('order_complete',args=[self.order.pk]),{}).status_code,400)
        self.assertFalse(ProductionBatch.objects.filter(order=self.order).exists())
        self.order.refresh_from_db();self.assertEqual(self.order.status,'approved')
        prefix=f'item-{self.item.pk}-'
        payload={prefix+'produced_units':'10',prefix+'wastage_units':'0',prefix+'batch_number':'DATE-BATCH-1',
            prefix+'qc_status':'pending',prefix+'excess_to_stock':'0',prefix+'excess_to_market_stock':'0',
            prefix+'excess_to_non_stock':'0','event_date':'2026-11-03'}
        response=self.client.post(reverse('order_complete',args=[self.order.pk]),payload)
        self.assertEqual(response.status_code,302)
        self.order.refresh_from_db();self.assertEqual(self.order.completed_date,date(2026,11,3))
        self.assertEqual(ProductionBatch.objects.get(order=self.order).production_date,date(2026,11,3))
        self.good.refresh_from_db();self.assertEqual(self.good.stock,10)

    def test_shared_run_also_requires_a_selected_date(self):
        run=ProductionRun.raw_objects.create(business=self.business,date=date(2026,11,1),status='draft')
        self.assertEqual(self.client.post(reverse('production_run_approve',args=[run.pk]),{}).status_code,400)
        run.refresh_from_db();self.assertEqual(run.status,'draft')
