from decimal import Decimal
import tempfile
from django.core.files.base import ContentFile
from django.test import override_settings
from django.test import TestCase
from core.models import Business
from inventory.models import FinishedGood, FinishedGoodChannelPrice, ProductCategory
from .forms import StorefrontProductForm
from .models import (
    CommerceIntake, CommerceSettings, DeliveryArea, DeliveryOrigin,
    DeliveryRateBand, DeliverySettings, StorefrontProduct,
)
from .services import accept_intake, create_intake


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
        self.assertEqual(delivery["quote_url"], f"/api/v1/storefronts/{self.business.slug}/delivery/quote")
        self.assertEqual(delivery["areas"][0]["id"], self.delivery_area.pk)

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
