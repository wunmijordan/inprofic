from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Business
from .forms import BulkPackProfileFormSet, IndividualSaleOptionFormSet, ProductCompositionItemFormSet, RawMaterialForm
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


    def test_optional_blank_bulk_row_does_not_block_or_create_pack(self):
        data = {
            "bulk_packs-TOTAL_FORMS": "1",
            "bulk_packs-INITIAL_FORMS": "0",
            "bulk_packs-MIN_NUM_FORMS": "0",
            "bulk_packs-MAX_NUM_FORMS": "1000",
            "bulk_packs-0-profile_key": "bulk:temporary-client-key",
            "bulk_packs-0-name": "",
            "bulk_packs-0-package_type": "",
            "bulk_packs-0-customer_quantity": "1",
            "bulk_packs-0-customer_unit": "",
            "bulk_packs-0-base_quantity": "1",
            "bulk_packs-0-price": "",
            "bulk_packs-0-min_order_quantity": "1",
            "bulk_packs-0-active": "on",
            "bulk_packs-0-sort_order": "0",
        }
        formset = BulkPackProfileFormSet(
            data,
            instance=self.good,
            prefix="bulk_packs",
            form_kwargs={"business": self.business},
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        self.assertFalse(formset.forms[0].has_changed())
        formset.save()
        self.assertFalse(BulkPackProfile.raw_objects.filter(finished_good=self.good).exists())

    def test_started_bulk_row_requires_its_own_bulk_fields(self):
        data = {
            "bulk_packs-TOTAL_FORMS": "1",
            "bulk_packs-INITIAL_FORMS": "0",
            "bulk_packs-MIN_NUM_FORMS": "0",
            "bulk_packs-MAX_NUM_FORMS": "1000",
            "bulk_packs-0-name": "Family bowl",
            "bulk_packs-0-customer_quantity": "1",
            "bulk_packs-0-customer_unit": "",
            "bulk_packs-0-base_quantity": "1",
            "bulk_packs-0-price": "",
            "bulk_packs-0-min_order_quantity": "1",
            "bulk_packs-0-active": "on",
            "bulk_packs-0-sort_order": "0",
        }
        formset = BulkPackProfileFormSet(
            data,
            instance=self.good,
            prefix="bulk_packs",
            form_kwargs={"business": self.business},
        )

        self.assertFalse(formset.is_valid())
        self.assertIn("price", formset.forms[0].errors)
        self.assertIn("customer_unit", formset.forms[0].errors)

    def test_raw_material_unit_fields_use_searchable_shared_datalist(self):
        form = RawMaterialForm(business=self.business)
        for field_name in ("purchase_unit", "package_unit", "usage_unit"):
            self.assertEqual(form.fields[field_name].widget.attrs.get("list"), "raw-material-unit-options")
            self.assertEqual(form.fields[field_name].widget.attrs.get("autocomplete"), "off")

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

    def test_individual_option_missing_enabled_channel_price_is_validation_error(self):
        option = IndividualSaleOption(
            business=self.business,
            finished_good=self.good,
            name="Unpriced Extra",
            customer_quantity=Decimal("1"),
            customer_unit="serving",
            base_quantity=Decimal("1"),
            physical_store_enabled=True,
            physical_store_price=None,
            online_enabled=False,
            distribution_enabled=False,
        )

        with self.assertRaises(ValidationError) as captured:
            option.full_clean()

        self.assertIn("physical_store_price", captured.exception.message_dict)
