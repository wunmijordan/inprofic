from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.business_reset import execute_business_reset, restore_business_reset_snapshot
from accounts.models import BusinessResetSnapshot, CustomUser
from core.models import AuditLog, Business
from inventory.models import FinishedGood


class BusinessResetStockFixture:
    def setUp(self):
        self.business = Business.objects.create(name="Reset business", slug="reset-business")
        self.other_business = Business.objects.create(name="Other business", slug="other-reset-business")
        self.actor = CustomUser.objects.create_superuser(username="reset-founder", password="test-password")
        self.product = self.make_product(self.business, "Shelf product", stock="12.50")
        self.customer_product = self.make_product(self.business, "Customer product", stock=None)
        self.other_product = self.make_product(self.other_business, "Other product", stock="12.50")

    def make_product(self, business, name, stock):
        return FinishedGood.raw_objects.create(
            business=business, name=name, stock=stock, total_produced="100.50",
            total_delivered_to_customers="80.25", transferred_market_stock="5.50",
        )

    def reset(self):
        _, snapshot = execute_business_reset(
            business=self.business, selected_groups=[], actor=self.actor, preview_counts={},
        )
        return snapshot

    def assert_counters(self, product, produced, delivered, transferred):
        product.refresh_from_db()
        self.assertEqual(product.total_produced, Decimal(produced))
        self.assertEqual(product.total_delivered_to_customers, Decimal(delivered))
        self.assertEqual(product.transferred_market_stock, Decimal(transferred))


class BusinessResetStockTests(BusinessResetStockFixture, TestCase):
    def test_reset_clears_all_product_counters_without_changing_other_business(self):
        self.reset()
        for product in (self.product, self.customer_product):
            self.assert_counters(product, "0", "0", "0")
        self.assertEqual(self.product.stock, 0)
        self.assertIsNone(self.customer_product.stock)
        self.assert_counters(self.other_product, "100.50", "80.25", "5.50")
        self.assertEqual(self.other_product.stock, Decimal("12.50"))
        self.assertTrue(AuditLog.raw_objects.filter(business=self.business, action="reset").exists())

    def test_restore_recovers_product_counters_and_nullable_stock(self):
        snapshot = self.reset()
        restore_business_reset_snapshot(snapshot=snapshot, actor=self.actor)
        for product in (self.product, self.customer_product):
            self.assert_counters(product, "100.50", "80.25", "5.50")
        self.assertEqual(self.product.stock, Decimal("12.50"))
        self.assertIsNone(self.customer_product.stock)
        self.assertTrue(AuditLog.raw_objects.filter(business=self.business, action="restore").exists())

    def test_legacy_snapshot_without_counters_still_restores(self):
        snapshot = self.reset()
        snapshot.stock_before_reset.pop("finished_good_counters")
        snapshot.save(update_fields=["stock_before_reset"])
        restore_business_reset_snapshot(snapshot=snapshot, actor=self.actor)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal("12.50"))


class BusinessResetSnapshotDeleteTests(BusinessResetStockFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.snapshot = self.reset()
        self.url = reverse("founder_platform_business_reset_snapshot_delete", args=[
            self.business.pk, self.snapshot.pk,
        ])
        self.client.force_login(self.actor)

    def test_confirmation_page_and_invalid_confirmation_preserve_copy(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This cannot be undone.")
        response = self.client.post(self.url, {"confirm_business": "wrong-slug"})
        self.assertContains(response, "Enter the exact business slug")
        self.assertTrue(BusinessResetSnapshot.raw_objects.filter(pk=self.snapshot.pk).exists())

    def test_confirmed_delete_removes_only_copy_and_audits(self):
        self.product.stock = Decimal("7.25")
        self.product.save(update_fields=["stock"])
        response = self.client.post(self.url, {"confirm_business": self.business.slug})
        self.assertRedirects(response, reverse("founder_platform_business_reset", args=[self.business.pk]))
        self.assertFalse(BusinessResetSnapshot.raw_objects.filter(pk=self.snapshot.pk).exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal("7.25"))
        self.assertTrue(AuditLog.raw_objects.filter(
            business=self.business, action="delete", model_name="BusinessResetSnapshot",
            object_id=str(self.snapshot.pk),
        ).exists())

    def test_foreign_business_snapshot_cannot_be_deleted(self):
        url = reverse("founder_platform_business_reset_snapshot_delete", args=[
            self.other_business.pk, self.snapshot.pk,
        ])
        response = self.client.post(url, {"confirm_business": self.other_business.slug})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(BusinessResetSnapshot.raw_objects.filter(pk=self.snapshot.pk).exists())

    def test_deleting_restored_copy_preserves_restored_stock(self):
        restore_business_reset_snapshot(snapshot=self.snapshot, actor=self.actor)
        response = self.client.post(self.url, {"confirm_business": self.business.slug})
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, Decimal("12.50"))
        self.assertEqual(self.product.total_produced, Decimal("100.50"))
        self.assertFalse(BusinessResetSnapshot.raw_objects.filter(pk=self.snapshot.pk).exists())

    def test_non_founder_cannot_delete_copy(self):
        user = CustomUser.objects.create_user(username="not-founder", password="test-password")
        self.client.force_login(user)
        response = self.client.post(self.url, {"confirm_business": self.business.slug})
        self.assertIn(response.status_code, [302, 403])
        self.assertTrue(BusinessResetSnapshot.raw_objects.filter(pk=self.snapshot.pk).exists())

    def test_delete_requires_csrf(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        response = client.post(self.url, {"confirm_business": self.business.slug})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(BusinessResetSnapshot.raw_objects.filter(pk=self.snapshot.pk).exists())

