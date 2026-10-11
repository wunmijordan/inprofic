from decimal import Decimal

from django.test import TestCase

from core.models import Business
from inventory.models import FinishedGood

from .models import CommerceSettings, StorefrontProduct
from .views import _public_catalog_data


class StorefrontVariantStockTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Variant Store", slug="variant-store", vertical=Business.VERTICAL_RETAIL)
        CommerceSettings.raw_objects.get_or_create(business=self.business)

    def _product(self, name, stock, parent=None):
        good = FinishedGood.raw_objects.create(
            business=self.business, name=name, unit="piece", stock=Decimal(stock), selling_price=Decimal("1000"),
            source_type=FinishedGood.SOURCE_PURCHASED_FOR_RESALE, variant_of=parent,
        )
        return StorefrontProduct.raw_objects.create(business=self.business, finished_good=good, published=True)

    def _cards(self):
        return {p.display_name: [v.display_name for v in p.public_variants] for p in _public_catalog_data(self.business)["products"]}

    def test_sold_out_primary_is_replaced_by_next_in_stock_variant(self):
        primary = self._product("Cola", "0")
        self._product("Cola Large", "5", primary.finished_good)
        self._product("Cola Mini", "0", primary.finished_good)
        self._product("Cola Zero", "3", primary.finished_good)
        self.assertEqual(self._cards(), {"Cola Large": ["Cola Zero"]})

    def test_sold_out_variant_is_not_offered(self):
        primary = self._product("Juice", "4")
        self._product("Juice Big", "0", primary.finished_good)
        self._product("Juice Small", "2", primary.finished_good)
        self.assertEqual(self._cards(), {"Juice": ["Juice Small"]})

    def test_card_disappears_when_nothing_is_in_stock(self):
        primary = self._product("Water", "0")
        self._product("Water Big", "0", primary.finished_good)
        self.assertEqual(self._cards(), {})
