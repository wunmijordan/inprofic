from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser, Role, UserBusiness
from accounts.services import seed_business_roles
from core.models import Business, CashAccount, FinancialTransaction
from core.services import tenant_backup_querysets
from inventory.models import FinishedGood, RawMaterial, RecipeItem

from sales.models import Customer, Sale

from .models import Order, OrderAllocation, OrderFlexibleMaterialReport, OrderItem, ProductionBatch, ProductionRun


def _formset(rows, prefix="items"):
    data = {f"{prefix}-TOTAL_FORMS": str(len(rows)), f"{prefix}-INITIAL_FORMS": "0", f"{prefix}-MIN_NUM_FORMS": "0", f"{prefix}-MAX_NUM_FORMS": "1000"}
    for index, (good_id, batches, pieces) in enumerate(rows):
        data[f"{prefix}-{index}-finished_good"] = str(good_id)
        data[f"{prefix}-{index}-batch_qty"] = str(batches)
        data[f"{prefix}-{index}-piece_qty"] = str(pieces)
    return data


class KitchenBase(TestCase):
    vertical = Business.VERTICAL_BAKERY

    def setUp(self):
        self.business = Business.objects.create(name="Kitchen Co", slug=f"kitchen-{self.vertical}", vertical=self.vertical)
        seed_business_roles(self.business)
        self.cook = self._user("cook.user", CustomUser.ROLE_KITCHEN_COOK)
        self.guided = self._user("guided.user", CustomUser.ROLE_KITCHEN_GUIDED)
        self.manager = CustomUser.objects.create_superuser(username="boss", password="safe-password-123", fullname="Boss")
        self.flour = RawMaterial.raw_objects.create(business=self.business, name="Flour", stock=Decimal("500"), cost_per_unit=Decimal("2"), reorder_level=Decimal("1"))
        self.sugar = RawMaterial.raw_objects.create(business=self.business, name="Sugar", stock=Decimal("500"), cost_per_unit=Decimal("3"), reorder_level=Decimal("1"))
        # Neither product is shelf-tracked: the floor must still be able to log them.
        self.cake = FinishedGood.raw_objects.create(business=self.business, name="Cake", unit="piece", units_per_batch=Decimal("10"), selling_price=Decimal("500"), stock=None)
        for good in (self.cake,):
            RecipeItem.objects.create(finished_good=good, raw_material=self.flour, qty_per_batch=Decimal("10"))
            RecipeItem.objects.create(finished_good=good, raw_material=self.sugar, qty_per_batch=Decimal("2"), flexible_usage=True)
        self.link = RecipeItem.objects.get(finished_good=self.cake, raw_material=self.sugar)

    def _user(self, username, role_key):
        user = CustomUser.objects.create_user(username=username, password="safe-password-123", fullname=username)
        UserBusiness.objects.create(user=user, business=self.business, role=Role.objects.get(business=self.business, key=role_key))
        return user

    def _log(self, user, rows=None, **extra):
        """Create an order the way a Baker / Chef does: through the normal order form."""
        self.client.force_login(user)
        data = {"date": date.today().isoformat(), "production_basis": "product_quantity", "base_material_quantity": "",
                "notes": "", **_formset(rows or [(self.cake.pk, "3", "0")])}
        data.update(extra)
        return self.client.post(reverse("order_add"), data)

    def _logged_order(self, user=None):
        self._log(user or self.cook)
        return Order.objects.get()


class KitchenRoleTests(KitchenBase):
    def test_role_names_follow_the_business_vertical(self):
        names = {r.key: r.name for r in Role.objects.filter(business=self.business, key__startswith="kitchen_")}
        self.assertEqual(names, {"kitchen_cook": "Baker", "kitchen_guided": "Baker (Guided)"})
        for vertical, expected in ((Business.VERTICAL_RESTAURANT, "Chef"), (Business.VERTICAL_GENERAL, "Production Staff")):
            biz = Business.objects.create(name=f"V {vertical}", slug=f"v-{vertical}", vertical=vertical)
            seed_business_roles(biz)
            self.assertEqual(Role.objects.get(business=biz, key="kitchen_cook").name, expected)

    def test_stock_first_verticals_get_no_kitchen_roles(self):
        shop = Business.objects.create(name="Shop", slug="plain-shop", vertical=Business.VERTICAL_RETAIL)
        seed_business_roles(shop)
        self.assertFalse(Role.objects.filter(business=shop, key__startswith="kitchen_").exists())

    def test_guided_role_has_no_inventory_access_but_cook_can_read_recipes(self):
        self.client.force_login(self.cook)
        self.assertEqual(self.client.get(reverse("inventory_dashboard") if False else "/inventory/").status_code, 200)
        self.client.force_login(self.guided)
        self.assertEqual(self.client.get("/inventory/").status_code, 403)

    def test_backup_registry_includes_the_flexible_material_report(self):
        models = {qs.model for qs in tenant_backup_querysets(self.business)}
        self.assertIn(OrderFlexibleMaterialReport, models)


class KitchenOrderFormTests(KitchenBase):
    """Baker / Chef roles use the normal Orders pages, gated to what they need."""

    def test_floor_users_land_on_the_orders_page(self):
        for user in (self.cook, self.guided):
            self.client.force_login(user)
            self.assertRedirects(self.client.get(reverse("dashboard")), reverse("orders_list"), fetch_redirect_response=False)

    def test_floor_users_can_open_the_form_from_the_orders_page_without_production_access(self):
        # Production Entry alone must be enough: no Production Orders view/edit grant.
        for user in (self.cook, self.guided):
            self.client.force_login(user)
            page = self.client.get(reverse("orders_list"))
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, reverse("order_add"))
            self.assertFalse(page.context["module_permissions"]["production"]["view"])
            self.assertEqual(self.client.get(reverse("order_add")).status_code, 200)

    def test_orders_page_hides_channel_and_payment_columns_from_floor_users(self):
        self._logged_order()
        self.client.force_login(self.cook)
        page = self.client.get(reverse("orders_list"))
        for heading in (">Destination<", ">Payment<", ">For<"):
            self.assertNotContains(page, heading)
        self.client.force_login(self.manager)
        page = self.client.get(reverse("orders_list"))
        for heading in (">Destination<", ">Payment<", ">For<"):
            self.assertContains(page, heading)

    def test_order_form_is_the_standard_form_with_nothing_but_the_floor_inputs_visible(self):
        self.client.force_login(self.cook)
        page = self.client.get(reverse("order_add"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="order-form"')
        self.assertContains(page, "kitchen-mode")
        self.assertContains(page, "Cake")  # not shelf-tracked, still offered

    def test_product_list_never_includes_another_business(self):
        rival = Business.objects.create(name="Rival Bakery", slug="rival-bakery")
        FinishedGood.raw_objects.create(business=rival, name="Rival Secret Loaf", unit="loaf", units_per_batch=Decimal("10"),
                                        stock=Decimal("0"), reorder_level=Decimal("1"), selling_price=Decimal("500"))
        self.client.force_login(self.cook)
        self.assertNotContains(self.client.get(reverse("order_add")), "Rival Secret Loaf")
        from production.forms import OrderItemFormSet
        from core.context import set_current_business
        set_current_business(self.business)
        try:
            blank = OrderItemFormSet(instance=None, order_type="physical_store").empty_form
            names = " ".join(str(g) for g in blank.fields["finished_good"].queryset)
        finally:
            set_current_business(None)
        self.assertNotIn("Rival Secret Loaf", names)

    def test_order_is_saved_pending_with_no_channel_customer_or_payment(self):
        response = self._log(self.cook, notes="made this morning", customer_name="Sneaky", order_type="distribution")
        order = Order.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual((order.status, order.order_type), ("pending", Order.TYPE_UNASSIGNED))
        self.assertIsNone(order.customer)
        self.assertEqual(order.customer_name, "")  # forced blank even if a crafted POST sends one
        self.assertEqual(order.created_by, self.cook)
        self.assertEqual(order.items.get().price, self.cake.selling_price_for("physical_store", None))

    def test_floor_users_see_only_unassigned_orders_and_no_prices(self):
        order = self._logged_order()
        other = Order.objects.create(business=self.business, date=date.today(), order_type="physical_store")
        OrderItem.objects.create(order=other, finished_good=self.cake, batch_qty=1, price=Decimal("500"))
        self.client.force_login(self.guided)
        page = self.client.get(reverse("orders_list"))
        self.assertContains(page, f"#{order.display_number}")
        self.assertNotContains(page, f'href="/orders/{other.pk}/"')
        detail = self.client.get(reverse("order_detail", args=[order.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "Line Total")
        self.assertNotContains(detail, "Approve")
        # assigned orders are off limits
        self.assertRedirects(self.client.get(reverse("order_detail", args=[other.pk])), reverse("orders_list"), fetch_redirect_response=False)

    def test_everything_else_under_orders_stays_closed_to_floor_users(self):
        order = self._logged_order()
        for path in (f"/orders/{order.pk}/approve/", f"/orders/{order.pk}/complete/", f"/orders/{order.pk}/invoice/", "/orders/runs/", f"/orders/{order.pk}/split/"):
            self.assertEqual(self.client.get(path).status_code, 403, path)
        self.client.post(f"/orders/{order.pk}/approve/")
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")

    def test_floor_user_can_edit_until_a_manager_assigns_it(self):
        order = self._logged_order()
        self.assertEqual(self.client.get(reverse("order_edit", args=[order.pk])).status_code, 200)
        Order.objects.filter(pk=order.pk).update(order_type="physical_store")
        self.assertRedirects(self.client.get(reverse("order_edit", args=[order.pk])), reverse("orders_list"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("order_detail", args=[order.pk])), reverse("orders_list"), fetch_redirect_response=False)

    def test_a_user_with_regular_production_access_is_not_restricted(self):
        from accounts.models import UserModulePermission
        membership = UserBusiness.objects.get(user=self.cook, business=self.business)
        UserModulePermission.objects.create(membership=membership, module="production", can_view=True, can_edit=True)
        self.client.force_login(self.cook)
        page = self.client.get(reverse("order_add"))
        self.assertNotContains(page, "kitchen-mode")


class KitchenFlexibleAndAssignmentTests(KitchenBase):
    def setUp(self):
        super().setUp()
        self.order = self._logged_order()
        self.item = self.order.items.get()
        self.flex_name = f"flex_qty_{self.item.pk}_{self.link.pk}"

    def test_floor_user_saves_flexible_amounts_on_the_order_page(self):
        self.client.force_login(self.guided)
        page = self.client.get(reverse("order_detail", args=[self.order.pk]))
        self.assertContains(page, "Save flexible amounts")
        self.client.post(reverse("order_flexible_save", args=[self.order.pk]), {self.flex_name: "2.5"})
        report = OrderFlexibleMaterialReport.raw_objects.get(order=self.order)
        self.assertEqual(report.per_batch_for(self.item.pk, self.link.pk), Decimal("2.5000"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")  # nothing released
        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("500"))

    def test_invalid_amount_is_rejected_and_nothing_is_saved(self):
        self.client.force_login(self.cook)
        self.client.post(reverse("order_flexible_save", args=[self.order.pk]), {self.flex_name: "-4"})
        self.assertFalse(OrderFlexibleMaterialReport.raw_objects.filter(order=self.order).exists())

    def test_approval_page_is_prefilled_and_blocked_until_a_channel_is_decided(self):
        self.client.force_login(self.cook)
        self.client.post(reverse("order_flexible_save", args=[self.order.pk]), {self.flex_name: "2.5"})
        self.client.force_login(self.manager)
        page = self.client.get(reverse("order_detail", args=[self.order.pk]))
        row = next(r for r in page.context["flexible_usages"] if not r["central"])
        self.assertEqual(row["actual_per_batch"], Decimal("2.5000"))  # not the recipe's planned 2
        self.assertContains(page, "Choose channel")
        self.client.post(reverse("order_approve", args=[self.order.pk]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")
        Order.objects.filter(pk=self.order.pk).update(order_type="physical_store")
        self.client.post(reverse("order_approve", args=[self.order.pk]), {self.flex_name: "2.5"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "approved")

    def test_ordinary_orders_are_untouched_by_the_feature(self):
        plain = Order.objects.create(business=self.business, date=date.today(), order_type="physical_store")
        OrderItem.objects.create(order=plain, finished_good=self.cake, batch_qty=1, price=Decimal("500"))
        self.client.force_login(self.manager)
        page = self.client.get(reverse("order_detail", args=[plain.pk]))
        self.assertNotContains(page, "Recorded by production staff")
        self.assertContains(page, "Approve")
        row = next(r for r in page.context["flexible_usages"] if not r["central"])
        self.assertEqual(row["actual_per_batch"], Decimal("2.00"))


class ChannelAllocationTests(KitchenBase):
    """One production order, several channels: produced once, delivered to each channel at completion."""

    def setUp(self):
        super().setUp()
        self.cake.stock = Decimal("0")                 # shelf-tracked, so store deliveries are visible
        self.cake.save()
        self.order = self._logged_order()               # 3 batches of Cake, 10 per batch = 30 pieces, no channel yet
        self.item = self.order.items.get()
        self.customer = Customer.raw_objects.create(business=self.business, name="Mr A")
        self.client.force_login(self.manager)

    # ---- helpers
    def _assign(self, order=None, destinations=(), item=None):
        """destinations: dicts with channel, b, p (+ optional customer, pay_status, store_mode, removed ...)."""
        order, item = order or self.order, item or self.item
        data = {}
        for index, dest in enumerate(destinations):
            dest = dict(dest)
            data[f"d{index}_b_{item.pk}"] = str(dest.pop("b", ""))
            data[f"d{index}_p_{item.pk}"] = str(dest.pop("p", ""))
            for key, value in dest.items():
                data[f"d{index}_{key}"] = str(value)
        return self.client.post(reverse("order_split", args=[order.pk]), data)

    def _store_and_customer(self, order=None, item=None, store=(1, 5), customer=(1, 5), **customer_extra):
        return self._assign(order, item=item, destinations=[
            {"channel": "physical_store", "b": store[0], "p": store[1]},
            {"channel": "distribution", "customer": self.customer.pk, "pay_status": "unpaid", "b": customer[0], "p": customer[1], **customer_extra},
        ])

    def _approve(self, order):
        self.client.post(reverse("order_approve", args=[order.pk]), {"force": "1"})
        order.refresh_from_db()
        self.assertEqual(order.status, "approved")

    def _complete(self, order, item, produced, wastage=0, **extra):
        prefix = f"item-{item.pk}-"
        data = {prefix + "produced_units": str(produced), prefix + "wastage_units": str(wastage), prefix + "batch_number": "M-1",
                prefix + "expiry_date": "", prefix + "wastage_reason": "Burnt" if wastage else "", prefix + "qc_status": "pending",
                prefix + "qc_notes": "", prefix + "shortage_reason": "", prefix + "excess_to_stock": "0",
                prefix + "excess_to_market_stock": "0", prefix + "excess_to_non_stock": "0", prefix + "excess_non_stock_purpose": ""}
        data.update({prefix + key: str(value) for key, value in extra.items()})
        response = self.client.post(reverse("order_complete", args=[order.pk]), data)
        order.refresh_from_db()
        self.assertEqual(order.status, "completed", response.context and [str(f.errors) for _i, f, _o in response.context["completion_forms"]])
        return response

    def _sale_units(self, sale):
        return sum((i.batch_qty * 10 + i.piece_qty for i in sale.items.all()), Decimal("0"))

    # ---- assigning
    def test_assigning_to_several_channels_keeps_one_order(self):
        response = self._store_and_customer()
        self.assertEqual(Order.objects.count(), 1)
        self.order.refresh_from_db()
        self.assertEqual((self.order.order_type, self.order.status), ("multi_channel", "pending"))
        allocations = list(self.order.allocations.all())
        self.assertEqual([a.channel for a in allocations], ["physical_store", "distribution"])
        self.assertEqual(allocations[1].customer_id, self.customer.pk)
        self.assertEqual([a.total_units for a in allocations], [Decimal("15.00"), Decimal("15.00")])
        self.assertFalse(ProductionRun.objects.exists())
        self.item.refresh_from_db()
        self.assertEqual((self.item.batch_qty, self.item.piece_qty), (Decimal("3.00"), Decimal("0.00")))   # production untouched
        self.assertRedirects(response, reverse("order_detail", args=[self.order.pk]))

    def test_every_unit_must_be_assigned(self):
        self._assign(destinations=[{"channel": "physical_store", "b": 1}, {"channel": "distribution", "customer": self.customer.pk, "pay_status": "unpaid", "b": 1}])
        self.assertFalse(OrderAllocation.objects.exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.order_type, Order.TYPE_UNASSIGNED)

    def test_one_destination_just_gives_the_order_that_channel(self):
        self._assign(destinations=[{"channel": "physical_store", "b": 3}])
        self.order.refresh_from_db()
        self.assertEqual(self.order.order_type, "physical_store")
        self.assertFalse(OrderAllocation.objects.exists())

    def test_batches_and_pieces_are_one_quantity_and_a_batch_can_be_broken(self):
        # 12 pieces is more than one 10-piece batch: it rolls over into 1 batch + 2 pieces.
        self._assign(destinations=[
            {"channel": "distribution", "customer": self.customer.pk, "pay_status": "unpaid", "b": 0, "p": 12},
            {"channel": "physical_store", "b": 1, "p": 8},
        ])
        first, second = self.order.allocations.all()
        line = first.lines.get()
        self.assertEqual((line.batch_qty, line.piece_qty), (Decimal("1.00"), Decimal("2.00")))
        self.assertEqual(second.total_units, Decimal("18.00"))

    def test_nothing_changes_when_an_assignment_is_invalid(self):
        self._assign(destinations=[{"channel": "physical_store", "b": 4}])                       # more than exists
        self._assign(destinations=[{"channel": "distribution", "b": 1}, {"channel": "physical_store", "b": 2}])   # customer order, no customer
        self._assign(destinations=[{"channel": "", "b": 1}, {"channel": "physical_store", "b": 2}])               # quantity without a channel
        self._assign(destinations=[{"channel": "physical_store", "b": 1}, {"channel": "physical_store", "b": 2}])  # stock twice
        self.assertFalse(OrderAllocation.objects.exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.order_type, Order.TYPE_UNASSIGNED)

    def test_a_destination_can_be_removed_and_is_ignored_even_if_it_still_holds_quantities(self):
        page = self.client.get(reverse("order_split", args=[self.order.pk]))
        self.assertContains(page, "data-remove-destination")
        self._assign(destinations=[
            {"channel": "physical_store", "b": 3},
            {"channel": "distribution", "b": 2, "removed": 1},        # no customer: would be an error if it counted
        ])
        self.order.refresh_from_db()
        self.assertEqual(self.order.order_type, "physical_store")

    def test_a_removed_destination_stays_hidden_when_the_page_comes_back_with_an_error(self):
        response = self._assign(destinations=[{"channel": "physical_store", "b": 9}, {"channel": "", "removed": 1}])
        destinations = response.context["destinations"]
        self.assertTrue(destinations[0]["visible"])
        self.assertFalse(destinations[1]["visible"])
        self.assertEqual(response.context["visible_destinations"], 1)

    def test_the_saved_assignment_comes_back_on_the_page_and_can_be_changed(self):
        self._store_and_customer()
        page = self.client.get(reverse("order_split", args=[self.order.pk]))
        self.assertEqual([d["channel"] for d in page.context["destinations"][:2]], ["physical_store", "distribution"])
        self.assertEqual(page.context["rows"][0]["cells"][0]["batch"], "1")
        self._assign(destinations=[{"channel": "physical_store", "b": 2}, {"channel": "online", "customer_name": "Walk-in Web", "pay_status": "unpaid", "b": 1}])
        self.assertEqual([a.channel for a in self.order.allocations.all()], ["physical_store", "online"])

    def test_a_multi_channel_order_is_edited_through_its_assignment_not_the_order_form(self):
        self._store_and_customer()
        response = self.client.get(reverse("order_edit", args=[self.order.pk]))
        self.assertRedirects(response, reverse("order_split", args=[self.order.pk]), fetch_redirect_response=False)

    def test_an_order_from_the_main_form_can_be_assigned_too_and_keeps_its_channel_prefilled(self):
        plain = Order.objects.create(business=self.business, date=date.today(), order_type="distribution", customer=self.customer, customer_name="Mr A")
        item = OrderItem.objects.create(order=plain, finished_good=self.cake, batch_qty=2, price=Decimal("500"), discount=Decimal("5"))
        page = self.client.get(reverse("order_split", args=[plain.pk]))
        self.assertEqual(page.context["destinations"][0]["channel"], "distribution")
        self.assertEqual(page.context["destinations"][0]["customer"], str(self.customer.pk))
        self._assign(order=plain, item=item, destinations=[
            {"channel": "distribution", "customer": self.customer.pk, "pay_status": "unpaid", "b": 1},
            {"channel": "physical_store", "b": 1},
        ])
        plain.refresh_from_db()
        self.assertEqual(plain.order_type, "multi_channel")
        self.assertEqual(plain.customer_id, None)
        self.assertEqual(plain.allocations.first().lines.get().discount, Decimal("5.00"))

    # ---- producing and completing
    def test_the_order_is_approved_once_and_releases_materials_once(self):
        self._store_and_customer()
        self._approve(self.order)
        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("470.0000"))                      # 3 batches x 10, not per channel

    def test_completion_delivers_each_channels_share_and_bills_the_customer(self):
        self._store_and_customer()
        self._approve(self.order)
        self._complete(self.order, self.item, produced=30)
        self.cake.refresh_from_db()
        self.assertEqual(self.cake.stock, Decimal("15.00"))                          # the store's share only
        self.assertEqual(self.cake.total_delivered_to_customers, Decimal("15.00"))
        self.assertEqual(ProductionBatch.objects.filter(order=self.order).count(), 1)  # produced once
        sale = Sale.objects.get(linked_order=self.order)
        self.assertEqual((sale.customer_master_id, sale.transaction_type, sale.source), (self.customer.pk, "unpaid", "distribution_order"))
        self.assertEqual(self._sale_units(sale), Decimal("15.00"))
        self.assertEqual(sale.total, Decimal("7500.00"))
        self.assertEqual([l.delivered_units for a in self.order.allocations.all() for l in a.lines.all()], [Decimal("15.00")] * 2)
        self.assertEqual(self.order.allocations.get(channel="distribution").sale_id, sale.pk)

    def test_when_less_is_produced_customers_are_filled_first_and_billed_only_for_what_arrives(self):
        self._store_and_customer()
        self._approve(self.order)
        self._complete(self.order, self.item, produced=20, wastage=2)               # 18 saleable of 30 allocated
        self.cake.refresh_from_db()
        self.assertEqual(self.cake.stock, Decimal("3.00"))                           # customer got 15, the store the other 3
        self.assertEqual(self._sale_units(Sale.objects.get(linked_order=self.order)), Decimal("15.00"))

    def test_a_short_run_can_leave_a_customer_short_and_the_bill_follows_delivery(self):
        self._store_and_customer()
        self._approve(self.order)
        self._complete(self.order, self.item, produced=10)
        self.cake.refresh_from_db()
        self.assertEqual(self.cake.stock, Decimal("0.00"))
        self.assertEqual(self._sale_units(Sale.objects.get(linked_order=self.order)), Decimal("10.00"))
        page = self.client.get(reverse("order_detail", args=[self.order.pk]))
        self.assertContains(page, "(short)")

    def test_planned_output_nobody_was_assigned_stays_in_general_stock(self):
        self.item.production_batch_qty = Decimal("4")                                # plan 40, order 30
        self.item.save()
        self._store_and_customer()
        self._approve(self.order)
        self._complete(self.order, self.item, produced=40)
        batch = ProductionBatch.objects.get(order=self.order)
        self.assertEqual(batch.planned_surplus_stock_units, Decimal("10.00"))
        self.cake.refresh_from_db()
        self.assertEqual(self.cake.stock, Decimal("25.00"))                          # store 15 + 10 left over

    def test_excess_beyond_the_plan_uses_the_usual_excess_destinations(self):
        self._store_and_customer()
        self._approve(self.order)
        self._complete(self.order, self.item, produced=35, excess_to_stock=5)
        self.cake.refresh_from_db()
        self.assertEqual(self.cake.stock, Decimal("20.00"))                          # store 15 + excess 5

    def test_a_paid_customer_allocation_records_its_cash_once_under_the_orders_reference(self):
        account = CashAccount.raw_objects.create(business=self.business, name="Till")
        self._assign(destinations=[
            {"channel": "physical_store", "b": 1, "p": 5},
            {"channel": "distribution", "customer": self.customer.pk, "pay_status": "paid", "pay_method": "Cash", "pay_account": account.pk, "b": 1, "p": 5},
        ])
        self._approve(self.order)
        self._complete(self.order, self.item, produced=30)
        payments = FinancialTransaction.raw_objects.filter(reference=f"ORDER-{self.order.pk}")
        self.assertEqual(payments.count(), 1)
        self.assertEqual(payments.get().amount, Decimal("7500.00"))
        self.assertEqual(Sale.objects.get(linked_order=self.order).transaction_type, "paid")

    def test_two_customers_each_get_their_own_sale(self):
        other = Customer.raw_objects.create(business=self.business, name="Mrs B")
        self._assign(destinations=[
            {"channel": "distribution", "customer": self.customer.pk, "pay_status": "unpaid", "b": 1},
            {"channel": "online", "customer": other.pk, "pay_status": "unpaid", "b": 1},
            {"channel": "physical_store", "b": 1},
        ])
        self._approve(self.order)
        self._complete(self.order, self.item, produced=30)
        sales = Sale.objects.filter(linked_order=self.order).order_by("id")
        self.assertEqual([(s.customer_master_id, s.source) for s in sales], [(self.customer.pk, "distribution_order"), (other.pk, "online_order")])
        self.assertEqual([self._sale_units(s) for s in sales], [Decimal("10.00")] * 2)

    def test_market_stock_allocation_enters_market_stock_and_is_reversible(self):
        from inventory.models import MarketStockLot
        self._assign(destinations=[{"channel": "distribution_market_stock", "b": 1}, {"channel": "physical_store", "b": 2}])
        self._approve(self.order)
        self._complete(self.order, self.item, produced=30)
        lot = MarketStockLot.raw_objects.get(production_batch__order=self.order)
        self.assertEqual(lot.quantity_available, Decimal("10.00"))
        self.cake.refresh_from_db()
        self.assertEqual(self.cake.stock, Decimal("20.00"))
        self.client.post(reverse("order_reverse", args=[self.order.pk]), {"reason": "test"})
        self.order.refresh_from_db()
        lot.refresh_from_db()
        self.assertEqual(self.order.status, "reversed")
        self.assertEqual(lot.quantity_available, Decimal("0.00"))
        self.cake.refresh_from_db()
        self.assertEqual(self.cake.stock, Decimal("0.00"))

    def test_reversal_undoes_every_channels_posting_and_the_order_can_be_recreated(self):
        self.item.production_batch_qty = Decimal("4")
        self.item.save()
        self._store_and_customer()
        self._approve(self.order)
        self._complete(self.order, self.item, produced=40)
        self.client.post(reverse("order_reverse", args=[self.order.pk]), {"reason": "Wrong run"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "reversed")
        self.cake.refresh_from_db()
        self.assertEqual((self.cake.stock, self.cake.total_produced, self.cake.total_delivered_to_customers),
                         (Decimal("0.00"), Decimal("0.00"), Decimal("0.00")))
        self.assertFalse(Sale.objects.filter(linked_order=self.order).exists())
        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("500.0000"))
        response = self.client.post(reverse("order_recreate", args=[self.order.pk]))
        again = Order.objects.exclude(pk=self.order.pk).get()
        self.assertEqual((again.status, again.order_type), ("pending", "multi_channel"))
        self.assertEqual([(a.channel, a.total_units, a.delivered_units) for a in again.allocations.all()],
                         [("physical_store", Decimal("15.00"), Decimal("0")), ("distribution", Decimal("15.00"), Decimal("0"))])
        self.assertRedirects(response, reverse("order_split", args=[again.pk]), fetch_redirect_response=False)

    def test_a_base_material_order_can_be_assigned_and_keeps_its_pool_and_basis(self):
        self.cake.base_material = self.flour
        self.cake.save()
        base = Order.objects.create(business=self.business, date=date.today(), order_type="physical_store",
                                    production_basis="base_material", base_material_quantity=Decimal("60"))
        item = OrderItem.objects.create(order=base, finished_good=self.cake, batch_qty=3, price=Decimal("500"),
                                        production_basis="base_material", base_material_quantity=Decimal("30"))
        self._store_and_customer(order=base, item=item)
        base.refresh_from_db()
        self.assertEqual((base.order_type, base.production_basis, base.base_material_quantity), ("multi_channel", "base_material", Decimal("60.0000")))
        self._approve(base)
        self._complete(base, item, produced=30)
        self.assertEqual(self._sale_units(Sale.objects.get(linked_order=base)), Decimal("15.00"))
        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("470.0000"))

    def test_a_multi_channel_order_can_be_a_member_of_a_shared_run(self):
        self._store_and_customer()
        other = Order.objects.create(business=self.business, date=date.today(), order_type="physical_store")
        OrderItem.objects.create(order=other, finished_good=self.cake, batch_qty=1, price=Decimal("500"))
        run = ProductionRun.objects.create(business=self.business, created_by=self.manager, date=date.today(), run_number="RUN-T1")
        run.orders.add(self.order, other)
        response = self.client.post(reverse("production_run_approve", args=[run.pk]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "approved", response.status_code)

    # ---- everywhere the order is shown
    def test_the_order_reads_clearly_on_its_pages_and_invoice(self):
        self._store_and_customer()
        detail = self.client.get(reverse("order_detail", args=[self.order.pk]))
        self.assertContains(detail, "data-channel-assignment")
        self.assertContains(detail, "Physical Store stock")
        self.assertContains(detail, "Mr A")
        self.assertContains(self.client.get(reverse("orders_list")), "Several channels")
        self._approve(self.order)
        self.assertContains(self.client.get(reverse("order_complete", args=[self.order.pk])), "assigned to several channels")
        pdf = self.client.get(reverse("order_invoice", args=[self.order.pk]))
        self.assertEqual((pdf.status_code, pdf["Content-Type"]), (200, "application/pdf"))

    def test_multi_channel_can_never_be_chosen_on_the_order_form(self):
        page = self.client.get(reverse("order_add"))
        self.assertNotContains(page, "multi_channel")
        before = Order.objects.count()
        data = {"date": date.today().isoformat(), "order_type": "multi_channel", "production_basis": "product_quantity",
                "base_material_quantity": "", "notes": "", **_formset([(self.cake.pk, "1", "0")])}
        self.client.post(reverse("order_add"), data)
        self.assertEqual(Order.objects.count(), before)
        self.assertFalse(Order.objects.filter(order_type="multi_channel").exists())

    def test_allocations_travel_in_tenant_backups_and_never_cross_tenants(self):
        from core.models import Business as Tenant
        from .models import OrderAllocationLine
        self._store_and_customer()
        rival = Tenant.objects.create(name="Rival", slug="rival-co", vertical=Tenant.VERTICAL_BAKERY)
        other = Order.raw_objects.create(business=rival, date=date.today(), order_type="multi_channel")
        other_alloc = OrderAllocation.objects.create(order=other, channel="physical_store")
        querysets = {qs.model: qs for qs in tenant_backup_querysets(self.business)}
        self.assertEqual(set(querysets[OrderAllocation].values_list("order_id", flat=True)), {self.order.pk})
        self.assertNotIn(other_alloc.pk, querysets[OrderAllocation].values_list("pk", flat=True))
        self.assertEqual(querysets[OrderAllocationLine].count(), 2)
        # ...and they survive a real JSON backup round trip.
        import json
        from django.core import serializers
        from core.services import tenant_backup_objects
        payload = json.loads(serializers.serialize("json", tenant_backup_objects(self.business)))
        self.assertEqual(sum(1 for row in payload if row["model"] == "production.orderallocation"), 2)
        self.assertEqual(sum(1 for row in payload if row["model"] == "production.orderallocationline"), 2)
        self.assertEqual(len(list(serializers.deserialize("json", json.dumps(payload)))), len(payload))

    def test_the_product_report_lists_multi_channel_production_only_once_it_exists(self):
        from core.views import _finished_good_search_detail
        channels = lambda: [row["channel"] for row in _finished_good_search_detail(self.cake)["production_channels"]]
        self.assertEqual(channels(), ["Physical store", "Distribution", "Online"])
        self._store_and_customer()
        self._approve(self.order)
        self._complete(self.order, self.item, produced=30)
        self.assertEqual(channels()[-1], "Multi-channel (shared production)")

    def test_orders_that_cannot_be_divided_are_refused_politely(self):
        self.order.status = "approved"
        self.order.save()
        response = self._assign(destinations=[{"channel": "physical_store", "b": 3}])
        self.assertRedirects(response, reverse("order_detail", args=[self.order.pk]), fetch_redirect_response=False)
        self.assertFalse(OrderAllocation.objects.exists())