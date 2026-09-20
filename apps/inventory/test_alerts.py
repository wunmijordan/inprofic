from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomUser
from core.models import Business

from .alert_services import acknowledge_inventory_alerts, alert_settings, inventory_alert_feed
from .models import FinishedGood, InventoryAlertState, RawMaterial


class PersistentInventoryAlertTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            name="Alert Store", slug="alert-store", vertical=Business.VERTICAL_RETAIL
        )
        self.user = CustomUser.objects.create_user(username="alerts.user", password="safe-password-123")
        self.raw = RawMaterial.raw_objects.create(
            business=self.business, name="Packaging", stock=Decimal("12"), reorder_level=Decimal("10"),
            purchase_unit="pack", package_qty=1, package_unit="pack", usage_unit="pack",
        )
        self.good = FinishedGood.raw_objects.create(
            business=self.business, name="Ready Product", unit="piece", stock=Decimal("10"),
            reorder_level=Decimal("10"), selling_price=Decimal("1000"),
        )

    def test_warning_and_low_conditions_are_distinct_and_acknowledgement_is_per_user(self):
        feed = inventory_alert_feed(business=self.business, user=self.user)
        by_type = {row["type"] for row in feed["alerts"]}
        self.assertTrue(feed["sound_enabled"])
        self.assertEqual(feed["sound_repeat_minutes"], 5)
        self.assertEqual(feed["raw_count"], 1)
        self.assertEqual(feed["finished_count"], 1)
        self.assertIn(InventoryAlertState.RAW_WARNING, by_type)
        self.assertIn(InventoryAlertState.FINISHED_LOW, by_type)
        self.assertNotIn(InventoryAlertState.RAW_LOW, by_type)
        acknowledge_inventory_alerts(
            business=self.business, user=self.user,
            alert_ids=[f"{InventoryAlertState.RAW_WARNING}:{self.raw.pk}"],
        )
        refreshed = inventory_alert_feed(business=self.business, user=self.user)
        self.assertNotIn(InventoryAlertState.RAW_WARNING, {row["type"] for row in refreshed["alerts"]})
        self.assertIn(InventoryAlertState.FINISHED_LOW, {row["type"] for row in refreshed["alerts"]})

    def test_zero_repeat_realerts_after_condition_clears_and_reoccurs(self):
        settings = alert_settings(self.business)
        settings.raw_warning_repeat_minutes = 0
        settings.save(update_fields=["raw_warning_repeat_minutes", "updated_at"])
        inventory_alert_feed(business=self.business, user=self.user)
        acknowledge_inventory_alerts(
            business=self.business, user=self.user,
            alert_ids=[f"{InventoryAlertState.RAW_WARNING}:{self.raw.pk}"],
        )
        self.raw.stock = Decimal("20")
        self.raw.save(update_fields=["stock", "updated_at"])
        inventory_alert_feed(business=self.business, user=self.user)
        state = InventoryAlertState.raw_objects.get(
            business=self.business, user=self.user, alert_type=InventoryAlertState.RAW_WARNING,
            object_id=self.raw.pk,
        )
        self.assertFalse(state.is_active)
        self.assertIsNone(state.acknowledged_at)
        self.raw.stock = Decimal("12")
        self.raw.save(update_fields=["stock", "updated_at"])
        feed = inventory_alert_feed(business=self.business, user=self.user)
        self.assertIn(InventoryAlertState.RAW_WARNING, {row["type"] for row in feed["alerts"]})
