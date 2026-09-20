import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import BusinessModuleAccess
from core.models import AuditLog, Business, CashAccount
from inventory.models import BulkPackProfile, FinishedGood, IndividualSaleOption, ProductPortionProfile

from .checkout_services import (
    create_checkout,
    expire_checkout_if_needed,
    attempt_materialize_paid_checkout,
    materialize_paid_checkout,
)
from .models import (
    CommerceCheckoutSession,
    CommerceIntegration,
    CommerceIntake,
    CommercePayment,
    CommercePaymentConfiguration,
    CommerceSettings,
    StorefrontProduct,
)
from .payment_services import (
    eligible_payment_methods,
    initiate_payment,
    record_verified_payment,
)
from .services import ChannelMinimumError


class CheckoutBoundaryTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            name="Checkout Bakery", slug="checkout-bakery", vertical=Business.VERTICAL_BAKERY
        )
        BusinessModuleAccess.objects.update_or_create(
            business=self.business, module="commerce", defaults={"enabled": True}
        )
        self.settings = CommerceSettings.raw_objects.create(
            business=self.business,
            enabled=True,
            api_enabled=True,
            insufficient_stock_policy=CommerceSettings.POLICY_REJECT,
            checkout_reservation_minutes=15,
        )
        self.integration = CommerceIntegration.raw_objects.create(
            business=self.business,
            name="Website",
            integration_type=CommerceIntegration.TYPE_API,
        )
        self.good = FinishedGood.raw_objects.create(
            business=self.business,
            name="Bread",
            unit="loaf",
            units_per_batch=1,
            stock=Decimal("10"),
            # Production verticals require an explicit positive threshold before
            # ordinary finished stock is exposed to the physical-store route.
            reorder_level=1,
            selling_price=Decimal("1000"),
        )
        self.product = StorefrontProduct.raw_objects.create(
            business=self.business,
            finished_good=self.good,
            published=True,
            min_quantity=1,
            preorder_min_quantity=1,
            distribution_min_quantity=10,
            allow_stock_order=True,
            allow_online_order=True,
            allow_distribution_order=True,
        )
        self.cash_account = CashAccount.raw_objects.create(
            business=self.business, name="Till", account_type="cash", active=True
        )
        self.bank_account = CashAccount.raw_objects.create(
            business=self.business, name="Bank", account_type="bank", active=True
        )
        self.payment_config = CommercePaymentConfiguration.raw_objects.create(
            business=self.business,
            currency="NGN",
            cash_enabled=True,
            cash_account=self.cash_account,
            bank_transfer_enabled=True,
            bank_name="Example Bank",
            bank_account_name="Checkout Bakery",
            bank_account_number="0123456789",
            bank_cash_account=self.bank_account,
            paystack_enabled=True,
            paystack_secret_key="sk_test_example",
            # Deliberately no Paystack settlement account yet.
        )

    def make_checkout(self, *, key="checkout-1", qty="2", order_mode="online"):
        return create_checkout(
            business=self.business,
            source=CommerceIntake.SOURCE_API,
            order_mode=order_mode,
            customer={"name": "Ada", "email": "ada@example.com"},
            items=[{"storefront_product": self.product, "quantity": qty}],
            idempotency_key=key,
        )[0]

    def test_external_checkout_rejects_physical_store_channel(self):
        with self.assertRaises(ValidationError) as raised:
            self.make_checkout(key="external-physical-rejected", order_mode="physical_store")
        self.assertIn("Physical-store pricing is reserved for the in-premise POS", str(raised.exception))

    def test_portion_and_bulk_pack_checkout_keep_customer_price_but_reserve_base_units(self):
        ProductPortionProfile.raw_objects.create(
            business=self.business, finished_good=self.good, active=True,
            customer_quantity=1, customer_unit="plate", base_quantity=3,
        )
        checkout = self.make_checkout(key="portion-standard", qty="2", order_mode="online")
        line = checkout.items.get()
        self.assertEqual(line.requested_quantity, Decimal("2.00"))
        self.assertEqual(line.production_quantity, Decimal("6.00"))
        self.assertEqual(line.customer_unit, "plate")
        pack = BulkPackProfile.raw_objects.create(
            business=self.business, finished_good=self.good, name="2 L Bowl",
            customer_quantity=2, customer_unit="litre", base_quantity=12,
            price=Decimal("3500"), min_order_quantity=1,
        )
        bulk_checkout = create_checkout(
            business=self.business, source=CommerceIntake.SOURCE_API, order_mode="distribution",
            customer={"name": "Ada"},
            items=[{"storefront_product": self.product, "quantity": "2", "bulk_pack_id": str(pack.public_id)}],
            idempotency_key="portion-bulk",
        )[0]
        bulk_line = bulk_checkout.items.get()
        self.assertEqual(bulk_line.unit_price, Decimal("3500.00"))
        self.assertEqual(bulk_line.production_quantity, Decimal("24.00"))
        self.assertEqual(bulk_checkout.amount, Decimal("7000.00"))
        with self.assertRaises(ValidationError):
            create_checkout(
                business=self.business, source=CommerceIntake.SOURCE_API, order_mode="online",
                customer={"name": "Ada"},
                items=[{"storefront_product": self.product, "quantity": "1", "bulk_pack_id": str(pack.public_id)}],
                idempotency_key="portion-bulk-online-rejected",
            )

    def test_individual_option_uses_parent_stock_and_external_channel_rules(self):
        option = IndividualSaleOption.raw_objects.create(
            business=self.business, finished_good=self.good, name="Extra Bread",
            customer_unit="slice", base_quantity=Decimal("0.50"),
            physical_store_enabled=True, physical_store_price=Decimal("500"),
            online_enabled=True, online_price=Decimal("650"), online_min_quantity=2,
            distribution_enabled=True, distribution_price=Decimal("550"), distribution_min_quantity=10,
        )
        checkout = create_checkout(
            business=self.business, source=CommerceIntake.SOURCE_API, order_mode="online",
            customer={"name": "Ada"},
            items=[{
                "storefront_product": self.product, "quantity": "2",
                "individual_option_id": str(option.public_id),
            }],
            idempotency_key="individual-online",
        )[0]
        line = checkout.items.get()
        self.assertEqual(line.individual_option_id, option.pk)
        self.assertEqual(line.unit_price, Decimal("650.00"))
        self.assertEqual(line.production_quantity, Decimal("1.00"))
        self.assertEqual(line.customer_unit, "slice")
        self.assertEqual(checkout.amount, Decimal("1300.00"))

        with self.assertRaises(ValidationError):
            create_checkout(
                business=self.business, source=CommerceIntake.SOURCE_API, order_mode="physical_store",
                customer={"name": "Ada"},
                items=[{
                    "storefront_product": self.product, "quantity": "2",
                    "individual_option_id": str(option.public_id),
                }],
                idempotency_key="individual-physical-external-rejected",
            )

        with self.assertRaises(ChannelMinimumError):
            create_checkout(
                business=self.business, source=CommerceIntake.SOURCE_API, order_mode="distribution",
                customer={"name": "Ada"},
                items=[{
                    "storefront_product": self.product, "quantity": "2",
                    "individual_option_id": str(option.public_id),
                }],
                idempotency_key="individual-bulk-min",
            )

    def test_payment_discovery_filters_enabled_but_incomplete_methods(self):
        methods = {row["code"] for row in eligible_payment_methods(self.business)}
        self.assertIn("cash", methods)
        self.assertIn("bank_transfer", methods)
        self.assertNotIn("paystack", methods)
        self.payment_config.paystack_account = self.bank_account
        self.payment_config.save(update_fields=["paystack_account", "updated_at"])
        methods = {row["code"] for row in eligible_payment_methods(self.business)}
        self.assertIn("paystack", methods)

    def test_checkout_idempotency_returns_same_session_without_intake(self):
        first, created = create_checkout(
            business=self.business,
            source=CommerceIntake.SOURCE_API,
            order_mode="online",
            customer={"name": "Ada"},
            items=[{"storefront_product": self.product, "quantity": "2"}],
            idempotency_key="same-checkout",
        )
        second, created_again = create_checkout(
            business=self.business,
            source=CommerceIntake.SOURCE_API,
            order_mode="online",
            customer={"name": "Ada"},
            items=[{"storefront_product": self.product, "quantity": "2"}],
            idempotency_key="same-checkout",
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(CommerceIntake.raw_objects.count(), 0)
        audit_row = AuditLog.raw_objects.get(
            business=self.business, action="commerce_checkout_create", object_id=str(first.pk)
        )
        self.assertEqual(audit_row.metadata["customer_name"], "Ada")
        self.assertEqual(audit_row.metadata["source"], CommerceIntake.SOURCE_API)

    def test_minimum_error_returns_alternative_order_modes(self):
        with self.assertRaises(ChannelMinimumError) as raised:
            self.make_checkout(key="distribution-min", qty="5", order_mode="distribution")
        codes = {row["code"] for row in raised.exception.alternatives}
        self.assertIn("online", codes)
        self.assertNotIn("physical_store", codes)

    def test_full_verified_payment_materializes_exactly_one_intake(self):
        checkout = self.make_checkout(key="paid-checkout")
        payment = initiate_payment(
            checkout=checkout,
            method=CommercePayment.METHOD_CASH,
            idempotency_key="cash-init",
        )
        receipt, created = record_verified_payment(
            payment=payment,
            amount=checkout.amount,
            actor=None,
            idempotency_key="cash-receipt",
        )
        self.assertTrue(created)
        checkout.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(checkout.status, CommerceCheckoutSession.STATUS_MATERIALIZED)
        self.assertIsNotNone(checkout.materialized_intake_id)
        self.assertEqual(payment.intake_id, checkout.materialized_intake_id)
        self.assertIsNone(payment.checkout_id)
        self.assertEqual(CommerceIntake.raw_objects.count(), 1)

        duplicate, created_again = record_verified_payment(
            payment=payment,
            amount=checkout.amount,
            actor=None,
            idempotency_key="cash-receipt",
        )
        self.assertFalse(created_again)
        self.assertEqual(duplicate.pk, receipt.pk)
        self.assertEqual(CommerceIntake.raw_objects.count(), 1)

    def test_unpaid_checkout_cannot_materialize_an_intake(self):
        checkout = self.make_checkout(key="unpaid-must-stay-outside-intake")
        with self.assertRaisesMessage(
            ValidationError,
            "Checkout cannot create an order until full payment is verified.",
        ):
            materialize_paid_checkout(checkout)
        checkout.refresh_from_db()
        self.assertEqual(checkout.status, CommerceCheckoutSession.STATUS_AWAITING_PAYMENT)
        self.assertIsNone(checkout.materialized_intake_id)
        self.assertEqual(CommerceIntake.raw_objects.count(), 0)

    def test_late_verified_payment_is_retained_for_review_and_recoverable(self):
        checkout = self.make_checkout(key="late-checkout")
        CommerceCheckoutSession.raw_objects.filter(pk=checkout.pk).update(
            reservation_expires_at=timezone.now() - timedelta(minutes=1)
        )
        checkout.refresh_from_db()
        # Simulate a gateway/manual receipt arriving after the hold window.
        # Payment may have been initialized before expiry; the authoritative
        # settlement arrives late.
        payment = CommercePayment.raw_objects.create(
            business=self.business,
            checkout=checkout,
            method=CommercePayment.METHOD_CASH,
            amount=checkout.amount,
            currency="NGN",
            reference="STP-LATE-TEST",
            idempotency_key="late-cash-init",
            status=CommercePayment.STATUS_PENDING,
        )
        record_verified_payment(
            payment=payment,
            amount=checkout.amount,
            actor=None,
            idempotency_key="late-cash-receipt",
        )
        checkout.refresh_from_db()
        self.assertEqual(checkout.status, CommerceCheckoutSession.STATUS_PAID_REVIEW)
        self.assertIsNone(checkout.materialized_intake_id)
        self.assertEqual(CommerceIntake.raw_objects.count(), 0)

        intake, created = attempt_materialize_paid_checkout(
            checkout, actor=None, allow_expired_recovery=True
        )
        self.assertTrue(created)
        self.assertIsNotNone(intake)
        self.assertEqual(CommerceIntake.raw_objects.count(), 1)

    def test_expired_unpaid_reservation_releases_stock(self):
        checkout = self.make_checkout(key="reserve-most", qty="8")
        CommerceCheckoutSession.raw_objects.filter(pk=checkout.pk).update(
            reservation_expires_at=timezone.now() - timedelta(seconds=1)
        )
        checkout.refresh_from_db()
        expire_checkout_if_needed(checkout)
        second = self.make_checkout(key="after-expiry", qty="5")
        self.assertEqual(second.items.get().reserved_stock_quantity, Decimal("5.00"))

    def test_checkout_status_is_tenant_isolated(self):
        checkout = self.make_checkout(key="tenant-check")
        other = Business.objects.create(name="Other", slug="other-checkout", vertical=Business.VERTICAL_RETAIL)
        BusinessModuleAccess.objects.update_or_create(
            business=other, module="commerce", defaults={"enabled": True}
        )
        CommerceSettings.raw_objects.create(business=other, enabled=True, api_enabled=True)
        other_integration = CommerceIntegration.raw_objects.create(
            business=other, name="Other API", integration_type=CommerceIntegration.TYPE_API
        )
        response = self.client.get(
            f"/api/v1/storefronts/{self.business.slug}/checkouts/{checkout.public_id}",
            HTTP_X_INPROFIC_KEY=other_integration.api_key,
        )
        self.assertEqual(response.status_code, 403)

    def test_checkout_api_requires_phone_and_keeps_email_optional(self):
        endpoint = reverse("commerce_api_checkouts", args=[self.business.slug])
        payload = {
            "order_mode": "online",
            "customer": {"name": "API Customer"},
            "items": [{"product_id": str(self.product.public_id), "quantity": "2"}],
        }
        response = self.client.post(
            endpoint,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_INPROFIC_KEY=self.integration.api_key,
            HTTP_IDEMPOTENCY_KEY="api-missing-phone",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("phone number is required", response.json()["detail"].lower())

        payload["customer"]["phone"] = "+2348000000000"
        response = self.client.post(
            endpoint,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_INPROFIC_KEY=self.integration.api_key,
            HTTP_IDEMPOTENCY_KEY="api-phone-no-email",
        )
        self.assertEqual(response.status_code, 201)
        checkout = CommerceCheckoutSession.raw_objects.get(
            business=self.business, public_id=response.json()["checkout_id"]
        )
        self.assertEqual(checkout.customer_phone, "+2348000000000")
        self.assertEqual(checkout.customer_email, "")
