from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from PIL import ImageDraw

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser
from core.models import Business, CashAccount, FinancialTransaction
from core.quantities import format_product_quantity, sale_quantity_display
from production.forms import OrderItemForm
from production.proposals import ProposalLineForm
from sales.forms import SaleItemForm
from sales.models import Customer, Sale, SaleItem
from .forms import DistributionReturnForm, MarketStockReleaseItemForm, MarketStockTransferForm, ProductStockIssueForm
from .models import DistributionReturn, FinishedGood, FinishedGoodChannelPrice, MarketStockLot, ProductPortionProfile


class SellingQuantityTests(TestCase):
    def setUp(self):
        self.day = timezone.localdate()
        self.business = Business.objects.create(name='Portion bakery',slug='portion-bakery')
        self.user = CustomUser.objects.create_superuser(username='portion-admin',password='test')
        self.good = FinishedGood.raw_objects.create(business=self.business,name='Burger buns',unit='bun',stock=80,reorder_level=4,units_per_batch=20)
        self.profile = ProductPortionProfile.raw_objects.create(business=self.business,finished_good=self.good,
            active=True,customer_unit='pack',base_quantity=4,show_public_label=False)
        FinishedGoodChannelPrice.objects.create(finished_good=self.good,channel='distribution',price=100)
        self.lot = MarketStockLot.raw_objects.create(business=self.business,finished_good=self.good,
            received_date=self.day-timedelta(days=1),quantity_received=60,quantity_available=60,unit_cost=20)
        self.customer = Customer.raw_objects.create(business=self.business,name='Ada')
        self.account = CashAccount.raw_objects.create(business=self.business,name='Till')
        self.client.force_login(self.user)

    def test_portion_labels_preserve_loose_units_and_ignore_public_caption_visibility(self):
        for amount, expected in [(40,'10 packs'),(4,'1 pack'),(42,'10 packs + 2 buns'),(2,'2 buns'),(-42,'-10 packs - 2 buns'),(0,'0 packs')]:
            self.assertEqual(format_product_quantity(amount,self.good),expected)
        self.profile.active = False
        self.profile.save()
        self.good.refresh_from_db()
        self.assertEqual(format_product_quantity(40,self.good),'40 buns')

    def test_release_input_changes_only_base_quantity_once_and_preserves_money(self):
        response = self.client.post(reverse('market_stock_release'),{
            'date':str(self.day),'customer':self.customer.pk,'payment_status':'paid','payment_method':'Cash','account':self.account.pk,
            'items-TOTAL_FORMS':1,'items-INITIAL_FORMS':0,'items-MIN_NUM_FORMS':1,'items-MAX_NUM_FORMS':100,
            'items-0-finished_good':self.good.pk,'items-0-quantity':10,
        })
        self.assertEqual(response.status_code,302)
        self.lot.refresh_from_db()
        sale = Sale.raw_objects.get(business=self.business)
        item = sale.items.get()
        self.assertEqual((item.total_units,self.lot.quantity_available,sale.total),(40,20,1000))
        self.assertEqual(FinancialTransaction.raw_objects.get(business=self.business).amount,1000)
        display = sale_quantity_display(item)
        self.assertEqual((display['quantity'],display['price'],display['price_unit']),('10 packs',100,'pack'))
        summary = self.client.get(reverse('market_stock')).context
        self.assertIn('10 packs',summary['distribution_share_text'])
        self.assertIn('100.00 per pack',summary['distribution_share_text'])
        self.assertEqual(summary['distribution_rows'][0]['distributed'],40)
        self.assertIn('5 packs',summary['distribution_rows'][0]['report_cells'])
        labels = []
        original = ImageDraw.ImageDraw.text
        def capture(draw, xy, text, *args, **kwargs):
            labels.append(text)
            return original(draw, xy, text, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw,'text',capture):
            image = self.client.get(reverse('market_stock_snapshot',args=['stock']))
        self.assertEqual(image.status_code,200)
        self.assertTrue(image.content.startswith(b'\x89PNG'))
        self.assertIn('10 packs',labels)
        returned = DistributionReturnForm({'date':str(self.day),'sale_item':item.pk,'quantity':1,'condition':'redistributable','reason':'Unsold'},business=self.business)
        self.assertTrue(returned.is_valid(),returned.errors)
        self.assertEqual(returned.cleaned_data['quantity'],4)
        response = self.client.post(reverse('distribution_return'),returned.data)
        self.assertEqual(response.status_code,302)
        self.assertEqual(DistributionReturn.raw_objects.get(business=self.business).quantity,4)
        self.assertEqual(FinancialTransaction.raw_objects.filter(business=self.business).count(),1)

    def test_production_and_sales_entry_use_selling_portions(self):
        for mode, expected in [('selling',40)]:
            data={'finished_good':self.good.pk,'quantity_unit':mode,'batch_qty':1,'piece_qty':10,
                  'production_batch_qty':0,'production_piece_qty':12,'discount':4}
            form = OrderItemForm(data)
            self.assertTrue(form.is_valid(),form.errors)
            self.assertEqual(form.cleaned_data['piece_qty'],expected)
            self.assertEqual(form.cleaned_data['batch_qty'],1)
            self.assertEqual(form.cleaned_data['production_piece_qty'],48)
            self.assertEqual(form.cleaned_data['discount'],4)
        sale = SaleItemForm({'finished_good':self.good.pk,'quantity_unit':'selling','batch_qty':0,'piece_qty':10,'discount':0})
        self.assertTrue(sale.is_valid(),sale.errors)
        self.assertEqual(sale.cleaned_data['piece_qty'],40)
        self.assertNotIn('quantity_unit',sale.fields)
        self.assertIn('Burger buns · pack',str(sale['finished_good']))
        self.assertNotIn('4 buns',str(sale['finished_good']))

    def test_proposals_offer_optional_portion_entry_without_changing_saved_units(self):
        form = ProposalLineForm({'channel':'market_stock','finished_good':self.good.pk,
            'quantity':10,'quantity_unit':'selling'},business=self.business)
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.save(commit=False).quantity,40)

    def test_stock_forms_convert_and_reject_inexact_base_units(self):
        for cls, extra in [(MarketStockReleaseItemForm,{'on_date':self.day}), (MarketStockTransferForm,{}),
                           (ProductStockIssueForm,{'stock_source':'physical'})]:
            form = cls({'finished_good':self.good.pk,'quantity':2,'date':str(self.day),'reason':'staff','recipient':'Team'},business=self.business,**extra)
            self.assertTrue(form.is_valid(),form.errors)
            self.assertEqual(form.cleaned_data['quantity'],8)
        self.profile.base_quantity=Decimal('0.333')
        self.profile.save()
        form=MarketStockReleaseItemForm({'finished_good':self.good.pk,'quantity':Decimal('.01')},business=self.business,on_date=self.day)
        self.assertFalse(form.is_valid())
        self.assertIn('quantity',form.errors)

    def test_saved_commercial_quantity_and_rate_take_precedence(self):
        sale=Sale.raw_objects.create(business=self.business,date=self.day)
        item=SaleItem.objects.create(sale=sale,finished_good=self.good,piece_qty=40,
            commercial_quantity=2,commercial_unit='box',commercial_unit_price=2000)
        display=sale_quantity_display(item)
        self.assertEqual((display['quantity'],display['price'],item.line_total),('2 boxes',2000,4000))
