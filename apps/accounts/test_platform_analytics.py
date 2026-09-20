from django.test import TestCase

from core.models import Business
from .analytics import founder_analytics_summary, record_platform_event
from .models import CustomUser, PlatformEvent


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
