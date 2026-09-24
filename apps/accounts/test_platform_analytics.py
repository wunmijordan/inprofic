from django.test import TestCase

from core.models import Business
from .analytics import founder_analytics_summary, founder_signup_contacts, record_founder_signup_contact, record_platform_event
from .models import CustomUser, FounderSignupContactState, PlatformEvent


class PlatformAnalyticsTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Analytics Store", slug="analytics-store")
        self.user = CustomUser.objects.create_user(username="analytics.user", password="safe-password-123")

    def test_founder_summary_uses_meaningful_first_party_events(self):
        PlatformEvent.objects.create(event_type=PlatformEvent.EVENT_SIGNUP_VIEW, session_key="lead-session")
        record_platform_event(PlatformEvent.EVENT_REGISTRATION, user=self.user, business=self.business)
        record_platform_event(PlatformEvent.EVENT_LOGIN, user=self.user, business=self.business)
        record_platform_event(PlatformEvent.EVENT_MODULE_VIEW, user=self.user, business=self.business, module="inventory")
        summary = founder_analytics_summary()
        self.assertEqual(summary["lead_sessions_30d"], 1)
        self.assertEqual(summary["registrations_30d"], 1)
        self.assertEqual(summary["logins_7d"], 1)
        self.assertEqual(summary["active_businesses_7d"], 1)
        self.assertEqual(summary["top_modules"][0]["module"], "inventory")

    def test_event_metadata_is_explicit_not_request_body_capture(self):
        record_platform_event(
            PlatformEvent.EVENT_SUBSCRIPTION_CHANGED, user=self.user, business=self.business,
            metadata={"plan": "business_pro"},
        )
        event = PlatformEvent.objects.get(event_type=PlatformEvent.EVENT_SUBSCRIPTION_CHANGED)
        self.assertEqual(event.metadata, {"plan": "business_pro"})
    def test_signup_contacts_use_registration_metadata_and_dedupe_email(self):
        second = CustomUser.objects.create_user(
            username="second.analytics", password="safe-password-123", email="LATEST@example.com", fullname="Latest Name"
        )
        record_platform_event(
            PlatformEvent.EVENT_REGISTRATION, user=self.user, business=self.business,
            metadata={"signup_email": "latest@example.com", "signup_name": "Older Name", "business_name": "Older Store"},
        )
        record_platform_event(
            PlatformEvent.EVENT_REGISTRATION, user=second, business=self.business,
            metadata={"signup_email": "LATEST@example.com", "signup_name": "Latest Name", "business_name": "Analytics Store"},
        )
        contacts = founder_signup_contacts()
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["email"], "LATEST@example.com")
        self.assertEqual(contacts[0]["name"], "Latest Name")

    def test_founder_can_export_signup_contacts_csv(self):
        founder = CustomUser.objects.create_superuser(
            username="analytics.founder", password="safe-password-123", email="founder@example.com"
        )
        record_platform_event(
            PlatformEvent.EVENT_REGISTRATION, user=self.user, business=self.business,
            metadata={"signup_email": "owner@example.com", "signup_name": "Owner", "business_name": "Analytics Store"},
        )
        self.client.force_login(founder)
        from django.urls import reverse
        response = self.client.get(reverse("founder_mailing_list_csv"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        content = response.content.decode()
        self.assertIn("owner@example.com", content)
        self.assertIn("Analytics Store", content)
    def test_signup_snapshot_keeps_business_details_independent_of_business_fk(self):
        record_founder_signup_contact(
            business=self.business, user=self.user, email="owner@example.com", name="Owner"
        )
        state = FounderSignupContactState.objects.get(email_key="owner@example.com")
        self.assertEqual(state.business_name, "Analytics Store")
        self.assertEqual(state.business_id_snapshot, self.business.pk)
        contacts = founder_signup_contacts(include_deleted=True)
        row = next(item for item in contacts if item["email"] == "owner@example.com")
        self.assertEqual(row["business"], "Analytics Store")
        self.assertEqual(row["service"], self.business.get_vertical_display())

