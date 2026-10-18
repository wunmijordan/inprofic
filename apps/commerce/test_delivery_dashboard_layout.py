from django.test import TestCase
from django.urls import reverse

from . import test_delivery_ui as ui_fixture  # module import keeps the fixture's own tests out of this class


class DeliveryDashboardLayoutTests(TestCase):
    """The dashboard is organised into Live queue / Closed deliveries / Setup, and stays theme-aware."""

    def setUp(self):
        ui_fixture.DeliverySetupUiTests.setUp(self)

    def _page(self):
        response = self.client.get(reverse("delivery_dashboard"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_work_areas_are_separated_into_tabs(self):
        page = self._page()
        for target in ("live", "closed", "setup"):
            self.assertIn(f'data-tab-target="{target}"', page)
        self.assertIn('data-tab-panel="setup"', page)
        # The existing setup guidance is still on the page, now inside the Setup tab.
        self.assertIn("Ready for customer delivery checkout", page)
        self.assertIn("Hosted storefront, in-premise POS and website API", page)

    def test_a_business_that_has_not_published_delivery_opens_on_setup(self):
        self.assertIn('data-tab="setup"', self._page())

    def test_summary_strip_shows_live_counts(self):
        page = self._page()
        for label in ("Awaiting pickup", "On the road", "Needs attention", "Active batches"):
            self.assertIn(label, page)

    def test_new_layout_uses_workspace_theme_tokens_not_fixed_colours(self):
        page = self._page()
        start = page.index(".dlv-tabs{")
        style = page[start:page.index("</style>", start)]
        self.assertIn("var(--ui-surface", style)
        self.assertIn("var(--ui-border", style)
        self.assertIn("var(--tenant-button", style)

    def test_tab_state_lives_outside_the_live_refreshed_region(self):
        page = self._page()
        workspace = page.index('data-delivery-tabs')
        live_root = page.index('id="delivery-live-root"')
        tabs = page.index('class="dlv-tabs"')
        self.assertLess(workspace, tabs)
        self.assertLess(tabs, live_root)   # the tab bar is not replaced when the queue refreshes

    def test_riders_without_manage_rights_never_see_the_setup_tab(self):
        self.client.force_login(self.rider)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()
        response = self.client.get(reverse("delivery_dashboard"))
        if response.status_code == 200:
            self.assertNotIn('data-tab-target="setup"', response.content.decode())


class ShipbubblePagesTests(TestCase):
    def setUp(self):
        ui_fixture.DeliverySetupUiTests.setUp(self)
        from unittest.mock import patch
        self.patches = [patch(f"{m}.shipbubble_platform_enabled", return_value=True)
                        for m in ("commerce.delivery_forms", "commerce.delivery_views", "commerce.delivery_providers", "core.context_processors")]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_provider_form_offers_shipbubble_with_a_key_and_parcel_defaults(self):
        page = self.client.get(reverse("delivery_provider_account_add")).content.decode()
        self.assertIn("Shipbubble couriers", page)
        self.assertIn("Courier choice", page)
        self.assertNotIn("OAuth", page)
        self.assertNotIn("LaaS", page)

    def test_dashboard_shows_the_shipbubble_card_and_webhook_path(self):
        from .models import DeliveryProviderAccount
        DeliveryProviderAccount.raw_objects.create(
            business=self.business, name="Shipbubble", provider_code="shipbubble", active=True, use_live_quotes=True, api_key="k")
        page = self.client.get(reverse("delivery_dashboard")).content.decode()
        self.assertIn("Live courier rates", page)
        self.assertIn("Shipbubble status webhook", page)
        self.assertNotIn("Register / verify webhooks", page)

    def test_webhook_endpoint_rejects_unsigned_and_accepts_signed_events(self):
        import hashlib, hmac, json
        from .models import DeliveryProviderAccount
        DeliveryProviderAccount.raw_objects.create(
            business=self.business, name="Shipbubble", provider_code="shipbubble", active=True, use_live_quotes=True, api_key="sb_key")
        url = reverse("shipbubble_delivery_webhook", args=[self.business.slug])
        body = json.dumps({"event": "shipment.status.changed", "order_id": "none", "status": "pending"}).encode()
        self.assertEqual(self.client.post(url, body, content_type="application/json").status_code, 403)
        signature = hmac.new(b"sb_key", body, hashlib.sha512).hexdigest()
        ok = self.client.post(url, body, content_type="application/json", HTTP_X_SHIP_SIGNATURE=signature)
        self.assertEqual(ok.status_code, 202)   # signed, but no matching delivery
