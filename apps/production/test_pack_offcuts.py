from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from accounts.models import CustomUser
from core.models import Business
from inventory.models import FinishedGood, RawMaterial, RecipeItem, ProductPortionProfile
from sales.models import Customer, Sale
from .models import Order, ProductionBatch


class PackOffcutTests(TestCase):
    def test_separate_pack_target_flows_through_approval_and_completion(self):
        day=timezone.localdate()
        business=Business.objects.create(name='Pack offcuts',slug='pack-offcuts')
        user=CustomUser.objects.create_superuser(username='offcut-admin',password='test')
        self.client.force_login(user)
        material=RawMaterial.raw_objects.create(business=business,name='Flour',stock=100,usage_unit='kg',cost_per_unit=2)
        good=FinishedGood.raw_objects.create(business=business,name='Buns',unit='bun',units_per_batch=20,stock=0,reorder_level=1,selling_price=1100)
        ProductPortionProfile.raw_objects.create(business=business,finished_good=good,active=True,customer_unit='pack',base_quantity=4)
        RecipeItem.objects.create(finished_good=good,raw_material=material,qty_per_batch=20)
        customer=Customer.raw_objects.create(business=business,name='Ada')
        response=self.client.post(reverse('order_add'),{'date':str(day),'order_type':'distribution','customer':customer.pk,
            'production_basis':'product_quantity','customer_payment_status':'unpaid','transaction_type':'unpaid',
            'items-TOTAL_FORMS':1,'items-INITIAL_FORMS':0,'items-MIN_NUM_FORMS':0,'items-MAX_NUM_FORMS':1000,
            'items-0-finished_good':good.pk,'items-0-batch_qty':0,'items-0-piece_qty':10,
            'items-0-production_batch_qty':0,'items-0-production_piece_qty':12,'items-0-discount':0})
        self.assertEqual(response.status_code,302,getattr(response,'context',None))
        order=Order.raw_objects.get(business=business);item=order.items.get()
        self.assertEqual((item.total_units,item.production_total_units,item.planned_overproduction_units),(40,48,8))
        edit=self.client.get(reverse('order_edit',args=[order.pk]))
        self.assertEqual(edit.context['formset'].forms[0].initial['production_piece_qty'],12)
        self.assertContains(edit,'Planned offcut')
        response=self.client.post(reverse('order_approve',args=[order.pk]),{'event_date':str(day)})
        order.refresh_from_db();self.assertEqual(order.status,'approved')
        material.refresh_from_db();self.assertEqual(material.stock,52)
        prefix=f'item-{item.pk}-'
        values={'produced_units':48,'wastage_units':0,'batch_number':'PACK-1','qc_status':'pending',
            'planned_surplus_stock_units':8,'excess_to_stock':0,'excess_to_market_stock':0,'excess_to_non_stock':0}
        response=self.client.post(reverse('order_complete',args=[order.pk]),{'event_date':str(day),f'offcut-{item.pk}-TOTAL_FORMS':0,f'offcut-{item.pk}-INITIAL_FORMS':0,**{prefix+k:str(v) for k,v in values.items()}})
        order.refresh_from_db();self.assertEqual(order.status,'completed',[(form.errors, offcuts.errors if offcuts else None) for _,form,offcuts in response.context['completion_forms']] if response.context else None)
        batch=ProductionBatch.raw_objects.get(order=order)
        self.assertEqual((batch.ordered_units,batch.planned_units,batch.planned_surplus_stock_units,batch.excess_units),(40,48,8,0))
        good.refresh_from_db();self.assertEqual(good.stock,8)
        sale=Sale.raw_objects.get(linked_order=order)
        self.assertEqual(sale.total,11000)

    def test_target_below_customer_quantity_reports_error_on_visible_field(self):
        from .forms import OrderItemFormSet
        business = Business.objects.create(name='Target validation', slug='target-validation')
        good = FinishedGood.raw_objects.create(business=business, name='Buns', unit='bun', units_per_batch=20)
        formset = OrderItemFormSet({
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
            'items-0-finished_good': good.pk, 'items-0-piece_qty': '10',
            'items-0-production_piece_qty': '8',
        }, prefix='items', instance=Order(business=business), business=business, order_type='distribution')
        self.assertFalse(formset.is_valid())
        self.assertIn('production_piece_qty', formset.forms[0].errors)
