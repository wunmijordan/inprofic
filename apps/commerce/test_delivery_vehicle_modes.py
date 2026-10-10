from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Business

from . import tests as base_tests  # module import: keeps the fixture class out of this module's test discovery
from .delivery_forms import DeliveryAreaForm, DeliveryDriverForm, DeliveryRateBandForm, DeliverySettingsForm
from .delivery_services import (
    _method_quote_for_assignment, create_delivery_batch, create_delivery_quote_options,
    delivery_quote_choices, ensure_delivery_assignment, public_delivery_config, serialize_delivery_quote,
    serialize_delivery_tracking,
)
from .models import (
    VEHICLE_CAR, VEHICLE_MOTORBIKE, CommerceIntake, DeliveryArea, DeliveryAssignment, DeliveryBatch,
    DeliveryDriver, DeliveryQuote, DeliveryRateBand, DeliverySettings,
)
from . import test_delivery_independent_rider as independent_rider_tests  # module import: avoids re-collecting its tests here

DEST = {"destination_address": "1 Test Road", "latitude": "6.4502000", "longitude": "3.4210000", "location_source": "map_pin"}


class VehicleModeBase(TestCase):
    def setUp(self):
        independent_rider_tests.IndependentRiderDispatchTests.setUp(self)  # business, in-house rider, ASSIGNED delivery, dispatcher login
        self.car_band = DeliveryRateBand.raw_objects.create(
            business=self.business, name="Car pricing", vehicle_mode=VEHICLE_CAR, min_distance_km=0, max_distance_km=20,
            base_fee="1500.00", per_km_fee="0", eta_min_minutes=10, eta_max_minutes=25,
        )
        self.area.car_rate_band = self.car_band
        self.area.save(update_fields=["car_rate_band", "updated_at"])

    def quotes(self, subtotal="5000", **extra):
        return create_delivery_quote_options(business=self.business, subtotal=subtotal, **{**DEST, "area_id": self.area.pk, **extra})

    def enable_car(self, on=True):
        DeliverySettings.raw_objects.filter(business=self.business).update(car_enabled=on)


class VehicleQuoteTests(VehicleModeBase):
    def test_car_is_offered_with_no_extra_switch_once_a_car_price_band_exists(self):
        # The default settings, a car band and its area link are all a business needs.
        self.assertTrue(DeliverySettings.raw_objects.get(business=self.business).car_enabled)
        settings, options = self.quotes()
        self.assertEqual([q.vehicle_mode for q in options], [VEHICLE_MOTORBIKE, VEHICLE_CAR])
        self.assertIsNone(delivery_quote_choices(settings, options)[0])

    def test_motorbike_only_setup_is_unchanged_when_no_car_band_exists(self):
        self.area.car_rate_band = None
        self.area.save(update_fields=["car_rate_band", "updated_at"])
        self.car_band.delete()
        settings, options = self.quotes()
        self.assertEqual([(q.vehicle_mode, str(q.fee)) for q in options], [(VEHICLE_MOTORBIKE, "500.00")])
        self.assertEqual(delivery_quote_choices(settings, options)[0], options[0])

    def test_pausing_car_keeps_the_bands_but_stops_offering_it(self):
        self.enable_car(False)
        settings, options = self.quotes()
        self.assertEqual([q.vehicle_mode for q in options], [VEHICLE_MOTORBIKE])
        self.assertTrue(DeliveryRateBand.raw_objects.filter(pk=self.car_band.pk, active=True).exists())

    def test_a_client_can_ask_for_one_vehicle(self):
        settings, options = self.quotes(vehicle_mode="car")
        self.assertEqual([(q.vehicle_mode, str(q.fee)) for q in options], [(VEHICLE_CAR, "1500.00")])
        self.assertEqual(delivery_quote_choices(settings, options)[0], options[0])
        _settings, options = self.quotes(vehicle_mode="Motorbike")
        self.assertEqual([q.vehicle_mode for q in options], [VEHICLE_MOTORBIKE])
        with self.assertRaisesMessage(ValidationError, "Choose Motorbike or Car"):
            self.quotes(vehicle_mode="helicopter")
        self.enable_car(False)
        with self.assertRaisesMessage(ValidationError, "not offered"):
            self.quotes(vehicle_mode="car")

    def test_both_vehicles_are_offered_at_their_own_prices(self):
        settings, options = self.quotes()
        self.assertEqual([(q.vehicle_mode, str(q.fee), q.eta_max_minutes) for q in options],
                         [(VEHICLE_MOTORBIKE, "500.00", 35), (VEHICLE_CAR, "1500.00", 25)])
        selected, presented = delivery_quote_choices(settings, options)
        self.assertIsNone(selected)  # the customer chooses the vehicle
        self.assertEqual(len(presented), 2)
        data = [serialize_delivery_quote(q) for q in presented]
        self.assertEqual([(d["vehicle_mode"], d["vehicle_label"]) for d in data], [("motorbike", "Motorbike"), ("car", "Car")])
        self.assertEqual(sum(1 for q in options if q.quote_group_id == options[0].quote_group_id), 2)

    def test_car_total_uses_the_car_band_not_the_motorbike_band(self):
        _settings, options = self.quotes(subtotal="4000")
        car = next(q for q in options if q.vehicle_mode == VEHICLE_CAR)
        self.assertEqual((str(car.fee), str(car.total)), ("1500.00", "5500.00"))

    def test_car_is_simply_not_offered_where_it_has_no_price_band(self):
        self.area.car_rate_band = None
        self.area.save(update_fields=["car_rate_band", "updated_at"])
        self.car_band.max_distance_km = "0.01"  # and its distance range cannot reach the destination
        self.car_band.save()
        settings, options = self.quotes()
        self.assertEqual([q.vehicle_mode for q in options], [VEHICLE_MOTORBIKE])
        self.assertEqual(delivery_quote_choices(settings, options)[0], options[0])

    def test_car_minimum_order_applies_per_band(self):
        self.car_band.minimum_order = "9000.00"
        self.car_band.save()
        _settings, options = self.quotes(subtotal="5000")
        self.assertEqual([q.vehicle_mode for q in options], [VEHICLE_MOTORBIKE])

    def test_car_distance_fallback_when_the_area_has_no_car_band(self):
        self.area.car_rate_band = None
        self.area.save(update_fields=["car_rate_band", "updated_at"])
        _settings, options = self.quotes()
        self.assertIn(VEHICLE_CAR, [q.vehicle_mode for q in options])

    def test_vehicle_choice_follows_the_routing_policy_for_hybrid(self):
        def q(provider, vehicle, fee, account=None):
            return SimpleNamespace(provider=provider, vehicle_mode=vehicle, fee=fee, eta_max_minutes=30, pk=len(provider) + fee,
                                   provider_account_id=account)
        settings = SimpleNamespace(default_provider=DeliverySettings.PROVIDER_HYBRID,
                                   hybrid_routing_policy=DeliverySettings.HYBRID_ROUTE_LOWEST)
        inhouse_m, inhouse_c = q("inhouse", "motorbike", 500), q("inhouse", "car", 1500)
        partner_m = q("third_party", "motorbike", 400, account=7)
        # Partner is cheapest, and only offers motorbike: chosen outright.
        selected, _ = delivery_quote_choices(settings, [inhouse_m, inhouse_c, partner_m])
        self.assertIs(selected, partner_m)
        # In-house is cheapest and has two vehicles: the customer picks between those two only.
        partner_dear = q("third_party", "motorbike", 900, account=7)
        selected, presented = delivery_quote_choices(settings, [inhouse_m, inhouse_c, partner_dear])
        self.assertIsNone(selected)
        self.assertEqual(presented, [inhouse_m, inhouse_c])
        # Customer-choice policy offers everything.
        settings.hybrid_routing_policy = DeliverySettings.HYBRID_ROUTE_CUSTOMER
        selected, presented = delivery_quote_choices(settings, [inhouse_m, inhouse_c, partner_dear])
        self.assertIsNone(selected)
        self.assertEqual(len(presented), 3)

    def test_storefront_quote_endpoint_returns_vehicle_choices(self):
        self.client.logout()
        response = self.client.post(reverse("storefront_delivery_quote", args=[self.business.slug]), {
            "subtotal": "5000", "address": DEST["destination_address"], "area_id": self.area.pk,
            "latitude": DEST["latitude"], "longitude": DEST["longitude"], "location_source": "map_pin",
        })
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["selection_required"])
        self.assertEqual([(o["vehicle_mode"], o["fee"]) for o in body["options"]], [("motorbike", "500.00"), ("car", "1500.00")])

    def test_discovery_config_lists_vehicle_modes_and_car_pricing(self):
        config = public_delivery_config(self.business)
        self.assertEqual(config["vehicle_modes"], ["motorbike", "car"])
        self.assertEqual(config["vehicle_labels"], {"motorbike": "Motorbike", "car": "Car"})
        self.assertEqual(config["areas"][0]["car_pricing"]["base_fee"], "1500.00")
        self.enable_car(False)  # paused: not advertised, not priced
        config = public_delivery_config(self.business)
        self.assertEqual(config["vehicle_modes"], ["motorbike"])
        self.assertIsNone(config["areas"][0]["car_pricing"])
        self.enable_car(True)
        self.car_band.active = False  # switched on but no active car band: nothing to offer
        self.car_band.save()
        self.assertEqual(public_delivery_config(self.business)["vehicle_modes"], ["motorbike"])


class VehicleHeadlessApiTests(VehicleModeBase):
    """Everything the checkout does for customers is also available to headless clients."""

    def _key(self):
        return {"HTTP_X_INPROFIC_KEY": self.integration.api_key}

    def _api_quote(self, **extra):
        import json
        body = {"subtotal": "5000", "address": DEST["destination_address"], "area_id": self.area.pk,
                "latitude": DEST["latitude"], "longitude": DEST["longitude"], "location_source": "map_pin", **extra}
        return self.client.post(reverse("commerce_api_delivery_quote", args=[self.business.slug]),
                                json.dumps(body), content_type="application/json", **self._key())

    def test_api_quote_returns_both_vehicles_for_the_client_to_present(self):
        body = self._api_quote().json()
        self.assertTrue(body["selection_required"])
        self.assertIsNone(body["quote_id"])
        self.assertEqual([(o["vehicle_mode"], o["vehicle_label"], o["fee"]) for o in body["options"]],
                         [("motorbike", "Motorbike", "500.00"), ("car", "Car", "1500.00")])

    def test_api_quote_accepts_a_vehicle_mode_and_selects_it(self):
        response = self._api_quote(vehicle_mode="car")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["selection_required"])
        self.assertEqual((body["vehicle_mode"], body["fee"]), ("car", "1500.00"))
        self.assertEqual(self._api_quote(vehicle_mode="boat").status_code, 400)

    def test_api_config_exposes_vehicle_modes_and_the_checkout_flow(self):
        body = self.client.get(reverse("commerce_api_products", args=[self.business.slug])).json()
        self.assertEqual(body["delivery"]["vehicle_modes"], ["motorbike", "car"])
        self.assertEqual(body["delivery"]["areas"][0]["car_pricing"]["band"], "Car pricing")
        self.assertEqual(body["checkout_flow"], {
            "order_button_label": "Place order", "delivery_quote_button_label": "Calculate delivery",
            "delivery_quote_required": True, "auto_submit_after_quote": False,
        })
        self.assertTrue(body["product_display"]["selected_variant_replaces_name"])
        self.assertNotIn("selected_variant_replaces_name", body["catalogue_display"])  # pinned contract untouched

    def test_api_products_link_variants_to_their_parent(self):
        from inventory.models import FinishedGood
        from .models import StorefrontProduct
        def make(name, **extra):
            good = FinishedGood.raw_objects.create(
                business=self.business, name=name, unit="loaf", units_per_batch=1, stock=9, reorder_level=1,
                selling_price=1000, **extra,
            )
            return StorefrontProduct.raw_objects.create(business=self.business, finished_good=good, published=True, min_quantity=1)
        parent = make("Jollof Rice")
        child_b = make("Jollof Rice - Large", variant_of=parent.finished_good)
        child_a = make("Jollof Rice - Small", variant_of=parent.finished_good)
        solo = make("Zobo")
        rows = {r["id"]: r for r in self.client.get(reverse("commerce_api_products", args=[self.business.slug])).json()["products"]}
        self.assertEqual([v["name"] for v in rows[str(parent.public_id)]["variants"]], ["Jollof Rice - Large", "Jollof Rice - Small"])
        self.assertEqual({v["id"] for v in rows[str(parent.public_id)]["variants"]}, {str(child_a.public_id), str(child_b.public_id)})
        self.assertEqual(rows[str(child_a.public_id)]["variant_of"], str(parent.public_id))
        self.assertIsNone(rows[str(parent.public_id)]["variant_of"])
        self.assertEqual((rows[str(solo.public_id)]["variant_of"], rows[str(solo.public_id)]["variants"]), (None, []))
        # A variant stays an orderable row with its own price, options and add-ons.
        self.assertIn("order_modes", rows[str(child_a.public_id)])

    def test_api_order_detail_and_tracking_report_the_vehicle(self):
        self.assignment.vehicle_mode = VEHICLE_CAR
        self.assignment.save(update_fields=["vehicle_mode", "updated_at"])
        order = self.client.get(
            reverse("commerce_api_order_detail", args=[self.business.slug, self.assignment.intake.public_id]), **self._key()
        ).json()
        self.assertEqual((order["delivery"]["vehicle_mode"], order["delivery"]["vehicle_label"]), ("car", "Car"))
        tracking = self.client.get(reverse("commerce_api_delivery_tracking", args=[self.business.slug, self.assignment.public_id]), **self._key()).json()
        self.assertEqual(tracking["delivery"]["vehicle_mode"], "car")

    def test_migration_switches_existing_businesses_on(self):
        import importlib
        from django.apps import apps as django_apps
        migration = importlib.import_module("commerce.migrations.0033_delivery_car_on_by_default")
        DeliverySettings.raw_objects.filter(business=self.business).update(car_enabled=False)
        migration.enable_car_delivery(django_apps, None)
        self.assertTrue(DeliverySettings.raw_objects.get(business=self.business).car_enabled)


class VehicleAssignmentTests(VehicleModeBase):
    def _car_assignment(self, name="Car Customer"):
        quote = DeliveryQuote.raw_objects.create(
            business=self.business, origin=self.origin, area=self.area, vehicle_mode=VEHICLE_CAR,
            destination_address="5 Car Road", destination_latitude="6.4502000", destination_longitude="3.4210000",
            distance_km="3.00", subtotal="2500.00", fee="1500.00", total="4000.00",
            eta_min_minutes=10, eta_max_minutes=25, expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        intake = CommerceIntake.raw_objects.create(
            business=self.business, ordering_mode=CommerceIntake.MODE_STOCK, sales_channel=CommerceIntake.CHANNEL_ONLINE,
            customer_name=name, customer_address="5 Car Road", payment_state=CommerceIntake.PAYMENT_CONFIRMED,
            fulfilment_state=CommerceIntake.FULFIL_COMPLETE, delivery_quote=quote, delivery_fee="1500.00",
        )
        return ensure_delivery_assignment(intake)

    def test_assignment_carries_the_vehicle_the_customer_chose(self):
        assignment = self._car_assignment()
        self.assertEqual(assignment.vehicle_mode, VEHICLE_CAR)
        self.assertEqual(self.assignment.vehicle_mode, VEHICLE_MOTORBIKE)  # existing deliveries unchanged
        payload = serialize_delivery_tracking(assignment)["delivery"]
        self.assertEqual((payload["vehicle_mode"], payload["vehicle_label"]), ("car", "Car"))

    def test_method_switch_quote_keeps_the_customers_vehicle(self):
        car = self._car_assignment()
        _settings, target = _method_quote_for_assignment(car, DeliverySettings.PROVIDER_INHOUSE)
        self.assertEqual((target.vehicle_mode, str(target.fee)), (VEHICLE_CAR, "1500.00"))
        self.enable_car(False)  # car no longer offered: fail clearly rather than silently re-pricing as a motorbike
        with self.assertRaisesMessage(ValidationError, "car delivery is not currently available"):
            _method_quote_for_assignment(car, DeliverySettings.PROVIDER_INHOUSE)

    def test_a_batch_is_one_vehicle_and_independent_riders_work_for_car_batches(self):
        car_one, car_two = self._car_assignment("Car A"), self._car_assignment("Car B")
        with self.assertRaisesMessage(ValidationError, "same vehicle"):
            create_delivery_batch(business=self.business, assignments=[self.assignment, car_one], driver=self.driver)
        batch = create_delivery_batch(
            business=self.business, assignments=[car_one, car_two], manual_rider_name="Chidi Car",
            manual_rider_phone="0803", manual_rider_vehicle="Corolla LAG-9",
        )
        self.assertEqual(batch.vehicle_mode, VEHICLE_CAR)
        self.assertTrue(batch.has_independent_rider)
        self.assertEqual(DeliveryAssignment.raw_objects.get(pk=car_one.pk).manual_rider_name, "Chidi Car")

    def test_independent_rider_works_on_a_car_delivery_like_any_other(self):
        car = self._car_assignment()
        url = reverse("delivery_assignment_update", args=[car.public_id])
        self.client.post(url, {"status": "assigned", "rider_source": "independent", "manual_rider_name": "Chidi Car",
                               "manual_rider_vehicle": "Corolla"})
        car.refresh_from_db()
        self.assertEqual((car.manual_rider_name, car.vehicle_mode), ("Chidi Car", VEHICLE_CAR))
        for status in ("picked_up", "delivered"):
            self.client.post(url, {"status": status, "rider_source": "independent", "manual_rider_name": "Chidi Car",
                                   "manual_rider_vehicle": "Corolla", "proof_note": "ok"})
        car.refresh_from_db()
        self.assertEqual(car.status, DeliveryAssignment.STATUS_DELIVERED)


class VehicleUiTests(VehicleModeBase):
    def test_dispatch_dashboard_shows_vehicle_icons_and_labels(self):
        self.assignment.vehicle_mode = VEHICLE_CAR
        self.assignment.save(update_fields=["vehicle_mode", "updated_at"])
        page = self.client.get(reverse("delivery_dashboard"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'aria-label="Car"')       # the delivery's icon
        self.assertContains(page, 'aria-label="Motorbike"')  # the motorbike price band / rider
        self.assertContains(page, "[Car] ")                  # batch picker names the vehicle
        self.assertContains(page, "(Motorbike)")             # rider pickers name each rider's vehicle

    def test_rider_portal_shows_the_vehicle(self):
        self.assignment.vehicle_mode = VEHICLE_CAR
        self.assignment.save(update_fields=["vehicle_mode", "updated_at"])
        self.driver.user = self.dispatcher
        self.driver.save(update_fields=["user", "updated_at"])
        page = self.client.get(reverse("delivery_rider_dashboard"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'aria-label="Car"')

    def test_customer_tracking_page_draws_the_vehicle_from_the_payload(self):
        self.client.logout()
        self.assignment.vehicle_mode = VEHICLE_CAR
        self.assignment.save(update_fields=["vehicle_mode", "updated_at"])
        page = self.client.get(reverse("storefront_delivery_tracking", args=[self.business.slug, self.assignment.public_id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="vehicle-icon-car"')
        self.assertContains(page, 'id="vehicle-icon-motorbike"')
        self.assertContains(page, "window.vehicleIcon(delivery.vehicle_mode")

    def test_setup_dashboard_says_whether_car_is_on_and_offers_a_shortcut(self):
        page = self.client.get(reverse("delivery_dashboard"))
        self.assertContains(page, "Car delivery:")
        self.assertContains(page, "Customers choose Motorbike or Car at checkout")
        self.assertContains(page, reverse("delivery_rate_band_add") + "?vehicle=car")
        self.assertTrue(page.context["delivery_setup"]["car_offered"])
        self.enable_car(False)
        page = self.client.get(reverse("delivery_dashboard"))
        self.assertContains(page, "paused in Delivery settings")
        self.assertFalse(page.context["delivery_setup"]["car_offered"])
        self.enable_car(True)
        self.area.car_rate_band = None
        self.area.save(update_fields=["car_rate_band", "updated_at"])
        self.car_band.delete()
        self.assertContains(self.client.get(reverse("delivery_dashboard")), "not offered yet")

    def test_add_car_price_band_shortcut_preselects_car(self):
        page = self.client.get(reverse("delivery_rate_band_add") + "?vehicle=car")
        self.assertEqual(page.context["form"].initial["vehicle_mode"], VEHICLE_CAR)
        self.assertEqual(self.client.get(reverse("delivery_rate_band_add")).context["form"].initial.get("vehicle_mode"), None)

    def test_setup_forms_expose_the_car_option(self):
        self.assertIn("car_enabled", DeliverySettingsForm(business=self.business).fields)
        self.assertIn("vehicle_mode", DeliveryRateBandForm().fields)
        self.assertIn("vehicle_mode", DeliveryDriverForm(business=self.business).fields)
        area_form = DeliveryAreaForm(business=self.business)
        self.assertEqual(list(area_form.fields["car_rate_band"].queryset), [self.car_band])
        self.assertNotIn(self.car_band, area_form.fields["rate_band"].queryset)
        self.assertEqual(area_form.fields["car_rate_band"].required, False)

    def test_new_rider_defaults_to_motorbike_and_can_be_a_car_rider(self):
        self.assertEqual(self.driver.vehicle_mode, VEHICLE_MOTORBIKE)
        form = DeliveryDriverForm({"name": "Car Rider", "provider": "inhouse", "vehicle_mode": "car", "active": "on"},
                                  business=self.business)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).vehicle_mode, VEHICLE_CAR)


class StorefrontCheckoutUiTests(TestCase):
    """The public storefront carries the hooks for the variant-name and order-button behaviour."""

    def setUp(self):
        from inventory.models import FinishedGood
        from .models import CommerceSettings, StorefrontProduct
        self.business = Business.objects.create(name="Hook Bakery", slug="hook-bakery")
        CommerceSettings.raw_objects.create(business=self.business, enabled=True, api_enabled=True)
        good = FinishedGood.raw_objects.create(
            business=self.business, name="Loaf", unit="loaf", units_per_batch=1, stock=5, reorder_level=1, selling_price=1000,
        )
        StorefrontProduct.raw_objects.create(business=self.business, finished_good=good, published=True, min_quantity=1)

    def test_product_name_is_a_swappable_title_and_variant_select_is_not_restored_on_reload(self):
        page = self.client.get(reverse("storefront", args=[self.business.slug]))
        self.assertContains(page, "data-product-title")
        self.assertContains(page, "cardTitle.textContent = card.dataset.productName")

    def test_order_button_reads_place_order_and_switches_to_calculate_delivery_only_for_delivery(self):
        page = self.client.get(reverse("storefront", args=[self.business.slug]))
        self.assertContains(page, '<strong class="block" data-order-label>Place order</strong>')
        self.assertContains(page, "'Choose delivery option' : 'Calculate delivery'")  # options showing, none chosen yet
        self.assertContains(page, "Choose Motorbike or Car above, then press Place order.")
        # The quote no longer auto-submits the form; the customer presses Place order.
        self.assertNotContains(page, "basketForm.requestSubmit(submitter)")
