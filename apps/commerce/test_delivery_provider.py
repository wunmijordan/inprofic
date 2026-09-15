from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase

from .delivery_providers import quote_glovo_delivery
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

    @patch("commerce.delivery_providers._glovo_request")
    def test_live_quote_uses_laas_address_book_contract(self, request_mock):
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
