from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from core.models import Business

from .delivery_forms import DeliveryAreaForm, DeliveryOriginForm, DeliveryRateBandForm


class DeliverySetupUiTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Map Bakery", slug="map-bakery")
        roles = seed_business_roles(self.business)
        self.admin = CustomUser.objects.create_user(
            username="map-admin", password="safe-password-123", fullname="Map Admin"
        )
        UserBusiness.objects.create(
            user=self.admin, business=self.business,
            role=roles[CustomUser.ROLE_BUSINESS_ADMIN],
        )
        self.rider = CustomUser.objects.create_user(
            username="rider-one", password="safe-password-123",
            fullname="Rider One", phone="08010000000", email="rider@example.test",
        )
        UserBusiness.objects.create(
            user=self.rider, business=self.business,
            role=roles[CustomUser.ROLE_DELIVERY_RIDER],
        )
        other_business = Business.objects.create(name="Other", slug="other-map-business")
        other_roles = seed_business_roles(other_business)
        self.other_user = CustomUser.objects.create_user(
            username="other-rider", password="safe-password-123", fullname="Other Rider"
        )
        UserBusiness.objects.create(
            user=self.other_user, business=other_business,
            role=other_roles[CustomUser.ROLE_DELIVERY_RIDER],
        )
        self.client.force_login(self.admin)
        session = self.client.session
        session["active_business_id"] = self.business.pk
        session.save()

    def test_rider_form_directory_is_tenant_scoped_and_contains_contact_details(self):
        response = self.client.get(reverse("delivery_driver_add"))

        self.assertEqual(response.status_code, 200)
        directory = response.context["delivery_user_directory"]
        by_id = {row["id"]: row for row in directory}
        self.assertIn(self.rider.pk, by_id)
        self.assertEqual(by_id[self.rider.pk]["phone"], "08010000000")
        self.assertEqual(by_id[self.rider.pk]["email"], "rider@example.test")
        self.assertNotIn(self.other_user.pk, by_id)
        self.assertContains(response, "delivery-user-directory")

    def test_origin_and_destination_forms_use_plain_language_labels_and_map_picker(self):
        origin = DeliveryOriginForm()
        area = DeliveryAreaForm(business=self.business)

        self.assertEqual(origin.fields["name"].label, "Delivery / office base name")
        self.assertEqual(origin.fields["latitude"].label, "Base latitude")
        self.assertEqual(area.fields["name"].label, "Delivery destination / zone name")
        self.assertEqual(area.fields["latitude"].label, "Destination centre latitude")

        response = self.client.get(reverse("delivery_origin_add"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-location-picker")
        self.assertContains(response, "location-picker.js")

    def test_delivery_forms_and_dashboard_explain_the_setup_flow(self):
        rate_form = DeliveryRateBandForm()
        self.assertIn("customer-facing", rate_form.fields["eta_min_minutes"].help_text)

        response = self.client.get(reverse("delivery_origin_add"))
        self.assertContains(response, "Distance and delivery fees are calculated")
        self.assertContains(response, "Good to know")

        dashboard = self.client.get(reverse("delivery_dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "Ready for customer delivery checkout")
        self.assertContains(dashboard, "Hosted storefront, in-premise POS and website API")
