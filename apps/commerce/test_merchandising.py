from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone
from django.test import Client

from core.models import Business
from inventory.models import FinishedGood
from retail.models import ProductPromotion
from .test_payments import CommercePaymentTestBase
from .models import StorefrontProduct, CommerceCheckoutSession, CommercePayment
from .merchandising import attach_merchandising, basket_quote


class MerchandisingTests(CommercePaymentTestBase):
    def campaign(self, **overrides):
        campaign = ProductPromotion.raw_objects.create(
            business=self.business,
            name="Three for two",
            mode="buy_get",
            qualifying_quantity=2,
            free_quantity=1,
            starts_on=timezone.localdate(),
            ends_on=timezone.localdate(),
            **overrides,
        )
        campaign.products.add(self.good)
        return campaign

    def test_offer_visibility_dates_channel_and_tenant(self):
        current = self.campaign(channel="physical_store")
        self.campaign(channel="distribution")
        expired = self.campaign()
        expired.ends_on = timezone.localdate() - timedelta(days=1)
        expired.save()
        other = Business.objects.create(
            name="Other", slug="other-store", vertical="retail"
        )
        other_campaign = ProductPromotion.raw_objects.create(
            business=other,
            name="Other offer",
            mode="buy_get",
            starts_on=timezone.localdate(),
            ends_on=timezone.localdate(),
        )
        other_campaign.products.add(
            self.good
        )  # Defensive against an invalid cross-tenant M2M.
        attach_merchandising(self.business, [self.product], "physical_store")
        self.assertEqual([p["id"] for p in self.product.card_promotions], [current.pk])
        self.assertIn(
            "Automatically added", self.product.card_promotions[0]["description"]
        )
        self.assertEqual(
            self.product.card_promotions[0]["help_items"][0]["name"], "Buy"
        )
        attach_merchandising(self.business, [self.product], "online")
        self.assertEqual(self.product.card_promotions, [])

    def test_reward_cards_and_merchandising_flags_share_api_metadata(self):
        reward = FinishedGood.raw_objects.create(
            business=self.business,
            name="Reward",
            unit="unit",
            stock=10,
            selling_price=100,
        )
        reward_product = StorefrontProduct.raw_objects.create(
            business=self.business,
            finished_good=reward,
            published=True,
            merchandising_badge="staff_pick",
        )
        campaign = self.campaign()
        campaign.reward_products.add(reward)
        response = self.client.get(
            reverse("commerce_api_products", args=[self.business.slug]),
            HTTP_X_INPROFIC_KEY=self.integration.api_key,
        )
        self.assertEqual(response.status_code, 200)
        row = next(
            p
            for p in response.json()["products"]
            if p["id"] == str(reward_product.public_id)
        )
        self.assertEqual(row["merchandising_badge"]["code"], "staff_pick")
        self.assertEqual(
            row["promotions"][0]["reward_product_ids"], [str(reward_product.public_id)]
        )
        self.assertEqual(row["ribbon_positions"]["promotions"], "top_left")

    def test_preview_matches_checkout_without_persistence_and_ignores_client_price(
        self,
    ):
        from .checkout_services import create_checkout, serialize_checkout
        from .models import CommerceIntake

        campaign = self.campaign()
        before = CommerceCheckoutSession.raw_objects.count()
        payments_before = CommercePayment.raw_objects.count()
        quote = basket_quote(
            self.business,
            {
                "items": [
                    {
                        "product_id": str(self.product.public_id),
                        "quantity": 3,
                        "price": "0.01",
                    }
                ]
            },
        )
        self.assertEqual(quote["subtotal"], "7500.00")
        self.assertEqual(quote["promotion"]["id"], campaign.pk)
        self.assertEqual(CommerceCheckoutSession.raw_objects.count(), before)
        self.assertEqual(CommercePayment.raw_objects.count(), payments_before)
        checkout, _ = create_checkout(
            business=self.business,
            source=CommerceIntake.SOURCE_API,
            customer={"name": "Buyer"},
            order_mode="online",
            items=[{"storefront_product": self.product, "quantity": 3}],
            idempotency_key="campaign-order",
        )
        self.assertEqual(serialize_checkout(checkout)["promotion"], quote["promotion"])
        self.assertEqual(checkout.amount, Decimal(quote["subtotal"]))
        self.assertEqual(
            sum(i.line_total for i in checkout.items.all()), checkout.amount
        )

    def test_hosted_quote_requires_csrf_and_api_quote_requires_key(self):
        path = reverse("storefront_basket_quote", args=[self.business.slug])
        response = Client(enforce_csrf_checks=True).post(
            path, {"items": []}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)
        path = reverse("commerce_api_basket_quote", args=[self.business.slug])
        self.assertEqual(
            self.client.post(path, {}, content_type="application/json").status_code, 403
        )
        response = self.api_post(
            path,
            {"items": [{"product_id": str(self.product.public_id), "quantity": 2}]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("checkout_id", response.json())

    def test_bestseller_requires_two_paid_orders_and_excludes_reversed_receipts(self):
        from .checkout_services import create_checkout
        from .payment_services import initiate_payment, record_verified_payment
        from .models import CommerceIntake, CommercePaymentReceipt

        for i in range(2):
            checkout, _ = create_checkout(
                business=self.business,
                source=CommerceIntake.SOURCE_STAFF_POS,
                customer={"name": "Buyer"},
                order_mode="physical_store",
                items=[{"storefront_product": self.product, "quantity": 1}],
                idempotency_key=f"rank-{i}",
            )
            payment = initiate_payment(
                checkout=checkout,
                method="cash",
                idempotency_key=f"rank-pay-{i}",
                surface="pos",
            )
            record_verified_payment(
                payment=payment,
                amount=checkout.amount,
                actor=None,
                external_reference=f"rank-{i}",
                idempotency_key=f"rank-receipt-{i}",
            )
            checkout.refresh_from_db()
            self.assertEqual(
                checkout.status, "materialized", checkout.materialization_error
            )
            attach_merchandising(self.business, [self.product], "online")
            self.assertEqual(
                self.product.card_badge["code"] if self.product.card_badge else None,
                "bestseller" if i else None,
            )
        CommercePaymentReceipt.raw_objects.filter(business=self.business).update(
            reversed_at=timezone.now()
        )
        attach_merchandising(self.business, [self.product], "online")
        self.assertIsNone(self.product.card_badge)

    def test_offline_campaign_snapshot_and_confirmed_discounted_sale(self):
        import uuid
        from accounts.models import CustomUser, UserBusiness
        from accounts.services import seed_business_roles

        campaign = self.campaign()
        user = CustomUser.objects.create_user(
            username="campaign-cashier", fullname="Cashier"
        )
        roles = seed_business_roles(self.business)
        UserBusiness.objects.create(
            user=user, business=self.business, role=roles["business_admin"]
        )
        self.client.force_login(user)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()
        snapshot = self.client.get("/retail/offline/snapshot/?interface=0").json()
        self.assertEqual(snapshot["promotions"][0]["id"], campaign.pk)
        payload = {
            "confirmed": True,
            "owner": snapshot["owner"],
            "entries": [
                {
                    "id": str(uuid.uuid4()),
                    "kind": "cash_sale",
                    "data": {
                        "customer": "Offline offer",
                        "total": "7500.00",
                        "items": [
                            {
                                "id": str(self.product.public_id),
                                "quantity": "3",
                                "price": "2500.00",
                            }
                        ],
                    },
                }
            ],
        }
        first = self.client.post(
            "/retail/offline/sync/", payload, content_type="application/json"
        ).json()
        self.assertTrue(first["results"][0]["ok"], first)
        self.good.refresh_from_db()
        self.assertEqual(self.good.stock, 16)
        second = self.client.post(
            "/retail/offline/sync/", payload, content_type="application/json"
        ).json()
        self.assertTrue(second["results"][0]["ok"], second)
        self.good.refresh_from_db()
        self.assertEqual(self.good.stock, 16)

    def test_quote_rejects_cross_business_product_and_invalid_payloads(self):
        from django.core.exceptions import ValidationError

        other = Business.objects.create(
            name="Elsewhere", slug="elsewhere", vertical="retail"
        )
        good = FinishedGood.raw_objects.create(
            business=other, name="Private", unit="unit", selling_price=1, stock=10
        )
        product = StorefrontProduct.raw_objects.create(
            business=other, finished_good=good, published=True
        )
        with self.assertRaises(ValidationError):
            basket_quote(
                self.business,
                {"items": [{"product_id": str(product.public_id), "quantity": 1}]},
            )
        response = self.api_post(
            reverse("commerce_api_basket_quote", args=[self.business.slug]), [1, 2]
        )
        self.assertEqual(response.status_code, 400)

    def test_multiple_channels_prices_and_expiry_leave_master_prices_unchanged(self):
        from .checkout_services import create_checkout
        from .models import CommerceIntake

        campaign = self.campaign()
        campaign.channels = ["online", "physical_store"]
        campaign.product_prices = {
            str(self.good.pk): {"online": "2000.00", "physical_store": "2100.00"}
        }
        campaign.save()
        attach_merchandising(self.business, [self.product], "online")
        self.assertEqual(self.product.preorder_price, Decimal("2000.00"))
        checkout, _ = create_checkout(
            business=self.business,
            source=CommerceIntake.SOURCE_STAFF_POS,
            customer={"name": "Buyer"},
            order_mode="physical_store",
            items=[{"storefront_product": self.product, "quantity": 1}],
            idempotency_key="price-only",
        )
        self.assertEqual(checkout.amount, Decimal("2100.00"))
        self.assertEqual(checkout.items.get().unit_price, Decimal("2100.00"))
        self.good.refresh_from_db()
        self.assertEqual(self.good.selling_price, Decimal("2500.00"))
        from .merchandising import active_promotions

        self.assertNotIn(campaign, active_promotions(self.business, "distribution"))
        campaign.ends_on = timezone.localdate() - timedelta(days=1)
        campaign.save()
        attach_merchandising(self.business, [self.product], "online")
        self.assertEqual(self.product.preorder_price, Decimal("2500.00"))

    def test_each_selected_product_price_is_used_and_higher_prices_are_supported(self):
        from .checkout_services import create_checkout
        from .models import CommerceIntake

        other = FinishedGood.raw_objects.create(
            business=self.business,
            name="Second X",
            unit="unit",
            stock=10,
            selling_price=4000,
        )
        second = StorefrontProduct.raw_objects.create(
            business=self.business, finished_good=other, published=True
        )
        campaign = self.campaign()
        campaign.products.add(other)
        campaign.qualifying_quantity = 100
        campaign.product_prices = {
            str(self.good.pk): {"online": "2600.00"},
            str(other.pk): {"online": "3000.00"},
        }
        campaign.save()
        checkout, _ = create_checkout(
            business=self.business,
            source=CommerceIntake.SOURCE_API,
            customer={"name": "Buyer"},
            order_mode="online",
            items=[
                {"storefront_product": self.product, "quantity": 1},
                {"storefront_product": second, "quantity": 1},
            ],
            idempotency_key="multi-X",
        )
        self.assertEqual(checkout.amount, Decimal("5600.00"))
        self.assertEqual(
            set(checkout.items.values_list("unit_price", flat=True)),
            {Decimal("2600.00"), Decimal("3000.00")},
        )

    def test_promotion_form_validates_multiple_channels_and_prices(self):
        from retail.forms import PromotionForm

        data = {
            "name": "Two channels",
            "mode": "buy_get",
            "products": [str(self.good.pk)],
            "reward_products": [str(self.good.pk)],
            "qualifying_quantity": "2",
            "free_quantity": "1",
            "starts_on": str(timezone.localdate()),
            "ends_on": str(timezone.localdate()),
            "channels": ["physical_store", "online"],
            f"price_{self.good.pk}_physical_store": "2300.00",
            f"price_{self.good.pk}_online": "2400.00",
            "active": "on",
        }
        form = PromotionForm(data, business=self.business)
        self.assertTrue(form.is_valid(), form.errors)
        form.instance.business = self.business
        campaign = form.save()
        self.assertEqual(campaign.channels, ["physical_store", "online"])
        self.assertEqual(
            campaign.product_prices[str(self.good.pk)]["online"], "2400.00"
        )
        form = PromotionForm(
            {**data, "channels": ["all", "online"]}, business=self.business
        )
        self.assertFalse(form.is_valid())
        self.assertIn("channels", form.errors)
        form = PromotionForm(
            {**data, f"price_{self.good.pk}_online": "-1"}, business=self.business
        )
        self.assertFalse(form.is_valid())
        self.assertIn(f"price_{self.good.pk}_online", form.errors)

    def test_headless_experience_discovers_configuration_and_excludes_pos_prices(self):
        campaign = self.campaign()
        campaign.channels = ["online", "physical_store"]
        campaign.product_prices = {
            str(self.good.pk): {"online": "2000.00", "physical_store": "1900.00"}
        }
        campaign.save()
        path = reverse("commerce_api_experience", args=[self.business.slug])
        self.assertEqual(self.client.get(path).status_code, 403)
        response = self.client.get(path, HTTP_X_INPROFIC_KEY=self.integration.api_key)
        self.assertEqual(response.status_code, 200)
        contract = response.json()
        self.assertTrue(contract["features"]["multiple_promotion_channels"])
        self.assertIn("payment_methods", contract)
        self.assertTrue(
            contract["endpoints"]["basket_quote"].startswith("http://testserver/")
        )
        self.assertEqual(response["Cache-Control"], "no-store")
        payload = self.client.get(
            reverse("commerce_api_products", args=[self.business.slug])
        ).json()
        row = next(
            p for p in payload["products"] if p["id"] == str(self.product.public_id)
        )
        self.assertEqual(row["online_price"], "2000.00")
        self.assertNotIn(
            "physical_store",
            row["promotions"][0]["unit_prices"][str(self.product.public_id)],
        )
        self.assertIn("experience", payload)
        response = self.api_post(
            reverse("commerce_api_checkouts", args=[self.business.slug]),
            [1, 2],
            idem="bad-top-level",
        )
        self.assertEqual(response.status_code, 400)

    def test_fractional_unit_price_override_and_headless_invalid_types(self):
        from .checkout_services import create_checkout
        from .models import CommerceIntake

        campaign = self.campaign()
        campaign.product_prices = {str(self.good.pk): {"online": "2000.00"}}
        campaign.save()
        checkout, _ = create_checkout(
            business=self.business,
            source=CommerceIntake.SOURCE_API,
            customer={"name": "Buyer"},
            order_mode="online",
            items=[{"storefront_product": self.product, "quantity": "1.5"}],
            idempotency_key="fractional-price",
        )
        self.assertEqual(checkout.amount, Decimal("3000.00"))
        self.assertEqual(checkout.items.get().unit_price, Decimal("2000.00"))
        path = reverse("commerce_api_checkouts", args=[self.business.slug])
        for payload in [
            {
                "order_mode": {},
                "items": [{"product_id": str(self.product.public_id), "quantity": 1}],
            },
            {
                "items": [
                    {
                        "product_id": str(self.product.public_id),
                        "quantity": 1,
                        "fulfilment_source": [],
                    }
                ]
            },
        ]:
            response = self.api_post(path, payload, idem="invalid-types")
            self.assertEqual(response.status_code, 400)

    def portion_fixture(self):
        from inventory.models import BulkPackProfile, IndividualSaleOption

        self.good.source_type = FinishedGood.SOURCE_PURCHASED_FOR_RESALE
        self.good.save(update_fields=["source_type"])
        pack = BulkPackProfile.raw_objects.create(
            business=self.business,
            finished_good=self.good,
            name="Pack of four",
            base_quantity=4,
            price=8000,
        )
        unit = IndividualSaleOption.raw_objects.create(
            business=self.business,
            finished_good=self.good,
            name="Single unit",
            customer_unit="unit",
            base_quantity=1,
            physical_store_price=2500,
            online_price=2500,
            distribution_enabled=True,
            distribution_price=2500,
        )
        return pack, unit

    def test_pack_qualifier_unit_reward_same_product_preserves_stock_and_prices(self):
        from .checkout_services import create_checkout
        from .models import CommerceIntake

        pack, unit = self.portion_fixture()
        campaign = self.campaign(
            product_portions={str(self.good.pk): pack.profile_key},
            reward_portions={str(self.good.pk): unit.profile_key},
            product_prices={str(self.good.pk): {"online": "7000.00"}},
            tooltip_title="A little extra",
            eligibility_rule="Buy two packs, enjoy a single on us.\nWhile this offer runs.",
        )
        campaign.reward_products.add(self.good)
        checkout, _ = create_checkout(
            business=self.business,
            source=CommerceIntake.SOURCE_API,
            customer={"name": "Portion customer"},
            order_mode="online",
            items=[
                {
                    "storefront_product": self.product,
                    "quantity": 2,
                    "bulk_pack_id": str(pack.public_id),
                },
                {
                    "storefront_product": self.product,
                    "quantity": 1,
                    "individual_option_id": str(unit.public_id),
                },
            ],
            idempotency_key="portion-checkout",
        )
        self.assertEqual(checkout.amount, Decimal("16500.00"))
        lines = list(checkout.items.all())
        self.assertEqual(
            sum(line.reserved_stock_quantity for line in lines), Decimal("10")
        )
        self.assertEqual(
            sum(
                line.payable_quantity * line.fulfilment_quantity_per_unit
                for line in lines
            ),
            Decimal("10"),
        )
        self.assertEqual(
            next(line for line in lines if line.is_promotion_reward).unit_price, 0
        )
        self.assertEqual(checkout.promotion_snapshot["help_title"], campaign.name)
        self.assertIn(
            "While this offer runs.", checkout.promotion_snapshot["description"]
        )
        pack.refresh_from_db()
        self.assertEqual(pack.price, 8000)
        standard_quote = basket_quote(
            self.business,
            {
                "order_mode": "online",
                "items": [{"product_id": str(self.product.public_id), "quantity": 3}],
            },
        )
        self.assertEqual(Decimal(standard_quote["subtotal"]), 7500)
        self.assertEqual(standard_quote["promotion"], {})

    def test_reward_packs_and_standard_portion_bundles_count_selling_portions(self):
        from .checkout_services import create_checkout
        from .models import CommerceIntake
        from inventory.models import ProductPortionProfile

        pack, unit = self.portion_fixture()
        campaign = self.campaign(
            product_portions={str(self.good.pk): unit.profile_key},
            reward_portions={str(self.good.pk): pack.profile_key},
        )
        campaign.reward_products.add(self.good)
        checkout, _ = create_checkout(
            business=self.business,
            source=CommerceIntake.SOURCE_API,
            customer={"name": "Customer"},
            order_mode="online",
            items=[
                {
                    "storefront_product": self.product,
                    "quantity": 2,
                    "individual_option_id": str(unit.public_id),
                },
                {
                    "storefront_product": self.product,
                    "quantity": 1,
                    "bulk_pack_id": str(pack.public_id),
                },
            ],
            idempotency_key="reward-pack",
        )
        self.assertEqual(checkout.amount, 13000)
        self.assertEqual(
            sum(line.reserved_stock_quantity for line in checkout.items.all()), 10
        )
        # A standard selling portion is already a pack when its profile says so.
        campaign.active = False
        campaign.save()
        ProductPortionProfile.raw_objects.create(
            business=self.business,
            finished_good=self.good,
            customer_unit="pack",
            base_quantity=4,
            active=True,
        )
        bundle = self.campaign()
        bundle.mode = "mix_match"
        bundle.qualifying_quantity = 2
        bundle.bundle_price = 4000
        bundle.save()
        self.product.finished_good = FinishedGood.raw_objects.get(pk=self.good.pk)
        checkout, _ = create_checkout(
            business=self.business,
            source=CommerceIntake.SOURCE_API,
            customer={"name": "Customer"},
            order_mode="online",
            items=[{"storefront_product": self.product, "quantity": 2}],
            idempotency_key="standard-pack",
        )
        self.assertEqual(checkout.amount, 4000)
        self.assertEqual(
            sum(line.reserved_stock_quantity for line in checkout.items.all()), 8
        )

    def test_portion_metadata_prices_and_custom_help_are_scoped_and_escaped(self):
        pack, unit = self.portion_fixture()
        campaign = self.campaign(
            product_portions={str(self.good.pk): pack.profile_key},
            reward_portions={str(self.good.pk): unit.profile_key},
            product_prices={
                str(self.good.pk): {"online": "7000.00", "physical_store": "1.00"}
            },
            eligibility_rule="<script>offer</script>",
            pricing_rule="<img src=x onerror=alert(1)>\nSecond line",
        )
        campaign.reward_products.add(self.good)
        payload = self.client.get(
            reverse("commerce_api_products", args=[self.business.slug])
        ).json()
        row = next(
            row
            for row in payload["products"]
            if row["id"] == str(self.product.public_id)
        )
        self.assertEqual(row["bulk_packs"][0]["price"], "7000.00")
        self.assertEqual(Decimal(row["online_price"]), 2500)
        offer = row["promotions"][0]
        self.assertEqual(offer["product_portions"][row["id"]], pack.profile_key)
        self.assertEqual(offer["reward_portions"][row["id"]], unit.profile_key)
        individual = next(
            item
            for item in payload["catalogue_items"]
            if item["kind"] == "individual_option"
        )
        self.assertEqual(individual["promotions"][0]["id"], campaign.pk)
        response = self.client.get(reverse("storefront", args=[self.business.slug]))
        self.assertContains(response, "&lt;script&gt;offer&lt;/script&gt;")
        self.assertNotContains(response, "<img src=x onerror=alert(1)>")

    def test_promotion_form_accepts_registered_portions_and_rejects_foreign_or_inactive_choices(
        self,
    ):
        from retail.forms import PromotionForm

        pack, unit = self.portion_fixture()
        data = {
            "name": "Pack reward",
            "mode": "buy_get",
            "products": [str(self.good.pk)],
            "reward_products": [str(self.good.pk)],
            "qualifying_quantity": 2,
            "free_quantity": 1,
            "starts_on": timezone.localdate(),
            "ends_on": timezone.localdate(),
            "channels": ["online"],
            f"portion_x_{self.good.pk}": pack.profile_key,
            f"portion_y_{self.good.pk}": unit.profile_key,
            f"price_{self.good.pk}_online": "7000.00",
            "tooltip_text": "Offer copy",
        }
        form = PromotionForm(
            data,
            business=self.business,
            instance=ProductPromotion(business=self.business),
        )
        self.assertTrue(form.is_valid(), form.errors)
        campaign = form.save()
        self.assertEqual(campaign.product_portions[str(self.good.pk)], pack.profile_key)
        self.assertEqual(campaign.reward_portions[str(self.good.pk)], unit.profile_key)
        data[f"portion_x_{self.good.pk}"] = "bulk:00000000-0000-0000-0000-000000000000"
        form = PromotionForm(data, business=self.business, instance=campaign)
        self.assertFalse(form.is_valid())
        self.assertIn(f"portion_x_{self.good.pk}", form.errors)
        pack.active = False
        pack.save()
        data[f"portion_x_{self.good.pk}"] = pack.profile_key
        self.assertFalse(
            PromotionForm(data, business=self.business, instance=campaign).is_valid()
        )

    def test_saved_promotion_edit_initials_include_portion_fields_and_prices(self):
        from retail.forms import PromotionForm

        pack, unit = self.portion_fixture()
        campaign = self.campaign(product_portions={str(self.good.pk): pack.profile_key})
        campaign.reward_products.add(self.good)
        for mode in ("buy_get", "mix_match"):
            campaign.mode = mode
            campaign.save()
            form = PromotionForm(
                instance=ProductPromotion.raw_objects.get(pk=campaign.pk),
                business=self.business,
            )
            self.assertIn(f"portion_x_{self.good.pk}", form.fields)
            self.assertIn(f"price_{self.good.pk}_online", form.fields)
            self.assertEqual(
                form[f"portion_x_{self.good.pk}"].value(), pack.profile_key
            )
            self.assertNotIn("tooltip_title", form.fields)
            self.assertNotIn("tooltip_text", form.fields)

    def test_optional_help_rules_are_omitted_and_dates_exclude_channel_list(self):
        from .merchandising import promotion_metadata

        campaign = self.campaign(channels=["online", "distribution"])
        offer = promotion_metadata(campaign, self.business)
        self.assertEqual(
            [item["name"] for item in offer["help_items"]], ["Buy", "Get free", "When"]
        )
        self.assertNotIn("Online", offer["help_items"][-1]["quantity_label"])
        campaign.eligibility_rule = "Members only"
        offer = promotion_metadata(campaign, self.business)
        self.assertEqual(
            offer["help_items"][-1],
            {"name": "Eligible items", "quantity_label": "Members only"},
        )
        self.assertNotIn("Pricing", [item["name"] for item in offer["help_items"]])
        campaign.pricing_rule = "Extra reward quantities are billed."
        self.assertEqual(
            promotion_metadata(campaign, self.business)["help_items"][-1]["name"],
            "Pricing",
        )

    def test_earned_reward_is_added_without_submitting_it_and_extra_y_stays_paid(self):
        from .checkout_services import create_checkout, serialize_checkout
        from .models import CommerceIntake

        reward = FinishedGood.raw_objects.create(
            business=self.business,
            name="Bonus",
            unit="unit",
            stock=10,
            selling_price=100,
            source_type=FinishedGood.SOURCE_PURCHASED_FOR_RESALE,
        )
        reward_product = StorefrontProduct.raw_objects.create(
            business=self.business,
            finished_good=reward,
            published=True,
            allow_stock_order=True,
        )
        campaign = self.campaign()
        campaign.reward_products.add(reward)
        for extra in (0, 1):
            items = [{"storefront_product": self.product, "quantity": 2}]
            if extra:
                items.append({"storefront_product": reward_product, "quantity": extra})
            checkout, _ = create_checkout(
                business=self.business,
                source=CommerceIntake.SOURCE_STAFF_POS,
                customer={"name": "Buyer"},
                items=items,
                order_mode="physical_store",
                idempotency_key=f"auto-bonus-{extra}",
            )
            self.assertEqual(checkout.amount, 5000 + 100 * extra)
            gifts = checkout.items.filter(is_promotion_reward=True)
            self.assertEqual(gifts.count(), 1)
            self.assertEqual(gifts.get().payable_quantity, 1)
            self.assertEqual(gifts.get().reserved_stock_quantity, 1)
            self.assertEqual(gifts.get().unit_price, 0)
            self.assertEqual(
                len(
                    [
                        row
                        for row in serialize_checkout(checkout)["items"]
                        if row["is_promotion_reward"]
                    ]
                ),
                1,
            )
            if extra:
                self.assertEqual(
                    checkout.items.get(
                        finished_good=reward, is_promotion_reward=False
                    ).unit_price,
                    100,
                )

    def test_automatic_reward_respects_combined_stock_and_ignores_client_free_flags(
        self,
    ):
        reward = FinishedGood.raw_objects.create(
            business=self.business,
            name="Bonus",
            unit="unit",
            stock=1,
            selling_price=100,
            source_type=FinishedGood.SOURCE_PURCHASED_FOR_RESALE,
        )
        product = StorefrontProduct.raw_objects.create(
            business=self.business,
            finished_good=reward,
            published=True,
            allow_stock_order=True,
        )
        campaign = self.campaign()
        campaign.reward_products.add(reward)
        # The customer buys the only available Y: it cannot also be gifted.
        quote = basket_quote(
            self.business,
            {
                "order_mode": "online",
                "items": [
                    {"product_id": str(self.product.public_id), "quantity": 2},
                    {
                        "product_id": str(product.public_id),
                        "quantity": 1,
                        "is_promotion_reward": True,
                        "unit_price": "0",
                    },
                ],
            },
        )
        self.assertEqual(Decimal(quote["subtotal"]), 5100)
        self.assertEqual(quote["promotion"], {})
        self.assertFalse(any(row["is_promotion_reward"] for row in quote["items"]))
        reward.refresh_from_db()
        self.assertEqual(reward.stock, 1)

    def test_multiple_offers_preserve_structured_help_across_portion_selections(self):
        from django.template.loader import render_to_string

        pack, unit = self.portion_fixture()
        first = self.campaign(channels=["online"], eligibility_rule="Members only")
        first.name = "Standard offer"
        first.save()
        second = self.campaign(
            channels=["online"],
            product_portions={str(self.good.pk): pack.profile_key},
            reward_portions={str(self.good.pk): unit.profile_key},
        )
        second.name = "Pack offer"
        second.save()
        second.reward_products.add(self.good)
        attach_merchandising(self.business, [self.product], "online")
        self.assertEqual(len(self.product.card_offer_groups), 1)
        group = self.product.card_offer_groups[0]
        self.assertEqual(group["title"], "2 offers")
        self.assertEqual(group["count"], 2)
        self.assertEqual(
            [section["title"] for section in group["sections"]],
            ["Standard offer", "Pack offer"],
        )
        self.assertEqual(
            [item["name"] for item in group["sections"][1]["items"]],
            ["Buy", "Get free", "When"],
        )
        html = render_to_string(
            "commerce/_product_ribbons.html", {"product": self.product}
        )
        self.assertIn("Help for 2 offers", html)
        self.assertEqual(html.count("data-help-section"), 2)
        self.assertEqual(html.count('class="form-help-tip__list"'), 2)
        self.assertIn("Pack of four", html)
        self.assertIn("Members only", html)
        self.assertNotIn("data-offer-selection", html)
        response = self.client.get(
            reverse("commerce_api_products", args=[self.business.slug])
        )
        product = next(
            row
            for row in response.json()["products"]
            if row["id"] == str(self.product.public_id)
        )
        self.assertEqual(product["promotion_help"][0]["title"], "2 offers")
        self.assertEqual(len(product["promotion_help"][0]["sections"]), 2)
        self.assertTrue(
            all(
                group["channel"] != "physical_store"
                for group in product["promotion_help"]
            )
        )

    def shared_reward_fixture(self, *, prices=True):
        reward = FinishedGood.raw_objects.create(
            business=self.business,
            name="Shared reward",
            unit="unit",
            stock=20,
            selling_price=100,
            source_type=FinishedGood.SOURCE_PURCHASED_FOR_RESALE,
        )
        other = FinishedGood.raw_objects.create(
            business=self.business,
            name="Second qualifier",
            unit="unit",
            stock=20,
            selling_price=1000,
            source_type=FinishedGood.SOURCE_PURCHASED_FOR_RESALE,
        )
        reward_product = StorefrontProduct.raw_objects.create(
            business=self.business,
            finished_good=reward,
            published=True,
            allow_stock_order=True,
        )
        other_product = StorefrontProduct.raw_objects.create(
            business=self.business,
            finished_good=other,
            published=True,
            allow_stock_order=True,
        )
        first = self.campaign(
            product_prices=(
                {str(self.good.pk): {"physical_store": "2000.00"}} if prices else {}
            )
        )
        first.qualifying_quantity = 1
        first.reward_products.add(reward)
        first.save()
        second = ProductPromotion.raw_objects.create(
            business=self.business,
            name="Second qualifier offer",
            mode="buy_get",
            qualifying_quantity=1,
            free_quantity=1,
            starts_on=timezone.localdate(),
            ends_on=timezone.localdate(),
            product_prices=(
                {str(other.pk): {"physical_store": "800.00"}} if prices else {}
            ),
        )
        second.products.add(other)
        second.reward_products.add(reward)
        return reward, other, other_product, reward_product

    def test_independent_qualifiers_each_earn_the_shared_reward(self):
        from .checkout_services import create_checkout

        reward, other, other_product, _ = self.shared_reward_fixture(prices=False)
        checkout, _ = create_checkout(
            business=self.business,
            source="staff_pos",
            order_mode="physical_store",
            customer={"name": "Buyer"},
            items=[
                {"storefront_product": self.product, "quantity": 2},
                {"storefront_product": other_product, "quantity": 3},
            ],
            idempotency_key="all-shared-rewards",
        )
        self.assertEqual(checkout.amount, 8000)
        gifts = checkout.items.filter(is_promotion_reward=True, finished_good=reward)
        self.assertEqual(sum(row.payable_quantity for row in gifts), 5)
        self.assertEqual(sum(row.reserved_stock_quantity for row in gifts), 5)

    def test_all_product_promotion_prices_survive_payment_and_receipt(self):
        from .checkout_services import create_checkout
        from .payment_services import initiate_payment, record_verified_payment
        from .views import _receipt_details

        reward, other, other_product, _ = self.shared_reward_fixture()
        checkout, _ = create_checkout(
            business=self.business,
            source="staff_pos",
            order_mode="physical_store",
            customer={"name": "Buyer"},
            items=[
                {"storefront_product": self.product, "quantity": 2},
                {"storefront_product": other_product, "quantity": 3},
            ],
            idempotency_key="all-promotion-prices",
        )
        self.assertEqual(checkout.amount, 6400)
        paid = {
            row.finished_good_id: row.unit_price
            for row in checkout.items.filter(is_promotion_reward=False)
        }
        self.assertEqual(
            paid, {self.good.pk: Decimal("2000"), other.pk: Decimal("800")}
        )
        payment = initiate_payment(
            checkout=checkout,
            method="cash",
            surface="pos",
            idempotency_key="all-promotion-prices-cash",
        )
        receipt, _ = record_verified_payment(
            payment=payment,
            amount=checkout.amount,
            actor=None,
            idempotency_key="all-promotion-prices-receipt",
        )
        details = _receipt_details(receipt)
        self.assertEqual(Decimal(details["total"]), 6400)
        paid_receipt = {
            row["name"]: Decimal(row["unit_price"])
            for row in details["items"]
            if not row["is_promotion_reward"]
        }
        self.assertEqual(paid_receipt[self.good.name], 2000)
        self.assertEqual(paid_receipt[other.name], 800)

    def test_shared_reward_stock_is_checked_across_all_applied_offers(self):
        from .checkout_services import create_checkout

        reward, other, other_product, _ = self.shared_reward_fixture()
        reward.stock = 3
        reward.save(update_fields=["stock"])
        checkout, _ = create_checkout(
            business=self.business,
            source="staff_pos",
            order_mode="physical_store",
            customer={"name": "Buyer"},
            items=[
                {"storefront_product": self.product, "quantity": 2},
                {"storefront_product": other_product, "quantity": 3},
            ],
            idempotency_key="shared-reward-stock",
        )
        gifts = checkout.items.filter(is_promotion_reward=True, finished_good=reward)
        self.assertEqual(sum(row.reserved_stock_quantity for row in gifts), 3)
        self.assertEqual(checkout.amount, 6400)

    def test_one_offer_counts_every_selected_qualifying_product(self):
        from .checkout_services import create_checkout

        reward, other, other_product, _ = self.shared_reward_fixture(prices=False)
        campaigns = list(
            ProductPromotion.raw_objects.filter(business=self.business).order_by("pk")
        )
        campaigns[1].active = False
        campaigns[1].save()
        campaigns[0].products.add(other)
        checkout, _ = create_checkout(
            business=self.business,
            source="staff_pos",
            order_mode="physical_store",
            customer={"name": "Buyer"},
            items=[
                {"storefront_product": self.product, "quantity": 2},
                {"storefront_product": other_product, "quantity": 3},
            ],
            idempotency_key="all-x-in-one-offer",
        )
        self.assertEqual(
            sum(
                row.payable_quantity
                for row in checkout.items.filter(is_promotion_reward=True)
            ),
            5,
        )
