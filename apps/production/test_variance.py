"""Recipe-versus-actual variance: ingredient release, shrinkage and output."""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from core.models import Business
from inventory.models import FinishedGood, RawMaterial, StockAdjustment
from production.models import Order, OrderItem, OrderMaterialUsage, ProductionBatch
from production.variance import ingredient_variance, output_variance, variance_report

DAY = date(2026, 3, 10)
RANGE = (date(2026, 3, 1), date(2026, 3, 31))


class VarianceTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Variance Bakery", slug="variance-bakery")
        self.flour = RawMaterial.objects.create(business=self.business, name="Flour", usage_unit="kg",
                                                stock=Decimal("100"), cost_per_unit=Decimal("2"))
        self.sugar = RawMaterial.objects.create(business=self.business, name="Sugar", usage_unit="kg",
                                                stock=Decimal("100"), cost_per_unit=Decimal("3"))
        self.bread = FinishedGood.objects.create(business=self.business, name="Bread", unit="loaf")
        self.order = Order.objects.create(business=self.business, date=DAY, status="completed", approved_date=DAY,
                                          order_type="physical_store")
        self.item = OrderItem.objects.create(order=self.order, finished_good=self.bread, batch_qty=Decimal("5"))

    def usage(self, material, planned, actual, order=None, item=None):
        return OrderMaterialUsage.objects.create(
            business=self.business, order=order or self.order, order_item=item or self.item,
            raw_material=material, planned_quantity=Decimal(planned), actual_quantity=Decimal(actual))

    def test_flags_ingredients_that_drift_from_the_recipe(self):
        self.usage(self.flour, "100", "100")
        self.usage(self.sugar, "5", "6")  # 20% over
        rows = {r["name"]: r for r in ingredient_variance(self.business, *RANGE)}
        self.assertEqual(rows["Flour"]["status"], "ok")
        self.assertEqual(rows["Sugar"]["status"], "review")
        self.assertEqual(rows["Sugar"]["variance"], Decimal("1"))
        self.assertEqual(rows["Sugar"]["variance_pct"], Decimal("20"))
        self.assertEqual(rows["Sugar"]["variance_value"], Decimal("3"))

    def test_review_rows_sort_first_and_small_drift_is_not_flagged(self):
        self.usage(self.flour, "100", "102")  # 2% is within tolerance
        self.usage(self.sugar, "5", "6")
        rows = ingredient_variance(self.business, *RANGE)
        self.assertEqual([r["name"] for r in rows], ["Sugar", "Flour"])
        self.assertEqual(rows[1]["status"], "ok")

    def test_writeoffs_are_reported_as_shrinkage_the_recipe_cannot_explain(self):
        self.usage(self.flour, "100", "100")
        StockAdjustment.objects.create(business=self.business, date=DAY, raw_material=self.flour,
                                       quantity=Decimal("-4"), unit_value=Decimal("2"), description="Spilled")
        (row,) = ingredient_variance(self.business, *RANGE)
        self.assertEqual((row["shrink_qty"], row["shrink_value"], row["status"]), (Decimal("4"), Decimal("8"), "watch"))

    def test_only_approved_orders_in_range_count(self):
        pending = Order.objects.create(business=self.business, date=DAY, status="pending", order_type="physical_store")
        item = OrderItem.objects.create(order=pending, finished_good=self.bread, batch_qty=Decimal("1"))
        self.usage(self.flour, "50", "80", order=pending, item=item)
        outside = Order.objects.create(business=self.business, date=date(2026, 5, 1), status="completed",
                                       approved_date=date(2026, 5, 1), order_type="physical_store")
        item2 = OrderItem.objects.create(order=outside, finished_good=self.bread, batch_qty=Decimal("1"))
        self.usage(self.flour, "50", "80", order=outside, item=item2)
        self.assertEqual(ingredient_variance(self.business, *RANGE), [])

    def test_output_variance_reports_yield_and_waste_value(self):
        ProductionBatch.objects.create(
            business=self.business, order=self.order, order_item=self.item, finished_good=self.bread,
            production_date=DAY, batch_number="B1", planned_units=Decimal("100"), produced_units=Decimal("90"),
            wastage_units=Decimal("10"), total_cost=Decimal("180"))
        (row,) = output_variance(self.business, *RANGE)
        self.assertEqual(row["yield_pct"], Decimal("90"))
        self.assertEqual(row["waste_pct"], Decimal("10"))
        self.assertEqual(row["waste_value"], Decimal("20"))  # 10 wasted units at 2 per unit
        self.assertEqual(variance_report(self.business, *RANGE)["totals"]["waste_value"], Decimal("20"))

    def test_page_csv_and_permissions(self):
        roles = seed_business_roles(self.business)
        admin = CustomUser.objects.create_user(username="var-admin", password="pw-123456")
        UserBusiness.objects.create(user=admin, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.usage(self.sugar, "5", "6")
        self.client.force_login(admin)
        query = {"start": RANGE[0].isoformat(), "end": RANGE[1].isoformat()}
        page = self.client.get(reverse("production_variance"), query)
        self.assertContains(page, "Recipe vs Actual")
        self.assertContains(page, "Sugar")
        csv_response = self.client.get(reverse("production_variance"), {**query, "format": "csv"})
        self.assertEqual(csv_response["Content-Type"], "text/csv")
        self.assertIn("Sugar", csv_response.content.decode())
        outsider = CustomUser.objects.create_user(username="var-outsider", password="pw-123456")
        self.client.force_login(outsider)
        self.assertNotEqual(self.client.get(reverse("production_variance"), query).status_code, 200)
