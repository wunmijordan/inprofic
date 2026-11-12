from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.context import set_ledger_context, raw_stock_reads_active
from core.models import Business
from .forms import FinishedGoodForm
from .models import FinishedGood


class ProductStockPrecisionTests(TestCase):
    def test_ledger_stock_precision_and_form_validation_use_the_correct_values(self):
        business = Business.objects.create(name='Stock precision', slug='stock-precision')
        product = FinishedGood.raw_objects.create(business=business, name='Buns', unit='bun', stock=10)
        data = {'name': 'Buns', 'source_type': product.source_type, 'unit': 'bun',
                'stock': '10.12', 'units_per_batch': '1', 'output_weight_grams': '0'}
        set_ledger_context('live', timezone.localdate(), 'open')
        try:
            with patch('core.ledger.item_balance', return_value=Decimal('20.120000')):
                self.assertEqual(str(product.stock), '20.12')
                form = FinishedGoodForm(data, instance=product, business=business)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.instance.__dict__['stock'], Decimal('10.12'))
                self.assertFalse(raw_stock_reads_active())
                data['stock'] = '10.123'
                invalid = FinishedGoodForm(data, instance=product, business=business)
                self.assertFalse(invalid.is_valid())
                self.assertIn('stock', invalid.errors)
        finally:
            set_ledger_context(None, None, None)

    def test_ledger_validation_does_not_hide_invalid_assigned_stock(self):
        from django.core.exceptions import ValidationError
        business = Business.objects.create(name='Assigned precision', slug='assigned-precision')
        product = FinishedGood.raw_objects.create(business=business, name='Bread', unit='loaf', stock=10)
        product.stock = Decimal('1.234')
        set_ledger_context('live', timezone.localdate(), 'open')
        try:
            with patch('core.ledger.item_balance', return_value=Decimal('20.120000')):
                with self.assertRaises(ValidationError) as error:
                    product.full_clean()
                self.assertIn('stock', error.exception.message_dict)
                self.assertFalse(raw_stock_reads_active())
        finally:
            set_ledger_context(None, None, None)
