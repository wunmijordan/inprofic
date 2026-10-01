from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from core.models import Business
from inventory.models import FinishedGood, MarketStockLot, StockMovement
from sales.models import Sale
from .forms import OrderForm, OrderItemFormSet
from .models import Order, OrderItem, OrderNumberSequence


class BusinessOrderNumberingTests(TestCase):
    def setUp(self):
        self.a = Business.objects.create(name="Business A", slug="business-a")
        self.b = Business.objects.create(name="Business B", slug="business-b")

    def make_order(self, business):
        return Order.raw_objects.create(
            business=business,
            date=date(2026, 9, 2),
            order_type="physical_store",
            production_destination="store",
        )

    def test_numbering_is_independent_per_business(self):
        a1 = self.make_order(self.a)
        b1 = self.make_order(self.b)
        a2 = self.make_order(self.a)
        self.assertEqual(a1.order_number, 1)
        self.assertEqual(b1.order_number, 1)
        self.assertEqual(a2.order_number, 2)
        self.assertNotEqual(a1.pk, b1.pk)

    def test_resetting_one_empty_business_does_not_change_another(self):
        a1 = self.make_order(self.a)
        b1 = self.make_order(self.b)
        a1.delete()
        OrderNumberSequence.raw_objects.update_or_create(business=self.a, defaults={"next_number": 1})
        a_again = self.make_order(self.a)
        b2 = self.make_order(self.b)
        self.assertEqual(a_again.order_number, 1)
        self.assertEqual(b2.order_number, 2)
        self.assertEqual(b1.order_number, 1)


class VerticalProductionUiTests(TestCase):
    def test_restaurant_uses_restaurant_order_vocabulary(self):
        business = Business.objects.create(
            name="Kitchen", slug="kitchen", vertical=Business.VERTICAL_RESTAURANT
        )
        roles = seed_business_roles(business)
        user = CustomUser.objects.create_user(
            username="kitchen.admin", password="safe-password-123", fullname="Kitchen Admin"
        )
        UserBusiness.objects.create(
            user=user, business=business,
            role=roles[CustomUser.ROLE_BUSINESS_ADMIN],
        )
        self.client.force_login(user)

        response = self.client.get(reverse("order_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Catering / Bulk Order")
        self.assertContains(response, "Kitchen / Counter Replenishment")
        self.assertNotContains(response, ">Physical Store Order<")


class MarketStockDistributionTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Bakery", slug="bakery")

    def test_market_stock_distribution_does_not_require_customer_or_payment(self):
        form = OrderForm(
            data={
                "date": "2026-09-02",
                "order_type": "distribution",
                "is_market_stock": "on",
                "notes": "Produce ahead of demand",
            },
            business=self.business,
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        order = form.save(commit=False)
        self.assertTrue(order.is_market_stock_order)
        self.assertFalse(order.is_customer_order)
        self.assertIsNone(order.customer)
        self.assertEqual(order.customer_payment_status, "paid")
        self.assertIsNone(order.customer_payment_account)

    def test_assigned_distribution_still_requires_customer(self):
        form = OrderForm(
            data={
                "date": "2026-09-02",
                "order_type": "distribution",
                "customer_payment_status": "unpaid",
            },
            business=self.business,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("customer", form.errors)

    def test_market_stock_order_can_select_distribution_only_product(self):
        good = FinishedGood.raw_objects.create(
            business=self.business,
            name="Distribution-only Bread",
            unit="loaf",
            stock=None,
            reorder_level=None,
        )
        formset = OrderItemFormSet(
            instance=Order(business=self.business, order_type="distribution", is_market_stock=True),
            market_stock=True,
            order_type="distribution",
        )

        self.assertTrue(
            formset.forms[0].fields["finished_good"].queryset.filter(pk=good.pk).exists()
        )

    def test_completion_puts_market_output_in_stock_without_creating_sale(self):
        user = CustomUser.objects.create_superuser(
            username="admin", password="safe-password-123", fullname="Admin"
        )
        self.client.force_login(user)
        good = FinishedGood.raw_objects.create(
            business=self.business,
            name="Bread",
            unit="loaf",
            units_per_batch=Decimal("10"),
            stock=None,
            reorder_level=None,
            selling_price=Decimal("1000"),
        )
        order = Order.raw_objects.create(
            business=self.business,
            created_by=user,
            date=date(2026, 9, 2),
            order_type="distribution",
            is_market_stock=True,
            status="approved",
        )
        item = OrderItem.objects.create(
            order=order,
            finished_good=good,
            batch_qty=Decimal("1"),
            piece_qty=Decimal("0"),
            price=Decimal("1000"),
        )

        response = self.client.post(
            reverse("order_complete", args=[order.pk]),
            {
                f"item-{item.pk}-produced_units": "12",
                f"item-{item.pk}-wastage_units": "0",
                f"item-{item.pk}-batch_number": "D260902-MARKET-1",
                f"item-{item.pk}-expiry_date": "",
                f"item-{item.pk}-wastage_reason": "",
                f"item-{item.pk}-qc_status": "pending",
                f"item-{item.pk}-qc_notes": "",
                f"item-{item.pk}-shortage_reason": "",
                f"item-{item.pk}-excess_to_stock": "0",
                f"item-{item.pk}-excess_to_market_stock": "2",
                f"item-{item.pk}-excess_to_non_stock": "0",
                f"item-{item.pk}-excess_non_stock_purpose": "",
            },
        )

        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        order.refresh_from_db()
        good.refresh_from_db()
        self.assertEqual(order.status, "completed")
        self.assertIsNone(good.stock)
        self.assertEqual(good.total_produced, Decimal("12.00"))
        self.assertEqual(good.total_delivered_to_customers, Decimal("0.00"))
        self.assertFalse(Sale.raw_objects.filter(linked_order=order).exists())
        movement = StockMovement.raw_objects.get(
            finished_good=good, movement_type=StockMovement.FG_MARKET_PRODUCTION
        )
        self.assertFalse(movement.affects_stock)
        self.assertEqual(movement.quantity, Decimal("12"))
        lot = MarketStockLot.raw_objects.get(production_batch__order=order)
        self.assertEqual(lot.quantity_available, Decimal("12.00"))
        self.assertEqual(lot.finished_good, good)
        self.assertEqual(lot.production_batch.excess_market_stock_units, Decimal("2.00"))

        reverse_response = self.client.post(
            reverse("order_reverse", args=[order.pk]),
            {"reason": "Test untouched market-lot reversal"},
        )
        self.assertRedirects(reverse_response, reverse("order_detail", args=[order.pk]))
        order.refresh_from_db()
        good.refresh_from_db()
        lot.refresh_from_db()
        self.assertEqual(order.status, "reversed")
        self.assertIsNone(good.stock)
        self.assertEqual(good.total_produced, Decimal("0.00"))
        self.assertEqual(lot.quantity_available, Decimal("0.00"))
        self.assertEqual(lot.closed_reason, "reversed")

class BaseMaterialProductionTests(TestCase):
    def setUp(self):
        from inventory.models import RawMaterial, RecipeItem

        self.business = Business.objects.create(name="Base Kitchen", slug="base-kitchen")
        self.rice = RawMaterial.raw_objects.create(
            business=self.business,
            name="Rice",
            category=RawMaterial.CATEGORY_INGREDIENT,
            purchase_unit="bag",
            package_qty=Decimal("50"),
            package_unit="kg",
            usage_unit="kg",
            usage_conversion_factor=Decimal("1"),
            stock=Decimal("100"),
            reorder_level=Decimal("10"),
            cost_per_unit=Decimal("1000"),
        )
        self.flour = RawMaterial.raw_objects.create(
            business=self.business,
            name="Flour",
            category=RawMaterial.CATEGORY_INGREDIENT,
            purchase_unit="bag",
            package_qty=Decimal("50"),
            package_unit="kg",
            usage_unit="kg",
            usage_conversion_factor=Decimal("1"),
            stock=Decimal("100"),
            reorder_level=Decimal("10"),
            cost_per_unit=Decimal("900"),
        )
        self.jollof = FinishedGood.raw_objects.create(
            business=self.business,
            name="Jollof Rice",
            unit="portion",
            units_per_batch=Decimal("50"),
            base_material=self.rice,
            stock=Decimal("0"),
            reorder_level=Decimal("0"),
            selling_price=Decimal("2500"),
        )
        self.fried = FinishedGood.raw_objects.create(
            business=self.business,
            name="Fried Rice",
            unit="portion",
            units_per_batch=Decimal("40"),
            base_material=self.rice,
            stock=Decimal("0"),
            reorder_level=Decimal("0"),
            selling_price=Decimal("2600"),
        )
        self.bread = FinishedGood.raw_objects.create(
            business=self.business,
            name="Bread",
            unit="loaf",
            units_per_batch=Decimal("20"),
            base_material=self.flour,
            stock=Decimal("0"),
            reorder_level=Decimal("0"),
            selling_price=Decimal("1500"),
        )
        RecipeItem.objects.create(
            finished_good=self.jollof,
            raw_material=self.rice,
            qty_per_batch=Decimal("5"),
        )
        RecipeItem.objects.create(
            finished_good=self.fried,
            raw_material=self.rice,
            qty_per_batch=Decimal("4"),
        )
        RecipeItem.objects.create(
            finished_good=self.bread,
            raw_material=self.flour,
            qty_per_batch=Decimal("6"),
        )

    def formset_data(self, rows):
        data = {
            "items-TOTAL_FORMS": str(len(rows)),
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
        }
        for index, row in enumerate(rows):
            data.update({
                f"items-{index}-finished_good": str(row["good"].pk),
                f"items-{index}-batch_qty": str(row.get("batches", "0")),
                f"items-{index}-piece_qty": str(row.get("pieces", "0")),
                f"items-{index}-production_batch_qty": "0",
                f"items-{index}-production_piece_qty": "0",
                f"items-{index}-discount": "0",
            })
        return data

    def base_formset(self, rows, *, allocated="15", market_stock=True):
        order = Order(
            business=self.business,
            order_type="distribution",
            is_market_stock=market_stock,
            production_basis=Order.BASIS_BASE_MATERIAL,
            base_material_quantity=Decimal(allocated),
        )
        return order, OrderItemFormSet(
            self.formset_data(rows),
            instance=order,
            market_stock=market_stock,
            order_type="distribution",
            production_basis=Order.BASIS_BASE_MATERIAL,
            base_material_quantity=allocated,
        )

    def test_shared_base_material_is_consumed_by_all_product_quantities(self):
        order, formset = self.base_formset([
            {"good": self.jollof, "batches": "1", "pieces": "25"},
            {"good": self.fried, "batches": "1", "pieces": "20"},
        ])

        self.assertTrue(formset.is_valid(), formset.errors)
        jollof_item = formset.forms[0].instance
        fried_item = formset.forms[1].instance
        self.assertEqual(jollof_item.base_material_quantity, Decimal("7.5"))
        self.assertEqual(fried_item.base_material_quantity, Decimal("6.0"))
        self.assertEqual(jollof_item.total_units, Decimal("75"))
        self.assertEqual(fried_item.total_units, Decimal("60"))
        self.assertEqual(jollof_item.production_basis, OrderItem.BASIS_BASE_MATERIAL)
        self.assertEqual(fried_item.production_basis, OrderItem.BASIS_BASE_MATERIAL)

        order.save()
        for form in formset.forms:
            form.instance.order = order
            form.instance.price = form.instance.finished_good.selling_price
            form.instance.save()
        self.assertEqual(order.base_material_used_quantity, Decimal("13.5000"))
        self.assertEqual(order.base_material_remaining_quantity, Decimal("1.5000"))
        self.assertEqual(order.material_requirements()[self.rice.pk][1], Decimal("13.5"))

        # Approval must use the same recipe-proportional requirement and must
        # not deduct the order-level allocation a second time.
        from .views import _material_release_plan

        _, usage_entries, aggregated, errors = _material_release_plan(order)
        self.assertEqual(errors, [])
        self.assertEqual(aggregated[self.rice.pk][1], Decimal("13.5"))
        self.assertEqual(
            sum(
                (entry["actual_quantity"] for entry in usage_entries if entry["raw_material"].pk == self.rice.pk),
                Decimal("0"),
            ),
            Decimal("13.5"),
        )

    def test_shared_base_material_cannot_be_overallocated(self):
        _, formset = self.base_formset([
            {"good": self.jollof, "batches": "3"},
            {"good": self.fried, "batches": "1"},
        ], allocated="15")

        self.assertFalse(formset.is_valid())
        self.assertIn("require 19.0000 kg of Rice", str(formset.non_form_errors()))

    def test_all_products_must_use_the_same_shared_base_material(self):
        _, formset = self.base_formset([
            {"good": self.jollof, "batches": "1"},
            {"good": self.bread, "batches": "1"},
        ], allocated="20")

        self.assertFalse(formset.is_valid())
        self.assertIn("finished_good", formset.forms[1].errors)
        self.assertIn("uses Rice as its base material", str(formset.forms[1].errors["finished_good"]))

    def test_customer_assigned_order_cannot_use_shared_base_material(self):
        _, formset = self.base_formset(
            [{"good": self.jollof, "batches": "1"}],
            allocated="15",
            market_stock=False,
        )

        self.assertFalse(formset.is_valid())
        self.assertIn("stock production", str(formset.non_form_errors()))

    def test_order_form_owns_the_base_material_allocation(self):
        form = OrderForm(
            data={
                "date": "2026-10-01",
                "order_type": "distribution",
                "is_market_stock": "on",
                "production_basis": Order.BASIS_BASE_MATERIAL,
                "base_material_quantity": "25",
            },
            business=self.business,
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        order = form.save(commit=False)
        self.assertEqual(order.production_basis, Order.BASIS_BASE_MATERIAL)
        self.assertEqual(order.base_material_quantity, Decimal("25"))

    def test_legacy_line_level_base_sizing_remains_readable(self):
        order = Order(
            business=self.business,
            order_type="distribution",
            is_market_stock=True,
            production_basis=Order.BASIS_PRODUCT_QUANTITY,
        )
        item = OrderItem(
            order=order,
            finished_good=self.jollof,
            production_basis=OrderItem.BASIS_BASE_MATERIAL,
            base_material_quantity=Decimal("7"),
            batch_qty=Decimal("0"),
            piece_qty=Decimal("0"),
        )

        expected_factor = Decimal("7") / Decimal("5")
        self.assertEqual(item.effective_production_batch_qty, expected_factor)
        self.assertEqual(item.total_units, expected_factor * Decimal("50"))

