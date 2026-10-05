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



class CentralFlexibleMaterialTests(TestCase):
    """Base-material orders take ONE central total per flexible ingredient and
    allot it across the order's products; other orders keep per-batch inputs."""

    def setUp(self):
        from inventory.models import RawMaterial, RecipeItem

        self.business = Business.objects.create(name="Flex Kitchen", slug="flex-kitchen")

        def material(name, stock="500"):
            return RawMaterial.raw_objects.create(
                business=self.business, name=name, category=RawMaterial.CATEGORY_INGREDIENT,
                purchase_unit="bag", package_qty=Decimal("50"), package_unit="kg", usage_unit="kg",
                usage_conversion_factor=Decimal("1"), stock=Decimal(stock), reorder_level=Decimal("1"),
                cost_per_unit=Decimal("100"),
            )

        self.rice = material("Rice")
        self.sugar = material("Sugar")

        def good(name, upb, sugar_per_batch):
            fg = FinishedGood.raw_objects.create(
                business=self.business, name=name, unit="portion", units_per_batch=Decimal(upb),
                base_material=self.rice, stock=Decimal("0"), reorder_level=Decimal("0"),
                selling_price=Decimal("1000"),
            )
            RecipeItem.objects.create(finished_good=fg, raw_material=self.rice, qty_per_batch=Decimal("5"))
            RecipeItem.objects.create(
                finished_good=fg, raw_material=self.sugar, qty_per_batch=Decimal(sugar_per_batch), flexible_usage=True,
            )
            return fg

        self.jollof = good("Jollof", "50", "2")   # 1.5 batches x 2 = 3 planned
        self.fried = good("Fried", "40", "1")     # 1.5 batches x 1 = 1.5 planned

    def make_order(self, basis):
        order = Order.raw_objects.create(
            business=self.business, date=date(2026, 9, 2), order_type="physical_store", status="pending",
            production_basis=basis,
            base_material_quantity=Decimal("50") if basis == Order.BASIS_BASE_MATERIAL else Decimal("0"),
        )
        for fg, pieces in ((self.jollof, "25"), (self.fried, "20")):
            OrderItem.objects.create(
                order=order, finished_good=fg, batch_qty=Decimal("1"), piece_qty=Decimal(pieces),
                price=Decimal("1000"), production_basis=basis,
            )
        return Order.objects.get(pk=order.pk)

    def usage_by_good(self, entries):
        return {
            e["order_item"].finished_good.name: e["actual_quantity"]
            for e in entries if e["raw_material"].pk == self.sugar.pk
        }

    def test_base_material_order_gets_one_central_row_per_flexible_material(self):
        from .views import _material_release_plan

        order = self.make_order(Order.BASIS_BASE_MATERIAL)
        rows, entries, aggregated, errors = _material_release_plan(order)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["central"])
        self.assertEqual(row["input_name"], f"flex_total_{order.pk}_{self.sugar.pk}")
        self.assertEqual(row["planned_total"], Decimal("4.5"))
        self.assertEqual(self.usage_by_good(entries), {"Jollof": Decimal("3"), "Fried": Decimal("1.5")})

    def test_central_total_is_allotted_in_proportion_to_each_products_plan(self):
        from .views import _material_release_plan

        order = self.make_order(Order.BASIS_BASE_MATERIAL)
        post = {f"flex_total_{order.pk}_{self.sugar.pk}": "9"}
        _, entries, aggregated, errors = _material_release_plan(order, post)
        self.assertEqual(errors, [])
        self.assertEqual(self.usage_by_good(entries), {"Jollof": Decimal("6"), "Fried": Decimal("3")})
        self.assertEqual(aggregated[self.sugar.pk][1], Decimal("9"))
        # The non-flexible base material is untouched by the central override.
        self.assertEqual(aggregated[self.rice.pk][1], Decimal("15"))

    def test_allotment_never_loses_or_invents_quantity_to_rounding(self):
        from .views import _material_release_plan

        order = self.make_order(Order.BASIS_BASE_MATERIAL)
        post = {f"flex_total_{order.pk}_{self.sugar.pk}": "1"}
        _, entries, aggregated, _ = _material_release_plan(order, post)
        shares = self.usage_by_good(entries)
        self.assertEqual(sum(shares.values(), Decimal("0")), Decimal("1.0000"))
        self.assertEqual(shares["Jollof"], Decimal("0.6667"))
        self.assertEqual(shares["Fried"], Decimal("0.3333"))
        self.assertEqual(aggregated[self.sugar.pk][1], Decimal("1.0000"))

    def test_invalid_central_total_is_reported_and_falls_back_to_plan(self):
        from .views import _material_release_plan

        order = self.make_order(Order.BASIS_BASE_MATERIAL)
        for bad in ("abc", "-2"):
            _, entries, _, errors = _material_release_plan(order, {f"flex_total_{order.pk}_{self.sugar.pk}": bad})
            self.assertEqual(len(errors), 1)
            self.assertEqual(self.usage_by_good(entries), {"Jollof": Decimal("3"), "Fried": Decimal("1.5")})

    def test_product_quantity_orders_keep_per_product_per_batch_inputs(self):
        from .views import _material_release_plan

        order = self.make_order(Order.BASIS_PRODUCT_QUANTITY)
        rows, entries, _, errors = _material_release_plan(order)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 2)
        self.assertFalse(any(r["central"] for r in rows))
        self.assertTrue(all(r["input_name"].startswith("flex_qty_") for r in rows))

    def test_order_detail_shows_central_input_and_approval_releases_allotted_totals(self):
        user = CustomUser.objects.create_superuser(username="flex-admin", password="safe-password-123", fullname="Admin")
        self.client.force_login(user)
        order = self.make_order(Order.BASIS_BASE_MATERIAL)

        page = self.client.get(reverse("order_detail", args=[order.pk]))
        self.assertContains(page, "Total used for this order")
        self.assertContains(page, f'name="flex_total_{order.pk}_{self.sugar.pk}"')
        self.assertNotContains(page, "Actual qty / batch")

        response = self.client.post(reverse("order_approve", args=[order.pk]), {f"flex_total_{order.pk}_{self.sugar.pk}": "9"})
        self.assertRedirects(response, reverse("order_detail", args=[order.pk]))
        from .models import OrderMaterialUsage

        usage = {
            u.order_item.finished_good.name: u.actual_quantity
            for u in OrderMaterialUsage.objects.filter(order=order, raw_material=self.sugar)
        }
        self.assertEqual(usage, {"Jollof": Decimal("6.0000"), "Fried": Decimal("3.0000")})
        self.sugar.refresh_from_db()
        self.assertEqual(self.sugar.stock, Decimal("491"))


class SharedRunBaseMaterialPoolTests(TestCase):
    """A shared run can carry one overall base-material pool that all member
    orders draw from, with the remainder available to an independent order."""

    setUp = CentralFlexibleMaterialTests.setUp
    make_order = CentralFlexibleMaterialTests.make_order

    def make_run(self, pool, orders=2):
        from .models import ProductionRun, ProductionRunOrder

        run = ProductionRun.raw_objects.create(
            business=self.business, date=date(2026, 9, 2), run_number=f"RUN-{pool}-{ProductionRun.raw_objects.count()}",
            production_basis=ProductionRun.BASIS_BASE_MATERIAL,
            base_material_quantity=Decimal(pool),
        )
        for _ in range(orders):
            ProductionRunOrder.objects.create(production_run=run, order=self.make_order(Order.BASIS_PRODUCT_QUANTITY))
        return ProductionRun.objects.get(pk=run.pk)

    def pool(self, run):
        from .views import _run_base_pool, _shared_run_release_plan

        _f, entries, _a, _s, _e = _shared_run_release_plan(run)
        return _run_base_pool(run, entries)

    def test_pool_totals_what_member_orders_draw_and_what_is_left(self):
        pool = self.pool(self.make_run("40"))
        self.assertEqual((pool["used"], pool["remaining"], pool["overflow"]), (Decimal("30"), Decimal("10"), Decimal("0")))

    def test_pool_reports_overflow_when_orders_need_more_than_the_pool(self):
        pool = self.pool(self.make_run("20"))
        self.assertEqual((pool["remaining"], pool["overflow"]), (Decimal("0"), Decimal("10")))

    def test_product_quantity_runs_have_no_pool(self):
        from .models import ProductionRun
        run = ProductionRun.raw_objects.create(business=self.business, date=date(2026, 9, 2), run_number="RUN-PLAIN")
        self.assertIsNone(self.pool(ProductionRun.objects.get(pk=run.pk)))

    def test_independent_order_cannot_exceed_the_remaining_pool_or_use_another_base(self):
        from types import SimpleNamespace
        from .views import _run_order_errors

        run = self.make_run("40")
        form = lambda qty: SimpleNamespace(cleaned_data={"production_basis": Order.BASIS_BASE_MATERIAL, "base_material_quantity": Decimal(qty)})
        formset = SimpleNamespace(forms=[SimpleNamespace(cleaned_data={"finished_good": self.jollof})])
        self.assertEqual(_run_order_errors(run, form("10"), formset), [])
        self.assertEqual(len(_run_order_errors(run, form("10.5"), formset)), 1)
        other = SimpleNamespace(forms=[SimpleNamespace(cleaned_data={"finished_good": self.jollof})])
        self.jollof.base_material = self.sugar
        self.assertEqual(len(_run_order_errors(run, form("1"), other)), 1)

    def test_approval_is_blocked_when_orders_overdraw_the_pool_and_allowed_when_they_fit(self):
        user = CustomUser.objects.create_superuser(username="run-admin", password="safe-password-123", fullname="Admin")
        self.client.force_login(user)
        over = self.make_run("20")
        self.client.post(reverse("production_run_approve", args=[over.pk]))
        over.refresh_from_db()
        self.assertEqual(over.status, "draft")
        fits = self.make_run("40")
        self.client.post(reverse("production_run_approve", args=[fits.pk]))
        fits.refresh_from_db()
        self.assertEqual(fits.status, "approved")

    def test_run_page_shows_pool_and_the_independent_order_action(self):
        user = CustomUser.objects.create_superuser(username="run-viewer", password="safe-password-123", fullname="Admin")
        self.client.force_login(user)
        run = self.make_run("40")
        page = self.client.get(reverse("production_run_detail", args=[run.pk]))
        self.assertContains(page, "Overall base material")
        self.assertContains(page, "Remaining")
        self.assertContains(page, "independent=1")
        form = self.client.get(reverse("order_add"), {"run": run.pk, "independent": "1"})
        self.assertEqual(form.status_code, 200)
        self.assertEqual(form.context["form"].initial["base_material_quantity"], Decimal("10"))


class SharedRunBaseMaterialMergeTests(SharedRunBaseMaterialPoolTests.__bases__[0]):
    """Base-material runs accept every pending order sharing the base material
    (customer orders included) and merge flexible ingredients into one run total."""

    setUp = CentralFlexibleMaterialTests.setUp
    make_order = CentralFlexibleMaterialTests.make_order
    make_run = SharedRunBaseMaterialPoolTests.make_run
    pool = SharedRunBaseMaterialPoolTests.pool

    def login(self):
        user = CustomUser.objects.create_superuser(username="merge-admin", password="safe-password-123", fullname="Admin")
        self.client.force_login(user)

    def customer_order(self):
        order = self.make_order(Order.BASIS_PRODUCT_QUANTITY)
        order.order_type, order.customer_name = "online", "Ada"
        order.save(update_fields=["order_type", "customer_name"])
        return order

    def foreign_base_order(self):
        syrup = FinishedGood.raw_objects.create(
            business=self.business, name="Syrup", unit="bottle", units_per_batch=Decimal("10"),
            base_material=self.sugar, stock=Decimal("0"), reorder_level=Decimal("0"), selling_price=Decimal("100"),
        )
        order = Order.raw_objects.create(business=self.business, date=date(2026, 9, 2), order_type="physical_store", status="pending")
        OrderItem.objects.create(order=order, finished_good=syrup, batch_qty=Decimal("1"), price=Decimal("100"))
        return Order.objects.get(pk=order.pk)

    def run_post(self, orders):
        return {
            "date": "2026-09-02", "run_number": "RUN-MERGE", "production_basis": "base_material",
            "base_material_quantity": "100", "orders": [o.pk for o in orders],
        }

    def test_customer_orders_sharing_the_base_material_can_be_drafted_into_the_run(self):
        from .models import ProductionRun
        self.login()
        orders = [self.customer_order(), self.make_order(Order.BASIS_PRODUCT_QUANTITY)]
        response = self.client.post(reverse("production_run_add"), self.run_post(orders))
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["form"].errors)
        run = ProductionRun.objects.get(run_number="RUN-MERGE")
        self.assertEqual(set(run.orders.values_list("pk", flat=True)), {o.pk for o in orders})

    def test_orders_that_do_not_share_one_base_material_are_rejected(self):
        from .models import ProductionRun
        self.login()
        orders = [self.customer_order(), self.foreign_base_order()]
        response = self.client.post(reverse("production_run_add"), self.run_post(orders))
        self.assertEqual(response.status_code, 200)
        self.assertIn("orders", response.context["form"].errors)
        self.assertFalse(ProductionRun.objects.filter(run_number="RUN-MERGE").exists())

    def test_run_form_lists_each_orders_base_material_for_the_picker(self):
        self.login()
        order = self.customer_order()
        page = self.client.get(reverse("production_run_add"))
        self.assertContains(page, 'id="run-order-bases"')
        self.assertEqual(page.context["order_bases"][order.pk], self.rice.pk)
        self.assertContains(page, "base: Rice")

    def test_flexible_ingredient_is_one_merged_total_across_the_whole_run(self):
        from .views import _shared_run_release_plan
        run = self.make_run("100")
        flex, entries, aggregated, _summary, errors = _shared_run_release_plan(run, {f"flex_total_run{run.pk}_{self.sugar.pk}": "18"})
        self.assertEqual(errors, [])
        self.assertEqual(len(flex), 1)
        row = flex[0]
        self.assertTrue(row["central"])
        self.assertIsNone(row["order"])
        self.assertEqual(row["planned_total"], Decimal("9"))
        self.assertEqual(len(row["allocations"]), 4)  # 2 orders x 2 products
        self.assertEqual(sum((a["actual"] for a in row["allocations"]), Decimal("0")), Decimal("18"))
        self.assertEqual({a["good"].name: a["actual"] for a in row["allocations"]}, {"Jollof": Decimal("6"), "Fried": Decimal("3")})
        self.assertEqual(aggregated[self.sugar.pk][1], Decimal("18"))

    def test_product_quantity_runs_keep_per_order_flexible_inputs(self):
        from .models import ProductionRun, ProductionRunOrder
        from .views import _shared_run_release_plan
        run = ProductionRun.raw_objects.create(business=self.business, date=date(2026, 9, 2), run_number="RUN-QTY")
        for _ in range(2):
            ProductionRunOrder.objects.create(production_run=run, order=self.make_order(Order.BASIS_PRODUCT_QUANTITY))
        flex, *_ = _shared_run_release_plan(ProductionRun.objects.get(pk=run.pk))
        self.assertEqual(len(flex), 4)
        self.assertFalse(any(r["central"] for r in flex))

    def test_approval_releases_the_merged_total_and_blocks_mismatched_orders(self):
        from .models import OrderMaterialUsage
        self.login()
        run = self.make_run("100")
        self.client.post(reverse("production_run_approve", args=[run.pk]), {f"flex_total_run{run.pk}_{self.sugar.pk}": "18"})
        run.refresh_from_db()
        self.assertEqual(run.status, "approved")
        used = sum(u.actual_quantity for u in OrderMaterialUsage.objects.filter(order__production_runs=run, raw_material=self.sugar))
        self.assertEqual(used, Decimal("18.0000"))
        self.sugar.refresh_from_db()
        self.assertEqual(self.sugar.stock, Decimal("482"))
        bad = self.make_run("100")
        from .models import ProductionRunOrder
        ProductionRunOrder.objects.create(production_run=bad, order=self.foreign_base_order())
        self.client.post(reverse("production_run_approve", args=[bad.pk]))
        bad.refresh_from_db()
        self.assertEqual(bad.status, "draft")

    def test_run_form_has_no_base_material_field_and_derives_it_from_the_orders(self):
        from .models import ProductionRun
        from .views import run_base_material
        self.login()
        page = self.client.get(reverse("production_run_add"))
        self.assertNotIn("base_material", page.context["form"].fields)
        self.assertContains(page, 'name="base_material_quantity"')
        orders = [self.customer_order(), self.make_order(Order.BASIS_PRODUCT_QUANTITY)]
        self.client.post(reverse("production_run_add"), self.run_post(orders))
        run = ProductionRun.objects.get(run_number="RUN-MERGE")
        self.assertEqual(run_base_material(run), self.rice)
        pool = self.pool(run)
        self.assertEqual((pool["material"], pool["total"]), (self.rice, Decimal("100")))

    def test_pool_without_orders_has_no_material_yet_and_cannot_be_approved(self):
        from .models import ProductionRun
        self.login()
        run = ProductionRun.raw_objects.create(
            business=self.business, date=date(2026, 9, 2), run_number="RUN-EMPTY",
            production_basis=ProductionRun.BASIS_BASE_MATERIAL, base_material_quantity=Decimal("50"),
        )
        run = ProductionRun.objects.get(pk=run.pk)
        self.assertIsNone(self.pool(run)["material"])
        page = self.client.get(reverse("production_run_detail", args=[run.pk]))
        self.assertContains(page, "comes from the products of the orders")


class RunOrderPickerDropdownTests(TestCase):
    """The pending-orders picker is a real multi-select dropdown, not a list box."""

    setUp = CentralFlexibleMaterialTests.setUp
    make_order = CentralFlexibleMaterialTests.make_order
    login = SharedRunBaseMaterialMergeTests.login
    customer_order = SharedRunBaseMaterialMergeTests.customer_order
    run_post = SharedRunBaseMaterialMergeTests.run_post

    def test_order_picker_is_enhanced_into_a_dropdown_and_keeps_its_select(self):
        self.login()
        self.customer_order()
        page = self.client.get(reverse("production_run_add"))
        field = page.context["form"].fields["orders"]
        self.assertNotIn("size", field.widget.attrs)
        self.assertEqual(field.widget.attrs["data-multiselect-dropdown"], "1")
        self.assertContains(page, "data-multiselect-dropdown")
        self.assertContains(page, "core/js/multiselect-dropdown.js")
        self.assertContains(page, 'multiple')
        self.assertNotContains(page, "use Ctrl or Cmd")

    def test_dropdown_selection_still_posts_through_the_underlying_select(self):
        from .models import ProductionRun
        self.login()
        orders = [self.customer_order(), self.make_order(Order.BASIS_PRODUCT_QUANTITY)]
        response = self.client.post(reverse("production_run_add"), self.run_post(orders))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ProductionRun.objects.get(run_number="RUN-MERGE").orders.count(), 2)


class RunPoolAdjustmentTests(TestCase):
    """The draft run page can adjust the overall pool without going back to run setup."""

    setUp = CentralFlexibleMaterialTests.setUp
    make_order = CentralFlexibleMaterialTests.make_order
    make_run = SharedRunBaseMaterialPoolTests.make_run
    login = SharedRunBaseMaterialMergeTests.login

    def test_page_shows_the_adjust_form_only_on_draft_base_material_runs(self):
        from .models import ProductionRun
        self.login()
        run = self.make_run("40")
        page = self.client.get(reverse("production_run_detail", args=[run.pk]))
        self.assertContains(page, reverse("production_run_adjust_pool", args=[run.pk]))
        self.assertContains(page, "Adjust pool")
        self.assertNotContains(page, "Raise pool to")  # not short
        plain = ProductionRun.raw_objects.create(business=self.business, date=date(2026, 9, 2), run_number="RUN-PLAIN2")
        self.assertNotContains(self.client.get(reverse("production_run_detail", args=[plain.pk])), "Adjust pool")

    def test_adjusting_the_pool_saves_audits_and_redirects(self):
        self.login()
        run = self.make_run("40")
        response = self.client.post(reverse("production_run_adjust_pool", args=[run.pk]), {"base_material_quantity": "55.5"})
        self.assertRedirects(response, reverse("production_run_detail", args=[run.pk]), fetch_redirect_response=False)
        run.refresh_from_db()
        self.assertEqual(run.base_material_quantity, Decimal("55.5000"))

    def test_invalid_pool_values_are_rejected(self):
        self.login()
        run = self.make_run("40")
        for bad in ("0", "-3", "abc", "", "nan"):
            self.client.post(reverse("production_run_adjust_pool", args=[run.pk]), {"base_material_quantity": bad})
            run.refresh_from_db()
            self.assertEqual(run.base_material_quantity, Decimal("40.0000"), bad)

    def test_short_pool_can_be_raised_to_what_orders_need_then_approved(self):
        self.login()
        run = self.make_run("20")  # orders need 30
        page = self.client.get(reverse("production_run_detail", args=[run.pk]))
        self.assertContains(page, "Raise pool to 30")
        self.client.post(reverse("production_run_approve", args=[run.pk]))
        run.refresh_from_db()
        self.assertEqual(run.status, "draft")
        self.client.post(reverse("production_run_adjust_pool", args=[run.pk]), {"use_needed": "1"})
        run.refresh_from_db()
        self.assertEqual(run.base_material_quantity, Decimal("30.0000"))
        self.client.post(reverse("production_run_approve", args=[run.pk]))
        run.refresh_from_db()
        self.assertEqual(run.status, "approved")

    def test_approved_runs_and_get_requests_cannot_change_the_pool(self):
        self.login()
        run = self.make_run("40")
        self.client.post(reverse("production_run_approve", args=[run.pk]))
        run.refresh_from_db()
        self.assertEqual(run.status, "approved")
        self.client.post(reverse("production_run_adjust_pool", args=[run.pk]), {"base_material_quantity": "99"})
        self.client.get(reverse("production_run_adjust_pool", args=[run.pk]), {"base_material_quantity": "77"})
        run.refresh_from_db()
        self.assertEqual(run.base_material_quantity, Decimal("40.0000"))


class RunPoolWithMisfitOrdersTests(TestCase):
    """A short pool must stay visible when an order without a base material is
    added, and an order's own base-material quantity never changes the run pool."""

    setUp = CentralFlexibleMaterialTests.setUp
    make_order = CentralFlexibleMaterialTests.make_order
    make_run = SharedRunBaseMaterialPoolTests.make_run
    pool = SharedRunBaseMaterialPoolTests.pool
    login = SharedRunBaseMaterialMergeTests.login
    foreign_base_order = SharedRunBaseMaterialMergeTests.foreign_base_order

    def no_base_order(self):
        plain = FinishedGood.raw_objects.create(
            business=self.business, name="Water", unit="bottle", units_per_batch=Decimal("10"),
            stock=Decimal("0"), reorder_level=Decimal("0"), selling_price=Decimal("100"),
        )
        order = Order.raw_objects.create(business=self.business, date=date(2026, 9, 2), order_type="online", customer_name="Ada", status="pending")
        OrderItem.objects.create(order=order, finished_good=plain, batch_qty=Decimal("1"), price=Decimal("100"))
        return Order.objects.get(pk=order.pk)

    def attach(self, run, order):
        from .models import ProductionRunOrder
        ProductionRunOrder.objects.create(production_run=run, order=order)
        return type(run).objects.get(pk=run.pk)

    def test_short_pool_stays_visible_when_an_order_without_a_base_material_is_attached(self):
        run = self.attach(self.make_run("20"), self.no_base_order())   # two rice orders need 30 of 20
        pool = self.pool(run)
        self.assertEqual(pool["material"], self.rice)
        self.assertEqual((pool["used"], pool["overflow"]), (Decimal("30"), Decimal("10")))   # not "fresh"
        self.assertEqual(len(pool["misfits"]), 1)
        self.assertIn("no single base material", pool["misfits"][0]["reason"])

    def test_misfit_order_is_flagged_on_the_page_and_blocks_approval(self):
        self.login()
        run = self.attach(self.make_run("100"), self.no_base_order())
        page = self.client.get(reverse("production_run_detail", args=[run.pk]))
        self.assertContains(page, "not counted against the pool")
        self.assertContains(page, "draw from this pool")
        self.client.post(reverse("production_run_approve", args=[run.pk]))
        run.refresh_from_db()
        self.assertEqual(run.status, "draft")

    def test_first_attached_order_with_a_base_defines_the_run_base_material(self):
        from .models import ProductionRun
        from .views import run_base_material
        run = ProductionRun.raw_objects.create(
            business=self.business, date=date(2026, 9, 2), run_number="RUN-FIRST",
            production_basis=ProductionRun.BASIS_BASE_MATERIAL, base_material_quantity=Decimal("50"),
        )
        run = self.attach(run, self.no_base_order())
        self.assertIsNone(run_base_material(run))                      # nothing has a base yet
        run = self.attach(run, self.foreign_base_order())              # sugar-based
        self.assertEqual(run_base_material(run), self.sugar)
        run = self.attach(run, self.make_order(Order.BASIS_PRODUCT_QUANTITY))   # rice-based -> misfit
        self.assertEqual(run_base_material(run), self.sugar)
        self.assertEqual(len(self.pool(run)["misfits"]), 2)

    def test_new_order_inside_a_base_material_run_must_have_a_base_material(self):
        from types import SimpleNamespace
        from .views import _run_order_errors
        run = self.make_run("20")                                      # already short
        plain = FinishedGood.raw_objects.create(
            business=self.business, name="Water", unit="bottle", units_per_batch=Decimal("10"),
            stock=Decimal("0"), reorder_level=Decimal("0"), selling_price=Decimal("100"),
        )
        customer_form = SimpleNamespace(cleaned_data={"production_basis": Order.BASIS_PRODUCT_QUANTITY})
        no_base = SimpleNamespace(forms=[SimpleNamespace(cleaned_data={"finished_good": plain})])
        errors = _run_order_errors(run, customer_form, no_base)
        self.assertEqual(len(errors), 1)
        self.assertIn("no base material set", errors[0])
        fits = SimpleNamespace(forms=[SimpleNamespace(cleaned_data={"finished_good": self.jollof})])
        self.assertEqual(_run_order_errors(run, customer_form, fits), [])   # short pool is NOT a creation error

    def test_an_orders_own_base_quantity_never_changes_the_run_pool(self):
        run = self.make_run("40")                                       # two rice orders use 30
        independent = self.make_order(Order.BASIS_BASE_MATERIAL)        # its own allotment is 50
        self.assertEqual(independent.base_material_quantity, Decimal("50"))
        run = self.attach(run, independent)
        pool = self.pool(run)
        self.assertEqual(pool["total"], Decimal("40"))                  # pool unchanged by the order's 50
        self.assertEqual(pool["used"], Decimal("45"))                   # counts what its products use (15), not 50
        self.assertEqual(run.base_material_quantity, Decimal("40.0000"))


class ItemsDialogTemplateTests(TestCase):
    """Batches and runs lists share the order list's compact item summary + dialog."""

    def test_list_templates_compile_and_use_shared_assets(self):
        from django.template.loader import get_template
        for name in ("production/batches_list.html", "production/runs_list.html", "procurement/procurement_list.html"):
            source = open(get_template(name).origin.name, encoding="utf8").read()
            self.assertIn("_items_dialog_assets.html", source, name)
        batches = open(get_template("production/batches_list.html").origin.name, encoding="utf8").read()
        self.assertNotIn("{% for i in o.items.all %}{{ i.finished_good.name }}{% if not forloop.last %}, {% endif %}", batches)

    def test_summary_partial_renders_order_and_purchase_lines(self):
        from types import SimpleNamespace as NS
        from django.template.loader import render_to_string
        good = NS(name="Meat Pie")
        order_items = [NS(finished_good=good, total_units=4), NS(finished_good=NS(name="Bun"), total_units=2)]
        html = render_to_string("partials/_items_summary.html", {"items": order_items, "dialog_prefix": "batch-order-items-", "row_pk": 7})
        self.assertIn("Meat Pie", html)
        self.assertIn("+1 more", html)
        self.assertIn('data-items-dialog-open="batch-order-items-7"', html)
        po_items = [NS(item_name="Flour", item_category="Raw material", finished_good_id=None),
                    NS(item_name="Sugar", item_category="Raw material", finished_good_id=None)]
        html = render_to_string("partials/_items_summary.html", {"items": po_items, "purchase": True, "dialog_prefix": "po-items-", "row_pk": 3})
        self.assertIn("Flour", html)
        self.assertIn("Raw material", html)
        self.assertIn("View 2 items", html)
