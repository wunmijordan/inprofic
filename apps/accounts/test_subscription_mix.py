from datetime import timedelta
from itertools import count

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Business

from .models import (
    BusinessSubscription, CustomUser, PaidPlanTrialClaim, PlatformEvent, SubscriptionPayment, SubscriptionPlan,
)
from .subscription_services import ensure_default_plans, founder_subscription_mix

_ids = count(1)


class FounderSubscriptionMixTests(TestCase):
    def setUp(self):
        plans = ensure_default_plans()
        self.starter = plans[SubscriptionPlan.CODE_STARTER]
        self.paid_plan = plans[SubscriptionPlan.CODE_PRODUCTION]
        self.now = timezone.now()
        self.assertTrue(self.starter.is_free_forever)
        self.assertFalse(self.paid_plan.is_free_forever)

    def _sub(self, label, plan, status, **fields):
        business = Business.objects.create(name=label, slug=label.lower().replace(" ", "-"))
        return BusinessSubscription.objects.create(primary_business=business, plan=plan, status=status, **fields)

    def _payment(self, sub, purpose=SubscriptionPayment.PURPOSE_SUBSCRIPTION, status=SubscriptionPayment.STATUS_PAID):
        return SubscriptionPayment.objects.create(
            subscription=sub, plan=self.paid_plan, amount="5000.00", status=status, purpose=purpose,
            reference=f"MIX-{next(_ids)}", paid_at=self.now,
        )

    def _claim(self, sub):
        return PaidPlanTrialClaim.objects.create(
            credential_kind=PaidPlanTrialClaim.KIND_EMAIL, credential_fingerprint=f"fp-{next(_ids):060d}",
            subscription=sub,
        )

    def _build_portfolio(self):
        active, trial = BusinessSubscription.STATUS_ACTIVE, BusinessSubscription.STATUS_TRIAL
        day = timedelta(days=1)
        # Free forever, never trialled: counts as free, not part of the trial cohort.
        self._sub("Plain Free", self.starter, active)
        # Trial still running and unpaid: free today, undecided for conversion.
        self._sub("Running Trial", self.paid_plan, trial, trial_ends_at=self.now + 10 * day)
        # Trial ended, then paid: converted. trial_ends_at is retained once paid.
        converted = self._sub("Converted", self.paid_plan, active, trial_ends_at=self.now - 5 * day, paid_until=self.now + 25 * day)
        self._payment(converted)
        # Paid early, while the trial was still running: converted and already paid.
        early = self._sub("Early Payer", self.paid_plan, active, trial_ends_at=self.now + 3 * day, paid_until=self.now + 33 * day)
        self._payment(early)
        # Lapsed back to free Starter; only a claim remembers the trial.
        self._claim(self._sub("Lapsed To Free", self.starter, active))
        # Lapsed to expired; only the signup event remembers the trial.
        expired = self._sub("Lapsed Expired", self.starter, BusinessSubscription.STATUS_EXPIRED)
        PlatformEvent.objects.create(
            event_type=PlatformEvent.EVENT_SUBSCRIPTION_STARTED, business=expired.primary_business,
            metadata={"plan": "starter", "status": "trial"},
        )
        # Lapsed, but paid a payroll add-on: that is not a plan conversion.
        payroll_only = self._sub("Payroll Only", self.starter, active)
        self._claim(payroll_only)
        self._payment(payroll_only, purpose=SubscriptionPayment.PURPOSE_PAYROLL_ADDON)
        # Founder lifetime: complimentary, excluded from free, paid and conversion.
        self._sub("Founder Gift", self.paid_plan, BusinessSubscription.STATUS_FOUNDER,
                  founder_lifetime=True, trial_ends_at=self.now - day)

    def test_buckets_and_conversion_rate(self):
        self._build_portfolio()
        mix = founder_subscription_mix(BusinessSubscription.objects.select_related("plan"), now=self.now)
        self.assertEqual(mix["free"], 3)        # Plain Free, Lapsed To Free, Payroll Only
        self.assertEqual(mix["trial"], 1)
        self.assertEqual(mix["free_total"], 4)
        self.assertEqual(mix["paid"], 2)
        self.assertEqual(mix["founder"], 1)
        self.assertEqual(mix["expired"], 1)
        self.assertEqual(mix["total"], 8)
        self.assertEqual(mix["free"] + mix["trial"] + mix["paid"] + mix["founder"] + mix["expired"], mix["total"])
        self.assertEqual(mix["trials_started"], 6)
        self.assertEqual(mix["trials_converted"], 2)
        self.assertEqual(mix["trials_undecided"], 1)
        self.assertEqual(mix["trials_lapsed"], 3)
        self.assertEqual(mix["trials_decided"], 5)
        self.assertEqual(mix["conversion_rate"], 40.0)

    def test_no_concluded_trials_gives_no_rate_instead_of_zero(self):
        self._sub("Running Trial", self.paid_plan, BusinessSubscription.STATUS_TRIAL, trial_ends_at=self.now + timedelta(days=5))
        self._sub("Plain Free", self.starter, BusinessSubscription.STATUS_ACTIVE)
        mix = founder_subscription_mix(BusinessSubscription.objects.select_related("plan"), now=self.now)
        self.assertIsNone(mix["conversion_rate"])
        self.assertEqual(mix["trials_undecided"], 1)

    def test_empty_platform(self):
        mix = founder_subscription_mix([], now=self.now)
        self.assertEqual(mix["total"], 0)
        self.assertIsNone(mix["conversion_rate"])

    def test_unpaid_trial_past_its_end_date_counts_as_lapsed_before_the_sync_runs(self):
        self._sub("Overdue", self.paid_plan, BusinessSubscription.STATUS_TRIAL, trial_ends_at=self.now - timedelta(hours=1))
        mix = founder_subscription_mix(BusinessSubscription.objects.select_related("plan"), now=self.now)
        self.assertEqual(mix["trial"], 0)
        self.assertEqual(mix["expired"], 1)
        self.assertEqual(mix["trials_lapsed"], 1)
        self.assertEqual(mix["conversion_rate"], 0.0)

    def test_founder_console_shows_the_numbers_and_the_live_snapshot_matches(self):
        self._build_portfolio()
        founder = CustomUser.objects.create_superuser(username="founder", password="safe-password-123", fullname="Founder")
        self.client.force_login(founder)
        page = self.client.get(reverse("founder_subscriptions"))
        self.assertEqual(page.status_code, 200)
        mix = page.context["platform_stats"]["subscription_mix"]
        self.assertEqual((mix["free_total"], mix["paid"], mix["conversion_rate"]), (4, 2, 40.0))
        self.assertContains(page, "Free subscriptions")
        self.assertContains(page, "Paid subscriptions")
        self.assertContains(page, "40.0%")
        self.assertContains(page, "2 of 5 concluded trials paid")
        PlatformEvent.objects.create(event_type=PlatformEvent.EVENT_REGISTRATION)  # a new signup triggers a full snapshot
        snapshot = self.client.get(reverse("founder_signup_live_snapshot"), {"after": 0}).json()
        self.assertEqual(snapshot["stats"]["subscription_mix"]["conversion_rate"], 40.0)
