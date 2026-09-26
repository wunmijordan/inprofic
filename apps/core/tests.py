from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser

from .jobs import (
    SCHEDULED_COMMANDS,
    acquire_scheduled_job_lease,
    finish_scheduled_job_lease,
    run_all_jobs,
)
from .models import Business, ScheduledJobLease
from .performance import PerformanceDiagnosticMiddleware
from .audit_views import _EXTERNAL_BUSINESS_ACTIVITY_MODELS, _public_audit_details


class AuditPresentationTests(TestCase):
    def test_external_trail_contains_business_activity_not_system_operations(self):
        self.assertIn("RawMaterial", _EXTERNAL_BUSINESS_ACTIVITY_MODELS)
        self.assertIn("PurchaseOrder", _EXTERNAL_BUSINESS_ACTIVITY_MODELS)
        self.assertIn("CommerceIntake", _EXTERNAL_BUSINESS_ACTIVITY_MODELS)
        self.assertNotIn("system", _EXTERNAL_BUSINESS_ACTIVITY_MODELS)
        self.assertNotIn("Business", _EXTERNAL_BUSINESS_ACTIVITY_MODELS)
        self.assertNotIn("DeliverySettings", _EXTERNAL_BUSINESS_ACTIVITY_MODELS)

    def test_metadata_is_humanized_and_private_provider_details_are_hidden(self):
        rows = _public_audit_details({
            "previous_status": "assigned",
            "driver_id": 42,
            "configured": True,
            "provider_payload": {"secret": "not-for-the-screen"},
            "api_key": "also-private",
        })

        details = {row["label"]: row["value"] for row in rows}
        self.assertEqual(details["Previous status"], "assigned")
        self.assertEqual(details["Rider record"], "42")
        self.assertEqual(details["Configuration ready"], "Yes")
        self.assertNotIn("Provider Payload", details)
        self.assertNotIn("Api Key", details)


class ScheduledJobRegistryTests(TestCase):
    @patch("core.jobs.call_command")
    def test_registry_runs_every_command(self, call_command):
        completed = run_all_jobs()

        self.assertEqual(completed, list(SCHEDULED_COMMANDS))
        self.assertEqual(
            [call.args[0] for call in call_command.call_args_list],
            list(SCHEDULED_COMMANDS),
        )

    @override_settings(SCHEDULED_JOB_LEASE_SECONDS=600)
    def test_database_lease_blocks_overlap_until_released(self):
        first = acquire_scheduled_job_lease()
        self.assertTrue(first)
        self.assertIsNone(acquire_scheduled_job_lease())

        finish_scheduled_job_lease(first, status=ScheduledJobLease.STATUS_SUCCEEDED)
        second = acquire_scheduled_job_lease()

        self.assertTrue(second)
        self.assertNotEqual(first, second)

    @override_settings(SCHEDULED_JOB_LEASE_SECONDS=600)
    def test_expired_database_lease_can_be_reclaimed(self):
        stale = ScheduledJobLease.objects.create(
            name="scheduled-job-registry",
            owner_token="stale-token",
            lease_expires_at=timezone.now() - timedelta(seconds=1),
            last_status=ScheduledJobLease.STATUS_RUNNING,
        )

        token = acquire_scheduled_job_lease()

        self.assertTrue(token)
        stale.refresh_from_db()
        self.assertEqual(stale.owner_token, token)
        self.assertEqual(stale.last_status, ScheduledJobLease.STATUS_RUNNING)


class OperationsEndpointTests(TestCase):
    def test_health_check_is_public_and_does_not_require_a_database_query(self):
        with self.assertNumQueries(0):
            response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_health_check_does_not_resolve_an_existing_login_session(self):
        user = CustomUser.objects.create_user(
            username="health-session-user",
            password="safe-password-123",
        )
        self.client.force_login(user)

        with self.assertNumQueries(0):
            response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    @override_settings(CRON_SECRET="test-cron-secret")
    def test_scheduled_jobs_require_the_bearer_secret(self):
        response = self.client.post(reverse("run_jobs"))

        self.assertEqual(response.status_code, 403)

    @override_settings(CRON_SECRET="test-cron-secret")
    @patch("core.operations.threading.Thread")
    @patch("core.operations.acquire_scheduled_job_lease", return_value="lease-token")
    def test_scheduled_jobs_are_accepted_and_started_in_background(self, acquire_lease, thread_cls):
        response = self.client.post(
            reverse("run_jobs"),
            HTTP_AUTHORIZATION="Bearer test-cron-secret",
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "accepted")
        acquire_lease.assert_called_once_with()
        thread_cls.assert_called_once()
        thread_cls.return_value.start.assert_called_once_with()

    @override_settings(CRON_SECRET="test-cron-secret")
    @patch("core.operations.threading.Thread")
    @patch("core.operations.acquire_scheduled_job_lease", return_value=None)
    def test_duplicate_scheduled_job_trigger_does_not_overlap(self, acquire_lease, thread_cls):
        response = self.client.post(
            reverse("run_jobs"),
            HTTP_AUTHORIZATION="Bearer test-cron-secret",
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "already_running")
        thread_cls.assert_not_called()

    @patch("core.operations.close_old_connections")
    @patch("core.operations.finish_scheduled_job_lease")
    @patch("core.operations.run_all_jobs", return_value=["sync_subscriptions"])
    def test_background_worker_releases_lease_after_success(self, run_all_jobs, finish_lease, close_connections):
        from core.operations import _run_scheduled_jobs_background

        _run_scheduled_jobs_background("lease-token")

        run_all_jobs.assert_called_once_with()
        finish_lease.assert_called_once_with(
            "lease-token",
            status=ScheduledJobLease.STATUS_SUCCEEDED,
        )
        self.assertEqual(close_connections.call_count, 2)

    @override_settings(CRON_SECRET="test-cron-secret")
    @patch("core.operations.finish_scheduled_job_lease")
    @patch("core.operations.threading.Thread")
    @patch("core.operations.acquire_scheduled_job_lease", return_value="lease-token")
    def test_thread_start_failure_releases_lease(self, acquire_lease, thread_cls, finish_lease):
        thread_cls.return_value.start.side_effect = RuntimeError("thread unavailable")

        response = self.client.post(
            reverse("run_jobs"),
            HTTP_AUTHORIZATION="Bearer test-cron-secret",
        )

        self.assertEqual(response.status_code, 500)
        finish_lease.assert_called_once_with(
            "lease-token",
            status=ScheduledJobLease.STATUS_FAILED,
            error="thread unavailable",
        )

    @patch("core.operations.close_old_connections")
    @patch("core.operations.finish_scheduled_job_lease")
    @patch("core.operations.run_all_jobs", side_effect=RuntimeError("job failed"))
    def test_background_worker_records_failure_and_releases_lease(self, run_all_jobs, finish_lease, close_connections):
        from core.operations import _run_scheduled_jobs_background

        with self.assertLogs("core.operations", level="ERROR"):
            _run_scheduled_jobs_background("lease-token")

        finish_lease.assert_called_once_with(
            "lease-token",
            status=ScheduledJobLease.STATUS_FAILED,
            error="job failed",
        )
        self.assertEqual(close_connections.call_count, 2)

    @override_settings(CRON_SECRET="test-cron-secret")
    def test_web_push_retry_requires_bearer_secret(self):
        response = self.client.post(reverse("dispatch_web_push"))
        self.assertEqual(response.status_code, 403)

    @override_settings(CRON_SECRET="test-cron-secret")
    @patch("commerce.webpush.dispatch_pending_pushes", return_value={
        "configured": True, "queued": 1, "sent": 1, "failed": 0, "expired": 0,
    })
    def test_web_push_retry_uses_bounded_dispatcher(self, dispatch):
        response = self.client.post(
            reverse("dispatch_web_push"),
            HTTP_AUTHORIZATION="Bearer test-cron-secret",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sent"], 1)
        dispatch.assert_called_once_with(notice_limit=40, delivery_limit=20)



class PerformanceDiagnosticMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(
        PERF_DIAGNOSTICS=True,
        PERF_SLOW_REQUEST_MS=0,
        PERF_SERVER_TIMING=True,
        PERF_EXCLUDED_PREFIXES=("/health/", "/static/", "/media/", "/ws/"),
    )
    def test_records_aggregate_sql_timing_without_logging_request_data(self):
        def get_response(request):
            Business.objects.exists()
            return HttpResponse("ok")

        request = self.factory.get("/business/example/42/?token=do-not-log")
        request.resolver_match = SimpleNamespace(view_name="business-detail")
        middleware = PerformanceDiagnosticMiddleware(get_response)

        with self.assertLogs("inprofic.performance", level="WARNING") as captured:
            response = middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertIn("app;dur=", response.headers["Server-Timing"])
        self.assertIn("sql;dur=", response.headers["Server-Timing"])
        self.assertIn('desc="1 queries"', response.headers["Server-Timing"])
        self.assertIn("route=business-detail", captured.output[0])
        self.assertNotIn("do-not-log", captured.output[0])
        self.assertNotIn("/business/example/42/", captured.output[0])

    @override_settings(PERF_DIAGNOSTICS=True, PERF_SLOW_REQUEST_MS=0)
    def test_excluded_health_request_has_no_diagnostic_overhead_header(self):
        middleware = PerformanceDiagnosticMiddleware(lambda request: HttpResponse("ok"))
        response = middleware(self.factory.get("/health/"))

        self.assertNotIn("Server-Timing", response.headers)


class BusinessBrandContrastTests(TestCase):
    def test_light_and_dark_business_backgrounds_choose_readable_text(self):
        self.assertEqual(Business._contrast_color("#FFFFFF"), "#182433")
        self.assertEqual(Business._contrast_color("#FFF1E8"), "#182433")
        self.assertEqual(Business._contrast_color("#050733"), "#FFFFFF")
        self.assertEqual(Business._contrast_color("not-a-colour"), "#FFFFFF")


class MarketingTrustStripTests(TestCase):
    def test_strip_counts_trials_but_only_auto_shows_paid_storefront_logos(self):
        from accounts.models import BusinessSubscription, MarketingTrustSettings, SubscriptionPlan

        plan, _ = SubscriptionPlan.objects.update_or_create(code=SubscriptionPlan.CODE_PRODUCTION, defaults={"name": "Production", "monthly_price": 100})
        paid = Business.objects.create(name="Paid Bakery", slug="paid-bakery", storefront_logo="logos/paid.png")
        trial = Business.objects.create(name="Trial Bakery", slug="trial-bakery", storefront_logo="logos/trial.png")
        BusinessSubscription.objects.create(primary_business=paid, plan=plan, status=BusinessSubscription.STATUS_ACTIVE, paid_until=timezone.now() + timedelta(days=20))
        BusinessSubscription.objects.create(primary_business=trial, plan=plan, status=BusinessSubscription.STATUS_TRIAL, trial_ends_at=timezone.now() + timedelta(days=20))
        MarketingTrustSettings.objects.create(pk=1, enabled=True)

        response = self.client.get(reverse("marketing_home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["trust_business_count"], 2)
        self.assertEqual([business.pk for business in response.context["trusted_businesses"]], [paid.pk])
        self.assertContains(response, "logos/paid.png")
        self.assertNotContains(response, "logos/trial.png")


class PwaEndpointTests(TestCase):
    def test_brand_manifest_is_public_and_uses_inprofic_identity(self):
        response = self.client.get(reverse("pwa_manifest"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"].split(";")[0], "application/manifest+json")
        payload = response.json()
        self.assertEqual(payload["name"], "INPROFIC")
        self.assertEqual(payload["id"], "/pwa/inprofic")
        self.assertEqual(payload["theme_color"], "#050733")
        self.assertEqual(payload["background_color"], "#FFF1E8")
        self.assertTrue(any(icon["sizes"] == "512x512" for icon in payload["icons"]))
        self.assertTrue(all("core/pwa/icon-" in icon["src"] for icon in payload["icons"]))
        self.assertEqual({icon["purpose"] for icon in payload["icons"]}, {"any", "monochrome"})
        dark_payload = self.client.get(f'{reverse("pwa_manifest")}?theme=dark').json()
        self.assertEqual(dark_payload["background_color"], "#050733")
        self.assertTrue(any("icon-mark-on-dark-192.png" in icon["src"] for icon in dark_payload["icons"]))
        windows_payload = self.client.get(f'{reverse("pwa_manifest")}?theme=dark&platform=windows').json()
        self.assertEqual({icon["purpose"] for icon in windows_payload["icons"]}, {"any"})
        self.assertTrue(all("icon-mark-windows-" in icon["src"] for icon in windows_payload["icons"]))

    def test_tenant_manifest_uses_tenant_name_and_theme_but_inprofic_icons(self):
        business = Business.objects.create(
            name="Northwind Foods",
            slug="northwind-foods",
            background_color="#173B45",
            accent_color="#126E82",
            tagline="Kitchen control",
        )
        from accounts.models import UserBusiness
        from accounts.services import seed_business_roles

        roles = seed_business_roles(business)
        user = CustomUser.objects.create_user(username="manifest-user", password="safe-password-123")
        UserBusiness.objects.create(
            user=user,
            business=business,
            role=roles[CustomUser.ROLE_BUSINESS_ADMIN],
        )
        self.client.force_login(user)
        response = self.client.get(reverse("pwa_manifest_tenant", kwargs={"business_slug": business.slug}))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["name"], "Northwind Foods")
        self.assertEqual(payload["theme_color"], "#173B45")
        self.assertEqual(payload["background_color"], "#FFF1E8")
        self.assertEqual(payload["id"], "/pwa/tenant/northwind-foods")
        self.assertEqual({icon["purpose"] for icon in payload["icons"]}, {"any", "monochrome"})
        dark_response = self.client.get(
            f'{reverse("pwa_manifest_tenant", kwargs={"business_slug": business.slug})}?theme=dark'
        )
        self.assertEqual(dark_response.json()["background_color"], "#050733")
        self.assertTrue(any("icon-mark-on-dark-192.png" in icon["src"] for icon in dark_response.json()["icons"]))

    def test_service_worker_has_root_scope_and_does_not_cache_dynamic_html(self):
        response = self.client.get(reverse("pwa_service_worker"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        script = response.content.decode()
        self.assertIn("request.mode === 'navigate'", script)
        self.assertIn("cache: 'no-store'", script)
        self.assertIn("url.pathname.startsWith('/static/')", script)
        self.assertIn("self.addEventListener('push'", script)
        self.assertIn("showNotification", script)
        self.assertIn("icon-mark-192.png", script)
        self.assertIn("icon-mark-on-dark-180.png", script)
        self.assertIn("icon-mark-on-dark-192.png", script)
        self.assertIn("INPROFIC_THEME", script)
        self.assertIn("preferredTheme", script)
        self.assertIn("icon-mark-monochrome-192.png", script)
        self.assertIn("inprofic-wordmark-on-light.png", script)
        self.assertIn("inprofic-wordmark-on-dark.png", script)
        self.assertIn("requireInteraction: true", script)
        self.assertIn("vibrate: [320, 140, 320, 140, 520]", script)
        self.assertIn("self.addEventListener('notificationclick'", script)

    def test_tenant_launch_selects_only_an_authorized_business(self):
        from accounts.models import UserBusiness
        from accounts.services import seed_business_roles

        business = Business.objects.create(name="Launch Bakery", slug="launch-bakery")
        roles = seed_business_roles(business)
        user = CustomUser.objects.create_user(username="launch-user", password="safe-password-123")
        UserBusiness.objects.create(
            user=user,
            business=business,
            role=roles[CustomUser.ROLE_BUSINESS_ADMIN],
        )
        self.client.force_login(user)
        response = self.client.get(reverse("pwa_launch", kwargs={"business_slug": business.slug}))
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["active_business_id"], business.pk)

class TenantBackupIsolationTests(TestCase):
    def test_backup_is_explicitly_scoped_including_child_models_without_business_fk(self):
        import json
        from django.core import serializers
        from inventory.models import RawMaterial, FinishedGood, RecipeItem
        from .models import CashAccount
        from .services import tenant_backup_objects

        first = Business.objects.create(name="First Tenant", slug="first-tenant")
        second = Business.objects.create(name="Second Tenant", slug="second-tenant")
        CashAccount.raw_objects.create(business=first, name="First Cash", account_type="cash")
        CashAccount.raw_objects.create(business=second, name="Second Cash", account_type="cash")
        raw_first = RawMaterial.raw_objects.create(business=first, name="Flour A")
        raw_second = RawMaterial.raw_objects.create(business=second, name="Flour B")
        good_first = FinishedGood.raw_objects.create(business=first, name="Bread A", unit="loaf")
        good_second = FinishedGood.raw_objects.create(business=second, name="Bread B", unit="loaf")
        recipe_first = RecipeItem.objects.create(finished_good=good_first, raw_material=raw_first, qty_per_batch=1)
        RecipeItem.objects.create(finished_good=good_second, raw_material=raw_second, qty_per_batch=2)

        payload = json.loads(serializers.serialize("json", tenant_backup_objects(first)))
        business_pks = {row["pk"] for row in payload if row["model"] == "core.business"}
        account_names = {row["fields"]["name"] for row in payload if row["model"] == "core.cashaccount"}
        recipe_pks = {row["pk"] for row in payload if row["model"] == "inventory.recipeitem"}

        self.assertEqual(business_pks, {first.pk})
        self.assertEqual(account_names, {"First Cash"})
        self.assertEqual(recipe_pks, {recipe_first.pk})
