from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomUser
from core.models import Business
from inventory.models import FinishedGood, RawMaterial

from .models import CommerceNotification, CommercePushDelivery, CommercePushSubscription
from .webpush import _sync_inventory_push_notices


class InventoryPushMessageTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Push Store", slug="push-store", vertical=Business.VERTICAL_RETAIL)
        self.user = CustomUser.objects.create_user(username="push.user", password="safe-password-123", is_superuser=True)
        self.sub = CommercePushSubscription.objects.create(
            business=self.business, user=self.user, endpoint="https://push.example/1", endpoint_hash="a" * 64,
            p256dh="p", auth="a",
        )

    def _raw(self, name, stock="1"):
        return RawMaterial.raw_objects.create(
            business=self.business, name=name, stock=Decimal(stock), reorder_level=Decimal("10"),
            purchase_unit="pack", package_qty=1, package_unit="pack", usage_unit="pack",
        )

    def _good(self, name, stock="1"):
        return FinishedGood.raw_objects.create(
            business=self.business, name=name, unit="piece", stock=Decimal(stock),
            reorder_level=Decimal("10"), selling_price=Decimal("1000"),
        )

    def _message(self):
        return CommerceNotification.raw_objects.get(business=self.business, dedupe_key="inventory-alert-summary").message

    def test_summary_counts_every_raw_material_and_finished_good(self):
        for index in range(3):
            self._raw(f"Raw {index}")
        for index in range(2):
            self._good(f"Good {index}")
        _sync_inventory_push_notices()
        message = self._message()
        self.assertIn("3 raw materials", message)
        self.assertIn("2 finished goods", message)

    def test_summary_updates_when_another_item_becomes_low(self):
        self._raw("Raw A")
        _sync_inventory_push_notices()
        self.assertIn("1 raw material needs attention", self._message())
        self._raw("Raw B")
        _sync_inventory_push_notices()
        self.assertIn("2 raw materials", self._message())

    def test_new_low_item_rearms_the_sent_push_with_the_new_count(self):
        from datetime import timedelta
        from .webpush import _enqueue_notifications, _requeue_due_repeats, _payload
        from unittest import mock
        self._raw("Raw A")
        with mock.patch("commerce.webpush.configured", return_value=True):
            visible = _sync_inventory_push_notices()
            _enqueue_notifications()
        delivery = CommercePushDelivery.objects.get()
        delivery.status = CommercePushDelivery.STATUS_SENT
        delivery.sent_at = timezone.now() - timedelta(minutes=1)
        delivery.save()
        self._raw("Raw B")
        visible = _sync_inventory_push_notices()
        self.assertTrue(any(meta["force"] for meta in visible.values()))
        _requeue_due_repeats(visible)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, CommercePushDelivery.STATUS_PENDING)
        self.assertIn("2 raw materials", __import__("json").loads(_payload(delivery))["body"])
