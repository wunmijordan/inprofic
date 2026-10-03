from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.db import models
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
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
from .performance import PerformanceDiagnosticMiddleware, performance_section
from .audit_views import _EXTERNAL_BUSINESS_ACTIVITY_MODELS, _public_audit_details


class CharFieldContractTests(SimpleTestCase):
    """Guard declared CharField values from exceeding their database column."""

    def test_string_defaults_and_choices_fit_declared_max_length(self):
        problems = []
        for model in apps.get_models():
            for field in model._meta.fields:
                if not isinstance(field, models.CharField) or not field.max_length:
                    continue
                if field.has_default() and isinstance(field.default, str) and len(field.default) > field.max_length:
                    problems.append(
                        f"{model._meta.label}.{field.name} default {field.default!r} "
                        f"is {len(field.default)} chars but max_length={field.max_length}"
                    )
                for value, _label in field.flatchoices:
                    if isinstance(value, str) and len(value) > field.max_length:
                        problems.append(
                            f"{model._meta.label}.{field.name} choice {value!r} "
                            f"is {len(value)} chars but max_length={field.max_length}"
                        )
        self.assertFalse(problems, "\n".join(problems))


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
        PERF_SLOW_QUERY_MS=0,
        PERF_SERVER_TIMING=True,
        PERF_EXCLUDED_PREFIXES=("/health/", "/static/", "/media/", "/ws/"),
    )
    def test_records_aggregate_sql_timing_without_logging_request_data(self):
        def get_response(request):
            with performance_section(request, "test.section"):
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
        self.assertIn("sqlmax;dur=", response.headers["Server-Timing"])
        self.assertIn('desc="1 queries"', response.headers["Server-Timing"])
        self.assertIn("route=business-detail", captured.output[0])
        self.assertIn("max_sql_ms=", captured.output[0])
        self.assertIn("non_sql_ms=", captured.output[0])
        self.assertIn("slow_sql_queries=1", captured.output[0])
        self.assertIn("sections=test.section:", captured.output[0])
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
        self.assertEqual(payload["theme_color"], "#FFF1E8")
        self.assertEqual(payload["background_color"], "#FFF1E8")
        self.assertTrue(any(icon["sizes"] == "512x512" for icon in payload["icons"]))
        self.assertTrue(all("core/pwa/icon-" in icon["src"] for icon in payload["icons"]))
        self.assertEqual({icon["purpose"] for icon in payload["icons"]}, {"any", "monochrome"})
        dark_payload = self.client.get(f'{reverse("pwa_manifest")}?theme=dark').json()
        self.assertEqual(dark_payload["background_color"], "#050733")
        self.assertEqual(dark_payload["theme_color"], "#050733")
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
        # Launch surfaces are neutral and theme-matched, never the tenant brand.
        self.assertEqual(payload["theme_color"], "#FFF1E8")
        self.assertEqual(payload["background_color"], "#FFF1E8")
        self.assertEqual(payload["id"], "/pwa/tenant/northwind-foods")
        self.assertEqual({icon["purpose"] for icon in payload["icons"]}, {"any", "monochrome"})
        dark_response = self.client.get(
            f'{reverse("pwa_manifest_tenant", kwargs={"business_slug": business.slug})}?theme=dark'
        )
        self.assertEqual(dark_response.json()["background_color"], "#050733")
        self.assertEqual(dark_response.json()["theme_color"], "#050733")
        self.assertTrue(any("icon-mark-on-dark-192.png" in icon["src"] for icon in dark_response.json()["icons"]))

    def test_manifest_theme_cookie_overrides_stale_query_and_keeps_pair_consistent(self):
        self.client.cookies["inprofic_theme"] = "dark"
        stale = self.client.get(f'{reverse("pwa_manifest")}?theme=light').json()
        self.assertEqual(stale["background_color"], "#050733")
        self.assertEqual(stale["theme_color"], "#050733")
        self.assertTrue(all("on-dark" in i["src"] for i in stale["icons"] if i["purpose"] == "any"))
        win = self.client.get(f'{reverse("pwa_manifest")}?platform=windows').json()
        self.assertEqual(win["background_color"], "#050733")
        self.assertTrue(all("icon-mark-windows-" in i["src"] for i in win["icons"]))
        self.client.cookies["inprofic_theme"] = "light"
        light = self.client.get(f'{reverse("pwa_manifest")}?theme=dark').json()
        self.assertEqual(light["background_color"], "#FFF1E8")
        response = self.client.get(reverse("pwa_manifest"))
        self.assertIn("Cookie", response["Vary"])
        self.assertIn("private", response["Cache-Control"])

    def test_favicon_is_swapped_by_resolved_theme(self):
        from django.template.loader import render_to_string

        html = render_to_string("pwa/head.html")
        self.assertIn('id="inprofic-favicon"', html)
        self.assertIn("inprofic-favicon-on-dark.png", html)
        self.assertIn("favicon.href = dark", html)

    def test_launch_overlay_follows_resolved_app_theme_not_only_the_os(self):
        from django.template.loader import render_to_string

        html = render_to_string("pwa/head.html")
        self.assertIn('data-launch-theme="dark"', html)
        self.assertIn("inprofic-wordmark-on-dark.png", html)
        self.assertIn("localStorage.getItem('inprofic-theme')", html)
        self.assertNotIn("@media(prefers-color-scheme:dark)", html)

    def test_offline_page_ships_both_wordmarks_for_light_and_dark(self):
        from django.template.loader import render_to_string

        html = render_to_string("pwa/offline.html")
        self.assertIn("inprofic-wordmark-on-light.png", html)
        self.assertIn("inprofic-wordmark-on-dark.png", html)
        self.assertIn('html[data-theme="dark"]', html)

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


class DashboardStockTickerTests(TestCase):
    """Daily balances: opening balance, live change, and the two finished-good balances."""

    def setUp(self):
        from decimal import Decimal
        from inventory.models import FinishedGood, RawMaterial, StockMovement

        self.Decimal = Decimal
        self.StockMovement = StockMovement
        self.business = Business.objects.create(name="Ticker Bakery", slug="ticker-bakery")
        self.user = CustomUser.objects.create_superuser(username="ticker-admin", password="safe-password-123", fullname="Admin")
        self.flour = RawMaterial.raw_objects.create(
            business=self.business, name="Flour", category=RawMaterial.CATEGORY_INGREDIENT,
            purchase_unit="bag", package_qty=Decimal("50"), package_unit="kg", usage_unit="kg",
            usage_conversion_factor=Decimal("1"), stock=Decimal("100"), reorder_level=Decimal("5"), cost_per_unit=Decimal("10"),
        )
        self.empty = RawMaterial.raw_objects.create(
            business=self.business, name="Empty", category=RawMaterial.CATEGORY_INGREDIENT,
            purchase_unit="bag", package_qty=Decimal("1"), package_unit="kg", usage_unit="kg",
            usage_conversion_factor=Decimal("1"), stock=Decimal("0"), reorder_level=Decimal("1"), cost_per_unit=Decimal("10"),
        )
        self.bread = FinishedGood.raw_objects.create(
            business=self.business, name="Bread", unit="loaf", units_per_batch=Decimal("10"),
            stock=Decimal("40"), reorder_level=Decimal("2"), selling_price=Decimal("500"),
        )

    def build(self):
        from .context import set_current_business
        from .stock_ticker import build_stock_ticker

        set_current_business(self.business)
        try:
            return build_stock_ticker(timezone.localdate())
        finally:
            set_current_business(None)

    def test_opening_balance_is_current_minus_todays_net_movement(self):
        D = self.Decimal
        self.StockMovement.objects.create(
            business=self.business, raw_material=self.flour, movement_type="raw_consumption",
            quantity=D("-12"), balance_after=D("100"),
        )
        self.StockMovement.objects.create(
            business=self.business, raw_material=self.flour, movement_type="raw_purchase",
            quantity=D("5"), balance_after=D("105"),
        )
        data = self.build()
        flour = next(r for r in data["raw"] if r["name"] == "Flour")
        self.assertEqual((flour["balance"], flour["opening"], flour["change"]), (100.0, 107.0, -7.0))
        self.assertNotIn("Empty", [r["name"] for r in data["raw"]])  # nothing available, no activity

    def test_item_with_no_activity_opens_at_its_current_balance(self):
        flour = next(r for r in self.build()["raw"] if r["name"] == "Flour")
        self.assertEqual((flour["balance"], flour["opening"], flour["change"]), (100.0, 100.0, 0.0))

    def test_finished_good_without_market_stock_has_a_single_store_balance(self):
        bread = next(r for r in self.build()["finished"] if r["name"] == "Bread")
        self.assertIsNone(bread["market"])
        self.assertEqual(bread["store"]["balance"], 40.0)

    def test_endpoint_requires_login_and_returns_json_without_caching(self):
        self.assertEqual(self.client.get(reverse("dashboard_stock_ticker")).status_code, 302)
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard_stock_ticker"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(set(response.json()), {"raw", "finished", "date"})

    def test_dashboard_embeds_initial_ticker_data_and_cards(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="stock-ticker-data"')
        self.assertContains(response, 'data-stock-ticker="raw"')
        self.assertContains(response, 'data-stock-ticker="finished"')
        self.assertContains(response, reverse("dashboard_stock_ticker"))
