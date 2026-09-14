import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from accounts.models import BusinessModuleAccess, CustomUser, UserBusiness
from accounts.services import seed_business_roles
from core.models import Business, CashAccount, FinancialTransaction
from inventory.models import FinishedGood, FinishedGoodChannelPrice
from production.models import Order

from .models import (
    CommerceCheckoutSession,
    CommerceGatewayEvent,
    CommerceIntegration,
    CommerceIntake,
    CommerceNotification,
    CommercePayment,
    CommercePaymentClaim,
    CommercePaymentConfiguration,
    CommercePaymentReceipt,
    CommerceSettings,
    StorefrontProduct,
)
from .payment_gateways import GatewayError, monnify_signature_valid, verify_monnify, verify_paystack
from .checkout_services import create_checkout
from .payment_services import initiate_payment, record_verified_payment, reverse_payment_receipt
from .services import create_intake


class CommercePaymentTestBase(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            name="Sample Store", slug="sample-store", vertical=Business.VERTICAL_RETAIL
        )
        BusinessModuleAccess.objects.update_or_create(
            business=self.business, module="commerce", defaults={"enabled": True}
        )
        CommerceSettings.raw_objects.create(business=self.business, enabled=True, api_enabled=True)
        self.integration = CommerceIntegration.raw_objects.create(
            business=self.business, name="Storefront", integration_type=CommerceIntegration.TYPE_API
        )
        self.settlement_account = CashAccount.raw_objects.create(
            business=self.business, name="Commerce Settlement", account_type="bank", active=True
        )
        self.cash_account = CashAccount.raw_objects.create(
            business=self.business, name="Storefront Till", account_type="cash", active=True
        )
        self.config = CommercePaymentConfiguration.raw_objects.create(
            business=self.business,
            currency="NGN",
            paystack_enabled=True,
            paystack_secret_key="paystack-test-secret",
            monnify_enabled=True,
            monnify_api_key="monnify-api",
            monnify_secret_key="monnify-test-secret",
            monnify_contract_code="contract-1",
            paystack_account=self.settlement_account,
            monnify_account=self.settlement_account,
            bank_transfer_enabled=True,
            bank_transfer_provider=CommercePaymentConfiguration.BANK_TRANSFER_PROVIDER_PAYSTACK,
            bank_cash_account=self.settlement_account,
            bank_name="Example Bank",
            bank_account_name="Sample Store",
            bank_account_number="0000000000",
            cash_enabled=True,
            cash_account=self.cash_account,
        )
        self.good = FinishedGood.raw_objects.create(
            business=self.business,
            name="Stock Product",
            unit="unit",
            units_per_batch=1,
            stock=20,
            reorder_level=2,
            selling_price=Decimal("2500.00"),
        )
        self.product = StorefrontProduct.raw_objects.create(
            business=self.business,
            finished_good=self.good,
            published=True,
            allow_stock_order=True,
            allow_preorder=False,
        )
        self.intake, _ = create_intake(
            business=self.business,
            source=CommerceIntake.SOURCE_API,
            ordering_mode=CommerceIntake.MODE_STOCK,
            customer={"name": "Sample Customer", "email": "customer@example.test"},
            items=[{"storefront_product": self.product, "quantity": "2"}],
            idempotency_key="order-key",
        )

    def api_post(self, path, payload, *, idem=None):
        headers = {"HTTP_X_INPROFIC_KEY": self.integration.api_key}
        if idem is not None:
            headers["HTTP_IDEMPOTENCY_KEY"] = idem
        return self.client.post(path, json.dumps(payload), content_type="application/json", **headers)

    def make_payment(self, method=CommercePayment.METHOD_CASH, **overrides):
        values = {
            "business": self.business,
            "intake": self.intake,
            "method": method,
            "status": CommercePayment.STATUS_PENDING,
            "amount": self.intake.total,
            "currency": "NGN",
            "reference": f"STP-{method.upper()}-{CommercePayment.raw_objects.count() + 1}",
            "gateway_reference": "",
            "idempotency_key": f"payment-{CommercePayment.raw_objects.count() + 1}",
        }
        values.update(overrides)
        return CommercePayment.raw_objects.create(**values)


class PosCashNotificationTests(CommercePaymentTestBase):
    def test_staff_pos_cash_skips_transient_payment_started_notification(self):
        with self.captureOnCommitCallbacks(execute=True):
            checkout, _ = create_checkout(
                business=self.business,
                source=CommerceIntake.SOURCE_STAFF_POS,
                order_mode=CommerceIntake.CHANNEL_PHYSICAL_STORE,
                customer={"name": "Walk-in Customer"},
                items=[{"storefront_product": self.product, "quantity": "1"}],
                idempotency_key="pos-cash-notification-test",
            )
        with self.captureOnCommitCallbacks(execute=True):
            payment = initiate_payment(
                checkout=checkout,
                method=CommercePayment.METHOD_CASH,
                idempotency_key="pos-cash-notification-payment",
                surface="pos",
            )
        self.assertFalse(CommerceNotification.raw_objects.filter(
            business=self.business,
            event_type=CommerceNotification.EVENT_PAYMENT_STARTED,
            dedupe_key=f"payment:{payment.public_id}:started",
        ).exists())


class HeadlessPaymentApiTests(CommercePaymentTestBase):
    def test_public_payment_methods_never_expose_cash(self):
        path = f"/api/v1/storefronts/{self.business.slug}/payment-methods"
        response = self.client.get(path, HTTP_X_INPROFIC_KEY=self.integration.api_key)
        self.assertEqual(response.status_code, 200)
        methods = {row["code"] for row in response.json()["methods"]}
        self.assertNotIn(CommercePayment.METHOD_CASH, methods)
        self.assertNotIn(CommercePayment.METHOD_POS_CARD, methods)
        self.assertIn(CommercePayment.METHOD_BANK_TRANSFER, methods)

    def test_direct_order_creation_endpoint_requires_checkout_first(self):
        path = f"/api/v1/storefronts/{self.business.slug}/orders"
        response = self.api_post(path, {"customer": {"name": "Sample Customer"}}, idem="existing-order")
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["code"], "checkout_first_required")

    def test_checkout_validates_channel_minimum_without_creating_intake(self):
        self.product.distribution_min_quantity = Decimal("10")
        self.product.allow_distribution_order = True
        self.product.save(update_fields=["distribution_min_quantity", "allow_distribution_order"])
        path = f"/api/v1/storefronts/{self.business.slug}/checkouts"
        response = self.api_post(
            path,
            {
                "order_mode": "distribution",
                "customer": {"name": "Sample Customer", "phone": "08000000000"},
                "items": [{"product_id": str(self.product.public_id), "quantity": "2"}],
            },
            idem="below-trade-minimum",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "minimum_not_met")
        self.assertEqual(CommerceCheckoutSession.raw_objects.count(), 0)

    def test_checkout_uses_channel_price_and_stays_pre_intake(self):
        FinishedGoodChannelPrice.objects.create(
            finished_good=self.good, channel="online", price=Decimal("2250.00")
        )
        self.product.allow_online_order = True
        self.product.save(update_fields=["allow_online_order"])
        path = f"/api/v1/storefronts/{self.business.slug}/checkouts"
        response = self.api_post(
            path,
            {
                "order_mode": "online",
                "customer": {"name": "Sample Customer", "phone": "08000000000"},
                "items": [{"product_id": str(self.product.public_id), "quantity": "2"}],
            },
            idem="retail-online-channel",
        )
        self.assertEqual(response.status_code, 201)
        checkout = CommerceCheckoutSession.raw_objects.get(public_id=response.json()["checkout_id"])
        self.assertEqual(checkout.amount, Decimal("4500.00"))
        self.assertEqual(CommerceIntake.raw_objects.filter(business=self.business).count(), 1)  # setup existing intake only

    def test_payment_initialization_is_tenant_scoped(self):
        other = Business.objects.create(name="Other Store", slug="other-store", vertical=Business.VERTICAL_RETAIL)
        BusinessModuleAccess.objects.update_or_create(business=other, module="commerce", defaults={"enabled": True})
        CommerceSettings.raw_objects.create(business=other, enabled=True, api_enabled=True)
        other_integration = CommerceIntegration.raw_objects.create(
            business=other, name="Other API", integration_type=CommerceIntegration.TYPE_API
        )
        path = f"/api/v1/storefronts/{other.slug}/orders/{self.intake.public_id}/payments/initiate"
        response = self.client.post(
            path,
            json.dumps({"method": "cash"}),
            content_type="application/json",
            HTTP_X_INPROFIC_KEY=other_integration.api_key,
            HTTP_IDEMPOTENCY_KEY="cross-tenant",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(CommercePayment.raw_objects.count(), 0)

    def test_current_payment_and_order_detail_remain_backward_compatible(self):
        detail_url = f"/api/v1/storefronts/{self.business.slug}/orders/{self.intake.public_id}"
        empty = self.client.get(detail_url, HTTP_X_INPROFIC_KEY=self.integration.api_key)
        self.assertEqual(empty.status_code, 200)
        self.assertIn("payment_state", empty.json())
        self.assertIsNone(empty.json()["payment"])

        payment = self.make_payment()
        current_url = f"{detail_url}/payments/current"
        current = self.client.get(current_url, HTTP_X_INPROFIC_KEY=self.integration.api_key)
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["payment_id"], str(payment.public_id))
        self.assertEqual(current.json()["balance"], "5000.00")
        enriched = self.client.get(detail_url, HTTP_X_INPROFIC_KEY=self.integration.api_key)
        self.assertEqual(enriched.json()["payment"]["method"], "cash")

    def test_bank_claim_is_evidence_only_and_deduplicated(self):
        payment = self.make_payment(method=CommercePayment.METHOD_BANK_TRANSFER)
        url = f"/api/v1/storefronts/{self.business.slug}/orders/{self.intake.public_id}/payments/current/claim"
        payload = {"payer_name": "Sample Customer", "transfer_reference": "BANK-SESSION-101"}
        first = self.api_post(url, payload)
        second = self.api_post(url, payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        payment.refresh_from_db()
        self.intake.refresh_from_db()
        self.assertEqual(payment.status, CommercePayment.STATUS_AWAITING_VERIFICATION)
        self.assertEqual(self.intake.payment_state, CommerceIntake.PAYMENT_PENDING)
        self.assertEqual(CommercePaymentClaim.raw_objects.count(), 1)
        self.assertEqual(CommercePaymentReceipt.raw_objects.count(), 0)

    @patch("commerce.payment_services.initialize_gateway")
    def test_browser_return_url_never_marks_gateway_payment_paid(self, initialize):
        initialize.return_value = {
            "authorization_url": "https://gateway.example.test/checkout/1",
            "gateway_reference": "provider-1",
            "metadata": {},
        }
        path = f"/api/v1/storefronts/{self.business.slug}/orders/{self.intake.public_id}/payments/initiate"
        response = self.api_post(
            path,
            {"method": "paystack", "return_url": "https://storefront.example.test/payment/return/"},
            idem="gateway-selection",
        )
        self.assertEqual(response.status_code, 200)
        payment = CommercePayment.raw_objects.get()
        self.assertEqual(payment.status, CommercePayment.STATUS_AWAITING_CUSTOMER)
        self.assertEqual(payment.amount_paid, Decimal("0"))
        self.assertEqual(FinancialTransaction.raw_objects.count(), 0)


class ManualVerificationTests(CommercePaymentTestBase):
    def _staff(self, role_key, username):
        roles = seed_business_roles(self.business)
        user = CustomUser.objects.create_user(username=username, password="test-password", fullname=username.title())
        UserBusiness.objects.create(user=user, business=self.business, role=roles[role_key])
        return user

    def test_only_authorized_staff_can_confirm_cash(self):
        payment = self.make_payment()
        user = self._staff(CustomUser.ROLE_STOCK_KEEPER, "stock-user")
        self.client.force_login(user)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()
        response = self.client.post(
            f"/finance/commerce-payments/{payment.public_id}/confirm/",
            {"amount": "5000", "confirmation_token": "unauthorized-attempt"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(CommercePaymentReceipt.raw_objects.count(), 0)

    def test_partial_manual_payment_records_actor_time_once_and_preserves_state_boundaries(self):
        payment = self.make_payment()
        user = self._staff(CustomUser.ROLE_ACCOUNTANT, "finance-user")
        self.client.force_login(user)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()
        url = f"/finance/commerce-payments/{payment.public_id}/confirm/"
        data = {
            "amount": "1250.00",
            "location": "Main counter",
            "note": "Counted with cashier",
            "confirmation_token": "cash-confirmation-1",
        }
        first = self.client.post(url, data)
        second = self.client.post(url, data)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        payment.refresh_from_db()
        self.intake.refresh_from_db()
        receipt = CommercePaymentReceipt.raw_objects.get()
        self.assertEqual(receipt.verified_by, user)
        self.assertIsNotNone(receipt.verified_at)
        self.assertEqual(receipt.location, "Main counter")
        self.assertEqual(payment.status, CommercePayment.STATUS_PARTIALLY_PAID)
        self.assertEqual(payment.amount_paid, Decimal("1250.00"))
        self.assertEqual(payment.balance, Decimal("3750.00"))
        self.assertEqual(self.intake.status, CommerceIntake.STATUS_PENDING)
        self.assertEqual(self.intake.fulfilment_state, CommerceIntake.FULFIL_PENDING)
        self.assertEqual(self.intake.payment_state, CommerceIntake.PAYMENT_PENDING)
        self.assertEqual(FinancialTransaction.raw_objects.count(), 1)

    def test_bank_verification_requires_claim_and_reuses_receivable_semantics(self):
        payment = self.make_payment(method=CommercePayment.METHOD_BANK_TRANSFER)
        with self.assertRaisesMessage(Exception, "claim is required"):
            record_verified_payment(
                payment=payment,
                amount="1000",
                actor=None,
                idempotency_key="missing-claim",
            )
        claim = CommercePaymentClaim.raw_objects.create(
            business=self.business,
            payment=payment,
            payer_name="Sample Customer",
            transfer_reference="TRANSFER-200",
        )
        receipt, created = record_verified_payment(
            payment=payment,
            amount="1000",
            actor=None,
            idempotency_key="bank-confirm-1",
            claim=claim,
        )
        duplicate, created_again = record_verified_payment(
            payment=payment,
            amount="1000",
            actor=None,
            idempotency_key="bank-confirm-1",
            claim=claim,
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(receipt.pk, duplicate.pk)
        payment.refresh_from_db()
        claim.refresh_from_db()
        self.assertEqual(payment.status, CommercePayment.STATUS_PARTIALLY_PAID)
        self.assertEqual(claim.status, CommercePaymentClaim.STATUS_ACCEPTED)
        self.assertEqual(FinancialTransaction.raw_objects.count(), 1)

    def test_verified_intake_payment_auto_accepts_stock_and_allocates_without_second_cash_entry(self):
        payment = self.make_payment()
        record_verified_payment(
            payment=payment,
            amount="5000",
            actor=None,
            idempotency_key="paid-auto-acceptance",
        )
        self.intake.refresh_from_db()
        self.good.refresh_from_db()
        sale = self.intake.accepted_sale
        self.assertIsNotNone(sale)
        self.assertEqual(self.intake.status, CommerceIntake.STATUS_ACCEPTED)
        self.assertEqual(self.intake.fulfilment_state, CommerceIntake.FULFIL_COMPLETE)
        self.assertEqual(self.good.stock, Decimal("18"))
        self.assertEqual(sale.payments.count(), 1)
        self.assertEqual(sale.payments.get().amount, Decimal("5000.00"))
        self.assertEqual(sale.transaction_type, "paid")
        self.assertEqual(FinancialTransaction.raw_objects.count(), 1)

    def test_staff_pos_full_payment_materializes_and_consumes_reservation_automatically(self):
        checkout, _ = create_checkout(
            business=self.business,
            source=CommerceIntake.SOURCE_STAFF_POS,
            order_mode=CommerceIntake.CHANNEL_PHYSICAL_STORE,
            customer={"name": "Counter Customer"},
            items=[{"storefront_product": self.product, "quantity": "1"}],
            idempotency_key="pos-auto-fulfil-checkout",
        )
        payment = initiate_payment(
            checkout=checkout, method=CommercePayment.METHOD_CASH,
            idempotency_key="pos-auto-fulfil-payment", surface="pos",
        )
        record_verified_payment(
            payment=payment, amount=payment.amount, actor=None,
            idempotency_key="pos-auto-fulfil-receipt",
        )
        checkout.refresh_from_db()
        self.good.refresh_from_db()
        intake = checkout.materialized_intake
        intake.refresh_from_db()
        self.assertEqual(checkout.status, CommerceCheckoutSession.STATUS_MATERIALIZED)
        self.assertIsNotNone(checkout.reservation_released_at)
        self.assertEqual(intake.status, CommerceIntake.STATUS_ACCEPTED)
        self.assertIsNotNone(intake.accepted_sale_id)
        self.assertEqual(self.good.stock, Decimal("19"))

    def test_paid_made_to_order_intake_creates_pending_production_order(self):
        business = Business.objects.create(
            name="Made To Order Works", slug="made-to-order-works", vertical=Business.VERTICAL_GENERAL
        )
        BusinessModuleAccess.objects.update_or_create(
            business=business, module="commerce", defaults={"enabled": True}
        )
        CommerceSettings.raw_objects.create(business=business, enabled=True, api_enabled=True)
        account = CashAccount.raw_objects.create(
            business=business, name="Order Payments", account_type="cash", active=True
        )
        CommercePaymentConfiguration.raw_objects.create(
            business=business, currency="NGN", cash_enabled=True, cash_account=account
        )
        good = FinishedGood.raw_objects.create(
            business=business, name="Custom Product", unit="unit", units_per_batch=1,
            stock=0, reorder_level=0, selling_price=Decimal("3000.00"),
        )
        product = StorefrontProduct.raw_objects.create(
            business=business, finished_good=good, published=True,
            allow_stock_order=True, allow_online_order=True, allow_preorder=True,
        )
        intake, _ = create_intake(
            business=business, source=CommerceIntake.SOURCE_API, sales_channel="online",
            customer={"name": "Made To Order Customer"},
            items=[{"storefront_product": product, "quantity": "2"}],
            idempotency_key="made-to-order-paid-intake",
        )
        payment = CommercePayment.raw_objects.create(
            business=business, intake=intake, method=CommercePayment.METHOD_CASH,
            status=CommercePayment.STATUS_PENDING, amount=intake.total, currency="NGN",
            reference="STP-MADE-TO-ORDER-1", idempotency_key="made-to-order-payment",
        )
        record_verified_payment(
            payment=payment, amount=intake.total, actor=None,
            idempotency_key="made-to-order-receipt",
        )
        intake.refresh_from_db()
        good.refresh_from_db()
        order = intake.accepted_order
        self.assertIsNotNone(order)
        self.assertEqual(order.status, "pending")
        self.assertEqual(order.customer_payment_status, "paid")
        self.assertEqual(intake.status, CommerceIntake.STATUS_ACCEPTED)
        self.assertEqual(intake.fulfilment_state, CommerceIntake.FULFIL_PENDING)
        self.assertEqual(good.stock, Decimal("0"))
        self.assertEqual(Order.raw_objects.filter(business=business).count(), 1)

    def test_receipt_reversal_is_compensating_and_idempotent(self):
        payment = self.make_payment()
        receipt, _ = record_verified_payment(
            payment=payment,
            amount="5000",
            actor=None,
            idempotency_key="cash-to-reverse",
        )
        reversed_receipt, created = reverse_payment_receipt(
            receipt=receipt, actor=None, reason="Till recount correction"
        )
        duplicate, created_again = reverse_payment_receipt(
            receipt=receipt, actor=None, reason="Till recount correction"
        )
        payment.refresh_from_db()
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(reversed_receipt.pk, duplicate.pk)
        self.assertEqual(payment.amount_paid, Decimal("0"))
        self.assertEqual(CommercePaymentReceipt.raw_objects.count(), 1)
        self.assertEqual(FinancialTransaction.raw_objects.count(), 2)
        self.assertTrue(FinancialTransaction.raw_objects.get(pk=receipt.financial_transaction_id).reversed)


class StorefrontPosGuardTests(CommercePaymentTestBase):
    def setUp(self):
        super().setUp()
        roles = seed_business_roles(self.business)
        self.staff = CustomUser.objects.create_user(
            username="pos.staff", password="safe-password-123", fullname="POS Staff"
        )
        membership = UserBusiness.objects.create(
            user=self.staff, business=self.business, role=roles[CustomUser.ROLE_POS_OPERATOR],
            active=True,
        )
        self.client.force_login(self.staff)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()

    def test_cash_must_be_confirmed_before_checkout_reserves_stock(self):
        response = self.client.post("/commerce/storefront-pos/", {
            "method": CommercePayment.METHOD_CASH,
            f"qty_{self.product.public_id}": "1",
            "customer_name": "Walk-in Customer",
            "pos_key": "cash-not-received",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm that the cash has physically been received")
        self.assertFalse(CommerceCheckoutSession.raw_objects.filter(
            business=self.business, source=CommerceIntake.SOURCE_STAFF_POS
        ).exists())

    @patch("commerce.views.initiate_payment")
    def test_terminal_initialization_failure_releases_new_pos_reservation(self, initiate_payment):
        self.config.paystack_terminal_enabled = True
        self.config.paystack_terminal_id = "TERM_test"
        self.config.paystack_terminal_customer_email = "walkin@example.test"
        self.config.paystack_terminal_account = self.settlement_account
        self.config.save(update_fields=[
            "paystack_terminal_enabled", "paystack_terminal_id",
            "paystack_terminal_customer_email", "paystack_terminal_account",
        ])
        initiate_payment.side_effect = GatewayError("Terminal unavailable")

        response = self.client.post("/commerce/storefront-pos/", {
            "method": CommercePayment.METHOD_POS_CARD,
            f"qty_{self.product.public_id}": "1",
            "customer_name": "Walk-in Customer",
            "pos_key": "terminal-failure",
        })

        self.assertEqual(response.status_code, 200)
        checkout = CommerceCheckoutSession.raw_objects.get(
            business=self.business, source=CommerceIntake.SOURCE_STAFF_POS
        )
        self.assertEqual(checkout.status, CommerceCheckoutSession.STATUS_CANCELLED)
        self.assertIsNotNone(checkout.reservation_released_at)



class GatewayWebhookTests(CommercePaymentTestBase):
    def _gateway_payment(self, method, reference, gateway_reference):
        return self.make_payment(
            method=method,
            reference=reference,
            gateway_reference=gateway_reference,
            status=CommercePayment.STATUS_AWAITING_CUSTOMER,
        )

    @patch("commerce.payment_services.verify_gateway")
    def test_paystack_signature_and_replay_safety(self, verify_gateway):
        payment = self._gateway_payment("paystack", "STP-PAYSTACK-WEBHOOK", "gateway-paystack-1")
        verify_gateway.return_value = (True, {"reference": payment.reference, "amount": "5000.00", "currency": "NGN"})
        payload = {"event": "charge.success", "data": {"reference": payment.reference, "id": 44}}
        raw = json.dumps(payload).encode()
        signature = hmac.new(self.config.paystack_secret_key.encode(), raw, hashlib.sha512).hexdigest()
        url = f"/api/v1/storefronts/{self.business.slug}/payments/paystack/webhook"
        invalid = self.client.post(url, raw, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE="bad")
        first = self.client.post(url, raw, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=signature)
        replay = self.client.post(url, raw, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=signature)
        self.assertEqual(invalid.status_code, 403)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, CommercePayment.STATUS_PAID)
        self.assertEqual(CommercePaymentReceipt.raw_objects.count(), 1)
        self.assertEqual(FinancialTransaction.raw_objects.count(), 1)
        self.assertEqual(CommerceGatewayEvent.raw_objects.count(), 2)

    @patch("commerce.payment_services.verify_gateway")
    def test_signed_pending_paystack_event_is_acknowledged_without_settlement(self, verify_gateway):
        payment = self._gateway_payment("paystack", "STP-PAYSTACK-PENDING", "gateway-pending-1")
        payload = {"event": "paymentrequest.pending", "data": {"reference": payment.reference}}
        raw = json.dumps(payload).encode()
        signature = hmac.new(self.config.paystack_secret_key.encode(), raw, hashlib.sha512).hexdigest()
        url = f"/api/v1/storefronts/{self.business.slug}/payments/paystack/webhook"

        response = self.client.post(url, raw, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=signature)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["settlement_attempted"])
        verify_gateway.assert_not_called()
        payment.refresh_from_db()
        self.assertEqual(payment.status, CommercePayment.STATUS_AWAITING_CUSTOMER)
        self.assertEqual(CommercePaymentReceipt.raw_objects.count(), 0)

    @patch("commerce.payment_services.verify_gateway")
    def test_signed_unmatched_gateway_event_is_acknowledged_without_settlement(self, verify_gateway):
        payload = {"event": "charge.success", "data": {"reference": "pos-provider-reference-not-known-to-commerce"}}
        raw = json.dumps(payload).encode()
        signature = hmac.new(self.config.paystack_secret_key.encode(), raw, hashlib.sha512).hexdigest()
        url = f"/api/v1/storefronts/{self.business.slug}/payments/paystack/webhook"

        response = self.client.post(url, raw, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=signature)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ignored"])
        verify_gateway.assert_not_called()
        self.assertEqual(CommercePaymentReceipt.raw_objects.count(), 0)

    @patch("commerce.payment_services.verify_gateway")
    def test_monnify_signature_and_replay_safety(self, verify_gateway):
        payment = self._gateway_payment("monnify", "STP-MONNIFY-WEBHOOK", "gateway-monnify-1")
        verify_gateway.return_value = (True, {"reference": payment.reference, "amount": "5000.00", "currency": "NGN"})
        payload = {"eventType": "SUCCESSFUL_TRANSACTION", "eventData": {"paymentReference": payment.reference}}
        raw = json.dumps(payload).encode()
        signature = hmac.new(self.config.monnify_secret_key.encode(), raw, hashlib.sha512).hexdigest()
        url = f"/api/v1/storefronts/{self.business.slug}/payments/monnify/webhook"
        first = self.client.post(url, raw, content_type="application/json", HTTP_MONNIFY_SIGNATURE=signature)
        replay = self.client.post(url, raw, content_type="application/json", HTTP_MONNIFY_SIGNATURE=signature)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, CommercePayment.STATUS_PAID)
        self.assertEqual(CommercePaymentReceipt.raw_objects.count(), 1)
        self.assertEqual(FinancialTransaction.raw_objects.count(), 1)


    @patch("commerce.payment_services.verify_gateway")
    def test_gateway_webhook_matches_automated_bank_transfer_by_gateway_provider(self, verify_gateway):
        payment = self._gateway_payment(
            CommercePayment.METHOD_BANK_TRANSFER,
            "STP-TRANSFER-WEBHOOK",
            "STP-TRANSFER-WEBHOOK",
        )
        payment.gateway_provider = CommercePayment.GATEWAY_PAYSTACK
        payment.save(update_fields=["gateway_provider"])
        verify_gateway.return_value = (True, {"reference": payment.reference, "amount": "5000.00", "currency": "NGN"})
        payload = {"event": "charge.success", "data": {"reference": payment.reference, "metadata": {"storetrack_payment_id": str(payment.public_id)}}}
        raw = json.dumps(payload).encode()
        signature = hmac.new(self.config.paystack_secret_key.encode(), raw, hashlib.sha512).hexdigest()
        url = f"/api/v1/storefronts/{self.business.slug}/payments/paystack/webhook"
        response = self.client.post(url, raw, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=signature)
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, CommercePayment.STATUS_PAID)

    def test_monnify_allows_missing_signature_only_for_sandbox_verification(self):
        self.config.monnify_base_url = "https://sandbox.monnify.com"
        self.assertTrue(monnify_signature_valid(self.config, b"{}", ""))
        self.config.monnify_base_url = "https://api.monnify.com"
        self.assertFalse(monnify_signature_valid(self.config, b"{}", ""))

    @patch("commerce.payment_gateways._json_request")
    def test_paystack_verification_rejects_amount_reference_currency_or_owner_mismatch(self, request_json):
        payment = self._gateway_payment("paystack", "STP-PAYSTACK-VERIFY", "provider-verify-1")
        request_json.return_value = {
            "status": True,
            "data": {
                "status": "success",
                "reference": payment.reference,
                "amount": 499999,
                "currency": "NGN",
                "metadata": {
                    "storetrack_payment_id": str(payment.public_id),
                    "storetrack_order_id": str(self.intake.public_id),
                    "business_id": self.business.pk,
                },
            },
        }
        verified, _ = verify_paystack(payment, self.config)
        self.assertFalse(verified)

    @patch("commerce.payment_gateways._json_request")
    def test_monnify_verification_matches_reference_amount_currency_and_owner(self, request_json):
        payment = self._gateway_payment("monnify", "STP-MONNIFY-VERIFY", "provider-verify-2")
        request_json.side_effect = [
            {"requestSuccessful": True, "responseBody": {"accessToken": "short-lived-token"}},
            {
                "requestSuccessful": True,
                "responseBody": {
                    "paymentStatus": "PAID",
                    "paymentReference": payment.reference,
                    "transactionReference": payment.gateway_reference,
                    "amountPaid": "5000.00",
                    "currencyCode": "NGN",
                    "metaData": {
                        "storetrackPaymentId": str(payment.public_id),
                        "storetrackOrderId": str(self.intake.public_id),
                        "businessId": str(self.business.pk),
                    },
                },
            },
        ]
        verified, _ = verify_monnify(payment, self.config)
        self.assertTrue(verified)
