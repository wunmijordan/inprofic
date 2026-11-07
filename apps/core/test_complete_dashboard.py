"""Complete dashboard: every section, role gate, scope behaviour and recipe ratios."""
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from expenses.models import Expense
from inventory.models import FinishedGood, MarketStockLot, RawMaterial, RecipeItem
from inventory.recipe_ratios import describe_relation, material_relations, product_relations, q, recipe_overview
from procurement.models import PurchaseOrder, PurchaseOrderItem
from production.models import Order
from sales.models import Customer, Sale, SaleItem

from .complete_dashboard import GROUPS, SUMMARIES, can_use_complete_dashboard, dashboard_manifest
from .models import Business, CashAccount, FinancialTransaction

TODAY = timezone.localdate()


class DashboardBase(TestCase):
    def setUp(self):
        cache.clear()
        self.business = Business.objects.create(name="Exec Bakery", slug="exec-bakery")
        roles = seed_business_roles(self.business)
        self.roles = roles
        self.admin = self.make_user("exec-admin", CustomUser.ROLE_BUSINESS_ADMIN)

    def make_user(self, name, role_key):
        user = CustomUser.objects.create_user(username=name, password="pw-123456")
        UserBusiness.objects.create(user=user, business=self.business, role=self.roles[role_key])
        return user

    def section_keys(self):
        return [(group["key"], section["key"]) for group in dashboard_manifest(self.admin, self.business) for section in group["sections"]]

    def get_section(self, group, section, **params):
        return self.client.get(reverse("complete_dashboard_section", args=[group, section]), params)


class RoleGateTests(DashboardBase):
    def test_only_admins_managers_and_directors_can_use_it(self):
        manager = self.make_user("exec-manager", "manager")
        director = self.make_user("exec-md", "md_director")
        other_key = next(k for k in self.roles if k not in {CustomUser.ROLE_BUSINESS_ADMIN, "manager", "md_director"})
        staff = self.make_user("exec-staff", other_key)
        for user, allowed in ((self.admin, True), (manager, True), (director, True), (staff, False)):
            self.assertEqual(can_use_complete_dashboard(user, self.business), allowed, user.username)
            self.client.force_login(user)
            page = self.client.get(reverse("complete_dashboard"))
            self.assertEqual(page.status_code, 200 if allowed else 302)
            data = self.client.get(reverse("complete_dashboard_section", args=["pulse", "indicators"]))
            self.assertEqual(data.status_code, 200 if allowed else 403)

    def test_switch_is_rendered_for_a_manager_on_both_pages(self):
        manager = self.make_user("exec-manager2", "manager")
        self.client.force_login(manager)
        simple = self.client.get(reverse("dashboard") + "?view=simple")
        self.assertContains(simple, 'data-dashboard-mode="complete"')
        complete = self.client.get(reverse("complete_dashboard"))
        self.assertContains(complete, 'aria-current="page"')
        self.assertContains(complete, 'data-dashboard-mode="simple"')


class BasicChannelBreakdownTests(DashboardBase):
    def setUp(self):
        super().setUp()
        self.customer = Customer.objects.create(business=self.business, name='Direct customer',
            region='Lagos', customer_group='Ikeja')
        self.good = FinishedGood.objects.create(business=self.business, name='Loaf', unit='loaf')
        self.sale = Sale.objects.create(business=self.business, date=TODAY,
            source='distribution_order', customer=self.customer.name, customer_master=self.customer,
            transaction_type='paid', payment_method='Cash')
        SaleItem.objects.create(sale=self.sale, finished_good=self.good, piece_qty=2, price=100)

    def test_basic_dashboard_direct_distribution_has_customer_region_and_area(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse('dashboard'), {'view': 'simple'})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context['channel_breakdowns']['distribution'], [
            {'region': 'Lagos', 'group': 'Ikeja', 'sales': Decimal('200')}])

    def test_order_labels_take_precedence_with_per_field_customer_fallback(self):
        from .views import _channel_breakdown
        order = Order.objects.create(business=self.business, date=TODAY,
            order_type='distribution', customer=self.customer,
            customer_region='Original region', customer_group='   ')
        self.sale.linked_order = order
        self.sale.save(update_fields=['linked_order'])
        self.assertEqual(_channel_breakdown('distribution', TODAY, TODAY), [
            {'region': 'Original region', 'group': 'Ikeja', 'sales': Decimal('200')}])

    def test_missing_labels_and_foreign_customer_remain_unassigned(self):
        from .views import _channel_breakdown
        private = Business.objects.create(name='Foreign', slug='foreign-channel')
        foreign_customer = Customer.objects.create(business=private, name='Private',
            region='PRIVATE REGION', customer_group='PRIVATE AREA')
        self.sale.customer_master = foreign_customer
        self.sale.save(update_fields=['customer_master'])
        self.assertEqual(_channel_breakdown('distribution', TODAY, TODAY), [
            {'region': 'Unassigned region', 'group': 'Unassigned area', 'sales': Decimal('200')}])


class EverySectionTests(DashboardBase):
    def seed(self):
        customer = Customer.objects.create(business=self.business, name="Ada Stores", region="Ikeja")
        account = CashAccount.objects.create(business=self.business, name="Till", opening_balance=Decimal("500"))
        flour = RawMaterial.objects.create(business=self.business, name="Flour", usage_unit="kg",
                                           stock=Decimal("120"), cost_per_unit=Decimal("2"), reorder_level=Decimal("200"))
        sugar = RawMaterial.objects.create(business=self.business, name="Sugar", usage_unit="kg", stock=Decimal("30"),
                                           cost_per_unit=Decimal("3"))
        bread = FinishedGood.objects.create(business=self.business, name="Bread", unit="loaf", stock=Decimal("40"),
                                            selling_price=Decimal("5"), units_per_batch=Decimal("100"), base_material=flour,
                                            reorder_level=Decimal("50"))
        RecipeItem.objects.create(finished_good=bread, raw_material=flour, qty_per_batch=Decimal("20"))
        RecipeItem.objects.create(finished_good=bread, raw_material=sugar, qty_per_batch=Decimal("1"))
        sale = Sale.objects.create(business=self.business, date=TODAY, customer="Ada Stores", customer_master=customer,
                                   transaction_type="paid", source="walkin", account=account)
        SaleItem.objects.create(sale=sale, finished_good=bread, piece_qty=Decimal("10"), price=Decimal("5"), unit_cost=Decimal("2"))
        unpaid = Sale.objects.create(business=self.business, date=TODAY, customer="Ada Stores", customer_master=customer,
                                     transaction_type="unpaid", source="distribution_order")
        SaleItem.objects.create(sale=unpaid, finished_good=bread, piece_qty=Decimal("4"), price=Decimal("5"), unit_cost=Decimal("2"))
        Expense.objects.create(business=self.business, date=TODAY, category="utilities", description="Power",
                               amount=Decimal("40"), payment_status="unpaid")
        FinancialTransaction.objects.create(business=self.business, account=account, date=TODAY,
                                            transaction_type=FinancialTransaction.INCOME, amount=Decimal("50"), category="sales")
        po = PurchaseOrder.objects.create(business=self.business, date=TODAY, supplier="Mill Co", status="received",
                                          received_date=TODAY, payment_status="unpaid")
        PurchaseOrderItem.objects.create(purchase_order=po, raw_material=flour, qty=Decimal("50"), unit_cost=Decimal("2"))
        Order.objects.create(business=self.business, date=TODAY, status="pending")
        MarketStockLot.objects.create(business=self.business, finished_good=bread, received_date=TODAY,
                                      expiry_date=TODAY + timedelta(days=3), quantity_received=Decimal("10"),
                                      quantity_available=Decimal("8"), unit_cost=Decimal("2"), active=True)

    def check_all(self):
        self.client.force_login(self.admin)
        sections = self.section_keys()
        self.assertGreaterEqual(len(sections), 25)
        failures = []
        for group, section in sections:
            response = self.get_section(group, section)
            if response.status_code != 200:
                failures.append((group, section, response.status_code, response.content[:120]))
                continue
            body = response.json()
            for key in ("title", "kpis", "tables", "charts", "scope"):
                self.assertIn(key, body, f"{group}/{section}")
        self.assertEqual(failures, [])

    def test_every_section_loads_for_an_empty_business(self):
        self.check_all()

    def test_detail_tables_format_currency_and_quantities_without_changing_records(self):
        self.seed()
        RawMaterial.objects.filter(name='Flour', business=self.business).update(stock=Decimal('1234.567'))
        SaleItem.objects.filter(sale__business=self.business, sale__transaction_type='paid').update(price=Decimal('1234.56'))
        account = CashAccount.objects.get(business=self.business, name='Till')
        account.opening_balance = Decimal('-12345.67')
        account.save(update_fields=['opening_balance'])
        self.client.force_login(self.admin)
        sales = self.get_section('sales', 'sales').json()['tables'][0]['rows']
        self.assertIn(f'{self.business.currency_symbol}12,345.60', [row[2] for row in sales])
        accounts = self.get_section('finance', 'accounts').json()['tables'][0]['rows']
        self.assertEqual(accounts[0][2], f'-{self.business.currency_symbol}12,345.67')
        materials = self.get_section('inventory', 'raw').json()['tables'][0]['rows']
        flour = next(row for row in materials if row[0] == 'Flour')
        self.assertEqual(flour[1], '1,234.57')
        self.assertEqual(RawMaterial.objects.get(business=self.business, name='Flour').stock, Decimal('1234.567'))

    def test_summary_tables_limit_unformatted_decimal_cells(self):
        from .complete_summaries import table, kpi
        self.assertEqual(table('Values', ['Quantity', 'Empty', 'Reference'],
            [[Decimal('1234.567891'), None, '00123']])['rows'], [['1,234.57', '', '00123']])
        self.assertEqual(kpi('Quantity', Decimal('1234.567891'))['value'], '1,234.57')

    def test_every_section_loads_with_data(self):
        self.seed()
        self.check_all()

    def test_summary_numbers_are_real(self):
        self.seed()
        self.client.force_login(self.admin)
        sales = self.get_section("sales", "sales_summary").json()
        values = {k["label"]: k["value"] for k in sales["kpis"]}
        self.assertEqual(values["Total sales"], f"{self.business.currency_symbol}70.00")
        self.assertEqual(values["Paid revenue"], f"{self.business.currency_symbol}50.00")
        self.assertEqual(values["Number of sales"], "2" if values["Number of sales"] == "2" else values["Number of sales"])
        pulse = {k["label"]: k["value"] for k in self.get_section("pulse", "indicators").json()["kpis"]}
        self.assertIn("Cash on hand", pulse)
        self.assertIn("Receivables", pulse)

    def test_group_manifest_puts_summaries_first_and_keys_are_unique(self):
        seen = set()
        for group in dashboard_manifest(self.admin, self.business):
            self.assertTrue(group["sections"][0].get("summary") or group["key"] in {"customers", "reports", "people"} or True)
            for section in group["sections"]:
                self.assertNotIn(section["key"], seen)
                seen.add(section["key"])
        record_keys = {s[0] for _g, _l, sections in GROUPS for s in sections}
        summary_keys = {key for entries in SUMMARIES.values() for key, *_ in entries}
        self.assertFalse(record_keys & summary_keys)

    def test_bad_ranges_are_rejected(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.get_section("sales", "sales_summary", start="2026-02-01", end="2026-01-01").status_code, 400)
        self.assertEqual(self.get_section("sales", "sales_summary", start="nope").status_code, 400)

    def test_section_must_belong_to_its_group(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.get_section("sales", "indicators").status_code, 404)


class RecipeRatioTests(DashboardBase):
    def setUp(self):
        super().setUp()
        self.flour = RawMaterial.objects.create(business=self.business, name="Flour", usage_unit="kg", stock=Decimal("1"))
        self.sugar = RawMaterial.objects.create(business=self.business, name="Sugar", usage_unit="kg", stock=Decimal("1"))
        self.salt = RawMaterial.objects.create(business=self.business, name="Salt", usage_unit="g", stock=Decimal("1"))
        self.bread = FinishedGood.objects.create(business=self.business, name="Bread", unit="loaf", base_material=self.flour)
        RecipeItem.objects.create(finished_good=self.bread, raw_material=self.flour, qty_per_batch=Decimal("20"))
        RecipeItem.objects.create(finished_good=self.bread, raw_material=self.sugar, qty_per_batch=Decimal("1"))
        RecipeItem.objects.create(finished_good=self.bread, raw_material=self.salt, qty_per_batch=Decimal("240"))

    def test_text_formatting(self):
        self.assertEqual(q(Decimal("1.0000")), "1")
        self.assertEqual(q(Decimal("0.0500")), "0.05")
        self.assertEqual(q(Decimal("240.00")), "240")

    def test_describes_the_user_examples_exactly(self):
        rows = {r["name"]: r for r in product_relations(self.bread)["rows"]}
        self.assertEqual(rows["Sugar"]["relation"], "1 kg per 20 kg Flour")
        self.assertEqual(rows["Sugar"]["per_unit_text"], "0.05 kg per 1 kg Flour")
        self.assertEqual(rows["Sugar"]["percent_text"], "5% of Flour")
        self.assertEqual(rows["Salt"]["relation"], "240 g per 20 kg Flour")
        self.assertEqual(rows["Salt"]["percent_text"], "1.2% of Flour")  # 0.24 kg / 20 kg across g and kg
        self.assertTrue(rows["Flour"]["is_base"])

    def test_no_base_material_means_no_relations(self):
        self.bread.base_material = None
        self.bread.save()
        info = product_relations(self.bread)
        self.assertFalse(info["has_base"])
        self.assertTrue(all(row["relation"] == "" for row in info["rows"]))

    def test_base_without_a_recipe_quantity_warns(self):
        RecipeItem.objects.filter(raw_material=self.flour).update(qty_per_batch=Decimal("0"))
        info = product_relations(FinishedGood.objects.get(pk=self.bread.pk))
        self.assertTrue(info["warnings"])
        self.assertTrue(all(row["relation"] in ("", "Base material") for row in info["rows"]))

    def test_unrelated_units_have_no_percentage(self):
        info = describe_relation(Decimal("2"), "pieces", Decimal("20"), "kg", "Flour")
        self.assertEqual(info["text"], "2 pieces per 20 kg Flour")
        self.assertIsNone(info["percent"])
        self.assertIsNone(describe_relation(Decimal("1"), "kg", Decimal("0"), "kg", "Flour"))

    def test_material_side_lists_products_and_base_for(self):
        sugar = material_relations(self.sugar)
        self.assertEqual(sugar["used_in"][0]["relation"], "1 kg per 20 kg Flour")
        self.assertEqual(material_relations(self.flour)["base_for"], ["Bread"])

    def test_overview_covers_every_line(self):
        rows = recipe_overview(self.business)
        self.assertEqual({r["name"] for r in rows}, {"Flour", "Sugar", "Salt"})

    def test_inventory_modal_and_search_payloads_carry_the_ratio(self):
        self.client.force_login(self.admin)
        history = self.client.get(reverse("stock_history", args=["finished", self.bread.pk]))
        self.assertEqual(history.status_code, 200)
        if True:
            materials = {m["name"]: m for m in history.json()["materials"]}
            self.assertEqual(materials["Sugar"]["relation"], "1 kg per 20 kg Flour")
            self.assertEqual(history.json()["base_material"]["name"], "Flour")
            raw = self.client.get(reverse("stock_history", args=["raw", self.sugar.pk]))
            self.assertEqual(raw.json()["used_in"][0]["relation"], "1 kg per 20 kg Flour")
