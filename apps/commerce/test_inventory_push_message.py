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
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
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
        self.assertIn("2 products are low", message)

    def test_summary_updates_when_another_item_becomes_low(self):
        self._raw("Raw A")
        _sync_inventory_push_notices()
        self.assertIn("1 raw material is low", self._message())
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

    def test_warning_and_low_counts_are_distinct_for_each_resource(self):
        self._raw("Raw low")
        self._raw("Raw warning", stock="12")
        self._good("Product low")
        self._good("Product warning A", stock="12")
        self._good("Product warning B", stock="14")
        _sync_inventory_push_notices()
        message = self._message()
        self.assertIn("1 raw material is low", message)
        self.assertIn("1 raw material is getting low", message)
        self.assertIn("1 product is low", message)
        self.assertIn("2 products are getting low", message)

    def test_updated_wording_rearms_an_existing_device_notification(self):
        self._raw("Raw A")
        _sync_inventory_push_notices()
        notice = CommerceNotification.raw_objects.get(business=self.business, dedupe_key="inventory-alert-summary")
        notice.message = "1 item needs attention."
        notice.save(update_fields=["message"])
        CommercePushDelivery.objects.create(
            notification=notice, subscription=self.sub,
            status=CommercePushDelivery.STATUS_SENT, sent_at=timezone.now(),
        )
        visible = _sync_inventory_push_notices()
        self.assertTrue(visible[notice.pk]["force"])
        self.assertEqual(self._message(), "1 raw material is low.")
        self.assertFalse(_sync_inventory_push_notices()[notice.pk]["force"])

    def test_stock_changes_schedule_delivery_only_after_commit(self):
        from unittest.mock import patch
        with patch('commerce.webpush.kick_push_dispatcher') as kick:
            with self.captureOnCommitCallbacks(execute=True):
                self._raw('Commit stock')
                kick.assert_not_called()
            kick.assert_called_once()

    def test_rolled_back_stock_does_not_dispatch(self):
        from django.db import transaction
        from unittest.mock import patch
        with patch('commerce.webpush.kick_push_dispatcher') as kick:
            with self.captureOnCommitCallbacks(execute=True):
                try:
                    with transaction.atomic():
                        self._raw('Rolled back')
                        raise ValueError('cancel')
                except ValueError:
                    pass
            kick.assert_not_called()

    def test_new_device_receives_an_already_processed_inventory_summary(self):
        from .webpush import _reconcile_operational_deliveries
        self._raw('Existing low stock')
        _sync_inventory_push_notices()
        notice = CommerceNotification.raw_objects.get(business=self.business, dedupe_key='inventory-alert-summary')
        notice.push_processed_at = timezone.now()
        notice.save()
        CommercePushDelivery.objects.create(notification=notice, subscription=self.sub,
            status=CommercePushDelivery.STATUS_SENT, sent_at=timezone.now())
        second = CommercePushSubscription.objects.create(business=self.business, user=self.user,
            endpoint='https://push.example/second', endpoint_hash='b'*64, p256dh='p', auth='a')
        visible = _sync_inventory_push_notices()
        self.assertEqual(_reconcile_operational_deliveries(visible), 1)
        delivery = CommercePushDelivery.objects.get(notification=notice, subscription=second)
        self.assertEqual(delivery.status, CommercePushDelivery.STATUS_PENDING)

    def test_cleared_stock_cancels_pending_device_alert(self):
        from .webpush import _reconcile_operational_deliveries
        material = self._raw('Recovering stock')
        _reconcile_operational_deliveries(_sync_inventory_push_notices())
        material.stock = 100
        material.save(update_fields=['stock'])
        _reconcile_operational_deliveries(_sync_inventory_push_notices())
        self.assertEqual(CommercePushDelivery.objects.get().status, CommercePushDelivery.STATUS_SUPERSEDED)

    def test_permission_revoked_before_delivery_does_not_send(self):
        from unittest.mock import patch
        from .webpush import _reconcile_operational_deliveries, _send_pending
        self._raw('Private stock')
        _reconcile_operational_deliveries(_sync_inventory_push_notices())
        self.user.is_superuser = False
        self.user.save(update_fields=['is_superuser'])
        with patch('commerce.webpush.configured', return_value=True), patch('pywebpush.webpush') as send:
            _send_pending()
        send.assert_not_called()
        self.assertEqual(CommercePushDelivery.objects.get().status, CommercePushDelivery.STATUS_SUPERSEDED)

    def test_finance_push_is_independent_from_commerce_desktop_settings(self):
        from unittest.mock import patch
        from core.models import CashAccount
        from .models import CommerceSettings
        from .webpush import _sync_finance_push_notices, _reconcile_operational_deliveries, _send_pending, _payload
        CommerceSettings.raw_objects.create(business=self.business, notifications_enabled=False, notification_desktop_enabled=False)
        CashAccount.raw_objects.create(business=self.business, name='Overdrawn till', opening_balance=-10)
        visible = _sync_finance_push_notices()
        _reconcile_operational_deliveries(visible)
        delivery = CommercePushDelivery.objects.get()
        self.assertIn('balancing issues', delivery.notification.message)
        self.assertEqual(__import__('json').loads(_payload(delivery))['channel'], 'finance')
        with patch('commerce.webpush.configured', return_value=True), patch('pywebpush.webpush') as send:
            result = _send_pending()
        self.assertEqual(result['sent'], 1)
        send.assert_called_once()

    def test_delivery_diagnostics_include_specific_inventory_wording_for_own_user(self):
        import json
        from django.test import RequestFactory
        from .notification_views import push_config
        from .webpush import _reconcile_operational_deliveries
        self._raw('Visible stock')
        _reconcile_operational_deliveries(_sync_inventory_push_notices())
        request = RequestFactory().get('/commerce/notifications/push/config/', {'device': self.sub.endpoint_hash})
        request.business, request.user = self.business, self.user
        data = json.loads(push_config(request).content)
        self.assertEqual(data['delivery']['pending'], 1)
        self.assertIn('1 raw material is low', data['delivery']['summaries'][0]['message'])
        self.assertNotIn('endpoint', json.dumps(data))

    def test_one_up_to_date_device_does_not_hide_a_stale_device(self):
        from datetime import timedelta
        from .webpush import _reconcile_operational_deliveries, _requeue_due_repeats
        self._raw('Device stock')
        visible = _sync_inventory_push_notices()
        _reconcile_operational_deliveries(visible)
        first = CommercePushDelivery.objects.get()
        first.status, first.sent_at = CommercePushDelivery.STATUS_SENT, timezone.now()
        first.save()
        second = CommercePushSubscription.objects.create(business=self.business, user=self.user,
            endpoint='https://push.example/stale', endpoint_hash='c'*64, p256dh='p', auth='a')
        older = CommercePushDelivery.objects.create(notification=first.notification, subscription=second,
            status=CommercePushDelivery.STATUS_SENT, sent_at=timezone.now()-timedelta(minutes=3))
        visible = _sync_inventory_push_notices()
        self.assertFalse(visible[first.notification_id]['force'])
        _requeue_due_repeats(visible)
        first.refresh_from_db(); older.refresh_from_db()
        self.assertEqual(first.status, CommercePushDelivery.STATUS_SENT)
        self.assertEqual(older.status, CommercePushDelivery.STATUS_PENDING)
