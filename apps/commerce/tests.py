from decimal import Decimal
import tempfile
from unittest.mock import patch
from django.core.files.base import ContentFile
from django.test import override_settings
from django.core.exceptions import ValidationError
from django.test import TestCase
from core.models import Business
from inventory.models import FinishedGood, FinishedGoodChannelPrice, ProductCategory
from .forms import StorefrontProductForm
from .models import (
    CommerceIntake, CommerceSettings, DeliveryArea, DeliveryOrigin,
    DeliveryRateBand, DeliverySettings, StorefrontProduct,
)
from .services import accept_intake, create_intake
from .delivery_services import (
    _destination, _haversine_km, delivery_area_coverage_polygon, resolve_delivery_location,
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
        kwargs=dict(business=self.business, source=CommerceIntake.SOURCE_API, ordering_mode=CommerceIntake.MODE_STOCK, customer={"name":"Ada"}, items=[{"storefront_product":self.product,"quantity":"2"}], idempotency_key="same")
        first, created = create_intake(**kwargs)
        second, created_again = create_intake(**kwargs)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.sales_channel, CommerceIntake.CHANNEL_PHYSICAL_STORE)

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
        row = response.json()["products"][0]
        category_payload = {"id": self.category.pk, "name": "Meals", "slug": "meals"}
        self.assertEqual(response.json()["categories"], [category_payload])
        self.assertEqual(row["category"], category_payload)
        self.assertEqual(row["image_url"], "https://cdn.example.test/moin-moin.jpg")
        self.assertEqual(row["preorder_min_quantity"], "5.00")
        self.assertEqual(
            [mode["code"] for mode in row["order_modes"]],
            ["physical_store", "online", "distribution"],
        )
        self.assertEqual(row["order_modes"][2]["label"], "Catering / Bulk Order")
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
