"""Starter recipes per vertical."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from core.models import Business
from inventory.models import FinishedGood, RawMaterial, RecipeItem
from inventory.recipe_ratios import product_relations
from inventory.sample_setup import SAMPLES, available_for, load_sample


class SampleSetupTests(TestCase):
    def business(self, vertical):
        return Business.objects.create(name=f"{vertical} co", slug=f"{vertical}-co", vertical=vertical)

    def test_each_production_vertical_gets_a_working_recipe_with_ratios(self):
        for vertical in SAMPLES:
            product = load_sample(self.business(vertical))
            self.assertTrue(product.name.endswith("(Sample)"))
            self.assertEqual(product.stock, Decimal("0"))
            info = product_relations(product)
            self.assertTrue(info["has_base"], vertical)
            self.assertEqual(info["warnings"], [], vertical)
            self.assertTrue(any(r["relation"] and r["relation"] != "Base material" for r in info["rows"]), vertical)

    def test_the_bakery_example_matches_the_one_the_owner_described(self):
        product = load_sample(self.business("bakery"))
        rows = {r["name"]: r for r in product_relations(product)["rows"]}
        self.assertEqual(rows["Sugar (Sample)"]["relation"], "1 kg per 20 kg Flour (Sample)")
        self.assertEqual(rows["Salt (Sample)"]["relation"], "240 g per 20 kg Flour (Sample)")

    def test_it_never_creates_stock_and_is_not_offered_twice(self):
        business = self.business("bakery")
        load_sample(business)
        self.assertIsNone(available_for(business))
        self.assertIsNone(load_sample(business))
        self.assertEqual(FinishedGood.objects.filter(business=business).count(), 1)
        self.assertEqual(sum(m.stock for m in RawMaterial.objects.filter(business=business)), Decimal("0"))

    def test_not_offered_to_traders_or_to_businesses_that_already_have_recipes(self):
        self.assertIsNone(available_for(self.business("retail")))
        self.assertIsNone(available_for(self.business("wholesale")))
        bakery = self.business("bakery")
        flour = RawMaterial.objects.create(business=bakery, name="Flour", usage_unit="kg")
        good = FinishedGood.objects.create(business=bakery, name="Bun", unit="bun")
        RecipeItem.objects.create(finished_good=good, raw_material=flour, qty_per_batch=Decimal("1"))
        self.assertIsNone(available_for(bakery))

    def test_page_and_post_flow(self):
        business = self.business("bakery")
        roles = seed_business_roles(business)
        admin = CustomUser.objects.create_user(username="sample-admin", password="pw-123456")
        UserBusiness.objects.create(user=admin, business=business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse("inventory")), "Try a sample recipe")
        self.assertContains(self.client.get(reverse("inventory_sample_setup")), "Add the sample")
        self.client.post(reverse("inventory_sample_setup"))
        self.assertEqual(FinishedGood.objects.filter(business=business).count(), 1)
        self.assertNotContains(self.client.get(reverse("inventory")), "Try a sample recipe")
