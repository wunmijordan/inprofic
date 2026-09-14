from decimal import Decimal
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from accounts.models import BusinessModuleAccess, CustomUser, UserBusiness
from accounts.services import seed_business_roles
from core.models import AuditLog, Business, CashAccount
from inventory.models import FinishedGood

from .checkout_services import create_checkout
from .consumers import CommerceNotificationConsumer
from .models import (
    CommerceCheckoutSession,
    CommerceIntake,
    CommerceNotification,
    CommerceNotificationRead,
    CommercePushDelivery,
    CommercePushSubscription,
    CommercePayment,
    CommercePaymentConfiguration,
    CommerceSettings,
    StorefrontProduct,
)
from .notification_services import notify_commerce
from .realtime import business_notification_group, user_notification_group
from .payment_services import record_verified_payment
from .services import create_intake


class CommerceNotificationTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            name="Notice Shop", slug="notice-shop", vertical=Business.VERTICAL_RETAIL
        )
        BusinessModuleAccess.objects.create(business=self.business, module="commerce", enabled=True)
        self.settings = CommerceSettings.raw_objects.create(
            business=self.business, enabled=True, hosted_storefront_enabled=True, api_enabled=True
        )
        roles = seed_business_roles(self.business)
        self.user = CustomUser.objects.create_user(
            username="notice-admin", password="password", fullname="Notice Admin"
        )
        UserBusiness.objects.create(
            user=self.user,
            business=self.business,
            role=roles[CustomUser.ROLE_BUSINESS_ADMIN],
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()
        self.good = FinishedGood.raw_objects.create(
            business=self.business,
            name="Shelf Item",
            unit="pack",
            stock=Decimal("9876"),
            reorder_level=0,
            selling_price=Decimal("275"),
        )
        self.product = StorefrontProduct.raw_objects.create(
            business=self.business,
            finished_good=self.good,
            published=True,
            description="A useful everyday item.",
        )

    def test_checkout_notification_is_persistent_and_idempotent(self):
        request = {
            "business": self.business,
            "source": CommerceIntake.SOURCE_API,
            "order_mode": "physical_store",
            "customer": {"name": "Ada"},
            "items": [{"storefront_product": self.product, "quantity": "2"}],
            "idempotency_key": "notification-checkout",
        }
        with self.captureOnCommitCallbacks(execute=True):
            first, created = create_checkout(**request)
        with self.captureOnCommitCallbacks(execute=True):
            second, created_again = create_checkout(**request)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            CommerceNotification.raw_objects.filter(
                business=self.business, event_type=CommerceNotification.EVENT_CHECKOUT_RECEIVED
            ).count(),
            1,
        )

    def test_staff_pos_checkout_does_not_emit_pending_checkout_notification(self):
        with self.captureOnCommitCallbacks(execute=True):
            checkout, created = create_checkout(
                business=self.business,
                source=CommerceIntake.SOURCE_STAFF_POS,
                order_mode="physical_store",
                customer={"name": "Walk-in Customer"},
                items=[{"storefront_product": self.product, "quantity": "1"}],
                idempotency_key="staff-pos-no-pending-notice",
            )
        self.assertTrue(created)
        self.assertEqual(checkout.source, CommerceIntake.SOURCE_STAFF_POS)
        self.assertFalse(CommerceNotification.raw_objects.filter(
            business=self.business,
            event_type=CommerceNotification.EVENT_CHECKOUT_RECEIVED,
            dedupe_key=f"checkout:{checkout.public_id}:received",
        ).exists())

    def test_notification_preferences_filter_categories(self):
        self.settings.notify_order_activity = False
        self.settings.save(update_fields=["notify_order_activity", "updated_at"])
        self.assertIsNone(notify_commerce(
            business=self.business,
            event_type=CommerceNotification.EVENT_INTAKE_RECEIVED,
            title="Order",
        ))
        payment_notice = notify_commerce(
            business=self.business,
            event_type=CommerceNotification.EVENT_PAYMENT_CLAIM,
            title="Payment claim",
        )
        self.assertIsNotNone(payment_notice)
        self.settings.notifications_enabled = False
        self.settings.save(update_fields=["notifications_enabled", "updated_at"])
        self.assertIsNone(notify_commerce(
            business=self.business,
            event_type=CommerceNotification.EVENT_PAYMENT_CONFIRMED,
            title="Payment",
        ))

    def test_existing_integration_intake_notifies_once(self):
        request = {
            "business": self.business,
            "source": CommerceIntake.SOURCE_CONNECTOR,
            "sales_channel": "online",
            "customer": {"name": "Connector Customer"},
            "items": [{"storefront_product": self.product, "quantity": "1"}],
            "idempotency_key": "connector-order-one",
        }
        with self.captureOnCommitCallbacks(execute=True):
            intake, created = create_intake(**request)
        with self.captureOnCommitCallbacks(execute=True):
            duplicate, created_again = create_intake(**request)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(intake.pk, duplicate.pk)
        self.assertEqual(
            CommerceNotification.raw_objects.filter(
                business=self.business,
                event_type=CommerceNotification.EVENT_INTAKE_RECEIVED,
            ).count(),
            1,
        )

    def test_feed_is_tenant_scoped_and_read_receipts_are_per_user(self):
        own = CommerceNotification.raw_objects.create(
            business=self.business,
            event_type=CommerceNotification.EVENT_INTAKE_RECEIVED,
            title="Own order",
        )
        own_payment = CommerceNotification.raw_objects.create(
            business=self.business,
            event_type=CommerceNotification.EVENT_PAYMENT_CLAIM,
            title="Own payment claim",
        )
        other_business = Business.objects.create(name="Other Shop", slug="other-notice-shop")
        CommerceNotification.raw_objects.create(
            business=other_business,
            event_type=CommerceNotification.EVENT_INTAKE_RECEIVED,
            title="Other order",
        )
        response = self.client.get(reverse("commerce_notification_feed"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unread_count"], 2)
        self.assertEqual(
            {row["title"] for row in response.json()["notifications"]},
            {"Own order", "Own payment claim"},
        )

        response = self.client.post(
            reverse("commerce_notification_read"),
            data=f'{{"notification_ids":["{own.public_id}"]}}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unread_count"], 1)
        self.assertTrue(CommerceNotificationRead.objects.filter(notification=own, user=self.user).exists())

        second_user = CustomUser.objects.create_user(
            username="notice-admin-two", password="password", fullname="Second Admin"
        )
        roles = seed_business_roles(self.business)
        UserBusiness.objects.create(
            user=second_user,
            business=self.business,
            role=roles[CustomUser.ROLE_BUSINESS_ADMIN],
        )
        self.client.force_login(second_user)
        response = self.client.get(reverse("commerce_notification_feed"))
        self.assertEqual(response.json()["unread_count"], 2)
        self.assertFalse(CommerceNotificationRead.objects.filter(notification=own_payment, user=self.user).exists())


    @override_settings(
        WEB_PUSH_VAPID_PUBLIC_KEY="BNmV-test-public-key",
        WEB_PUSH_VAPID_PRIVATE_KEY="test-private-key",
        WEB_PUSH_VAPID_SUBJECT="mailto:founder@example.com",
    )
    def test_web_push_subscription_is_scoped_to_current_user_and_tenant(self):
        payload = {
            "endpoint": "https://push.example.test/device-one",
            "keys": {"p256dh": "p256dh-value", "auth": "auth-value"},
        }
        response = self.client.post(
            reverse("commerce_push_subscribe"),
            data=__import__("json").dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(CommercePushSubscription.objects.filter(
            business=self.business, user=self.user, active=True
        ).exists())
        config = self.client.get(reverse("commerce_push_config"))
        self.assertEqual(config.status_code, 200)
        self.assertTrue(config.json()["configured"])
        self.assertEqual(config.json()["active_devices"], 1)

        response = self.client.post(
            reverse("commerce_push_unsubscribe"),
            data=__import__("json").dumps({"endpoint": payload["endpoint"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CommercePushSubscription.objects.get(
            business=self.business, user=self.user
        ).active)

    @override_settings(
        WEB_PUSH_VAPID_PUBLIC_KEY="BNmV-test-public-key",
        WEB_PUSH_VAPID_PRIVATE_KEY="test-private-key",
        WEB_PUSH_VAPID_SUBJECT="mailto:founder@example.com",
        WEB_PUSH_TIMEOUT_SECONDS=1,
    )
    def test_web_push_outbox_is_durable_and_marks_success(self):
        from .webpush import dispatch_pending_pushes, endpoint_hash
        subscription = CommercePushSubscription.objects.create(
            business=self.business, user=self.user,
            endpoint="https://push.example.test/device-two",
            endpoint_hash=endpoint_hash("https://push.example.test/device-two"),
            p256dh="p256dh-value", auth="auth-value", active=True,
        )
        notice = CommerceNotification.raw_objects.create(
            business=self.business, event_type=CommerceNotification.EVENT_INTAKE_RECEIVED,
            title="Background order", message="A new order arrived.",
        )
        with patch("pywebpush.webpush") as send:
            result = dispatch_pending_pushes()
        self.assertEqual(result["sent"], 1)
        send.assert_called_once()
        notice.refresh_from_db()
        self.assertIsNotNone(notice.push_processed_at)
        delivery = CommercePushDelivery.objects.get(notification=notice, subscription=subscription)
        self.assertEqual(delivery.status, CommercePushDelivery.STATUS_SENT)

    def test_catalogue_hides_stock_count_and_has_search_and_multi_product_basket(self):
        response = self.client.get(reverse("storefront", args=[self.business.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "catalogue-search")
        self.assertContains(response, "basket-dialog")
        self.assertContains(response, "Add to basket")
        self.assertContains(response, "generated-basket-field")
        self.assertContains(response, "storetrack:storefront-basket:")
        self.assertContains(response, "storetrack:storefront-order-history:")
        self.assertContains(response, "Track orders")
        self.assertContains(response, "localStorage.setItem")
        self.assertContains(response, "Find your next favourite.")
        self.assertNotContains(response, "Available now")
        self.assertNotContains(response, "9876")
        self.assertNotContains(response, "available-stock route")

        self.product.image = "commerce/products/business-test/catalogue-image.jpg"
        self.product.save(update_fields=["image", "updated_at"])
        api_response = self.client.get(reverse("commerce_api_products", args=[self.business.slug]))
        product_data = api_response.json()["products"][0]
        self.assertEqual(product_data["image"], product_data["image_url"])
        self.assertTrue(product_data["image_url"].startswith("http://testserver/media/commerce/products/"))

        self.settings.storefront_headline = "Everything you need, beautifully chosen."
        self.settings.save(update_fields=["storefront_headline", "updated_at"])
        response = self.client.get(reverse("storefront", args=[self.business.slug]))
        self.assertContains(response, "Everything you need, beautifully chosen.")

    def test_commerce_settings_exposes_storefront_personalization(self):
        response = self.client.get(reverse("commerce_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'name="storefront_headline"')
        self.assertContains(response, 'name="storefront_hero_image"')
        self.assertContains(response, 'name="storefront_hero_image_position"')
        self.assertNotContains(response, 'name="storefront_hero_image_fit"')
        self.assertNotContains(response, 'name="storefront_hero_image_scale"')

    def test_hosted_basket_waits_for_verified_payment_and_audits_customer(self):
        other_good = FinishedGood.raw_objects.create(
            business=self.business,
            name="Second Shelf Item",
            unit="pack",
            stock=Decimal("50"),
            reorder_level=0,
            selling_price=Decimal("125"),
        )
        other_product = StorefrontProduct.raw_objects.create(
            business=self.business,
            finished_good=other_good,
            published=True,
        )
        account = CashAccount.raw_objects.create(
            business=self.business, name="Store till", account_type="cash", active=True
        )
        CommercePaymentConfiguration.raw_objects.create(
            business=self.business, cash_enabled=True, cash_account=account
        )
        response = self.client.post(reverse("storefront_order", args=[self.business.slug]), {
            "checkout_key": "hosted-basket-one",
            "order_mode": "physical_store",
            "customer_name": "Amina Shopper",
            "phone": "+2348000000000",
            "product_id": [str(self.product.public_id), str(other_product.public_id)],
            "quantity": ["2", "3"],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CommerceIntake.raw_objects.filter(business=self.business).count(), 0)
        checkout = CommerceCheckoutSession.raw_objects.get(business=self.business)
        self.assertEqual(checkout.items.count(), 2)
        self.assertEqual(checkout.customer_phone, "+2348000000000")
        self.assertEqual(checkout.customer_email, "")
        audit_row = AuditLog.raw_objects.get(
            business=self.business, action="commerce_checkout_create", object_id=str(checkout.pk)
        )
        self.assertEqual(audit_row.metadata["customer_name"], "Amina Shopper")
        self.assertIn("Amina Shopper", audit_row.description)

        checkout_page = self.client.get(response.url)
        self.assertContains(checkout_page, "Cash")
        self.assertContains(checkout_page, "Amina Shopper")
        self.assertContains(checkout_page, "storetrack:storefront-order-history:")
        checkout_status = self.client.get(reverse(
            "storefront_checkout_status", args=[self.business.slug, checkout.public_id]
        ))
        self.assertEqual(checkout_status.status_code, 200)
        self.assertEqual(checkout_status.json()["checkout_status"], "awaiting_payment")
        self.assertIsNone(checkout_status.json()["order_id"])
        other_business = Business.objects.create(
            name="Other Catalogue", slug="other-catalogue", vertical=Business.VERTICAL_RETAIL
        )
        BusinessModuleAccess.objects.create(business=other_business, module="commerce", enabled=True)
        CommerceSettings.raw_objects.create(business=other_business, enabled=True)
        cross_tenant = self.client.get(reverse(
            "storefront_checkout", args=[other_business.slug, checkout.public_id]
        ))
        self.assertEqual(cross_tenant.status_code, 404)
        cross_tenant_status = self.client.get(reverse(
            "storefront_checkout_status", args=[other_business.slug, checkout.public_id]
        ))
        self.assertEqual(cross_tenant_status.status_code, 404)
        payment_response = self.client.post(
            reverse("storefront_checkout_payment", args=[self.business.slug, checkout.public_id]),
            {"method": "cash"},
        )
        self.assertEqual(payment_response.status_code, 302)
        payment = CommercePayment.raw_objects.get(checkout=checkout)
        self.assertEqual(CommerceIntake.raw_objects.filter(business=self.business).count(), 0)
        pending_page = self.client.get(payment_response.url)
        self.assertContains(pending_page, "Pay an authorized staff member")
        self.assertContains(pending_page, 'fetch(')
        self.assertNotContains(pending_page, "window.setTimeout(function(){ window.location.reload(); }, 7000)")
        review_page = self.client.get(reverse("commerce_payment_queue"))
        self.assertContains(review_page, "Amina Shopper")
        self.assertContains(review_page, str(checkout.public_id))

        record_verified_payment(
            payment=payment,
            amount=payment.amount,
            actor=self.user,
            idempotency_key="hosted-cash-confirmation",
        )
        checkout.refresh_from_db()
        self.assertEqual(checkout.status, CommerceCheckoutSession.STATUS_MATERIALIZED)
        self.assertEqual(CommerceIntake.raw_objects.filter(business=self.business).count(), 1)
        confirmation = self.client.get(
            reverse("storefront_checkout", args=[self.business.slug, checkout.public_id])
        )
        self.assertContains(confirmation, "Your order is in.")
        tracking = self.client.get(reverse(
            "storefront_order_status",
            args=[self.business.slug, checkout.materialized_intake.public_id],
        ))
        self.assertContains(tracking, "Order tracking")
        self.assertContains(tracking, "storetrack:storefront-order-history:")

    def test_hosted_checkout_requires_phone_but_not_email(self):
        response = self.client.post(reverse("storefront_order", args=[self.business.slug]), {
            "checkout_key": "hosted-without-phone",
            "order_mode": "physical_store",
            "customer_name": "Phone Required",
            "product_id": [str(self.product.public_id)],
            "quantity": ["1"],
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Phone number is required", status_code=400)
        self.assertFalse(
            CommerceCheckoutSession.raw_objects.filter(
                business=self.business, idempotency_key="hosted-without-phone"
            ).exists()
        )


class CommerceNotificationSocketTests(TransactionTestCase):
    def setUp(self):
        self.business = Business.objects.create(
            name="Socket Shop", slug="socket-shop", vertical=Business.VERTICAL_RETAIL
        )
        BusinessModuleAccess.objects.create(
            business=self.business, module="commerce", enabled=True
        )
        roles = seed_business_roles(self.business)
        self.user = CustomUser.objects.create_user(
            username="socket-admin", password="password", fullname="Socket Admin"
        )
        UserBusiness.objects.create(
            user=self.user,
            business=self.business,
            role=roles[CustomUser.ROLE_BUSINESS_ADMIN],
        )
        self.other_business = Business.objects.create(
            name="Other Socket Shop", slug="other-socket-shop"
        )
        self.outsider = CustomUser.objects.create_user(
            username="socket-outsider", password="password", fullname="Outsider"
        )

    def test_access_resolver_enforces_active_membership_and_permission(self):
        self.assertEqual(
            CommerceNotificationConsumer._resolve_access(self.user, self.business.pk),
            (self.business.pk, self.user.pk),
        )
        self.assertIsNone(
            CommerceNotificationConsumer._resolve_access(self.outsider, self.business.pk)
        )

    def test_group_names_keep_business_and_user_signals_isolated(self):
        self.assertNotEqual(
            business_notification_group(self.business.pk),
            business_notification_group(self.other_business.pk),
        )
        self.assertIn(
            business_notification_group(self.business.pk),
            user_notification_group(self.business.pk, self.user.pk),
        )

    def test_changed_event_sends_browser_refresh_signal(self):
        consumer = CommerceNotificationConsumer()
        consumer.send_json = AsyncMock()
        async_to_sync(consumer.notifications_changed)({
            "type": "notifications.changed",
            "reason": "created",
        })
        consumer.send_json.assert_awaited_once_with({
            "type": "notifications.changed",
            "reason": "created",
        })
