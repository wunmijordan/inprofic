from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse

from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles

from .delivery_services import delivery_status_choices_for, serialize_delivery_tracking
from .delivery_services import assign_delivery_batch_rider, create_delivery_batch
from .models import DeliverySettings, CommerceIntake, DeliveryAssignment, DeliveryBatch, DeliveryDriver, DeliveryEvent, DeliveryQuote
from . import tests as base_tests  # module import: keeps the fixture class out of this module's test discovery


class IndependentRiderDispatchTests(TestCase):
    """A dispatcher books a ride outside INPROFIC, types the rider in, and walks the status by hand."""

    def setUp(self):
        # Reuse the realtime-tracking fixture: a business with an in-house rider on an ASSIGNED delivery.
        base_tests.DeliveryRealtimeTrackingTests.setUp(self)
        roles = seed_business_roles(self.business)
        self.dispatcher = CustomUser.objects.create_user(
            username="dispatcher", password="safe-password-123", fullname="Dispatch Manager"
        )
        UserBusiness.objects.create(
            user=self.dispatcher, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN]
        )
        self.client.force_login(self.dispatcher)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()
        self.url = reverse("delivery_assignment_update", args=[self.assignment.public_id])

    def _post(self, **data):
        return self.client.post(self.url, data, follow=True)

    def _fresh(self):
        return DeliveryAssignment.raw_objects.get(pk=self.assignment.pk)

    def _independent(self, **overrides):
        data = {
            "status": DeliveryAssignment.STATUS_ASSIGNED,
            "rider_source": "independent",
            "manual_rider_name": "Tunde Bolt",
            "manual_rider_phone": "08031112222",
            "manual_rider_vehicle": "Bike LAG-123",
        }
        data.update(overrides)
        return data

    def test_dispatcher_overrides_inhouse_rider_with_independent_rider(self):
        with patch("commerce.delivery_services._notify_rider_assignment") as notify:
            self._post(**self._independent())
        assignment = self._fresh()
        self.assertIsNone(assignment.driver_id)
        self.assertEqual(assignment.manual_rider_name, "Tunde Bolt")
        self.assertTrue(assignment.has_independent_rider)
        self.assertIsNotNone(assignment.driver_assigned_at)
        # The overridden in-house rider is told the job is no longer theirs.
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs["previous_driver_id"], self.driver.pk)
        event = DeliveryEvent.raw_objects.filter(assignment=assignment).latest("id")
        self.assertTrue(event.metadata["rider_changed"])
        self.assertEqual(event.metadata["rider"], "Tunde Bolt")

    def test_customer_sees_independent_rider_exactly_like_an_inhouse_rider(self):
        inhouse = serialize_delivery_tracking(self._fresh())["delivery"]
        self._post(**self._independent(manual_rider_vehicle="Motorbike"))
        independent = serialize_delivery_tracking(self._fresh())["delivery"]
        self.assertEqual(independent["driver"], "Tunde Bolt")
        self.assertEqual(independent["driver_vehicle"], "Motorbike")
        # Same fields as for an in-house rider, and no internal "independent" marker.
        self.assertEqual(set(independent), set(inhouse))
        self.assertNotIn("manual_rider", independent)

    def test_dispatcher_walks_independent_rider_through_every_status(self):
        self._post(**self._independent())
        for status in (DeliveryAssignment.STATUS_PICKED_UP, DeliveryAssignment.STATUS_DELIVERED):
            # The form re-submits the same rider details with each status change.
            self._post(**self._independent(status=status, proof_note="Handed to customer"))
            self.assertEqual(self._fresh().status, status)
        assignment = self._fresh()
        self.assertEqual(assignment.manual_rider_name, "Tunde Bolt")
        self.assertIsNotNone(assignment.delivered_at)
        statuses = list(DeliveryEvent.raw_objects.filter(assignment=assignment).order_by("id").values_list("status", flat=True))
        self.assertEqual(statuses[-3:], ["assigned", "picked_up", "delivered"])

    def test_resubmitting_same_independent_rider_keeps_assignment_time(self):
        self._post(**self._independent())
        first = self._fresh().driver_assigned_at
        self._post(**self._independent(status=DeliveryAssignment.STATUS_PICKED_UP))
        self.assertEqual(self._fresh().driver_assigned_at, first)

    def test_status_only_update_never_wipes_independent_rider(self):
        self._post(**self._independent())
        # An older form / client that sends no rider fields at all.
        self._post(status=DeliveryAssignment.STATUS_PICKED_UP)
        assignment = self._fresh()
        self.assertEqual(assignment.manual_rider_name, "Tunde Bolt")
        self.assertEqual(assignment.manual_rider_phone, "08031112222")
        # ...and the legacy blank in-house select does not wipe it either.
        self._post(status=DeliveryAssignment.STATUS_PICKED_UP, driver_id="")
        self.assertEqual(self._fresh().manual_rider_name, "Tunde Bolt")

    def test_switching_back_to_inhouse_rider_clears_independent_details(self):
        self._post(**self._independent())
        self._post(status=DeliveryAssignment.STATUS_ASSIGNED, rider_source="inhouse", driver_id=str(self.driver.pk))
        assignment = self._fresh()
        self.assertEqual(assignment.driver_id, self.driver.pk)
        self.assertEqual(assignment.manual_rider_name, "")
        self.assertEqual(assignment.manual_rider_phone, "")
        self.assertFalse(assignment.has_independent_rider)

    def test_independent_source_requires_a_name(self):
        response = self._post(**self._independent(manual_rider_name="  "))
        self.assertContains(response, "independent rider")
        assignment = self._fresh()
        self.assertEqual(assignment.driver_id, self.driver.pk)  # nothing changed
        self.assertEqual(assignment.manual_rider_name, "")

    def test_invalid_rider_source_is_rejected(self):
        self._post(**self._independent(rider_source="teleport"))
        self.assertEqual(self._fresh().driver_id, self.driver.pk)

    def test_cannot_use_another_businesses_rider(self):
        from core.models import Business
        other = Business.objects.create(name="Other Co", slug="other-co")
        foreign = DeliveryDriver.raw_objects.create(business=other, name="Foreign", provider=DeliveryDriver.PROVIDER_INHOUSE)
        response = self.client.post(self.url, {
            "status": DeliveryAssignment.STATUS_ASSIGNED, "rider_source": "inhouse", "driver_id": str(foreign.pk),
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._fresh().driver_id, self.driver.pk)

    def test_dispatch_is_only_offered_valid_next_statuses(self):
        values = [value for value, _ in delivery_status_choices_for(self._fresh())]
        self.assertEqual(values[0], DeliveryAssignment.STATUS_ASSIGNED)
        self.assertIn(DeliveryAssignment.STATUS_PICKED_UP, values)
        self.assertNotIn(DeliveryAssignment.STATUS_DELIVERED, values)
        self.assertNotIn(DeliveryAssignment.STATUS_PENDING, values)

    def test_dashboard_renders_independent_rider_and_form_controls(self):
        self._post(**self._independent())
        response = self.client.get(reverse("delivery_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tunde Bolt")
        self.assertContains(response, "independent rider")
        self.assertContains(response, 'name="rider_source"')
        self.assertContains(response, 'name="manual_rider_name"')
        queue_item = response.context["assignments"][0]
        # The status dropdown is populated (the select was empty when this broke).
        self.assertTrue(queue_item.status_options)
        self.assertEqual(queue_item.rider_source_default, "independent")

    def test_business_with_no_inhouse_riders_defaults_to_independent_entry(self):
        self.assignment.driver = None
        self.assignment.save(update_fields=["driver", "updated_at"])
        DeliveryDriver.raw_objects.filter(business=self.business).update(active=False)
        response = self.client.get(reverse("delivery_dashboard"))
        self.assertEqual(response.context["assignments"][0].rider_source_default, "independent")
        self.assertFalse(response.context["has_inhouse_riders"])


class IndependentRiderBatchTests(TestCase):
    """An independent rider carries a multi-order batch exactly as an in-house rider would."""

    def setUp(self):
        IndependentRiderDispatchTests.setUp(self)
        self.second = self._second_assignment()
        self.other_driver = DeliveryDriver.raw_objects.create(
            business=self.business, name="Rider Two", provider=DeliveryDriver.PROVIDER_INHOUSE, vehicle_type="Van",
        )

    def _second_assignment(self):
        quote = DeliveryQuote.raw_objects.create(
            business=self.business, origin=self.origin, area=self.area,
            destination_address="99 Second Road", destination_latitude="6.4502000",
            destination_longitude="3.4220000", distance_km="3.00", subtotal="2500.00",
            fee="500.00", total="3000.00", eta_min_minutes=15, eta_max_minutes=35,
            expires_at=timezone.now() + timezone.timedelta(hours=1), status=DeliveryQuote.STATUS_USED,
        )
        intake = CommerceIntake.raw_objects.create(
            business=self.business, ordering_mode=CommerceIntake.MODE_STOCK,
            sales_channel=CommerceIntake.CHANNEL_ONLINE, customer_name="Bola Customer",
            customer_address="99 Second Road", payment_state=CommerceIntake.PAYMENT_CONFIRMED,
            fulfilment_state=CommerceIntake.FULFIL_COMPLETE, delivery_quote=quote, delivery_fee="500.00",
        )
        return DeliveryAssignment.raw_objects.create(
            business=self.business, intake=intake, quote=quote, origin=self.origin,
            driver=self.driver, provider=DeliverySettings.PROVIDER_INHOUSE,
            status=DeliveryAssignment.STATUS_ASSIGNED,
        )

    def _both(self):
        # Fresh rows, as the dashboard form would post them.
        return [DeliveryAssignment.raw_objects.get(pk=row.pk) for row in (self.assignment, self.second)]

    def _create_independent_batch(self):
        response = self.client.post(reverse("delivery_batch_create"), {
            "delivery_id": [str(row.public_id) for row in self._both()],
            "rider_source": "independent", "manual_rider_name": "Tunde Bolt",
            "manual_rider_phone": "08031112222", "manual_rider_vehicle": "Bike LAG-123",
        }, follow=True)
        return response, DeliveryBatch.raw_objects.latest("id")

    def _route_and_pickup(self, batch):
        stops = {"delivery_id": [], "sequence": [], "stop_minutes": []}
        for position, row in enumerate(batch.assignments.order_by("id"), 1):
            stops["delivery_id"].append(str(row.public_id))
            stops["sequence"].append(str(position))
            stops["stop_minutes"].append(str(position * 5))
        self.client.post(reverse("delivery_batch_route_update", args=[batch.public_id]), stops)
        self.client.post(reverse("delivery_batch_pickup_update", args=[batch.public_id]))

    def test_independent_rider_can_be_given_a_batch(self):
        response, batch = self._create_independent_batch()
        self.assertIsNone(batch.driver_id)
        self.assertTrue(batch.has_independent_rider)
        self.assertEqual(batch.rider_name, "Tunde Bolt")
        for row in self._both():
            self.assertEqual(row.batch_id, batch.pk)
            self.assertIsNone(row.driver_id)
            self.assertEqual((row.manual_rider_name, row.manual_rider_phone, row.manual_rider_vehicle),
                             ("Tunde Bolt", "08031112222", "Bike LAG-123"))
            self.assertIsNotNone(row.driver_assigned_at)
            # Customers see the batch rider like any other rider.
            self.assertEqual(serialize_delivery_tracking(row)["delivery"]["driver"], "Tunde Bolt")
        self.assertContains(response, "Dispatch arranges its route")

    def test_inhouse_batch_still_works_and_carries_no_independent_details(self):
        self.client.post(reverse("delivery_batch_create"), {
            "delivery_id": [str(row.public_id) for row in self._both()],
            "rider_source": "inhouse", "driver_id": str(self.driver.pk),
            "manual_rider_name": "ignored",
        })
        batch = DeliveryBatch.raw_objects.latest("id")
        self.assertEqual(batch.driver_id, self.driver.pk)
        self.assertEqual(batch.manual_rider_name, "")
        self.assertEqual(self._both()[0].manual_rider_name, "")

    def test_batch_requires_exactly_one_kind_of_rider(self):
        ids = [str(row.public_id) for row in self._both()]
        both = self.client.post(reverse("delivery_batch_create"), {
            "delivery_id": ids, "rider_source": "independent", "manual_rider_name": "   ",
        }, follow=True)
        self.assertContains(both, "independent rider")
        neither = self.client.post(reverse("delivery_batch_create"), {"delivery_id": ids, "rider_source": "inhouse"}, follow=True)
        self.assertContains(neither, "Choose an in-house rider")
        with self.assertRaises(ValidationError):
            create_delivery_batch(business=self.business, assignments=self._both(), driver=self.driver, manual_rider_name="X")
        self.assertFalse(DeliveryBatch.raw_objects.exists())

    def test_dispatch_routes_picks_up_and_completes_an_independent_batch(self):
        _, batch = self._create_independent_batch()
        self._route_and_pickup(batch)
        batch.refresh_from_db()
        self.assertEqual(batch.status, DeliveryBatch.STATUS_PICKED_UP)
        for row in self._both():
            self.assertEqual(row.status, DeliveryAssignment.STATUS_PICKED_UP)
            self.assertIsNotNone(row.picked_up_at)
        self.assertEqual([row.batch_stop_minutes for row in batch.assignments.order_by("batch_stop_sequence")], [5, 10])
        for row in self._both():
            # The form re-posts the same status and nothing about the rider.
            self.client.post(reverse("delivery_assignment_update", args=[row.public_id]), {
                "status": DeliveryAssignment.STATUS_DELIVERED, "proof_note": "Handed over",
            })
        batch.refresh_from_db()
        self.assertEqual(batch.status, DeliveryBatch.STATUS_COMPLETED)

    def test_pickup_needs_a_saved_route_and_route_positions_must_differ(self):
        _, batch = self._create_independent_batch()
        self.client.post(reverse("delivery_batch_pickup_update", args=[batch.public_id]))
        batch.refresh_from_db()
        self.assertEqual(batch.status, DeliveryBatch.STATUS_DRAFT)
        ids = [str(row.public_id) for row in batch.assignments.all()]
        response = self.client.post(
            reverse("delivery_batch_route_update", args=[batch.public_id]),
            {"delivery_id": ids, "sequence": ["1", "1"], "stop_minutes": ["0", "0"]}, follow=True,
        )
        self.assertContains(response, "different route position")

    def test_dispatch_overrides_a_batch_rider_both_ways_and_notifies_the_replaced_rider(self):
        self.client.post(reverse("delivery_batch_create"), {
            "delivery_id": [str(row.public_id) for row in self._both()],
            "rider_source": "inhouse", "driver_id": str(self.driver.pk),
        })
        batch = DeliveryBatch.raw_objects.latest("id")
        url = reverse("delivery_batch_rider_update", args=[batch.public_id])
        with patch("commerce.delivery_services._notify_rider_assignment") as notify:
            self.client.post(url, {"rider_source": "independent", "manual_rider_name": "Tunde Bolt", "manual_rider_phone": "0803"})
        batch.refresh_from_db()
        self.assertIsNone(batch.driver_id)
        self.assertEqual(batch.manual_rider_name, "Tunde Bolt")
        for row in self._both():
            self.assertEqual((row.driver_id, row.manual_rider_name), (None, "Tunde Bolt"))
        self.assertEqual(notify.call_count, 2)  # once per delivery moved
        self.assertTrue(all(call.kwargs["previous_driver_id"] == self.driver.pk for call in notify.call_args_list))
        event = DeliveryEvent.raw_objects.filter(assignment=self.assignment).latest("id")
        self.assertTrue(event.metadata["rider_changed"])
        # ...and back to an in-house rider: independent details are cleared everywhere.
        self.client.post(url, {"rider_source": "inhouse", "driver_id": str(self.other_driver.pk)})
        batch.refresh_from_db()
        self.assertEqual(batch.driver_id, self.other_driver.pk)
        self.assertEqual(batch.manual_rider_name, "")
        for row in self._both():
            self.assertEqual((row.driver_id, row.manual_rider_name), (self.other_driver.pk, ""))

    def test_unchanged_rider_is_a_no_op_and_finished_batches_are_locked(self):
        _, batch = self._create_independent_batch()
        before = DeliveryEvent.raw_objects.count()
        assign_delivery_batch_rider(batch=batch, manual_rider_name="Tunde Bolt", manual_rider_phone="08031112222", manual_rider_vehicle="Bike LAG-123")
        self.assertEqual(DeliveryEvent.raw_objects.count(), before)
        DeliveryBatch.raw_objects.filter(pk=batch.pk).update(status=DeliveryBatch.STATUS_COMPLETED)
        with self.assertRaises(ValidationError):
            assign_delivery_batch_rider(batch=batch, driver=self.driver)

    def test_a_single_delivery_inside_a_batch_cannot_get_a_different_rider(self):
        _, batch = self._create_independent_batch()
        row = self._both()[0]
        url = reverse("delivery_assignment_update", args=[row.public_id])
        self.client.post(url, {"status": "assigned", "rider_source": "inhouse", "driver_id": str(self.driver.pk)})
        self.assertIsNone(DeliveryAssignment.raw_objects.get(pk=row.pk).driver_id)
        self.client.post(url, {"status": "assigned", "rider_source": "independent", "manual_rider_name": "Someone Else"})
        self.assertEqual(DeliveryAssignment.raw_objects.get(pk=row.pk).manual_rider_name, "Tunde Bolt")
        # Re-saving with the batch's own rider is fine.
        self.client.post(url, {"status": "assigned", "rider_source": "independent", "manual_rider_name": "Tunde Bolt",
                               "manual_rider_phone": "08031112222", "manual_rider_vehicle": "Bike LAG-123", "note": "ok"})
        self.assertEqual(DeliveryAssignment.raw_objects.get(pk=row.pk).status_note, "ok")

    def test_another_businesses_batch_is_not_reachable(self):
        from core.models import Business
        _, batch = self._create_independent_batch()
        other = Business.objects.create(name="Other Co", slug="other-co-batch")
        batch.business = other
        DeliveryBatch.raw_objects.filter(pk=batch.pk).update(business=other)
        for name in ("delivery_batch_rider_update", "delivery_batch_route_update", "delivery_batch_pickup_update"):
            response = self.client.post(reverse(name, args=[batch.public_id]), {"rider_source": "inhouse"})
            self.assertEqual(response.status_code, 404, name)

    def test_dashboard_shows_independent_batch_controls_in_the_right_state(self):
        _, batch = self._create_independent_batch()
        page = self.client.get(reverse("delivery_dashboard"))
        self.assertContains(page, "Tunde Bolt")
        self.assertContains(page, "Change batch rider")
        self.assertContains(page, reverse("delivery_batch_route_update", args=[batch.public_id]))
        self.assertNotContains(page, reverse("delivery_batch_pickup_update", args=[batch.public_id]))  # not routed yet
        self.assertContains(page, "Rider is set by batch")
        self._route_and_pickup(batch)
        DeliveryBatch.raw_objects.filter(pk=batch.pk).update(status=DeliveryBatch.STATUS_ROUTED)
        page = self.client.get(reverse("delivery_dashboard"))
        self.assertContains(page, reverse("delivery_batch_pickup_update", args=[batch.public_id]))
