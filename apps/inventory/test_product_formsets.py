from decimal import Decimal

from django.test import TestCase

from core.models import Business
from .forms import BulkPackProfileFormSet, IndividualSaleOptionFormSet, ProductCompositionItemFormSet
from .models import BulkPackProfile, FinishedGood, IndividualSaleOption, ProductCompositionItem


class ExistingAwareProductFormSetTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            name="Portion Restaurant", slug="portion-restaurant", vertical=Business.VERTICAL_RESTAURANT
        )
        self.good = FinishedGood.raw_objects.create(
            business=self.business, name="Jollof Rice", unit="scoop",
            stock=Decimal("50"), reorder_level=Decimal("2"), selling_price=Decimal("1000"),
        )

    def test_existing_bulk_rows_do_not_inject_blank_edit_row(self):
        BulkPackProfile.raw_objects.create(
            business=self.business, finished_good=self.good, name="2 L Bowl",
            package_type="bowl", customer_quantity=2, customer_unit="litre",
            base_quantity=12, price=Decimal("9000"), min_order_quantity=1,
        )
        formset = BulkPackProfileFormSet(instance=self.good, prefix="bulk_packs", form_kwargs={"business": self.business})
        self.assertEqual(formset.initial_form_count(), 1)
        self.assertEqual(formset.total_form_count(), 1)

    def test_empty_existing_sections_keep_one_discoverable_starter_row(self):
        option_formset = IndividualSaleOptionFormSet(
            instance=self.good, prefix="individual_options", form_kwargs={"business": self.business}
        )
        composition_formset = ProductCompositionItemFormSet(
            instance=self.good, prefix="composition_items",
            form_kwargs={"business": self.business, "parent_good": self.good, "profile_choices": [("standard", "Standard portion")]},
        )
        self.assertEqual(option_formset.total_form_count(), 1)
        self.assertEqual(composition_formset.total_form_count(), 1)

    def test_individual_option_is_backed_by_same_finished_good(self):
        option = IndividualSaleOption.raw_objects.create(
            business=self.business, finished_good=self.good, name="Extra Jollof Rice",
            customer_unit="serving", base_quantity=2,
            physical_store_enabled=True, physical_store_price=Decimal("2000"),
            online_enabled=True, online_price=Decimal("2200"),
            distribution_enabled=False,
        )
        self.assertEqual(option.finished_good_id, self.good.pk)
        self.assertEqual(option.base_quantity, Decimal("2"))
        self.assertEqual(FinishedGood.raw_objects.filter(business=self.business).count(), 1)
