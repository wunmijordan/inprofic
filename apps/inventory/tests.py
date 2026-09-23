from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import CustomUser
from core.models import Business, CashAccount
from core.pdf_fonts import PDF_MONO_MEDIUM_FONT
from sales.forms import SaleItemForm
from sales.models import Customer, Sale
from .forms import FinishedGoodChannelPriceForm, FinishedGoodChannelPriceFormSet, FinishedGoodForm, ProductPortionProfileForm, RawMaterialForm
from .models import (
    DistributionReturn,
    FinishedGood,
    MarketStockLot,
    MarketStockMovement,
    RawMaterial,
    StockMovement,
)
from .services import (
    reconcile_expired_market_lot,
    consume_transferred_physical_stock,
    record_distribution_return,
    record_finished_good_movement,
    release_market_stock,
    transfer_market_stock_to_physical,
)
from .views import _raw_material_stock_breakdown_markup


class FinishedGoodChannelPriceFormTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            name="Channel Price Bakery",
            slug="channel-price-bakery",
            vertical=Business.VERTICAL_BAKERY,
        )

    def test_vertical_channel_labels_keep_optional_blank_choice(self):
        form = FinishedGoodChannelPriceForm(business=self.business)

        choices = list(form.fields["channel"].choices)
        self.assertEqual(choices[0][0], "")
        self.assertEqual(choices[0][1], "Select channel")
        self.assertIn(("online", "Online Order"), choices)

    def test_untouched_optional_channel_rows_do_not_block_product_save(self):
        parent = FinishedGood(business=self.business)
        data = {
            "channel_prices-TOTAL_FORMS": "3",
            "channel_prices-INITIAL_FORMS": "0",
            "channel_prices-MIN_NUM_FORMS": "0",
            "channel_prices-MAX_NUM_FORMS": "3",
        }
        for index in range(3):
            data[f"channel_prices-{index}-channel"] = ""
            data[f"channel_prices-{index}-price"] = ""

        formset = FinishedGoodChannelPriceFormSet(
            data,
            instance=parent,
            prefix="channel_prices",
            form_kwargs={"business": self.business},
        )

        self.assertTrue(formset.is_valid(), formset.errors)

    def test_selected_channel_requires_a_price_with_clear_message(self):
        form = FinishedGoodChannelPriceForm(
            data={"channel": "online", "price": ""},
            business=self.business,
        )

        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["price"], ["Enter a price for this channel."])


class StockFirstVerticalFormTests(TestCase):
    def setUp(self):
        self.retail = Business.objects.create(
            name="Stock First Retail", slug="stock-first-retail", vertical=Business.VERTICAL_RETAIL
        )
        self.wholesale = Business.objects.create(
            name="Stock First Wholesale", slug="stock-first-wholesale", vertical=Business.VERTICAL_WHOLESALE
        )

    def test_retail_finished_good_omits_production_only_fields(self):
        form = FinishedGoodForm(business=self.retail)
        self.assertNotIn("source_type", form.fields)
        self.assertNotIn("units_per_batch", form.fields)
        self.assertNotIn("base_material", form.fields)
        self.assertEqual(form.fields["unit"].label, "Stock / selling unit")
        self.assertEqual(form.instance.source_type, FinishedGood.SOURCE_PURCHASED_FOR_RESALE)

    def test_wholesale_portion_uses_stock_first_language(self):
        form = ProductPortionProfileForm(business=self.wholesale)
        self.assertEqual(form.fields["active"].label, "Use a standard customer selling unit")
        self.assertEqual(form.fields["base_quantity"].label, "Stock units per selling unit")

    def test_nonproduction_supply_form_uses_procurement_language(self):
        form = RawMaterialForm(business=self.retail)
        self.assertEqual(form.fields["stock_purchase_units"].label, "Opening stock (supplier units)")
        self.assertEqual(form.fields["usage_conversion_factor"].label, "Issue / stock conversion")
        self.assertIn("Stock Products", form.fields["category"].help_text)



class RawMaterialPdfStockBreakdownTests(SimpleTestCase):
    def test_pdf_markup_matches_inventory_purchase_and_usage_breakdown(self):
        material = RawMaterial(
            purchase_unit="bag",
            package_qty=Decimal("50"),
            package_unit="kg",
            usage_unit="kg",
            usage_conversion_factor=Decimal("1"),
            stock=Decimal("130"),
        )

        self.assertEqual(
            _raw_material_stock_breakdown_markup(material),
            f"<font name='{PDF_MONO_MEDIUM_FONT}'>2</font> "
            "<font color='#78716C' size='9'><b><i>bags</i></b></font>, "
            f"<font name='{PDF_MONO_MEDIUM_FONT}'>30.00</font> "
            "<font color='#78716C' size='9'><b><i>kg</i></b></font>",
        )

    def test_pdf_markup_keeps_singular_purchase_unit_for_one(self):
        material = RawMaterial(
            purchase_unit="carton",
            package_qty=Decimal("12"),
            usage_unit="piece",
            usage_conversion_factor=Decimal("1"),
            stock=Decimal("17"),
        )

        markup = _raw_material_stock_breakdown_markup(material)

        self.assertIn("<i>carton</i>", markup)
        self.assertIn(f"<font name='{PDF_MONO_MEDIUM_FONT}'>5.00</font>", markup)


class RawMaterialManualStockInputTests(SimpleTestCase):
    def test_six_decimal_purchase_fraction_preserves_three_decimal_stock(self):
        business = Business(name="Butter Bakery", vertical=Business.VERTICAL_BAKERY)
        form = RawMaterialForm(
            data={
                "name": "Butter",
                "category": RawMaterial.CATEGORY_INGREDIENT,
                "purchase_unit": "carton",
                "package_qty": "15",
                "package_unit": "kg",
                "usage_unit": "kg",
                "usage_conversion_factor": "1",
                "reorder_level_purchase_units": "0",
                "stock_purchase_units": "0.750133",
                "cost_per_purchase_unit": "0",
            },
            business=business,
        )

        self.assertTrue(form.is_valid(), form.errors)
        material = form.save(commit=False)
        self.assertEqual(material.stock, Decimal("11.252"))

    def test_density_based_volume_package_fraction_preserves_weighed_stock(self):
        business = Business(name="Milk Bakery", vertical=Business.VERTICAL_BAKERY)
        form = RawMaterialForm(
            data={
                "name": "Powdered Milk",
                "category": RawMaterial.CATEGORY_INGREDIENT,
                "purchase_unit": "container",
                "package_qty": "4",
                "package_unit": "litre",
                "usage_unit": "kg",
                # 1 litre of this powder weighs 0.56kg.
                "usage_conversion_factor": "0.56",
                "reorder_level_purchase_units": "0",
                # 1.356kg / (4L * 0.56kg/L) = 0.605357 container.
                "stock_purchase_units": "0.605357",
                "cost_per_purchase_unit": "0",
            },
            business=business,
        )

        self.assertTrue(form.is_valid(), form.errors)
        material = form.save(commit=False)
        self.assertEqual(material.stock, Decimal("1.356"))


class RawMaterialPdfThemeTests(TestCase):
    def test_tenant_themed_pdf_renders_successfully(self):
        business = Business.objects.create(
            name="Blue Kitchen",
            slug="blue-kitchen",
            background_color="#173B45",
            accent_color="#D6A84B",
        )
        RawMaterial.raw_objects.create(
            business=business,
            name="Flour",
            purchase_unit="bag",
            package_qty=Decimal("50"),
            package_unit="kg",
            usage_unit="kg",
            usage_conversion_factor=Decimal("1"),
            stock=Decimal("130"),
            reorder_level=Decimal("50"),
        )
        user = CustomUser.objects.create_superuser(
            username="pdf-admin",
            password="safe-password-123",
            fullname="PDF Admin",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("raw_material_inventory_pdf"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertGreater(len(response.content), 1000)


class DistributionMarketStockTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Market Bakery", slug="market-bakery")
        self.user = CustomUser.objects.create_superuser(
            username="market-admin", password="safe-password-123", fullname="Market Admin"
        )
        self.customer = Customer.raw_objects.create(
            business=self.business, created_by=self.user, name="Distributor One"
        )
        self.good = FinishedGood.raw_objects.create(
            business=self.business,
            created_by=self.user,
            name="Distribution Bread",
            unit="loaf",
            units_per_batch=Decimal("10"),
            stock=None,
            reorder_level=None,
            selling_price=Decimal("1000"),
        )
        self.account = CashAccount.raw_objects.create(
            business=self.business, created_by=self.user, name="Bank", account_type="bank"
        )

    def make_lot(self, *, quantity="10", expiry=None, cost="400"):
        return MarketStockLot.raw_objects.create(
            business=self.business,
            created_by=self.user,
            finished_good=self.good,
            source=MarketStockLot.SOURCE_PRODUCTION,
            received_date=date(2026, 9, 1),
            expiry_date=expiry,
            quantity_received=Decimal(quantity),
            quantity_available=Decimal(quantity),
            unit_cost=Decimal(cost),
        )

    def test_release_uses_first_expiry_first_out_and_creates_distribution_sale(self):
        later = self.make_lot(quantity="7", expiry=date(2026, 9, 20), cost="450")
        earlier = self.make_lot(quantity="5", expiry=date(2026, 9, 10), cost="400")

        sale = release_market_stock(
            business=self.business,
            good=self.good,
            customer=self.customer,
            quantity=Decimal("6"),
            date=date(2026, 9, 3),
            payment_status="unpaid",
            payment_method="Transfer",
            account=None,
            user=self.user,
        )

        earlier.refresh_from_db()
        later.refresh_from_db()
        self.good.refresh_from_db()
        self.assertEqual(earlier.quantity_available, Decimal("0.00"))
        self.assertEqual(later.quantity_available, Decimal("6.00"))
        self.assertEqual(sale.source, "distribution_order")
        self.assertEqual(sale.transaction_type, "unpaid")
        self.assertEqual(sale.items.count(), 2)
        self.assertEqual(sale.total, Decimal("6000.00"))
        self.assertEqual(self.good.total_delivered_to_customers, Decimal("6.00"))
        self.assertEqual(
            MarketStockMovement.raw_objects.filter(movement_type=MarketStockMovement.RELEASE).count(),
            2,
        )

    def test_transfer_is_the_shelf_exception_for_non_physical_product(self):
        lot = self.make_lot(quantity="8")

        transfer_market_stock_to_physical(
            business=self.business,
            good=self.good,
            quantity=Decimal("3"),
            date=date(2026, 9, 3),
            user=self.user,
            reason="Remaining route stock moved to counter",
        )

        lot.refresh_from_db()
        self.good.refresh_from_db()
        self.assertEqual(lot.quantity_available, Decimal("5.00"))
        self.assertEqual(self.good.stock, Decimal("3.00"))
        self.assertEqual(self.good.transferred_market_stock, Decimal("3.00"))
        self.assertTrue(self.good.can_sell_from_physical_store)
        self.assertEqual(self.good.physical_saleable_stock, Decimal("3.00"))
        self.assertTrue(SaleItemForm().fields["finished_good"].queryset.filter(pk=self.good.pk).exists())
        movement = StockMovement.raw_objects.get(movement_type=StockMovement.FG_MARKET_TRANSFER)
        self.assertTrue(movement.affects_stock)

        record_finished_good_movement(
            self.good,
            Decimal("-2"),
            StockMovement.FG_SALE,
            note="Shelf sale of transferred Distribution stock",
        )
        consume_transferred_physical_stock(self.good, Decimal("2"))
        self.good.refresh_from_db()
        self.assertEqual(self.good.stock, Decimal("1.00"))
        self.assertEqual(self.good.transferred_market_stock, Decimal("1.00"))

    def test_redistributable_return_reenters_market_stock_and_damage_is_written_off(self):
        self.make_lot(quantity="10", cost="400")
        sale = release_market_stock(
            business=self.business,
            good=self.good,
            customer=self.customer,
            quantity=Decimal("5"),
            date=date(2026, 9, 3),
            payment_status="unpaid",
            payment_method="Transfer",
            account=None,
            user=self.user,
        )
        sale_item = sale.items.get()

        redistributable = record_distribution_return(
            business=self.business,
            sale_item=sale_item,
            quantity=Decimal("2"),
            date=date(2026, 9, 4),
            condition=DistributionReturn.REDISTRIBUTABLE,
            reason="Distributor could not sell remaining units",
            user=self.user,
        )
        damaged = record_distribution_return(
            business=self.business,
            sale_item=sale_item,
            quantity=Decimal("1"),
            date=date(2026, 9, 4),
            condition=DistributionReturn.DAMAGED,
            reason="Crushed during return transit",
            user=self.user,
        )

        self.assertEqual(redistributable.market_lot.quantity_available, Decimal("2"))
        self.assertIsNone(damaged.market_lot)
        self.assertEqual(damaged.writeoff_value, Decimal("400.000000"))
        self.assertTrue(
            StockMovement.raw_objects.filter(
                movement_type=StockMovement.FG_DISTRIBUTION_DAMAGE,
                quantity=Decimal("-1"),
                affects_stock=False,
            ).exists()
        )

    def test_expired_lot_is_unsellable_then_reconciled_at_frozen_cost(self):
        lot = self.make_lot(
            quantity="4",
            expiry=date(2026, 9, 2),
            cost="350",
        )
        with self.assertRaises(ValidationError):
            release_market_stock(
                business=self.business,
                good=self.good,
                customer=self.customer,
                quantity=Decimal("1"),
                date=date(2026, 9, 3),
                payment_status="unpaid",
                payment_method="Transfer",
                account=None,
                user=self.user,
            )

        reconciled = reconcile_expired_market_lot(
            business=self.business,
            lot=lot,
            date=date(2026, 9, 3),
            user=self.user,
            reason="Shelf life elapsed",
        )

        lot.refresh_from_db()
        self.assertEqual(reconciled, Decimal("4"))
        self.assertEqual(lot.quantity_available, Decimal("0"))
        self.assertEqual(lot.closed_reason, "expired")
        expiry = MarketStockMovement.raw_objects.get(movement_type=MarketStockMovement.EXPIRY)
        self.assertEqual(expiry.value, Decimal("-1400.00000000"))

    def test_market_lot_cannot_be_released_before_its_receipt_date(self):
        self.make_lot(quantity="2")

        with self.assertRaises(ValidationError):
            release_market_stock(
                business=self.business,
                good=self.good,
                customer=self.customer,
                quantity=Decimal("1"),
                date=date(2026, 8, 31),
                payment_status="unpaid",
                payment_method="Transfer",
                account=None,
                user=self.user,
            )

    def test_force_sale_cannot_exceed_non_physical_market_transfer_allowance(self):
        self.make_lot(quantity="3")
        transfer_market_stock_to_physical(
            business=self.business,
            good=self.good,
            quantity=Decimal("3"),
            date=date(2026, 9, 3),
            user=self.user,
            reason="Counter test",
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()

        response = self.client.post(reverse("sale_add"), {
            "date": "2026-09-03",
            "customer": "Walk-in",
            "transaction_type": "unpaid",
            "unpaid_description": "Test issue",
            "payment_method": "Cash",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-finished_good": str(self.good.pk),
            "items-0-batch_qty": "0",
            "items-0-piece_qty": "4",
            "items-0-discount": "0",
            "force": "1",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot exceed the quantity explicitly transferred")
        self.assertEqual(Sale.raw_objects.filter(business=self.business).count(), 0)
        self.good.refresh_from_db()
        self.assertEqual(self.good.stock, Decimal("3.00"))
        self.assertEqual(self.good.transferred_market_stock, Decimal("3.00"))

    def test_return_quantity_cannot_exceed_original_distribution_sale_line(self):
        self.make_lot(quantity="3")
        sale = release_market_stock(
            business=self.business,
            good=self.good,
            customer=self.customer,
            quantity=Decimal("3"),
            date=date(2026, 9, 3),
            payment_status="unpaid",
            payment_method="Transfer",
            account=None,
            user=self.user,
        )
        with self.assertRaises(ValidationError):
            record_distribution_return(
                business=self.business,
                sale_item=sale.items.get(),
                quantity=Decimal("4"),
                date=date(2026, 9, 4),
                condition=DistributionReturn.REDISTRIBUTABLE,
                reason="Invalid over-return",
                user=self.user,
            )

    def test_market_stock_pages_render_for_inventory_editor(self):
        self.make_lot(quantity="3")
        self.client.force_login(self.user)

        for route_name in (
            "market_stock",
            "market_stock_release",
            "market_stock_transfer",
            "distribution_return",
        ):
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
