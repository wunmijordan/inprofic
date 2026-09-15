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
        self.good = FinishedGood.raw_objects.create(
            business=self.business,
            name="Jollof Rice",
            unit="portion",
            units_per_batch=Decimal("50"),
            base_material=self.rice,
            stock=Decimal("0"),
            reorder_level=Decimal("0"),
            selling_price=Decimal("2500"),
        )
        RecipeItem.objects.create(
            finished_good=self.good,
            raw_material=self.rice,
            qty_per_batch=Decimal("5"),
        )

    def formset_data(self, *, base_qty="15"):
        return {
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-finished_good": str(self.good.pk),
            "items-0-production_basis": OrderItem.BASIS_BASE_MATERIAL,
            "items-0-base_material_quantity": base_qty,
            "items-0-batch_qty": "0",
            "items-0-piece_qty": "0",
            "items-0-production_batch_qty": "0",
            "items-0-production_piece_qty": "0",
            "items-0-discount": "0",
        }

    def test_market_stock_order_can_be_sized_by_base_material(self):
        order = Order(
            business=self.business,
            order_type="distribution",
            is_market_stock=True,
        )
        formset = OrderItemFormSet(
            self.formset_data(base_qty="15"),
            instance=order,
            market_stock=True,
            order_type="distribution",
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        form = formset.forms[0]
        self.assertEqual(form.cleaned_data["batch_qty"], Decimal("3"))
        self.assertEqual(form.instance.production_basis, OrderItem.BASIS_BASE_MATERIAL)
        self.assertEqual(form.instance.base_material_quantity, Decimal("15"))
        self.assertEqual(form.instance.total_units, Decimal("150"))
        self.assertEqual(form.instance.effective_production_batch_qty, Decimal("3"))

    def test_base_material_keeps_fractional_run_precision(self):
        order = Order(
            business=self.business,
            order_type="distribution",
            is_market_stock=True,
        )
        formset = OrderItemFormSet(
            self.formset_data(base_qty="7"),
            instance=order,
            market_stock=True,
            order_type="distribution",
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        item = formset.forms[0].instance
        expected_factor = Decimal("7") / Decimal("5")
        self.assertEqual(item.effective_production_batch_qty, expected_factor)
        self.assertEqual(item.total_units, expected_factor * Decimal("50"))

    def test_customer_assigned_order_cannot_use_base_material_sizing(self):
        order = Order(
            business=self.business,
            order_type="distribution",
            is_market_stock=False,
        )
        formset = OrderItemFormSet(
            self.formset_data(base_qty="15"),
            instance=order,
            market_stock=False,
            order_type="distribution",
        )

        self.assertFalse(formset.is_valid())
        self.assertIn("production_basis", formset.forms[0].errors)
