import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from core.models import Business

from .delivery_forms import DeliveryProviderAccountForm
from .delivery_providers import (
    ProviderDispatchError, _generic_tracking_url, _shipbubble_request, consume_shipbubble_webhook,
    quote_shipbubble_delivery,
)
from .models import DeliveryProviderAccount

COURIER_A = {"courier_id": "c1", "courier_name": "Courier A", "service_code": "sA", "rate_card_amount": 2500, "total": 2300,
             "pickup_eta_time": "2099-01-01 10:00:00", "delivery_eta_time": "2099-01-01 15:00:00"}
COURIER_B = {"courier_id": "c2", "courier_name": "Courier B", "service_code": "sB", "rate_card_amount": 3500, "total": 3400,
             "pickup_eta_time": "2099-01-01 09:00:00", "delivery_eta_time": "2099-01-01 11:00:00"}


def fake_shipbubble(calls):
    def _call(url, *, payload=None, method="POST", headers=None, **_kw):
        calls.append((url, payload, method, headers))
        if url.endswith("/shipping/address/validate"):
            return {"status": "success", "data": {"address_code": 777}}
        if url.endswith("/shipping/fetch_rates"):
            return {"status": "success", "data": {"request_token": "tok123", "couriers": [COURIER_A, COURIER_B],
                                                 "cheapest_courier": COURIER_A, "fastest_courier": COURIER_B}}
        raise AssertionError(f"unexpected call {url}")
    return _call


class ShipbubbleContractTests(SimpleTestCase):
    def _account(self, **meta):
        return DeliveryProviderAccount(
            name="Shipbubble", provider_code=DeliveryProviderAccount.PROVIDER_SHIPBUBBLE, active=True,
            use_live_quotes=True, api_key="sb_sandbox_key", address_book_id="555",
            metadata={"category_id": 69709726, **meta},
        )

    def test_configuration_needs_only_an_api_key(self):
        account = self._account()
        self.assertTrue(account.is_configured_for_quote)
        self.assertTrue(account.is_configured_for_dispatch)
        account.api_key = ""
        self.assertFalse(account.is_configured_for_quote)
        self.assertFalse(account.is_configured_for_dispatch)

    @patch("commerce.delivery_providers.shipbubble_platform_enabled", return_value=True)
    def test_quote_follows_the_rates_contract_and_shows_the_customer_rate(self, _enabled):
        calls = []
        with patch("commerce.delivery_providers._http_json", side_effect=fake_shipbubble(calls)):
            quote = quote_shipbubble_delivery(self._account(), destination_address="12 Allen Ave, Ikeja, Lagos", subtotal=Decimal("8000"))
        self.assertEqual(quote["fee"], Decimal("2500"))          # rate_card_amount, not the wallet total
        self.assertEqual(quote["quote_id"], "tok123")
        self.assertEqual(quote["response"]["courier"]["service_code"], "sA")
        rates_call = next(c for c in calls if c[0].endswith("/shipping/fetch_rates"))
        body = rates_call[1]
        self.assertEqual((body["sender_address_code"], body["reciever_address_code"]), (555, 777))  # Shipbubble's spelling
        self.assertEqual((body["service_type"], body["category_id"]), ("pickup", 69709726))
        self.assertEqual(rates_call[3]["Authorization"], "Bearer sb_sandbox_key")
        validate = next(c for c in calls if c[0].endswith("/shipping/address/validate"))
        self.assertTrue(validate[1]["address"].endswith("Nigeria"))

    @patch("commerce.delivery_providers.shipbubble_platform_enabled", return_value=True)
    def test_courier_preference_can_choose_the_fastest(self, _enabled):
        with patch("commerce.delivery_providers._http_json", side_effect=fake_shipbubble([])):
            quote = quote_shipbubble_delivery(self._account(courier_preference="fastest"), destination_address="Ikeja, Lagos")
        self.assertEqual(quote["fee"], Decimal("3500"))
        self.assertEqual(quote["response"]["courier"]["courier_id"], "c2")

    @patch("commerce.delivery_providers.shipbubble_platform_enabled", return_value=True)
    def test_api_failures_become_readable_errors(self, _enabled):
        failed = {"status": "failed", "message": "Insufficient wallet balance"}
        with patch("commerce.delivery_providers._http_json", return_value=failed):
            with self.assertRaisesMessage(ProviderDispatchError, "Insufficient wallet balance"):
                _shipbubble_request(self._account(), "shipping/labels", payload={})

    def test_quote_is_unavailable_when_the_founder_switch_is_off(self):
        with patch("commerce.delivery_providers.shipbubble_platform_enabled", return_value=False):
            with self.assertRaises(Exception):
                quote_shipbubble_delivery(self._account(), destination_address="Ikeja, Lagos")


class ShipbubbleWebhookAndFormTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Courier Co", slug="courier-co")
        self.account = DeliveryProviderAccount.raw_objects.create(
            business=self.business, name="Shipbubble", provider_code="shipbubble", active=True,
            use_live_quotes=True, api_key="sb_secret",
        )

    def _sign(self, body, key="sb_secret"):
        return hmac.new(key.encode(), body, hashlib.sha512).hexdigest()

    @patch("commerce.delivery_providers.shipbubble_platform_enabled", return_value=True)
    def test_webhook_requires_a_valid_hmac_sha512_signature(self, _enabled):
        body = json.dumps({"event": "wallet.threshold.alert"}).encode()
        with self.assertRaises(ProviderDispatchError):
            consume_shipbubble_webhook(business=self.business, raw_body=body, payload=json.loads(body), signature="")
        with self.assertRaises(ProviderDispatchError):
            consume_shipbubble_webhook(business=self.business, raw_body=body, payload=json.loads(body), signature=self._sign(body, "wrong"))
        result = consume_shipbubble_webhook(business=self.business, raw_body=body, payload=json.loads(body), signature=self._sign(body))
        self.assertIsNone(result)  # a valid wallet alert is accepted and raises a notification, no assignment

    @patch("commerce.delivery_providers.shipbubble_platform_enabled", return_value=True)
    def test_unknown_shipment_is_acknowledged_without_error(self, _enabled):
        payload = {"event": "shipment.status.changed", "order_id": "SB-404", "status": "in_transit"}
        body = json.dumps(payload).encode()
        self.assertIsNone(consume_shipbubble_webhook(business=self.business, raw_body=body, payload=payload, signature=self._sign(body)))

    @patch("commerce.delivery_forms.shipbubble_platform_enabled", return_value=True)
    def test_form_asks_for_a_key_only_and_stores_parcel_defaults(self, _enabled):
        data = {"name": "Shipbubble couriers", "provider_code": "shipbubble", "active": "on", "api_key": "",
                "courier_preference": "fastest", "default_weight_kg": "2", "package_length_cm": "20",
                "package_width_cm": "25", "package_height_cm": "10"}
        form = DeliveryProviderAccountForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("api_key", form.errors)
        self.assertNotIn("api_secret", form.errors)
        self.assertNotIn("auth_endpoint", form.errors)
        data["api_key"] = "sb_key"
        form = DeliveryProviderAccountForm(data=data)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        account = form.save(commit=False)
        self.assertTrue(account.use_live_quotes)
        self.assertEqual(account.metadata["courier_preference"], "fastest")
        self.assertEqual(account.metadata["package_length_cm"], "20")
        self.assertEqual(account.status_mapping["completed"], "delivered")

    @patch("commerce.delivery_forms.shipbubble_platform_enabled", return_value=True)
    def test_custom_partner_fields_are_hidden_for_shipbubble_and_vice_versa(self, _enabled):
        form = DeliveryProviderAccountForm()
        self.assertEqual(form.fields["api_secret"].widget.attrs["data-provider-scope"], "generic")
        self.assertEqual(form.fields["courier_preference"].widget.attrs["data-provider-scope"], "shipbubble")


class ProviderNeutralContractTests(SimpleTestCase):
    def test_custom_partner_can_be_active_without_api_automation(self):
        account = DeliveryProviderAccount(name="Local Courier", provider_code=DeliveryProviderAccount.PROVIDER_GENERIC, active=True)
        self.assertTrue(account.is_configured_for_dispatch)
        self.assertFalse(account.is_configured_for_automatic_dispatch)

    def test_custom_partner_auto_dispatch_requires_endpoint_and_auth(self):
        account = DeliveryProviderAccount(
            name="Courier API", provider_code=DeliveryProviderAccount.PROVIDER_GENERIC,
            active=True, auto_dispatch=True, order_endpoint="/jobs", api_key="secret",
        )
        self.assertTrue(account.is_configured_for_automatic_dispatch)
        account.api_key = ""
        self.assertFalse(account.is_configured_for_automatic_dispatch)
        account.metadata = {"auth_type": "none"}
        self.assertTrue(account.is_configured_for_automatic_dispatch)

    @patch("commerce.delivery_forms.shipbubble_platform_enabled", return_value=False)
    def test_founder_disabled_shipbubble_is_not_an_available_provider_choice(self, _enabled):
        form = DeliveryProviderAccountForm()
        choices = dict(form.fields["provider_code"].choices)
        self.assertIn(DeliveryProviderAccount.PROVIDER_GENERIC, choices)
        self.assertNotIn(DeliveryProviderAccount.PROVIDER_SHIPBUBBLE, choices)
        self.assertNotIn("courier_preference", form.fields)

    @patch("commerce.delivery_forms.shipbubble_platform_enabled", return_value=True)
    def test_new_provider_accounts_start_inactive(self, _enabled):
        form = DeliveryProviderAccountForm()
        self.assertFalse(form.fields["active"].initial)

    def test_custom_tracking_template_uses_external_reference(self):
        account = DeliveryProviderAccount(
            name="Local Courier", provider_code=DeliveryProviderAccount.PROVIDER_GENERIC,
            tracking_base_url="https://courier.example.test/track/{external_reference}",
        )
        self.assertEqual(_generic_tracking_url(account, "JOB 42/NG"), "https://courier.example.test/track/JOB%2042%2FNG")

    def test_custom_tracking_base_appends_external_reference(self):
        account = DeliveryProviderAccount(
            name="Local Courier", provider_code=DeliveryProviderAccount.PROVIDER_GENERIC,
            tracking_base_url="https://courier.example.test/track",
        )
        self.assertEqual(_generic_tracking_url(account, "partner-123"), "https://courier.example.test/track/partner-123")


class ShipbubbleDispatchTests(TestCase):
    """A paid delivery is booked with the courier the customer was quoted, and can be cancelled."""

    def setUp(self):
        from . import tests as base_tests
        base_tests.DeliveryRealtimeTrackingTests.setUp(self)
        from .models import DeliveryAssignment as Assignment, DeliverySettings
        self.account = DeliveryProviderAccount.raw_objects.create(
            business=self.business, name="Shipbubble", provider_code="shipbubble", active=True, use_live_quotes=True,
            api_key="sb_key", address_book_id="555", metadata={"category_id": 1},
            status_mapping={"pending": "assigned", "in_transit": "picked_up", "completed": "delivered", "cancelled": "cancelled"},
        )
        quote = self.assignment.quote
        quote.provider_payload = {"provider": "shipbubble", "response": {"courier": {"courier_id": "c2", "service_code": "sB"}}}
        quote.save(update_fields=["provider_payload"])
        Assignment.raw_objects.filter(pk=self.assignment.pk).update(
            provider_account=self.account, provider=DeliverySettings.PROVIDER_THIRD_PARTY, driver=None,
        )

    def _dispatch(self):
        from .delivery_providers import dispatch_assignment_to_provider
        calls = []
        base = fake_shipbubble(calls)

        def handler(url, **kw):
            if url.endswith("/shipping/labels"):
                calls.append((url, kw.get("payload"), kw.get("method"), kw.get("headers")))
                return {"status": "success", "data": {"order_id": "SB-1001", "status": "pending", "tracking_url": "https://track.example/SB-1001"}}
            return base(url, **kw)

        with patch("commerce.delivery_providers.shipbubble_platform_enabled", return_value=True), \
                patch("commerce.delivery_providers._http_json", side_effect=handler):
            result = dispatch_assignment_to_provider(self.assignment)
        return result, calls

    def test_dispatch_books_the_quoted_courier_with_the_real_recipient(self):
        result, calls = self._dispatch()
        label = next(c for c in calls if c[0].endswith("/shipping/labels"))
        self.assertEqual(label[1], {"request_token": "tok123", "service_code": "sB", "courier_id": "c2"})  # the quoted courier, not just the cheapest
        validate = [c for c in calls if c[0].endswith("/shipping/address/validate")][-1]
        self.assertEqual(validate[1]["name"], self.assignment.intake.customer_name)
        self.assertEqual(result.provider_order_id, "SB-1001")
        self.assertEqual(result.external_tracking_url, "https://track.example/SB-1001")
        self.assertEqual(result.status, "assigned")

    def test_cancel_calls_the_cancel_endpoint(self):
        from .delivery_providers import cancel_assignment_with_provider
        from .models import DeliveryAssignment as Assignment
        Assignment.raw_objects.filter(pk=self.assignment.pk).update(provider_order_id="SB-1001")
        calls = []
        with patch("commerce.delivery_providers.shipbubble_platform_enabled", return_value=True), \
                patch("commerce.delivery_providers._http_json", side_effect=lambda url, **kw: calls.append(url) or {"status": "success", "message": "ok"}):
            cancel_assignment_with_provider(self.assignment)
        self.assertTrue(calls[0].endswith("/shipping/labels/cancel/SB-1001"))

    def test_signed_status_webhook_moves_the_delivery(self):
        from .models import DeliveryAssignment as Assignment
        Assignment.raw_objects.filter(pk=self.assignment.pk).update(provider_order_id="SB-1001")
        payload = {"event": "shipment.status.changed", "order_id": "SB-1001", "status": "completed"}
        body = json.dumps(payload).encode()
        signature = hmac.new(b"sb_key", body, hashlib.sha512).hexdigest()
        with patch("commerce.delivery_providers.shipbubble_platform_enabled", return_value=True):
            consume_shipbubble_webhook(business=self.business, raw_body=body, payload=payload, signature=signature)
        self.assertEqual(Assignment.raw_objects.get(pk=self.assignment.pk).status, "delivered")
