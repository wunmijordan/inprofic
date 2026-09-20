from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase

from .delivery_forms import DeliveryProviderAccountForm
from .delivery_providers import _generic_tracking_url, quote_glovo_delivery
from .models import DeliveryProviderAccount


class GlovoProviderContractTests(SimpleTestCase):
    def _account(self):
        return DeliveryProviderAccount(
            name="Glovo",
            provider_code=DeliveryProviderAccount.PROVIDER_GLOVO,
            active=True,
            use_live_quotes=True,
            base_url="https://provider.example.test",
            auth_endpoint="/oauth/token",
            quote_endpoint="/v2/laas/quotes",
            order_endpoint="/v2/laas/quotes/{quote_id}/parcels",
            api_key="client-id",
            api_secret="client-secret",
            address_book_id="pickup-123",
        )

    def test_glovo_configuration_requires_live_quote_prerequisites(self):
        account = self._account()
        self.assertTrue(account.is_configured_for_quote)
        self.assertTrue(account.is_configured_for_dispatch)
        account.address_book_id = ""
        self.assertFalse(account.is_configured_for_quote)
        self.assertFalse(account.is_configured_for_dispatch)

    @patch("commerce.delivery_providers.glovo_platform_enabled", return_value=True)
    @patch("commerce.delivery_providers._glovo_request")
    def test_live_quote_uses_laas_address_book_contract(self, request_mock, _platform_enabled):
        request_mock.return_value = {
            "quoteId": "quote-123",
            "quotePrice": "2500.00",
            "currencyCode": "NGN",
            "distanceInMeters": 7200,
            "expiresAt": "2030-01-01T12:10:00Z",
            "estimatedTimeOfDelivery": {"lowerBound": "PT20M", "upperBound": "PT35M"},
        }
        result = quote_glovo_delivery(
            self._account(), destination_address="1 Example Street", latitude=6.5, longitude=3.4
        )
        self.assertEqual(result["quote_id"], "quote-123")
        self.assertEqual(result["fee"], Decimal("2500.00"))
        self.assertEqual(result["distance_km"], Decimal("7.20"))
        self.assertEqual(result["eta_min_minutes"], 20)
        self.assertEqual(result["eta_max_minutes"], 35)
        payload = request_mock.call_args.kwargs["payload"]
        self.assertEqual(payload["pickupDetails"]["addressBook"]["id"], "pickup-123")
        self.assertEqual(payload["deliveryAddress"]["rawAddress"], "1 Example Street")


class ProviderNeutralContractTests(SimpleTestCase):
    def test_custom_partner_can_be_active_without_api_automation(self):
        account = DeliveryProviderAccount(
            name="Local Courier", provider_code=DeliveryProviderAccount.PROVIDER_GENERIC, active=True
        )
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

    @patch("commerce.delivery_forms.glovo_platform_enabled", return_value=False)
    def test_founder_disabled_glovo_is_not_an_available_provider_choice(self, _enabled):
        form = DeliveryProviderAccountForm()
        choices = dict(form.fields["provider_code"].choices)
        self.assertIn(DeliveryProviderAccount.PROVIDER_GENERIC, choices)
        self.assertNotIn(DeliveryProviderAccount.PROVIDER_GLOVO, choices)
        self.assertNotIn("use_live_quotes", form.fields)

    @patch("commerce.delivery_forms.glovo_platform_enabled", return_value=True)
    def test_new_provider_accounts_start_inactive(self, _enabled):
        form = DeliveryProviderAccountForm()
        self.assertFalse(form.fields["active"].initial)
        self.assertFalse(form.fields["use_live_quotes"].initial)

    def test_custom_tracking_template_uses_external_reference(self):
        account = DeliveryProviderAccount(
            name="Local Courier", provider_code=DeliveryProviderAccount.PROVIDER_GENERIC,
            tracking_base_url="https://courier.example.test/track/{external_reference}",
        )
        self.assertEqual(
            _generic_tracking_url(account, "JOB 42/NG"),
            "https://courier.example.test/track/JOB%2042%2FNG",
        )

    def test_custom_tracking_base_appends_external_reference(self):
        account = DeliveryProviderAccount(
            name="Local Courier", provider_code=DeliveryProviderAccount.PROVIDER_GENERIC,
            tracking_base_url="https://courier.example.test/track",
        )
        self.assertEqual(
            _generic_tracking_url(account, "partner-123"),
            "https://courier.example.test/track/partner-123",
        )
