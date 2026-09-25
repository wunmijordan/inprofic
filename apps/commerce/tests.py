from decimal import Decimal
import tempfile
from unittest.mock import patch
from django.core.files.base import ContentFile
from django.test import override_settings
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from accounts.models import BusinessModuleAccess, CustomUser, UserBusiness
from core.models import Business
from inventory.models import (
    BulkPackProfile, FinishedGood, FinishedGoodChannelPrice, ProductCategory,
    ProductCompositionItem, ProductPortionProfile, RawMaterial,
)
from .forms import StorefrontProductForm
from .models import (
    CommerceIntegration, CommerceIntake, CommerceSettings, DeliveryArea, DeliveryAssignment,
    DeliveryDriver, DeliveryEvent, DeliveryOrigin, DeliveryQuote, DeliveryRateBand, DeliverySettings,
    StorefrontProduct,
)
from .services import accept_intake, create_intake
from .checkout_services import create_checkout
from .delivery_services import (
    _destination, _haversine_km, delivery_area_coverage_polygon, resolve_delivery_location,
    serialize_delivery_tracking, update_delivery_status,
)


class CommerceIntakeTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Bakery", slug="bakery-commerce", vertical=Business.VERTICAL_BAKERY)
        self.good = FinishedGood.raw_objects.create(business=self.business, name="Mini Loaf", unit="loaf", units_per_batch=110, stock=20, reorder_level=5, selling_price=100)
        self.product = StorefrontProduct.raw_objects.create(business=self.business, finished_good=self.good, published=True, allow_stock_order=True, allow_preorder=True)

    def test_preorder_intake_does_not_mutate_stock_or_production_before_acceptance(self):
        intake, created = create_intake(
            business=self.business, source=CommerceIntake.SOURCE_API, ordering_mode=CommerceIntake.MODE_PREORDER,
            customer={"name":"Ada"}, items=[{"storefront_product":self.product,"quantity":"50"}], idempotency_key="abc",
        )
        self.assertTrue(created)
        self.good.refresh_from_db()
        self.assertEqual(self.good.stock, Decimal("20"))
        self.assertIsNone(intake.accepted_order_id)

    def test_idempotency_key_returns_same_intake(self):
        kwargs=dict(business=self.business, source=CommerceIntake.SOURCE_API, sales_channel=CommerceIntake.CHANNEL_ONLINE, customer={"name":"Ada"}, items=[{"storefront_product":self.product,"quantity":"2"}], idempotency_key="same")
        first, created = create_intake(**kwargs)
        second, created_again = create_intake(**kwargs)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.sales_channel, CommerceIntake.CHANNEL_ONLINE)

    def test_public_order_numbers_increment_independently_per_business(self):
        other_business = Business.objects.create(name="Other Retailer", slug="other-retailer", vertical=Business.VERTICAL_RETAIL)
        first = CommerceIntake.raw_objects.create(
            business=self.business, ordering_mode=CommerceIntake.MODE_STOCK, customer_name="First Customer"
        )
        other_first = CommerceIntake.raw_objects.create(
            business=other_business, ordering_mode=CommerceIntake.MODE_STOCK, customer_name="Other Customer"
        )
        second = CommerceIntake.raw_objects.create(
            business=self.business, ordering_mode=CommerceIntake.MODE_STOCK, customer_name="Second Customer"
        )

        self.assertEqual(first.public_number, "WEB-000001")
        self.assertEqual(other_first.public_number, "WEB-000001")
        self.assertEqual(second.public_number, "WEB-000002")


class ProductionCommerceChannelTests(TestCase):
    def test_general_production_distribution_channel_uses_trade_price_and_pending_production(self):
        business = Business.objects.create(
            name="Sample Manufacturer", slug="sample-manufacturer", vertical=Business.VERTICAL_GENERAL
        )
        good = FinishedGood.raw_objects.create(
            business=business, name="Component Set", unit="set", units_per_batch=10,
            stock=0, reorder_level=0, selling_price=Decimal("100"),
        )
        FinishedGoodChannelPrice.objects.create(
            finished_good=good, channel="distribution", price=Decimal("80")
        )
        product = StorefrontProduct.raw_objects.create(
            business=business, finished_good=good, published=True,
            distribution_min_quantity=5, allow_preorder=True,
        )
        intake, _ = create_intake(
            business=business,
            source=CommerceIntake.SOURCE_API,
            sales_channel="distribution",
            customer={"name": "Trade Customer"},
            items=[{"storefront_product": product, "quantity": "5"}],
            idempotency_key="general-distribution",
        )
        self.assertEqual(intake.ordering_mode, CommerceIntake.MODE_PREORDER)
        self.assertEqual(intake.total, Decimal("400"))
        accept_intake(intake)
        intake.refresh_from_db()
        self.assertEqual(intake.accepted_order.order_type, "distribution")
        self.assertEqual(intake.accepted_order.items.get().price, Decimal("80"))

    def test_channel_toggle_alone_enables_production_preorder_route(self):
        business = Business.objects.create(name="Channel Manufacturer", slug="channel-manufacturer")
        good = FinishedGood.raw_objects.create(
            business=business, name="Made Product", unit="unit", units_per_batch=1,
            stock=0, reorder_level=0, selling_price=Decimal("100"),
        )
        product = StorefrontProduct.raw_objects.create(
            business=business, finished_good=good, published=True,
            allow_online_order=True, allow_preorder=False,
        )
        intake, _ = create_intake(
            business=business, source=CommerceIntake.SOURCE_API, sales_channel="online",
            customer={"name": "Online Customer"},
            items=[{"storefront_product": product, "quantity": "1"}],
        )
        self.assertEqual(intake.ordering_mode, CommerceIntake.MODE_PREORDER)


class CommerceApiProductTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Sample Restaurant", slug="sample-restaurant", vertical=Business.VERTICAL_RESTAURANT)
        CommerceSettings.raw_objects.create(business=self.business, enabled=True, api_enabled=True)
        self.category = ProductCategory.raw_objects.create(
            business=self.business, name="Meals", slug="meals", sort_order=1,
        )
        self.good = FinishedGood.raw_objects.create(
            business=self.business, name="Moin Moin", product_category=self.category,
            unit="plate", units_per_batch=1, stock=12, reorder_level=2,
            selling_price=2500,
        )
        self.product = StorefrontProduct.raw_objects.create(
            business=self.business, finished_good=self.good, published=True,
            image_url="https://cdn.example.test/moin-moin.jpg",
            min_quantity=1, preorder_min_quantity=5, allow_stock_order=True, allow_preorder=True,
        )
        DeliverySettings.raw_objects.create(business=self.business, enabled=True)
        DeliveryOrigin.raw_objects.create(
            business=self.business, name="Main kitchen", address="1 Test Road",
            latitude="6.4500000", longitude="3.4000000", is_default=True,
        )
        band = DeliveryRateBand.raw_objects.create(
            business=self.business, name="Nearby", min_distance_km=0,
            max_distance_km=20, base_fee=500, per_km_fee=0,
        )
        self.delivery_area = DeliveryArea.raw_objects.create(
            business=self.business, name="Victoria Island", code="victoria-island",
            latitude="6.4300000", longitude="3.4200000", rate_band=band,
        )

    def test_product_api_exposes_public_image_and_preorder_minimum(self):
        response = self.client.get(f"/api/v1/storefronts/{self.business.slug}/products")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        row = payload["products"][0]
        self.assertEqual(payload["price_display"], {"selected_channel_only": True, "full_menu_strategy": "rotate", "rotation_interval_ms": 2000, "transition_axis": "vertical"})
        category_payload = {"id": self.category.pk, "name": "Meals", "slug": "meals"}
        self.assertEqual(response.json()["categories"], [category_payload])
        self.assertEqual(row["category"], category_payload)
        self.assertEqual(row["image_url"], "https://cdn.example.test/moin-moin.jpg")
        self.assertEqual(row["preorder_min_quantity"], "5.00")
        self.assertEqual(
            [mode["code"] for mode in row["order_modes"]],
            ["online", "distribution"],
        )
        self.assertNotIn("stock_price", row)
        self.assertEqual(row["order_modes"][1]["label"], "Catering / Bulk Order")
        delivery = response.json()["delivery"]
        self.assertTrue(delivery["enabled"])
        self.assertEqual(delivery["location_url"], f"/api/v1/storefronts/{self.business.slug}/delivery/location")
        self.assertEqual(delivery["quote_url"], f"/api/v1/storefronts/{self.business.slug}/delivery/quote")
        self.assertEqual(delivery["destination_address_validation"], "server_geocode_or_map_pin")
        self.assertEqual(delivery["destination_address_flow"], "server_geocode_then_map_pin_fallback")
        area = delivery["areas"][0]
        self.assertEqual(area["id"], self.delivery_area.pk)
        self.assertEqual(area["radius_km"], "5.00")
        self.assertEqual(area["coverage_shape"], "circle")
        self.assertEqual(len(area["coverage_polygon"]), 36)
        self.assertEqual(area["pricing"]["distance_basis"], "delivery_base_to_precise_destination")

    def test_product_api_exposes_customer_portion_and_bulk_packs_without_internal_conversion(self):
        self.good.unit = "scoop"
        self.good.units_per_batch = Decimal("120")
        self.good.save(update_fields=["unit", "units_per_batch", "updated_at"])
        ProductPortionProfile.raw_objects.create(
            business=self.business, finished_good=self.good, active=True,
            customer_quantity=1, customer_unit="plate", base_quantity=3,
        )
        packaging = RawMaterial.raw_objects.create(
            business=self.business, name="Takeaway pack", stock=50, reorder_level=5,
            purchase_unit="piece", package_qty=1, package_unit="piece", usage_unit="piece",
        )
        ProductCompositionItem.objects.create(
            finished_good=self.good, profile_key="standard", component_raw_material=packaging,
            quantity=1, public_label="Takeaway pack", public_quantity_label="1 pack",
        )
        pack = BulkPackProfile.raw_objects.create(
            business=self.business, finished_good=self.good, name="2 L Bowl",
            customer_quantity=2, customer_unit="litre", base_quantity=12, price=Decimal("9000"),
        )
        response = self.client.get(f"/api/v1/storefronts/{self.business.slug}/products")
        self.assertEqual(response.status_code, 200)
        row = response.json()["products"][0]
        self.assertEqual(row["unit"], "plate")
        self.assertNotIn("base_quantity", row)
        self.assertEqual(row["contents"][1]["name"], "Takeaway pack")
        bulk = row["bulk_packs"][0]
        self.assertEqual(bulk["id"], str(pack.public_id))
        self.assertEqual(bulk["name"], "2 L Bowl")
        self.assertNotIn("base_quantity", bulk)
        self.assertNotIn("physical_store", {mode["code"] for mode in row["order_modes"]})

    def test_product_api_exposes_uploaded_image_as_absolute_url(self):
        # Minimal valid 1x1 transparent GIF.
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root, MEDIA_URL="/media/"):
            self.product.image.save(
                "catalog.gif",
                ContentFile(b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"),
                save=True,
            )
            response = self.client.get(f"/api/v1/storefronts/{self.business.slug}/products")
            self.assertTrue(response.json()["products"][0]["image_url"].startswith("http://testserver/media/commerce/products/"))

    def test_publish_form_uses_upload_and_has_no_redundant_preorder_toggle(self):
        form = StorefrontProductForm(instance=self.product, business=self.business)
        self.assertIn("image", form.fields)
        self.assertNotIn("image_url", form.fields)
        self.assertNotIn("allow_preorder", form.fields)


class DeliveryCoverageTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Coverage Store", slug="coverage-store")
        self.origin = DeliveryOrigin.raw_objects.create(
            business=self.business, name="Base", address="1 Base Road",
            latitude="6.4500000", longitude="3.4000000", is_default=True,
        )
        self.band = DeliveryRateBand.raw_objects.create(
            business=self.business, name="Zone pricing", min_distance_km=0, max_distance_km=50,
            base_fee=500, per_km_fee=100,
        )
        self.area = DeliveryArea.raw_objects.create(
            business=self.business, name="Central Zone", code="central-zone",
            latitude="6.4500000", longitude="3.4200000", radius_km="1.00",
            extension_ne_km="1.50", rate_band=self.band,
        )
        DeliverySettings.raw_objects.create(business=self.business, enabled=True)

    def test_coverage_polygon_includes_diagonal_extension(self):
        polygon = delivery_area_coverage_polygon(self.area, step_degrees=45)
        self.assertEqual(len(polygon), 8)
        # NE extends farther than the unextended east/south edges.
        distances = [
            _haversine_km(self.area.latitude, self.area.longitude, row["latitude"], row["longitude"])
            for row in polygon
        ]
        self.assertGreater(max(distances), Decimal("2.40"))
        self.assertLess(min(distances), Decimal("1.10"))

    @patch("commerce.delivery_services._geocode_results")
    def test_location_resolver_moves_typed_address_to_exact_map_point(self, geocode):
        geocode.return_value = [{
            "latitude": Decimal("6.4501000"),
            "longitude": Decimal("3.4210000"),
            "address": "12 Valid Street, Central Zone",
        }]
        result = resolve_delivery_location(
            business=self.business, address="12 Valid Street", area_id=self.area.pk,
        )
        self.assertEqual(result["validated_by"], "geocoded_address")
        self.assertEqual(result["address"], "12 Valid Street, Central Zone")
        self.assertEqual(result["latitude"], Decimal("6.4501000"))

    @patch("commerce.delivery_services._reverse_geocode")
    def test_location_resolver_reverse_geocodes_map_pin_into_address(self, reverse_geocode):
        reverse_geocode.return_value = "18 Pin Road, Central Zone"
        result = resolve_delivery_location(
            business=self.business, area_id=self.area.pk,
            latitude="6.4501000", longitude="3.4210000",
        )
        self.assertEqual(result["validated_by"], "map_pin")
        self.assertEqual(result["address"], "18 Pin Road, Central Zone")
        self.assertTrue(result["address_resolved"])

    @patch("commerce.delivery_services._geocode_candidates")
    def test_typed_address_must_resolve_inside_selected_area(self, geocode):
        geocode.return_value = [(Decimal("6.4500000"), Decimal("3.4210000"))]
        area, latitude, longitude, source = _destination(
            business=self.business, origin=self.origin, address="12 Valid Street", area_id=self.area.pk,
        )
        self.assertEqual(area.pk, self.area.pk)
        self.assertEqual(source, "geocoded_address")
        self.assertEqual(latitude, Decimal("6.4500000"))
        self.assertEqual(longitude, Decimal("3.4210000"))

        geocode.return_value = [(Decimal("6.4500000"), Decimal("3.5000000"))]
        with self.assertRaisesMessage(ValidationError, "could not locate"):
            _destination(
                business=self.business, origin=self.origin, address="Unlocatable Street", area_id=self.area.pk,
            )

    def test_map_pin_is_a_supported_address_fallback_but_still_obeys_coverage(self):
        area, latitude, longitude, source = _destination(
            business=self.business, origin=self.origin, address="Landmark near customer", area_id=self.area.pk,
            latitude="6.4500000", longitude="3.4210000",
        )
        self.assertEqual(area.pk, self.area.pk)
        self.assertEqual(source, "map_pin")
        self.assertEqual(latitude, Decimal("6.4500000"))

        with self.assertRaisesMessage(ValidationError, "outside Central Zone"):
            _destination(
                business=self.business, origin=self.origin, address="Far away", area_id=self.area.pk,
                latitude="6.4500000", longitude="3.5000000",
            )


class DeliveryRealtimeTrackingTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Realtime Store", slug="realtime-store")
        BusinessModuleAccess.objects.create(business=self.business, module="commerce", enabled=True)
        BusinessModuleAccess.objects.create(business=self.business, module="delivery", enabled=True)
        CommerceSettings.raw_objects.create(
            business=self.business, enabled=True, api_enabled=True, hosted_storefront_enabled=True,
        )
        DeliverySettings.raw_objects.create(
            business=self.business, enabled=True, customer_tracking_enabled=True,
        )
        self.integration = CommerceIntegration.raw_objects.create(
            business=self.business, name="Website", integration_type=CommerceIntegration.TYPE_API,
        )
        self.origin = DeliveryOrigin.raw_objects.create(
            business=self.business, name="Base", address="1 Base Road",
            latitude="6.4500000", longitude="3.4000000", is_default=True,
        )
        self.band = DeliveryRateBand.raw_objects.create(
            business=self.business, name="Local", min_distance_km=0, max_distance_km=20,
            base_fee="500.00", per_km_fee="0", eta_min_minutes=15, eta_max_minutes=35,
        )
        self.area = DeliveryArea.raw_objects.create(
            business=self.business, name="Local Area", latitude="6.4500000", longitude="3.4200000",
            radius_km="5.00", rate_band=self.band,
        )
        self.quote = DeliveryQuote.raw_objects.create(
            business=self.business, origin=self.origin, area=self.area,
            destination_address="12 Customer Road", destination_latitude="6.4501000",
            destination_longitude="3.4210000", distance_km="3.00", subtotal="2500.00",
            fee="500.00", total="3000.00", eta_min_minutes=15, eta_max_minutes=35,
            expires_at=timezone.now() + timezone.timedelta(hours=1), status=DeliveryQuote.STATUS_USED,
        )
        self.intake = CommerceIntake.raw_objects.create(
            business=self.business, ordering_mode=CommerceIntake.MODE_STOCK,
            sales_channel=CommerceIntake.CHANNEL_ONLINE, customer_name="Ada Customer",
            customer_address="12 Customer Road", payment_state=CommerceIntake.PAYMENT_CONFIRMED,
            delivery_quote=self.quote, delivery_fee="500.00",
        )
        self.driver = DeliveryDriver.raw_objects.create(
            business=self.business, name="Rider One", provider=DeliveryDriver.PROVIDER_INHOUSE,
            vehicle_type="Motorbike",
        )
        self.assignment = DeliveryAssignment.raw_objects.create(
            business=self.business, intake=self.intake, quote=self.quote, origin=self.origin,
            driver=self.driver, provider=DeliverySettings.PROVIDER_INHOUSE,
            status=DeliveryAssignment.STATUS_ASSIGNED, eta_at=None,
        )
        DeliveryEvent.raw_objects.create(
            business=self.business, assignment=self.assignment,
            status=DeliveryAssignment.STATUS_PENDING, note="Delivery created.",
        )

    @patch("commerce.delivery_services.publish_delivery_changed")
    def test_pickup_starts_eta_window_and_emits_realtime_signal(self, publish):
        before = timezone.now()
        with self.captureOnCommitCallbacks(execute=True):
            updated = update_delivery_status(
                assignment=self.assignment, status=DeliveryAssignment.STATUS_PICKED_UP,
                driver=self.driver, note="Collected from base.",
            )
        self.assertIsNotNone(updated.picked_up_at)
        self.assertGreaterEqual(updated.picked_up_at, before)
        self.assertEqual(
            updated.eta_at,
            updated.picked_up_at + timezone.timedelta(minutes=self.quote.eta_max_minutes),
        )
        payload = serialize_delivery_tracking(
            DeliveryAssignment.raw_objects.select_related("intake", "driver", "quote").prefetch_related("events").get(pk=updated.pk)
        )
        self.assertEqual(payload["delivery"]["status"], "picked_up")
        self.assertEqual(
            payload["delivery"]["eta_min_at"],
            (updated.picked_up_at + timezone.timedelta(minutes=15)).isoformat(),
        )
        self.assertEqual(payload["timeline"][0]["status"], "confirmed")
        self.assertEqual(payload["timeline"][-1]["status"], "picked_up")
        publish.assert_called_once()

    def test_public_tracking_snapshot_is_customer_safe_and_pickup_based(self):
        response = self.client.get(
            f"/shop/{self.business.slug}/deliveries/{self.assignment.public_id}/status/"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["order"]["number"], self.intake.public_number)
        self.assertIsNone(data["delivery"]["eta_min_at"])
        self.assertIsNone(data["delivery"]["eta_max_at"])
        self.assertEqual(data["realtime"]["event_type"], "delivery.changed")
        self.assertIn(str(self.assignment.public_id), data["realtime"]["websocket_path"])

    def test_headless_tracking_requires_api_key_and_exposes_timeline(self):
        url = f"/api/v1/storefronts/{self.business.slug}/deliveries/{self.assignment.public_id}/tracking"
        denied = self.client.get(url)
        allowed = self.client.get(url, HTTP_X_INPROFIC_KEY=self.integration.api_key)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)
        data = allowed.json()
        self.assertEqual(data["delivery"]["id"], str(self.assignment.public_id))
        self.assertEqual(data["timeline"][0]["status_label"], "Order confirmed")
        self.assertEqual(data["realtime"]["api_status_path"], url)


class StorefrontTenantLogoTests(TestCase):
    def test_public_storefront_shows_tenant_logo_only_in_storefront_branding(self):
        logo_bytes = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root, MEDIA_URL="/media/"):
            business = Business.objects.create(name="Logo Bakery", slug="logo-bakery")
            business.storefront_logo.save("logo.png", ContentFile(logo_bytes), save=True)
            CommerceSettings.raw_objects.create(
                business=business,
                enabled=True,
                hosted_storefront_enabled=True,
            )
            response = self.client.get(f"/shop/{business.slug}/")
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, business.storefront_logo.url)
            self.assertEqual(response.content.decode().count(business.storefront_logo.url), 1)
            self.assertContains(response, "INPROFIC")
            self.assertNotContains(response, f'<footer class="border-t border-stone-200 bg-white px-5 py-8 text-center"><p class="font-display text-lg font-semibold">{business.name}</p>', html=False)

class CommerceAttributionTests(TestCase):
    def test_attribution_accepts_utm_and_infers_known_referrer(self):
        from .attribution import normalize_attribution
        explicit = normalize_attribution({"utm_source": "Instagram", "utm_medium": "social", "utm_campaign": "launch"})
        self.assertEqual(explicit["source"], "instagram")
        self.assertEqual(explicit["medium"], "social")
        self.assertEqual(explicit["campaign"], "launch")
        inferred = normalize_attribution({}, referrer="https://www.facebook.com/some-post")
        self.assertEqual(inferred["source"], "facebook")
        self.assertEqual(inferred["medium"], "referral")

    def test_checkout_snapshots_normalized_attribution_inside_its_tenant(self):
        business = Business.objects.create(name="Attributed Shop", slug="attributed-shop", vertical=Business.VERTICAL_RETAIL)
        good = FinishedGood.raw_objects.create(
            business=business, name="Desk", unit="piece", units_per_batch=1, stock=5,
            reorder_level=0, selling_price=Decimal("120.00"),
        )
        product = StorefrontProduct.raw_objects.create(
            business=business, finished_good=good, published=True, allow_online_order=True,
        )
        checkout, created = create_checkout(
            business=business, source=CommerceIntake.SOURCE_STOREFRONT,
            customer={"name": "Ada"}, items=[{"storefront_product": product, "quantity": "1"}],
            idempotency_key="attribution-snapshot", order_mode="online",
            attribution={"utm_source": "WhatsApp", "utm_medium": "social", "utm_campaign": "Launch"},
        )
        self.assertTrue(created)
        self.assertEqual(checkout.business, business)
        self.assertEqual(checkout.attribution_source, "whatsapp")
        self.assertEqual(checkout.attribution_medium, "social")
        self.assertEqual(checkout.attribution_campaign, "Launch")

    def test_storefront_visit_is_recorded_with_generic_source(self):
        from .models import StorefrontAttributionVisit
        business = Business.objects.create(name="Tracked Store", slug="tracked-store", vertical=Business.VERTICAL_RETAIL)
        CommerceSettings.raw_objects.create(business=business, enabled=True, hosted_storefront_enabled=True)
        response = self.client.get(f"/shop/{business.slug}/?source=whatsapp&campaign=launch")
        self.assertEqual(response.status_code, 200)
        visit = StorefrontAttributionVisit.raw_objects.get(business=business)
        self.assertEqual(visit.attribution_source, "whatsapp")
        self.assertEqual(visit.attribution_campaign, "launch")
        self.assertContains(response, 'name="source" value="whatsapp"')


class CommerceQrCodeTests(TestCase):
    def setUp(self):
        from accounts.services import seed_business_roles

        self.business = Business.objects.create(name="QR Tenant", slug="qr-tenant")
        BusinessModuleAccess.objects.create(business=self.business, module="commerce", enabled=True)
        roles = seed_business_roles(self.business)
        self.user = CustomUser.objects.create_user(username="qr-admin", password="safe-password-123")
        UserBusiness.objects.create(user=self.user, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.client.force_login(self.user)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()

    def test_qr_is_a_downloadable_png_for_the_active_tenant(self):
        response = self.client.get(reverse("commerce_qr_code"), {"target": "order_now", "source": "whatsapp", "campaign": "launch"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn("inprofic-qr-tenant-order_now-qr.png", response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "private, no-store, max-age=0")

    def test_qr_rejects_unknown_destinations(self):
        response = self.client.get(reverse("commerce_qr_code"), {"target": "outside"})
        self.assertEqual(response.status_code, 400)
