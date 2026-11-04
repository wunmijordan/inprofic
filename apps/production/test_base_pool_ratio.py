"""How each base-material sizing mode scales ingredients, and the pool ratio check."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from core.models import Business
from inventory.models import FinishedGood, RawMaterial, RecipeItem
from inventory.recipe_ratios import pool_ratio_warnings
from production.models import Order, OrderItem


class BasePoolRatioTests(TestCase):
    """Recipe: per 20 kg flour, 1 kg sugar and 240 g salt."""

    def setUp(self):
        self.business = Business.objects.create(name="Ratio Bakery", slug="ratio-bakery")
        self.flour = RawMaterial.objects.create(business=self.business, name="Flour", usage_unit="kg",
                                                stock=Decimal("500"), output_grams_per_usage_unit=Decimal("1500"))
        self.sugar = RawMaterial.objects.create(business=self.business, name="Sugar", usage_unit="kg", stock=Decimal("500"))
        self.salt = RawMaterial.objects.create(business=self.business, name="Salt", usage_unit="g", stock=Decimal("50000"))
        self.bread = FinishedGood.objects.create(business=self.business, name="Bread", unit="loaf",
                                                 units_per_batch=Decimal("100"), base_material=self.flour,
                                                 output_weight_grams=Decimal("400"))
        for material, qty in ((self.flour, 20), (self.sugar, 1), (self.salt, 240)):
            RecipeItem.objects.create(finished_good=self.bread, raw_material=material, qty_per_batch=Decimal(qty))

    def needs(self, order):
        return {m.name: q for m, q in order.material_requirements().values()}

    def pool_order(self, pool, batches):
        order = Order.objects.create(business=self.business, date="2026-01-01", order_type="physical_store",
                                     status="pending", production_basis="base_material",
                                     base_material=self.flour, base_material_quantity=Decimal(pool))
        OrderItem.objects.create(order=order, finished_good=self.bread, batch_qty=Decimal(batches),
                                 production_basis="base_material", base_material_quantity=Decimal(pool))
        return order

    def test_product_quantity_sizing_keeps_the_recipe_ratio(self):
        order = Order.objects.create(business=self.business, date="2026-01-01", order_type="physical_store")
        OrderItem.objects.create(order=order, finished_good=self.bread, batch_qty=Decimal("5"))
        self.assertEqual(self.needs(order), {"Flour": Decimal("100"), "Sugar": Decimal("5"), "Salt": Decimal("1200")})

    def test_line_level_base_sizing_scales_every_ingredient_from_the_base(self):
        order = Order.objects.create(business=self.business, date="2026-01-01", order_type="physical_store")
        OrderItem.objects.create(order=order, finished_good=self.bread, batch_qty=Decimal("1"),
                                 production_basis="base_material", base_material_quantity=Decimal("100"))
        self.assertEqual(self.needs(order), {"Flour": Decimal("100"), "Sugar": Decimal("5"), "Salt": Decimal("1200")})

    def test_pool_larger_than_the_ordered_batches_breaks_the_ratio_and_is_reported(self):
        order = self.pool_order("100", "4")
        self.assertEqual(self.needs(order), {"Flour": Decimal("100"), "Sugar": Decimal("4"), "Salt": Decimal("960")})
        (warning,) = pool_ratio_warnings(order)
        self.assertEqual((warning["pool"], warning["recipe_need"], warning["direction"]), ("100", "80", "less"))
        released = {o["name"]: (o["released"], o["at_ratio"]) for o in warning["others"]}
        self.assertEqual(released["Sugar"], ("4 kg", "5 kg"))
        self.assertEqual(released["Salt"], ("960 g", "1200 g"))

    def test_no_warning_when_the_pool_matches_the_ordered_batches(self):
        self.assertEqual(pool_ratio_warnings(self.pool_order("100", "5")), [])
        self.assertEqual(pool_ratio_warnings(self.pool_order("101", "5")), [])  # within the 2% tolerance

    def test_no_warning_outside_pool_mode(self):
        order = Order.objects.create(business=self.business, date="2026-01-01", order_type="physical_store")
        OrderItem.objects.create(order=order, finished_good=self.bread, batch_qty=Decimal("5"))
        self.assertEqual(pool_ratio_warnings(order), [])

    def test_order_page_shows_the_ratio_check_only_when_it_matters(self):
        roles = seed_business_roles(self.business)
        admin = CustomUser.objects.create_user(username="ratio-admin", password="pw-123456")
        UserBusiness.objects.create(user=admin, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.client.force_login(admin)
        mismatched = self.pool_order("100", "4")
        page = self.client.get(reverse("order_detail", args=[mismatched.pk]))
        self.assertContains(page, "Ingredient ratio check: Bread")
        self.assertContains(page, "960 g")
        matched = self.pool_order("100", "5")
        self.assertNotContains(self.client.get(reverse("order_detail", args=[matched.pk])), "Ingredient ratio check")
