import json
import time
from decimal import Decimal

from django.test import RequestFactory, TestCase
from django.test import override_settings
from django.db import connection
from django.core.management import call_command
from django.core import mail
from django.core.exceptions import ValidationError
from django.template.loader import get_template
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Business, CashAccount, FinancialTransaction
from .models import (
    BusinessFeatureAccess, BusinessModuleAccess, BusinessSubscription, CustomUser,
    RoleModulePermission, SubscriptionPayment, SubscriptionPaymentSettings, SubscriptionPolicySettings, FounderTrialGrant,
    SubscriptionPlanModule, SubscriptionPromotion, MarketingPromoCampaign, SubscriptionService, UserBusiness, UserModulePermission, PlatformEvent, FounderSignupContactState,
    PayrollCalculationRule, PayrollRecurringAdjustment, PayrollRun, PayrollStaffProfile,
)
from .payroll import (
    calculate_payslip,
    issue_bulk_payroll_run,
    recurring_adjustment_totals,
    sync_staff_recurring_totals,
)
from .services import business_has_module, can_use_commerce_storefront, is_live_tester, seed_business_roles, user_has_permission
from .subscription_services import (
    apply_subscription_entitlements,
    apply_subscriptions_entitlements,
    build_plan_feature_matrix,
    create_payment_request,
    ensure_default_plans,
    grant_founder_lifetime,
    grant_founder_trial_extension,
    mark_payment_paid,
    payment_amount,
    payment_is_locked,
    start_trial_for_business,
)


class PayrollCalculationTests(TestCase):
    def test_tax_pension_and_employer_contributions_are_calculated_separately(self):
        business = Business.objects.create(name="Payroll Business", slug="payroll-business")
        PayrollCalculationRule.objects.create(
            business=business, name="Transport allowance",
            category=PayrollCalculationRule.CATEGORY_ALLOWANCE,
            effect=PayrollCalculationRule.EFFECT_EARNING,
            method=PayrollCalculationRule.METHOD_FIXED,
            basis=PayrollCalculationRule.BASIS_BASE_PAY,
            fixed_amount=Decimal("5000.00"), sort_order=10,
        )
        PayrollCalculationRule.objects.create(
            business=business, name="PAYE",
            category=PayrollCalculationRule.CATEGORY_TAX,
            effect=PayrollCalculationRule.EFFECT_EMPLOYEE_DEDUCTION,
            method=PayrollCalculationRule.METHOD_PERCENTAGE,
            basis=PayrollCalculationRule.BASIS_GROSS_PAY,
            rate=Decimal("10.0000"), threshold_amount=Decimal("50000.00"), sort_order=20,
            statutory=True,
        )
        PayrollCalculationRule.objects.create(
            business=business, name="Employee pension",
            category=PayrollCalculationRule.CATEGORY_PENSION,
            effect=PayrollCalculationRule.EFFECT_EMPLOYEE_DEDUCTION,
            method=PayrollCalculationRule.METHOD_PERCENTAGE,
            basis=PayrollCalculationRule.BASIS_BASE_PAY,
            rate=Decimal("8.0000"), sort_order=30, statutory=True,
        )
        PayrollCalculationRule.objects.create(
            business=business, name="Employer pension",
            category=PayrollCalculationRule.CATEGORY_PENSION,
            effect=PayrollCalculationRule.EFFECT_EMPLOYER_CONTRIBUTION,
            method=PayrollCalculationRule.METHOD_PERCENTAGE,
            basis=PayrollCalculationRule.BASIS_BASE_PAY,
            rate=Decimal("10.0000"), sort_order=40, statutory=True,
        )

        result = calculate_payslip(
            business=business,
            base_pay=Decimal("100000.00"),
            manual_allowances=Decimal("10000.00"),
            manual_deductions=Decimal("0.00"),
        )

        self.assertEqual(result["gross_pay"], Decimal("115000.00"))
        self.assertEqual(result["allowances"], Decimal("15000.00"))
        self.assertEqual(result["deductions"], Decimal("14500.00"))
        self.assertEqual(result["net_pay"], Decimal("100500.00"))
        self.assertEqual(result["employer_contributions"], Decimal("10000.00"))
        self.assertEqual(result["employer_cost"], Decimal("125000.00"))

    def test_multiple_named_recurring_items_are_totalled_without_losing_detail(self):
        business = Business.objects.create(name="Recurring Payroll", slug="recurring-payroll")
        staff = PayrollStaffProfile.objects.create(
            business=business, full_name="Ada Staff", base_pay=Decimal("100000.00")
        )
        PayrollRecurringAdjustment.objects.create(
            staff=staff, kind=PayrollRecurringAdjustment.KIND_ALLOWANCE,
            name="Housing", amount=Decimal("10000.00"),
        )
        PayrollRecurringAdjustment.objects.create(
            staff=staff, kind=PayrollRecurringAdjustment.KIND_ALLOWANCE,
            name="Transport", amount=Decimal("5000.00"),
        )
        PayrollRecurringAdjustment.objects.create(
            staff=staff, kind=PayrollRecurringAdjustment.KIND_DEDUCTION,
            name="Cooperative", amount=Decimal("2000.00"),
        )

        allowances, deductions = sync_staff_recurring_totals(staff)
        staff.refresh_from_db()
        self.assertEqual(allowances, Decimal("15000.00"))
        self.assertEqual(deductions, Decimal("2000.00"))
        self.assertEqual(staff.recurring_allowances, Decimal("15000.00"))
        self.assertEqual(staff.recurring_deductions, Decimal("2000.00"))
        self.assertEqual(staff.recurring_adjustments.count(), 3)

        result = calculate_payslip(
            business=business, base_pay=staff.base_pay,
            recurring_allowances=allowances, recurring_deductions=deductions,
            manual_allowances=Decimal("1000.00"), manual_deductions=Decimal("500.00"),
        )
        self.assertEqual(result["gross_pay"], Decimal("116000.00"))
        self.assertEqual(result["allowances"], Decimal("16000.00"))
        self.assertEqual(result["deductions"], Decimal("2500.00"))
        self.assertEqual(result["net_pay"], Decimal("113500.00"))


class PayrollFinanceIntegrationTests(TestCase):
    def test_bulk_payroll_posts_all_payslips_and_account_outflows_atomically(self):
        business = Business.objects.create(name="Finance Payroll", slug="finance-payroll")
        user = CustomUser.objects.create_user(
            username="payroll-admin",
            fullname="Payroll Admin",
            password="test-password",
        )
        staff_a = PayrollStaffProfile.objects.create(
            business=business,
            full_name="Ada One",
            base_pay=Decimal("100000.00"),
        )
        staff_b = PayrollStaffProfile.objects.create(
            business=business,
            full_name="Bola Two",
            base_pay=Decimal("80000.00"),
        )
        account_a = CashAccount.objects.create(
            business=business,
            name="Payroll Bank A",
            account_type="bank",
        )
        account_b = CashAccount.objects.create(
            business=business,
            name="Payroll Bank B",
            account_type="bank",
        )

        payroll_run, payslips = issue_bulk_payroll_run(
            business=business,
            user=user,
            staff_members=[staff_a, staff_b],
            period_start=timezone.localdate().replace(day=1),
            period_end=timezone.localdate(),
            pay_date=timezone.localdate(),
            funding=[
                (account_a, Decimal("90000.00")),
                (account_b, Decimal("90000.00")),
            ],
        )

        self.assertEqual(payroll_run.staff_count, 2)
        self.assertEqual(payroll_run.total_net_pay, Decimal("180000.00"))
        self.assertEqual(len(payslips), 2)
        self.assertTrue(all(row.payroll_run_id == payroll_run.pk for row in payslips))
        self.assertEqual(payroll_run.funding_lines.count(), 2)
        transactions = FinancialTransaction.objects.filter(
            business=business,
            category="Payroll / Wages",
            reference=f"PAYRUN-{payroll_run.pk}",
        )
        self.assertEqual(transactions.count(), 2)
        self.assertEqual(
            sum((row.amount for row in transactions), Decimal("0.00")),
            Decimal("180000.00"),
        )
        self.assertTrue(
            all(
                row.transaction_type == FinancialTransaction.OUTFLOW
                for row in transactions
            )
        )

        from core.views import _cash_payroll

        self.assertEqual(
            _cash_payroll(timezone.localdate(), timezone.localdate()),
            Decimal("180000.00"),
        )

    def test_bulk_payroll_rejects_finance_allocation_that_does_not_match_net_wages(self):
        business = Business.objects.create(name="Mismatch Payroll", slug="mismatch-payroll")
        user = CustomUser.objects.create_user(
            username="mismatch-admin",
            fullname="Mismatch Admin",
            password="test-password",
        )
        staff = PayrollStaffProfile.objects.create(
            business=business,
            full_name="Mismatch Staff",
            base_pay=Decimal("50000.00"),
        )
        account = CashAccount.objects.create(
            business=business,
            name="Main Bank",
            account_type="bank",
        )

        with self.assertRaisesMessage(ValidationError, "allocation must match"):
            issue_bulk_payroll_run(
                business=business,
                user=user,
                staff_members=[staff],
                period_start=timezone.localdate().replace(day=1),
                period_end=timezone.localdate(),
                pay_date=timezone.localdate(),
                funding=[(account, Decimal("40000.00"))],
            )

        self.assertFalse(PayrollRun.objects.filter(business=business).exists())
        self.assertFalse(FinancialTransaction.objects.filter(business=business).exists())


class TenantSignupTests(TestCase):
    def test_public_home_is_marketing_and_authenticated_home_remembers_workspace(self):
        response = self.client.get(reverse("marketing_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "From stock to sale, in one place", html=False)
        self.assertContains(response, "Sign in")

    def test_marketing_uses_founder_configured_trial_length(self):
        policy = SubscriptionPolicySettings.load()
        policy.general_trial_days = 17
        policy.save(update_fields=["general_trial_days", "updated_at"])
        ensure_default_plans()

        response = self.client.get(reverse("marketing_home"))

        self.assertContains(response, "17-day trial")
        self.assertNotContains(response, "30-day trial")

    def test_marketing_shows_one_plan_container_payroll_addon_badge(self):
        ensure_default_plans()
        response = self.client.get(reverse("marketing_home"))
        self.assertContains(response, "Payroll", count=1)
        self.assertContains(response, "payroll-offer-sticker", count=1)
        self.assertContains(response, "plans-flow-section", count=1)

    def test_payroll_adjustment_partial_is_packaged_and_resolvable(self):
        template = get_template("accounts/_payroll_adjustment_formset.html")
        self.assertEqual(template.template.name, "accounts/_payroll_adjustment_formset.html")

    def test_marketing_plan_prices_include_thousands_separators(self):
        plans = ensure_default_plans()
        plan = plans["production"]
        plan.monthly_price = Decimal("12345.67")
        plan.save(update_fields=["monthly_price"])

        response = self.client.get(reverse("marketing_home"))

        self.assertContains(response, "₦12,345.67")

    def test_marketing_page_shows_active_promotion_without_replacing_base_price(self):
        from django.utils import timezone

        plans = ensure_default_plans()
        plan = plans["production"]
        plan.monthly_price = Decimal("10000.00")
        plan.save(update_fields=["monthly_price"])
        SubscriptionPromotion.objects.create(
            plan=plan,
            reason="Launch Promo",
            discount_type=SubscriptionPromotion.DISCOUNT_PERCENT,
            discount_value=Decimal("25.00"),
            starts_at=timezone.now() - timezone.timedelta(minutes=1),
            ends_at=timezone.now() + timezone.timedelta(days=7),
        )

        response = self.client.get(reverse("marketing_home"))

        self.assertContains(response, "Launch Promo")
        self.assertContains(response, "₦10,000.00")
        self.assertContains(response, "₦7,500.00")
        plan.refresh_from_db()
        self.assertEqual(plan.monthly_price, Decimal("10000.00"))

    def test_marketing_page_renders_founder_campaign_only_while_linked_promo_is_live(self):
        from django.utils import timezone
        plans = ensure_default_plans()
        plan = plans["business_pro"]
        promo = SubscriptionPromotion.objects.create(
            plan=plan, reason="Launch Window", discount_type=SubscriptionPromotion.DISCOUNT_PERCENT,
            discount_value=Decimal("20"), starts_at=timezone.now() - timezone.timedelta(minutes=1),
            ends_at=timezone.now() + timezone.timedelta(days=3),
        )
        MarketingPromoCampaign.objects.create(
            name="Homepage launch", promotion=promo,
            content_html='<h2 style="font-family:Fraunces">Move faster with one workspace.</h2>',
            animation_style=MarketingPromoCampaign.ANIMATION_KINETIC,
            theme=MarketingPromoCampaign.THEME_MIDNIGHT,
        )
        response = self.client.get(reverse("marketing_home"))
        self.assertContains(response, "Move faster with one workspace.")
        self.assertContains(response, 'href="#plans"')
        promo.active = False
        promo.save(update_fields=["active"])
        response = self.client.get(reverse("marketing_home"))
        self.assertNotContains(response, "Move faster with one workspace.")

    def test_marketing_campaign_sanitises_copy_and_limits_font_families(self):
        from django.utils import timezone
        plans = ensure_default_plans()
        promo = SubscriptionPromotion.objects.create(
            plan=plans["starter"], reason="Safe creative", discount_type=SubscriptionPromotion.DISCOUNT_PERCENT,
            discount_value=Decimal("10"), starts_at=timezone.now() - timezone.timedelta(minutes=1),
            ends_at=timezone.now() + timezone.timedelta(days=1),
        )
        campaign = MarketingPromoCampaign.objects.create(
            name="Safe", promotion=promo,
            content_html='<script>alert(1)</script><p style="font-family:Comic Sans MS;color:#d14900" onclick="bad()">Hello</p><span style="font-family:IBM Plex Mono">Code</span>',
        )
        self.assertNotIn("<script", campaign.content_html)
        self.assertNotIn("onclick", campaign.content_html)
        self.assertNotIn("Comic Sans", campaign.content_html)
        self.assertIn("IBM Plex Mono", campaign.content_html)

    def test_signup_provisions_business_admin_and_free_starter(self):
        response = self.client.post(reverse("signup"), {
            "business_name": "Plate & Pantry",
            "vertical": Business.VERTICAL_RESTAURANT,
            "fullname": "Ada Admin",
            "username": "ada.admin",
            "email": "ada@example.com",
            "phone": "",
            "password1": "Zx!92-long-safe-passphrase",
            "password2": "Zx!92-long-safe-passphrase",
        })

        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        business = Business.objects.get(name="Plate & Pantry")
        user = CustomUser.objects.get(username="ada.admin")
        membership = UserBusiness.objects.get(user=user, business=business)
        self.assertEqual(business.vertical, Business.VERTICAL_RESTAURANT)
        self.assertEqual(membership.role.key, CustomUser.ROLE_BUSINESS_ADMIN)
        self.assertEqual(business.module_access.filter(enabled=True).count(), 6)
        subscription = BusinessSubscription.objects.get(primary_business=business)
        self.assertEqual(subscription.plan.code, "starter")
        self.assertEqual(subscription.status, BusinessSubscription.STATUS_ACTIVE)
        self.assertIsNone(subscription.trial_ends_at)
        self.assertEqual(subscription.plan.monthly_price, Decimal("0.00"))
        self.assertEqual(subscription.plan.user_limit, 1)
        self.assertEqual(subscription.plan.additional_service_limit, 0)
        self.assertFalse(business.module_access.get(module="commerce").enabled)
        self.assertEqual(self.client.session["active_business_id"], business.pk)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="INPROFIC <welcome@example.com>",
        INPROFIC_SUPPORT_EMAIL="support@example.com",
    )
    def test_signup_sends_branded_welcome_email_and_records_contact(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("signup"), {
                "business_name": "Welcome Bakery",
                "vertical": Business.VERTICAL_RESTAURANT,
                "fullname": "Welcome Admin",
                "username": "welcome.admin",
                "email": "welcome@example.com",
                "phone": "",
                "password1": "Zx!92-long-safe-passphrase",
                "password2": "Zx!92-long-safe-passphrase",
            })

        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["welcome@example.com"])
        self.assertIn("Welcome to INPROFIC", message.subject)
        self.assertIn("Welcome Bakery", message.body)
        html = next(content for content, mimetype in message.alternatives if mimetype == "text/html")
        self.assertIn("#050733", html)
        self.assertIn("#d14900", html)
        self.assertIn("Open Welcome Bakery", html)
        event = PlatformEvent.objects.get(event_type=PlatformEvent.EVENT_REGISTRATION, user__username="welcome.admin")
        self.assertEqual(event.metadata["signup_email"], "welcome@example.com")
        self.assertEqual(event.metadata["business_name"], "Welcome Bakery")

    def test_signup_uses_a_unique_business_slug(self):
        Business.objects.create(name="Plate and Pantry", slug="plate-and-pantry")
        self.client.post(reverse("signup"), {
            "business_name": "Plate and Pantry",
            "vertical": Business.VERTICAL_GENERAL,
            "fullname": "Second Admin",
            "username": "second.admin",
            "email": "second@example.com",
            "password1": "Zx!92-long-safe-passphrase",
            "password2": "Zx!92-long-safe-passphrase",
        })
        self.assertTrue(Business.objects.filter(slug="plate-and-pantry-2").exists())


class TenantRoutingTests(TestCase):
    def setUp(self):
        self.alpha = Business.objects.create(name="Alpha", slug="alpha")
        self.beta = Business.objects.create(name="Beta", slug="beta")
        alpha_roles = seed_business_roles(self.alpha)
        beta_roles = seed_business_roles(self.beta)
        self.user = CustomUser.objects.create_user(
            username="tenant.user", password="safe-password-123", fullname="Tenant User"
        )
        UserBusiness.objects.create(
            user=self.user, business=self.alpha,
            role=alpha_roles[CustomUser.ROLE_BUSINESS_ADMIN],
        )
        UserBusiness.objects.create(
            user=self.user, business=self.beta,
            role=beta_roles[CustomUser.ROLE_BUSINESS_ADMIN],
        )
        self.client.force_login(self.user)

    def test_first_active_membership_is_selected_then_can_be_switched(self):
        response = self.client.get(reverse("business_settings"))
        self.assertEqual(response.context["biz"], self.alpha)

        response = self.client.post(reverse("switch_business"), {
            "business_id": self.beta.pk,
            "next": reverse("business_settings"),
        })
        self.assertRedirects(response, reverse("business_settings"), fetch_redirect_response=False)
        response = self.client.get(reverse("business_settings"))
        self.assertEqual(response.context["biz"], self.beta)

    def test_authenticated_root_continues_to_dashboard(self):
        response = self.client.get(reverse("marketing_home"))
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)

    def test_authenticated_user_can_open_marketing_page_from_navigation(self):
        response = self.client.get(f'{reverse("marketing_home")}?view=marketing')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Go to your workspace")

    @override_settings(AUTHENTICATED_IDLE_TIMEOUT_SECONDS=60)
    def test_idle_authenticated_root_requires_sign_in_instead_of_showing_marketing(self):
        session = self.client.session
        session["storetrack_last_activity"] = int(time.time()) - 120
        session.save()
        response = self.client.get(reverse("marketing_home"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('dashboard')}",
            fetch_redirect_response=False,
        )

    def test_user_cannot_switch_to_business_without_membership(self):
        outsider = Business.objects.create(name="Outsider", slug="outsider")
        response = self.client.post(reverse("switch_business"), {"business_id": outsider.pk})
        self.assertEqual(response.status_code, 403)
        self.assertNotEqual(self.client.session.get("active_business_id"), outsider.pk)

    def test_business_admin_cannot_edit_another_tenants_user_by_id(self):
        outsider = Business.objects.create(name="Outsider", slug="outsider")
        outsider_roles = seed_business_roles(outsider)
        outsider_user = CustomUser.objects.create_user(
            username="outside.user", password="safe-password-123", fullname="Outside User"
        )
        UserBusiness.objects.create(
            user=outsider_user, business=outsider,
            role=outsider_roles[CustomUser.ROLE_MANAGER],
        )
        response = self.client.get(reverse("user_edit", args=[outsider_user.pk]))
        self.assertEqual(response.status_code, 404)

    def test_switch_rejects_external_next_url(self):
        response = self.client.post(reverse("switch_business"), {
            "business_id": self.beta.pk,
            "next": "https://example.invalid/phishing",
        })
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)

    def test_recent_activity_does_not_rewrite_database_session(self):
        session = self.client.session
        session["active_business_id"] = self.alpha.pk
        session["storetrack_last_activity"] = int(time.time())
        session.save()

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("reports"))

        self.assertEqual(response.status_code, 200)
        session_updates = [
            query["sql"] for query in queries
            if "UPDATE" in query["sql"].upper() and "DJANGO_SESSION" in query["sql"].upper()
        ]
        self.assertEqual(session_updates, [])

    def test_navigation_permissions_use_a_bounded_number_of_queries(self):
        session = self.client.session
        session["active_business_id"] = self.alpha.pk
        session["storetrack_last_activity"] = int(time.time())
        session.save()

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("reports"))

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 14)

    def test_empty_dashboard_avoids_per_chart_point_queries(self):
        session = self.client.session
        session["active_business_id"] = self.alpha.pk
        session["storetrack_last_activity"] = int(time.time())
        session.save()

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 85)


    def test_new_membership_tour_can_be_completed_and_replayed(self):
        membership = UserBusiness.objects.get(user=self.user, business=self.alpha)
        membership.onboarding_tour_version = 0
        membership.save(update_fields=["onboarding_tour_version"])
        session = self.client.session
        session["active_business_id"] = self.alpha.pk
        session.save()

        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, 'id="ip-onboarding-tour"')
        self.assertContains(response, "Do not show again")
        self.assertContains(response, 'id="ip-tour-media"')
        self.assertContains(response, "Try this next")
        self.assertContains(response, "const tourMedia = {")
        self.assertContains(response, "built-in animated art ALWAYS stays above")
        self.assertContains(response, "const openingDelay = 2400")
        self.assertContains(response, "if (forced) openTour()")

        complete = self.client.post(reverse("onboarding_tour_complete"))
        self.assertEqual(complete.status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.onboarding_tour_version, 1)
        self.assertNotContains(self.client.get(reverse("dashboard")), 'id="ip-onboarding-tour"')
        replay = self.client.get(f'{reverse("dashboard")}?tour=1')
        self.assertContains(replay, 'id="ip-onboarding-tour"')
        self.assertContains(replay, 'data-force="1"')


class BusinessSettingsAccessTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Bakery", slug="bakery")
        roles = seed_business_roles(self.business)
        self.admin = CustomUser.objects.create_user(
            username="admin", password="safe-password-123", fullname="Business Admin"
        )
        self.manager = CustomUser.objects.create_user(
            username="manager", password="safe-password-123", fullname="Manager"
        )
        UserBusiness.objects.create(
            user=self.admin, business=self.business,
            role=roles[CustomUser.ROLE_BUSINESS_ADMIN],
        )
        UserBusiness.objects.create(
            user=self.manager, business=self.business,
            role=roles[CustomUser.ROLE_MANAGER],
        )

    def test_business_admin_can_update_preferences_without_changing_slug(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("business_settings"), {
            "name": "New Name",
            "slug": "bakery",
            "vertical": Business.VERTICAL_RESTAURANT,
            "currency_symbol": "$",
            "background_color": "#173B45",
            "accent_color": "#126E82",
            "tagline": "Kitchen control",
            "restaurant_table_service": "on",
        })
        self.assertRedirects(response, reverse("business_settings"), fetch_redirect_response=False)
        self.business.refresh_from_db()
        self.assertEqual(self.business.name, "New Name")
        self.assertEqual(self.business.slug, "bakery")
        self.assertEqual(self.business.accent_color, "#126E82")
        self.assertEqual(self.business.background_color, "#173B45")
        self.assertEqual(self.business.vertical, Business.VERTICAL_RESTAURANT)

    def test_business_admin_can_choose_a_custom_public_slug(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("business_settings"), {
            "name": self.business.name,
            "slug": "theoven",
            "vertical": self.business.vertical,
            "currency_symbol": self.business.currency_symbol,
            "background_color": self.business.background_color,
            "accent_color": self.business.accent_color,
            "tagline": self.business.tagline,
            "restaurant_table_service": "on",
        })
        self.assertRedirects(response, reverse("business_settings"), fetch_redirect_response=False)
        self.business.refresh_from_db()
        self.assertEqual(self.business.slug, "theoven")

    def test_non_admin_cannot_open_business_preferences(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("business_settings")).status_code, 403)

    def test_business_module_entitlement_overrides_role_permission(self):
        BusinessModuleAccess.objects.create(
            business=self.business, module="dashboard", enabled=False,
            source=BusinessModuleAccess.SOURCE_PLAN,
        )
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("business_settings")).status_code, 200)

    def test_user_without_membership_cannot_reach_tenant_data(self):
        user = CustomUser.objects.create_user(
            username="orphan", password="safe-password-123", fullname="Orphan User"
        )
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "No active business access", status_code=403)


class SubscriptionEntitlementTests(TestCase):
    def setUp(self):
        from django.utils import timezone
        self.timezone = timezone
        self.business = Business.objects.create(name="Plan Bakery", slug="plan-bakery")
        self.plans = ensure_default_plans()

    def _subscribe(self, code):
        plan = self.plans[code]
        subscription = BusinessSubscription.objects.create(
            primary_business=self.business, plan=plan, status=BusinessSubscription.STATUS_TRIAL,
            trial_ends_at=self.timezone.now() + self.timezone.timedelta(days=30),
        )
        SubscriptionService.objects.create(subscription=subscription, business=self.business, is_primary=True)
        apply_subscription_entitlements(subscription)
        return subscription

    def test_starter_matrix_keeps_commerce_production_and_finance_disabled(self):
        subscription = self._subscribe("starter")
        self.assertTrue(business_has_module(self.business, "inventory"))
        self.assertTrue(business_has_module(self.business, "sales"))
        self.assertFalse(business_has_module(self.business, "procurement"))
        self.assertFalse(business_has_module(self.business, "production"))
        self.assertFalse(business_has_module(self.business, "finance"))
        self.assertFalse(business_has_module(self.business, "commerce"))
        self.assertFalse(BusinessFeatureAccess.objects.get(business=self.business, feature="reports_full").enabled)
        self.assertEqual(subscription.plan.code, "starter")

    def test_business_pro_enables_commerce(self):
        self._subscribe("business_pro")
        self.assertTrue(business_has_module(self.business, "commerce"))

    def test_feature_matrix_uses_icons_for_binary_states_but_keeps_capacity_and_partial_text(self):
        plans = [self.plans["starter"], self.plans["production"], self.plans["business_pro"]]
        matrix = build_plan_feature_matrix(plans)
        rows = {row["label"]: row for row in matrix}
        self.assertTrue(all(value["state"] == "text" for value in rows["Users"]["values"]))
        self.assertEqual(rows["Procurement"]["values"][0]["state"], "off")
        self.assertEqual(rows["Inventory"]["values"][0]["state"], "included")
        self.assertEqual(rows["Reports"]["values"][0]["state"], "partial")
        self.assertEqual(rows["Reports"]["values"][0]["text"], "Basic")

    def test_bulk_entitlement_refresh_has_bounded_queries(self):
        second_business = Business.objects.create(name="Plan Retail", slug="plan-retail")
        subscriptions = []
        for business in (self.business, second_business):
            subscription = BusinessSubscription.objects.create(
                primary_business=business,
                plan=self.plans["production"],
                status=BusinessSubscription.STATUS_TRIAL,
                trial_ends_at=self.timezone.now() + self.timezone.timedelta(days=30),
            )
            SubscriptionService.objects.create(
                subscription=subscription, business=business, is_primary=True,
            )
            subscriptions.append(subscription)

        with CaptureQueriesContext(connection) as queries:
            apply_subscriptions_entitlements(subscriptions)

        self.assertLessEqual(len(queries), 12)
        self.assertTrue(BusinessModuleAccess.objects.filter(
            business=second_business, module="production", enabled=True,
        ).exists())

    def test_expired_subscription_keeps_only_dashboard_module_recovery(self):
        subscription = self._subscribe("production")
        subscription.trial_ends_at = self.timezone.now() - self.timezone.timedelta(seconds=1)
        subscription.save(update_fields=["trial_ends_at"])
        apply_subscription_entitlements(subscription)
        self.assertTrue(business_has_module(self.business, "dashboard"))
        self.assertFalse(business_has_module(self.business, "users"))
        self.assertFalse(business_has_module(self.business, "inventory"))

    def test_yearly_price_applies_founder_configured_discount_to_services(self):
        plan = self.plans["business_pro"]
        plan.monthly_price = 1000
        plan.yearly_discount_percent = 10
        plan.additional_service_discount_percent = 25
        plan.save()
        # Primary 1000 + additional service 750 = 1750/month; yearly at 10% off = 18,900.
        self.assertEqual(payment_amount(plan, 2, 12, billing_cycle=SubscriptionPayment.CYCLE_YEARLY), 18900)

    def test_trial_label_identifies_trial_and_date(self):
        subscription = self._subscribe("starter")
        self.assertIn("Trial", subscription.trial_status_label)
        self.assertIn(str(subscription.trial_ends_at.year), subscription.trial_status_label)

    def test_founder_general_trial_policy_controls_new_trial_length(self):
        policy = SubscriptionPolicySettings.load()
        policy.general_trial_days = 17
        policy.save(update_fields=["general_trial_days", "updated_at"])
        subscription = start_trial_for_business(self.business, self.plans["production"])
        remaining = subscription.trial_ends_at - self.timezone.now()
        self.assertGreater(remaining.total_seconds(), 16 * 86400)
        self.assertLessEqual(remaining.total_seconds(), 17 * 86400 + 5)

    def test_founder_trial_extension_adds_after_existing_trial_and_is_audited(self):
        founder = CustomUser.objects.create_superuser(username="trial-founder", password="safe-password-123")
        subscription = self._subscribe("production")
        previous_end = subscription.trial_ends_at
        subscription = grant_founder_trial_extension(
            subscription, self.plans["business_pro"], 14, founder, "Launch support"
        )
        self.assertEqual(subscription.plan, self.plans["business_pro"])
        self.assertEqual(subscription.trial_ends_at, previous_end + self.timezone.timedelta(days=14))
        grant = FounderTrialGrant.objects.get(subscription=subscription)
        self.assertEqual(grant.days, 14)
        self.assertEqual(grant.granted_by, founder)
        self.assertEqual(grant.note, "Launch support")

    def test_current_trial_plan_payment_stays_locked_until_final_seven_days(self):
        from django.core.exceptions import ValidationError

        subscription = self._subscribe("starter")
        self.plans["starter"].monthly_price = 1000
        self.plans["starter"].save(update_fields=["monthly_price"])
        self.assertTrue(payment_is_locked(subscription, self.plans["starter"]))
        with self.assertRaisesMessage(ValidationError, "opens within 7 days"):
            create_payment_request(subscription, self.plans["starter"], provider="paystack")

        subscription.trial_ends_at = self.timezone.now() + self.timezone.timedelta(days=6, hours=12)
        subscription.save(update_fields=["trial_ends_at"])
        self.assertFalse(payment_is_locked(subscription, self.plans["starter"]))

    def test_founder_plan_is_locked_but_other_plan_remains_open(self):
        founder = CustomUser.objects.create_superuser(username="grant-founder", password="safe-password-123")
        subscription = self._subscribe("starter")
        grant_founder_lifetime(subscription, self.plans["starter"], founder)
        subscription.refresh_from_db()
        self.assertTrue(payment_is_locked(subscription, self.plans["starter"]))
        self.assertFalse(payment_is_locked(subscription, self.plans["production"]))

    def test_subscription_sync_preserves_founder_lifetime_grant(self):
        founder = CustomUser.objects.create_superuser(
            username="sync-founder", password="safe-password-123"
        )
        subscription = self._subscribe("business_pro")
        grant_founder_lifetime(subscription, self.plans["business_pro"], founder)

        call_command("sync_subscriptions", verbosity=0)

        subscription.refresh_from_db()
        self.assertTrue(subscription.founder_lifetime)
        self.assertEqual(subscription.status, BusinessSubscription.STATUS_FOUNDER)
        self.assertTrue(
            BusinessModuleAccess.objects.get(
                business=self.business, module="commerce"
            ).enabled
        )

    def test_stale_payment_confirmation_cannot_revoke_newer_founder_grant(self):
        founder = CustomUser.objects.create_superuser(
            username="payment-founder", password="safe-password-123"
        )
        subscription = self._subscribe("starter")
        payment = SubscriptionPayment.objects.create(
            subscription=subscription,
            plan=self.plans["production"],
            amount=Decimal("1000.00"),
            months=1,
            billing_cycle=SubscriptionPayment.CYCLE_MONTHLY,
            provider="manual",
            reference="STALE-BEFORE-FOUNDER",
        )

        grant_founder_lifetime(subscription, self.plans["business_pro"], founder)
        mark_payment_paid(payment)

        payment.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(payment.status, SubscriptionPayment.STATUS_PAID)
        self.assertTrue(subscription.founder_lifetime)
        self.assertEqual(subscription.status, BusinessSubscription.STATUS_FOUNDER)
        self.assertEqual(subscription.plan, self.plans["business_pro"])

    def test_payment_created_after_founder_grant_can_switch_plan(self):
        founder = CustomUser.objects.create_superuser(
            username="switch-founder", password="safe-password-123"
        )
        subscription = self._subscribe("starter")
        grant_founder_lifetime(subscription, self.plans["starter"], founder)
        subscription.refresh_from_db()
        payment = SubscriptionPayment.objects.create(
            subscription=subscription,
            plan=self.plans["production"],
            amount=Decimal("1000.00"),
            months=1,
            billing_cycle=SubscriptionPayment.CYCLE_MONTHLY,
            provider="manual",
            reference="POST-FOUNDER-SWITCH",
        )

        mark_payment_paid(payment)

        subscription.refresh_from_db()
        self.assertFalse(subscription.founder_lifetime)
        self.assertEqual(subscription.status, BusinessSubscription.STATUS_ACTIVE)
        self.assertEqual(subscription.plan, self.plans["production"])


class LiveTesterRoleTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Live Demo Bakery", slug="live-demo-bakery")
        roles = seed_business_roles(self.business)
        self.tester_role = roles[CustomUser.ROLE_LIVE_TESTER]
        self.user = CustomUser.objects.create_user(
            username="live.tester", password="safe-password-123", fullname="Live Tester"
        )
        UserBusiness.objects.create(
            user=self.user, business=self.business, role=self.tester_role, active=True
        )
        self.client.force_login(self.user)

    def test_live_tester_gets_operational_edit_navigation_but_not_user_admin(self):
        self.assertTrue(is_live_tester(self.user, self.business))
        self.assertTrue(user_has_permission(self.user, self.business, "inventory", "view"))
        self.assertTrue(user_has_permission(self.user, self.business, "inventory", "edit"))
        self.assertTrue(user_has_permission(self.user, self.business, "finance", "edit"))
        self.assertFalse(user_has_permission(self.user, self.business, "users", "view"))
        self.assertFalse(user_has_permission(self.user, self.business, "users", "edit"))

    def test_live_tester_can_open_add_form_but_cannot_submit_business_changes(self):
        from inventory.models import RawMaterial

        response = self.client.get(reverse("raw_material_add"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Demo · read only")

        response = self.client.post(reverse("raw_material_add"), {"name": "Must Not Persist"})
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Demo access is read-only", status_code=403)
        self.assertFalse(RawMaterial.raw_objects.filter(business=self.business, name="Must Not Persist").exists())

    def test_live_tester_can_still_logout(self):
        response = self.client.post(reverse("logout"))
        self.assertEqual(response.status_code, 302)

    def test_demo_role_is_hidden_from_business_admin_but_assignable_by_superuser(self):
        self.assertEqual(self.tester_role.name, "Demo")
        self.assertFalse(self.tester_role.visible_to_admin)

        admin_role = Role.objects.get(business=self.business, key=CustomUser.ROLE_BUSINESS_ADMIN)
        admin = CustomUser.objects.create_user(
            username="business.admin.demo.visibility", password="safe-password-123", fullname="Business Admin"
        )
        UserBusiness.objects.create(user=admin, business=self.business, role=admin_role, active=True)

        self.client.force_login(admin)
        response = self.client.get(reverse("users_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Demo")
        response = self.client.get(reverse("user_edit", args=[self.user.pk]))
        self.assertEqual(response.status_code, 403)

        founder = CustomUser.objects.create_superuser(
            username="founder.demo.visibility", password="safe-password-123", fullname="Founder"
        )
        self.client.force_login(founder)
        response = self.client.get(reverse("users_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Demo")

    def test_existing_custom_demo_role_is_adopted_instead_of_duplicated(self):
        other_business = Business.objects.create(name="Existing Demo Tenant", slug="existing-demo-tenant")
        existing_demo = Role.objects.create(
            business=other_business, key="demo", name="Demo", active=True, visible_to_admin=True
        )
        existing_user = CustomUser.objects.create_user(
            username="existing.demo.user", password="safe-password-123", fullname="Existing Demo User"
        )
        membership = UserBusiness.objects.create(
            user=existing_user, business=other_business, role=existing_demo, active=True
        )

        roles = seed_business_roles(other_business)
        canonical = roles[CustomUser.ROLE_LIVE_TESTER]
        membership.refresh_from_db()
        self.assertEqual(canonical.pk, existing_demo.pk)
        self.assertEqual(canonical.key, CustomUser.ROLE_LIVE_TESTER)
        self.assertEqual(canonical.name, "Demo")
        self.assertFalse(canonical.visible_to_admin)
        self.assertEqual(membership.role_id, canonical.pk)
        self.assertEqual(Role.objects.filter(business=other_business, name="Demo").count(), 1)



class SeedQueryEfficiencyTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Seed Efficiency", slug="seed-efficiency")

    def test_role_seed_steady_state_is_bounded_and_repairs_missing_permissions(self):
        roles = seed_business_roles(self.business)

        with CaptureQueriesContext(connection) as queries:
            seeded = seed_business_roles(self.business)

        self.assertEqual(set(seeded), set(roles))
        self.assertLessEqual(len(queries), 3)

        manager = seeded[CustomUser.ROLE_MANAGER]
        RoleModulePermission.objects.filter(role=manager, module="sales").delete()
        with CaptureQueriesContext(connection) as repair_queries:
            seed_business_roles(self.business)

        self.assertTrue(RoleModulePermission.objects.filter(role=manager, module="sales").exists())
        self.assertLessEqual(len(repair_queries), 5)

    def test_plan_seed_steady_state_is_bounded_and_repairs_entitlement_drift(self):
        plans = ensure_default_plans()

        with CaptureQueriesContext(connection) as queries:
            seeded = ensure_default_plans()

        self.assertEqual(set(seeded), set(plans))
        self.assertLessEqual(len(queries), 3)

        starter = seeded["starter"]
        row = SubscriptionPlanModule.objects.get(plan=starter, module="inventory")
        row.enabled = False
        row.level = "none"
        row.save(update_fields=["enabled", "level"])
        SubscriptionPlanModule.objects.filter(plan=starter, module="sales").delete()

        with CaptureQueriesContext(connection) as repair_queries:
            ensure_default_plans()

        inventory = SubscriptionPlanModule.objects.get(plan=starter, module="inventory")
        sales = SubscriptionPlanModule.objects.get(plan=starter, module="sales")
        self.assertTrue(inventory.enabled)
        self.assertEqual(inventory.level, "full")
        self.assertTrue(sales.enabled)
        self.assertEqual(sales.level, "full")
        self.assertLessEqual(len(repair_queries), 6)


class FounderPaymentSettingsTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Platform Business", slug="platform-business")
        self.user = CustomUser.objects.create_superuser(
            username="founder", password="safe-password-123", fullname="Founder"
        )
        self.plans = ensure_default_plans()
        self.client.force_login(self.user)

    def test_founder_can_toggle_new_subscription_payment_channels(self):
        response = self.client.post(reverse("founder_subscriptions"), {
            "action": "save_payment_channels",
            "monnify_enabled": "on",
        })
        self.assertRedirects(response, reverse("founder_subscriptions"), fetch_redirect_response=False)
        payment_settings = SubscriptionPaymentSettings.load()
        self.assertFalse(payment_settings.paystack_enabled)
        self.assertTrue(payment_settings.monnify_enabled)
        self.assertEqual(payment_settings.updated_by, self.user)

    def test_active_plan_card_marks_free_starter(self):
        from .subscription_services import start_trial_for_business

        start_trial_for_business(self.business, self.plans["starter"])
        response = self.client.get(reverse("subscription_plans"))
        self.assertContains(response, "Current · Free")
        self.assertContains(response, "Your free plan")

    def test_paid_trial_can_be_cancelled_but_not_reused(self):
        from django.core.exceptions import ValidationError
        from .subscription_services import cancel_paid_plan_trial, start_paid_plan_trial, start_trial_for_business

        self.plans["production"].monthly_price = Decimal("1000.00")
        self.plans["production"].save(update_fields=["monthly_price"])
        subscription = start_trial_for_business(self.business, self.plans["starter"])
        start_paid_plan_trial(subscription, self.plans["production"], self.user)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, BusinessSubscription.STATUS_TRIAL)
        self.assertEqual(subscription.plan, self.plans["production"])

        cancel_paid_plan_trial(subscription)
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, BusinessSubscription.STATUS_ACTIVE)
        self.assertEqual(subscription.plan.code, "starter")
        with self.assertRaises(ValidationError):
            start_paid_plan_trial(subscription, self.plans["production"], self.user)

    def test_active_plan_change_requires_explicit_acknowledgement(self):
        from .subscription_services import start_trial_for_business

        start_trial_for_business(self.business, self.plans["starter"])
        selected = self.plans["production"]
        selected.monthly_price = 1000
        selected.save(update_fields=["monthly_price"])
        url = reverse("subscription_payment_plan", args=[selected.code])
        response = self.client.post(url, {
            "provider": "paystack", "billing_cycle": "monthly", "months": "1",
        })
        self.assertRedirects(response, url, fetch_redirect_response=False)
        self.assertFalse(SubscriptionPayment.objects.exists())

    def test_disabled_provider_is_hidden_and_rejected_for_new_checkout(self):
        payment_settings = SubscriptionPaymentSettings.load()
        payment_settings.paystack_enabled = False
        payment_settings.monnify_enabled = True
        payment_settings.save()
        url = reverse("subscription_payment_plan", args=[self.plans["starter"].code])

        response = self.client.get(url)
        self.assertEqual(response.context["available_payment_providers"], [("monnify", "Monnify")])
        subscription = BusinessSubscription.objects.get(primary_business=self.business)
        from django.utils import timezone
        subscription.trial_ends_at = timezone.now() + timezone.timedelta(days=6)
        subscription.save(update_fields=["trial_ends_at"])
        response = self.client.post(url, {
            "provider": "paystack", "billing_cycle": "monthly", "months": "1",
        })
        self.assertRedirects(response, url, fetch_redirect_response=False)
        self.assertFalse(SubscriptionPayment.objects.exists())


    def test_subscription_payment_snapshots_promotion_and_base_amount(self):
        from django.utils import timezone
        from .subscription_services import start_trial_for_business

        current = self.plans["starter"]
        target = self.plans["production"]
        target.monthly_price = Decimal("20000.00")
        target.save(update_fields=["monthly_price"])
        subscription = start_trial_for_business(self.business, current)
        promo = SubscriptionPromotion.objects.create(
            plan=target, reason="Launch Promo",
            discount_type=SubscriptionPromotion.DISCOUNT_AMOUNT,
            discount_value=Decimal("5000.00"),
            starts_at=timezone.now() - timezone.timedelta(minutes=1),
            ends_at=timezone.now() + timezone.timedelta(days=2),
            created_by=self.user,
        )

        payment = create_payment_request(
            subscription, target, provider=SubscriptionPayment.PROVIDER_PAYSTACK
        )

        self.assertEqual(payment.base_amount, Decimal("20000.00"))
        self.assertEqual(payment.amount, Decimal("15000.00"))
        self.assertEqual(payment.promotion_id, promo.pk)
        self.assertEqual(payment.promotion_reason, "Launch Promo")
        self.assertEqual(payment.promotion_discount_amount, Decimal("5000.00"))

    def test_yearly_only_promotion_is_not_applied_to_monthly_payment(self):
        from django.utils import timezone
        from .subscription_services import start_trial_for_business

        current = self.plans["starter"]
        target = self.plans["production"]
        target.monthly_price = Decimal("10000.00")
        target.yearly_discount_percent = Decimal("0.00")
        target.save(update_fields=["monthly_price", "yearly_discount_percent"])
        subscription = start_trial_for_business(self.business, current)
        promo = SubscriptionPromotion.objects.create(
            plan=target, reason="Annual commitment",
            discount_type=SubscriptionPromotion.DISCOUNT_PERCENT,
            discount_value=Decimal("10.00"),
            billing_cycle=SubscriptionPromotion.CYCLE_YEARLY,
            starts_at=timezone.now() - timezone.timedelta(minutes=1),
            ends_at=timezone.now() + timezone.timedelta(days=2),
            created_by=self.user,
        )

        monthly = create_payment_request(
            subscription, target, billing_cycle=SubscriptionPayment.CYCLE_MONTHLY,
            provider=SubscriptionPayment.PROVIDER_PAYSTACK,
        )
        yearly = create_payment_request(
            subscription, target, billing_cycle=SubscriptionPayment.CYCLE_YEARLY,
            provider=SubscriptionPayment.PROVIDER_PAYSTACK,
        )

        self.assertIsNone(monthly.promotion_id)
        self.assertEqual(monthly.amount, Decimal("10000.00"))
        self.assertEqual(yearly.promotion_id, promo.pk)
        self.assertEqual(yearly.base_amount, Decimal("120000.00"))
        self.assertEqual(yearly.amount, Decimal("108000.00"))


    def _plan_pricing_payload(self, *, starter_free, starter_price="0.00"):
        payload = {"action": "save_plan_pricing"}
        for plan in self.plans.values():
            price = starter_price if plan.code == "starter" else str(plan.monthly_price)
            payload[f"monthly_price_{plan.pk}"] = price
            payload[f"yearly_discount_{plan.pk}"] = str(plan.yearly_discount_percent)
            payload[f"addon_discount_{plan.pk}"] = str(plan.additional_service_discount_percent)
            if plan.code == "starter":
                payload[f"user_limit_{plan.pk}"] = "1"
                payload[f"service_limit_{plan.pk}"] = "0"
                if starter_free:
                    payload[f"free_forever_{plan.pk}"] = "on"
            else:
                payload[f"user_limit_{plan.pk}"] = str(plan.user_limit or 1)
                payload[f"service_limit_{plan.pk}"] = str(plan.additional_service_limit or 0)
                if plan.user_limit is None:
                    payload[f"users_unlimited_{plan.pk}"] = "on"
                if plan.additional_service_limit is None:
                    payload[f"services_unlimited_{plan.pk}"] = "on"
        return payload

    def test_founder_can_switch_starter_paid_then_back_to_free_without_abrupt_loss(self):
        from .subscription_services import start_trial_for_business

        starter = self.plans["starter"]
        subscription = start_trial_for_business(self.business, starter)
        self.assertTrue(subscription.is_effectively_active)

        response = self.client.post(
            reverse("founder_subscriptions"),
            self._plan_pricing_payload(starter_free=False, starter_price="2500.00"),
        )
        self.assertRedirects(response, reverse("founder_subscriptions"), fetch_redirect_response=False)
        starter.refresh_from_db(); subscription.refresh_from_db()
        self.assertFalse(starter.is_free_forever)
        self.assertEqual(starter.monthly_price, Decimal("2500.00"))
        self.assertEqual(starter.trial_days, 30)
        self.assertEqual(subscription.status, BusinessSubscription.STATUS_TRIAL)
        self.assertIsNotNone(subscription.trial_ends_at)
        self.assertTrue(subscription.is_effectively_active)

        response = self.client.post(
            reverse("founder_subscriptions"),
            self._plan_pricing_payload(starter_free=True, starter_price="2500.00"),
        )
        self.assertRedirects(response, reverse("founder_subscriptions"), fetch_redirect_response=False)
        starter.refresh_from_db(); subscription.refresh_from_db()
        self.assertTrue(starter.is_free_forever)
        self.assertEqual(starter.monthly_price, Decimal("0.00"))
        self.assertEqual(starter.trial_days, 0)
        self.assertEqual(subscription.status, BusinessSubscription.STATUS_ACTIVE)
        self.assertIsNone(subscription.trial_ends_at)
        self.assertIsNone(subscription.paid_until)
        self.assertTrue(subscription.is_effectively_active)

    def test_new_workspace_uses_paid_starter_trial_when_founder_switches_starter_to_paid(self):
        from .subscription_services import start_trial_for_business

        starter = self.plans["starter"]
        starter.monthly_price = Decimal("1500.00")
        starter.trial_days = 30
        starter.save(update_fields=["monthly_price", "trial_days"])
        another = Business.objects.create(name="Paid Starter Tenant", slug="paid-starter-tenant")

        subscription = start_trial_for_business(another, starter)

        self.assertEqual(subscription.plan_id, starter.pk)
        self.assertEqual(subscription.status, BusinessSubscription.STATUS_TRIAL)
        self.assertIsNotNone(subscription.trial_ends_at)
        self.assertTrue(subscription.is_effectively_active)

    def test_storefront_pos_access_is_supplemental_not_general_commerce_edit(self):
        roles = seed_business_roles(self.business)
        staff = CustomUser.objects.create_user(
            username="walkin.cashier", password="safe-password-123", fullname="Walk-in Cashier"
        )
        membership = UserBusiness.objects.create(
            user=staff, business=self.business, role=roles[CustomUser.ROLE_POS_OPERATOR],
            active=True,
        )
        BusinessModuleAccess.objects.update_or_create(
            business=self.business, module="commerce", defaults={"enabled": True}
        )
        from commerce.models import CommerceSettings
        CommerceSettings.raw_objects.update_or_create(
            business=self.business, defaults={"enabled": True}
        )

        self.assertTrue(can_use_commerce_storefront(staff, self.business))
        self.assertFalse(user_has_permission(staff, self.business, "dashboard", "view"))
        self.assertFalse(user_has_permission(staff, self.business, "commerce", "view"))
        self.assertFalse(user_has_permission(staff, self.business, "commerce", "edit"))
        self.assertFalse(membership.role.key == CustomUser.ROLE_BUSINESS_ADMIN)


    def test_pos_can_be_supplemental_without_replacing_primary_role(self):
        roles = seed_business_roles(self.business)
        staff = CustomUser.objects.create_user(
            username="manager.with.pos", password="safe-password-123", fullname="Manager With POS"
        )
        membership = UserBusiness.objects.create(
            user=staff, business=self.business, role=roles[CustomUser.ROLE_MANAGER], active=True,
        )
        UserModulePermission.objects.update_or_create(
            membership=membership, module="pos", defaults={"can_view": True, "can_edit": True}
        )
        BusinessModuleAccess.objects.update_or_create(
            business=self.business, module="commerce", defaults={"enabled": True}
        )
        from commerce.models import CommerceSettings
        CommerceSettings.raw_objects.update_or_create(
            business=self.business, defaults={"enabled": True}
        )

        self.assertEqual(membership.role.key, CustomUser.ROLE_MANAGER)
        self.assertTrue(user_has_permission(staff, self.business, "dashboard", "view"))
        self.assertTrue(user_has_permission(staff, self.business, "pos", "view"))
        self.assertTrue(can_use_commerce_storefront(staff, self.business))

    def test_pos_is_hidden_when_commerce_is_not_enabled(self):
        roles = seed_business_roles(self.business)
        staff = CustomUser.objects.create_user(
            username="pos.disabled", password="safe-password-123", fullname="POS Disabled"
        )
        UserBusiness.objects.create(
            user=staff, business=self.business, role=roles[CustomUser.ROLE_POS_OPERATOR], active=True,
        )
        BusinessModuleAccess.objects.update_or_create(
            business=self.business, module="commerce", defaults={"enabled": True}
        )
        from commerce.models import CommerceSettings
        CommerceSettings.raw_objects.update_or_create(
            business=self.business, defaults={"enabled": False}
        )

        self.assertFalse(can_use_commerce_storefront(staff, self.business))


class FounderMarketingVisitAnalyticsTests(TestCase):
    def setUp(self):
        from .analytics import invalidate_founder_analytics_cache

        invalidate_founder_analytics_cache()
        self.factory = RequestFactory()

    def tearDown(self):
        from .analytics import invalidate_founder_analytics_cache

        invalidate_founder_analytics_cache()

    def test_country_code_is_normalized_to_name_and_centroid(self):
        from .analytics import marketing_location_metadata

        request = self.factory.get("/", HTTP_CF_IPCOUNTRY="NG")
        metadata = marketing_location_metadata(request)

        self.assertEqual(metadata["country_code"], "NG")
        self.assertEqual(metadata["country_name"], "Nigeria")
        self.assertEqual(metadata["location"], "Nigeria")
        self.assertEqual(metadata["location_precision"], "country")
        self.assertAlmostEqual(metadata["latitude"], 10.0)
        self.assertAlmostEqual(metadata["longitude"], 8.0)

    def test_edge_city_coordinates_override_country_centroid(self):
        from .analytics import marketing_location_metadata

        request = self.factory.get(
            "/",
            HTTP_X_VERCEL_IP_COUNTRY="NG",
            HTTP_X_VERCEL_IP_COUNTRY_REGION="LA",
            HTTP_X_VERCEL_IP_CITY="Lagos",
            HTTP_X_VERCEL_IP_LATITUDE="6.5244",
            HTTP_X_VERCEL_IP_LONGITUDE="3.3792",
        )
        metadata = marketing_location_metadata(request)

        self.assertEqual(metadata["location"], "Lagos, LA, Nigeria")
        self.assertEqual(metadata["location_precision"], "edge")
        self.assertAlmostEqual(metadata["latitude"], 6.5244)
        self.assertAlmostEqual(metadata["longitude"], 3.3792)

    def test_browser_timezone_enriches_unknown_visit_without_precise_geolocation(self):
        from .analytics import browser_marketing_location_metadata

        metadata = browser_marketing_location_metadata(
            timezone_name="Africa/Lagos",
            language="en-NG",
            current={
                "country": "",
                "country_code": "",
                "country_name": "",
                "location_precision": "unknown",
                "location": "Unknown",
            },
        )

        self.assertEqual(metadata["country_code"], "NG")
        self.assertEqual(metadata["country_name"], "Nigeria")
        self.assertEqual(metadata["city"], "Lagos")
        self.assertEqual(metadata["location_precision"], "timezone")
        self.assertEqual(metadata["browser_timezone"], "Africa/Lagos")
        self.assertIsNotNone(metadata["latitude"])
        self.assertIsNotNone(metadata["longitude"])

    def test_browser_timezone_does_not_override_edge_coordinates(self):
        from .analytics import browser_marketing_location_metadata

        current = {
            "country": "NG",
            "country_code": "NG",
            "country_name": "Nigeria",
            "city": "Ikeja",
            "latitude": 6.6018,
            "longitude": 3.3515,
            "location_precision": "edge",
            "location": "Ikeja, Nigeria",
        }
        metadata = browser_marketing_location_metadata(
            timezone_name="Africa/Lagos",
            language="en-NG",
            current=current,
        )

        self.assertEqual(metadata["location_precision"], "edge")
        self.assertEqual(metadata["latitude"], 6.6018)
        self.assertEqual(metadata["longitude"], 3.3515)

    def test_browser_location_endpoint_enriches_current_session_visit(self):
        session = self.client.session
        session["marketing_test"] = True
        session.save()
        event = PlatformEvent.objects.create(
            event_type=PlatformEvent.EVENT_MARKETING_VISIT,
            session_key=session.session_key,
            metadata={
                "country": "",
                "country_code": "",
                "location": "Unknown",
                "location_precision": "unknown",
            },
        )

        response = self.client.post(
            reverse("marketing_location_enrich"),
            data=json.dumps({"timezone": "Africa/Lagos", "language": "en-NG"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["updated"])
        event.refresh_from_db()
        self.assertEqual(event.metadata["country_code"], "NG")
        self.assertEqual(event.metadata["location_precision"], "timezone")
        self.assertEqual(event.metadata["city"], "Lagos")

    def test_founder_summary_enriches_legacy_country_code_visits_for_map(self):
        from .analytics import founder_analytics_summary, invalidate_founder_analytics_cache

        PlatformEvent.objects.create(
            event_type=PlatformEvent.EVENT_MARKETING_VISIT,
            session_key="visitor-a",
            metadata={"country": "NG", "location": "NG"},
        )
        PlatformEvent.objects.create(
            event_type=PlatformEvent.EVENT_MARKETING_VISIT,
            session_key="visitor-a",
            metadata={"country": "NG", "location": "NG"},
        )
        invalidate_founder_analytics_cache()

        summary = founder_analytics_summary()

        self.assertEqual(summary["marketing_unique_sessions_30d"], 1)
        self.assertEqual(summary["marketing_countries_30d"], 1)
        self.assertEqual(summary["marketing_map_points"][0]["location"], "Nigeria")
        self.assertEqual(summary["marketing_map_points"][0]["total"], 2)
        self.assertEqual(summary["recent_marketing_visits"][0]["location"], "Nigeria")
        self.assertEqual(len(summary["marketing_daily_visits"]), 14)


class FounderPlatformDeletionTests(TestCase):
    def setUp(self):
        self.founder = CustomUser.objects.create_superuser(
            username="deletion-founder", password="safe-password-123", fullname="Deletion Founder"
        )
        self.business = Business.objects.create(name="Delete Me Ltd", slug="delete-me-ltd")
        self.account = CustomUser.objects.create_user(
            username="delete-me", password="safe-password-123", fullname="Delete Me"
        )
        self.client.force_login(self.founder)

    def test_founder_can_preview_and_delete_business(self):
        url = reverse("founder_platform_business_delete", args=[self.business.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Delete business")

        response = self.client.post(url, {"confirm_delete": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Business.objects.filter(pk=self.business.pk).exists())

    def test_business_delete_marks_signup_history_deleted_without_changing_hard_delete(self):
        record = FounderSignupContactState.objects.create(
            email_key="owner@example.com", signup_email="owner@example.com", signup_name="Owner",
            business_name=self.business.name, business_id_snapshot=self.business.pk,
            vertical=self.business.vertical, service=self.business.get_vertical_display(),
        )
        PlatformEvent.objects.create(
            event_type=PlatformEvent.EVENT_REGISTRATION, business=self.business,
            metadata={"signup_email": "owner@example.com", "signup_name": "Owner", "business_name": self.business.name},
        )

        response = self.client.post(
            reverse("founder_platform_business_delete", args=[self.business.pk]),
            {"confirm_delete": "yes"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Business.objects.filter(pk=self.business.pk).exists())
        record.refresh_from_db()
        self.assertIsNotNone(record.deleted_at)
        self.assertEqual(record.business_name, "Delete Me Ltd")
        self.assertFalse(record.permanently_hidden)

    def test_signup_list_only_allows_permanent_delete_after_business_delete(self):
        active = FounderSignupContactState.objects.create(
            email_key="active@example.com", signup_email="active@example.com",
            business_name="Active Business", business_id_snapshot=self.business.pk,
        )
        action_url = reverse("founder_mailing_list_contact_action")
        response = self.client.post(action_url, {"email": active.signup_email, "contact_action": "permanent_delete"})
        self.assertEqual(response.status_code, 302)
        active.refresh_from_db()
        self.assertFalse(active.permanently_hidden)

        active.deleted_at = timezone.now()
        active.save(update_fields=["deleted_at", "updated_at"])
        response = self.client.post(action_url, {"email": active.signup_email, "contact_action": "permanent_delete"})
        self.assertEqual(response.status_code, 302)
        active.refresh_from_db()
        self.assertTrue(active.permanently_hidden)

    def test_founder_live_signup_snapshot_returns_new_signup_and_refreshed_rows(self):
        event = PlatformEvent.objects.create(
            event_type=PlatformEvent.EVENT_REGISTRATION, business=self.business,
            metadata={
                "signup_email": "live@example.com",
                "signup_name": "Live Owner",
                "business_name": self.business.name,
                "vertical": self.business.vertical,
            },
        )
        response = self.client.get(
            reverse("founder_signup_live_snapshot"), {"after": 0}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["latest_registration_id"], event.pk)
        self.assertEqual(data["new_signups"][-1]["business"], "Delete Me Ltd")
        self.assertIn("Delete Me Ltd", data["businesses_html"])
        self.assertIn("live@example.com", data["contacts_html"])

    def test_founder_live_signup_snapshot_short_circuits_when_cursor_is_current(self):
        event = PlatformEvent.objects.create(
            event_type=PlatformEvent.EVENT_REGISTRATION, business=self.business,
            metadata={"signup_email": "current@example.com"},
        )
        response = self.client.get(
            reverse("founder_signup_live_snapshot"), {"after": event.pk}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["unchanged"])
        self.assertEqual(data["latest_registration_id"], event.pk)
        self.assertEqual(data["new_signups"], [])
        self.assertNotIn("businesses_html", data)

    def test_founder_can_delete_another_user_but_not_self(self):
        delete_url = reverse("founder_platform_user_delete", args=[self.account.pk])
        response = self.client.post(delete_url, {"confirm_delete": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CustomUser.objects.filter(pk=self.account.pk).exists())

        self_url = reverse("founder_platform_user_delete", args=[self.founder.pk])
        response = self.client.post(self_url, {"confirm_delete": "yes"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(CustomUser.objects.filter(pk=self.founder.pk).exists())
        self.assertContains(response, "cannot delete the account you are currently using")

    def test_primary_business_with_additional_services_must_be_deleted_last(self):
        plan = ensure_default_plans()["production"]
        subscription = BusinessSubscription.objects.create(
            primary_business=self.business, plan=plan, status=BusinessSubscription.STATUS_ACTIVE,
        )
        SubscriptionService.objects.create(
            subscription=subscription, business=self.business, is_primary=True,
        )
        additional = Business.objects.create(name="Additional Branch", slug="additional-branch")
        SubscriptionService.objects.create(
            subscription=subscription, business=additional, is_primary=False,
        )

        url = reverse("founder_platform_business_delete", args=[self.business.pk])
        response = self.client.post(url, {"confirm_delete": "yes"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Business.objects.filter(pk=self.business.pk).exists())
        self.assertContains(response, "Delete those businesses first")



class BusinessRestoreTests(TestCase):
    def _backup(self, businesses):
        import os
        import sqlite3
        import tempfile
        from django.core.files.uploadedfile import SimpleUploadedFile

        handle = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        path = handle.name
        handle.close()
        try:
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE core_business (id INTEGER PRIMARY KEY, name varchar(120), currency_symbol varchar(5), slug varchar(60), vertical varchar(20), accent_color varchar(7), background_color varchar(7), tagline varchar(100), restaurant_table_service bool)"
            )
            for row in businesses:
                connection.execute(
                    "INSERT INTO core_business (id,name,currency_symbol,slug,vertical,accent_color,background_color,tagline,restaurant_table_service) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        row["id"], row["name"], "₦", row["slug"], row.get("vertical", "general"),
                        "#D14900", "#050733", row.get("tagline", "Existing tenant"), 1,
                    ),
                )
            connection.commit()
            connection.close()
            data = open(path, "rb").read()
        finally:
            os.unlink(path)
        return SimpleUploadedFile("backup.sqlite3", data, content_type="application/octet-stream")

    def test_dry_run_requires_source_id_when_backup_has_multiple_tenants(self):
        from .backup_restore import analyze_backup_sqlite
        target = Business.objects.create(name="Destination", slug="destination")
        upload = self._backup([
            {"id": 1, "name": "Existing One", "slug": "existing-one"},
            {"id": 2, "name": "Existing Two", "slug": "existing-two"},
        ])
        report = analyze_backup_sqlite(upload, target)
        self.assertFalse(report["ready"])
        self.assertEqual(len(report["source_businesses"]), 2)
        self.assertIn("multiple tenants", " ".join(report["blockers"]).lower())

    def test_import_is_tenant_scoped_and_copies_business_profile_without_slug(self):
        from .backup_restore import restore_backup_sqlite
        target = Business.objects.create(name="Fresh Destination", slug="keep-this-slug")
        upload = self._backup([
            {"id": 7, "name": "Existing Company", "slug": "old-slug", "vertical": "retail", "tagline": "Imported history"},
        ])
        result = restore_backup_sqlite(upload, target, 7)
        target.refresh_from_db()
        self.assertEqual(target.name, "Existing Company")
        self.assertEqual(target.slug, "keep-this-slug")
        self.assertEqual(target.vertical, "retail")
        self.assertEqual(result["total_rows"], 0)

class JSONBusinessRestoreTests(TestCase):
    def _json_backup(self):
        import json
        from django.core.files.uploadedfile import SimpleUploadedFile
        payload = [
            {"model": "core.business", "pk": 1, "fields": {
                "name": "Existing One", "currency_symbol": "₦", "slug": "existing-one",
                "vertical": "general", "accent_color": "#D14900", "background_color": "#050733",
                "tagline": "Existing data", "storefront_logo": "", "restaurant_table_service": True,
            }},
            {"model": "core.business", "pk": 2, "fields": {
                "name": "Other Tenant", "currency_symbol": "₦", "slug": "other-tenant",
                "vertical": "retail", "accent_color": "#D14900", "background_color": "#050733",
                "tagline": "Must not leak", "storefront_logo": "", "restaurant_table_service": True,
            }},
            {"model": "core.cashaccount", "pk": 10, "fields": {
                "business": 1, "created_by": None, "name": "Existing Cash", "account_type": "cash",
                "opening_balance": "100.00", "active": True,
            }},
            {"model": "core.cashaccount", "pk": 20, "fields": {
                "business": 2, "created_by": None, "name": "Other Cash", "account_type": "cash",
                "opening_balance": "999.00", "active": True,
            }},
        ]
        return SimpleUploadedFile(
            "backup.json", json.dumps(payload).encode("utf-8"), content_type="application/json"
        )

    def test_json_dry_run_requires_source_tenant_for_old_unscoped_backup(self):
        from .backup_restore import analyze_backup
        target = Business.objects.create(name="Destination", slug="json-destination")
        report = analyze_backup(self._json_backup(), target)
        self.assertFalse(report["ready"])
        self.assertEqual(len(report["source_businesses"]), 2)
        self.assertIn("multiple tenants", " ".join(report["blockers"]).lower())

    def test_json_import_filters_other_tenant_rows(self):
        from core.models import CashAccount
        from .backup_restore import restore_backup
        target = Business.objects.create(name="Destination", slug="json-import-destination")
        result = restore_backup(self._json_backup(), target, 1)
        target.refresh_from_db()

        self.assertEqual(target.name, "Existing One")
        self.assertEqual(target.slug, "json-import-destination")
        self.assertEqual(result["models"]["core.cashaccount"], 1)
        self.assertEqual(
            list(CashAccount.raw_objects.filter(business=target).values_list("name", flat=True)),
            ["Existing Cash"],
        )

    def test_json_import_reconstructs_omitted_customer_and_location_dependencies(self):
        import json
        from django.core.files.uploadedfile import SimpleUploadedFile
        from sales.models import Customer, Sale
        from production.models import Order
        from inventory.models import InventoryLocation, StockMovement
        from .backup_restore import restore_backup

        payload = [
            {"model": "core.business", "pk": 1, "fields": {
                "name": "Existing Bakery", "currency_symbol": "₦", "slug": "existing-bakery",
                "vertical": "bakery", "accent_color": "#D14900", "background_color": "#050733",
                "tagline": "", "storefront_logo": "", "restaurant_table_service": True,
            }},
            {"model": "core.cashaccount", "pk": 4, "fields": {
                "business": 1, "created_by": None, "name": "Existing Bank", "account_type": "bank",
                "opening_balance": "0.00", "active": True,
            }},
            {"model": "inventory.rawmaterial", "pk": 1, "fields": {
                "business": 1, "created_by": None, "name": "Flour", "category": "ingredient",
                "purchase_unit": "bag", "package_qty": "50.00", "package_unit": "kg",
                "usage_unit": "kg", "usage_conversion_factor": "1.000000", "stock": "10.000",
                "reorder_level": "1.00", "cost_per_unit": "100.000000",
            }},
            {"model": "inventory.finishedgood", "pk": 1, "fields": {
                "business": 1, "created_by": None, "name": "Bread", "unit": "loaf",
                "units_per_batch": "10.00", "stock": "2.00", "total_produced": "20.00",
                "total_delivered_to_customers": "10.00", "reorder_level": "1.00",
                "selling_price": "500.00", "transferred_market_stock": "0.00",
            }},
            {"model": "production.order", "pk": 5, "fields": {
                "business": 1, "created_by": None, "date": "2026-09-01", "order_number": 1,
                "order_type": "distribution", "is_market_stock": False, "production_destination": "store",
                "non_stock_purpose": "", "customer": 8, "customer_name": "Existing Customer",
                "customer_region": "Mainland", "customer_group": "Wholesale", "transaction_type": "paid",
                "customer_payment_status": "paid", "customer_payment_method": "Transfer",
                "customer_payment_account": 4, "unpaid_description": "", "payment_method": "Transfer",
                "account": 4, "status": "completed", "notes": "", "approved_date": "2026-09-01",
                "completed_date": "2026-09-01", "reversed_at": None, "reversed_reason": "", "reversed_by": None,
            }},
            {"model": "production.orderitem", "pk": 1, "fields": {
                "order": 5, "finished_good": 1, "batch_qty": "1.00", "piece_qty": "0.00",
                "production_batch_qty": "1.00", "production_piece_qty": "0.00",
                "discount": "0.00", "price": "500.00",
            }},
            {"model": "sales.sale", "pk": 6, "fields": {
                "business": 1, "created_by": None, "date": "2026-09-01", "customer": "Existing Customer",
                "customer_master": 8, "transaction_type": "paid", "unpaid_description": "",
                "account": 4, "payment_method": "Transfer", "source": "distribution_order",
                "linked_order": 5, "service_mode": "", "table_reference": "",
            }},
            {"model": "sales.saleitem", "pk": 1, "fields": {
                "sale": 6, "finished_good": 1, "batch_qty": "1.00", "piece_qty": "0.00",
                "discount": "0.00", "price": "500.00", "unit_cost": "100.000000", "production_batch": 77,
            }},
            {"model": "inventory.stockmovement", "pk": 1, "fields": {
                "business": 1, "created_by": None, "raw_material": 1, "finished_good": None,
                "movement_type": "raw_consumption", "quantity": "-1.000", "affects_stock": True,
                "balance_after": "9.000", "note": "Existing use", "reference": "PROD-5",
                "unit_value": "100.000000", "location": 1,
            }},
        ]
        upload = SimpleUploadedFile("backup.json", json.dumps(payload).encode(), content_type="application/json")
        target = Business.objects.create(name="Destination", slug="restore-reconstruct-target")
        result = restore_backup(upload, target, 1)

        customer = Customer.raw_objects.get(business=target)
        self.assertEqual(customer.name, "Existing Customer")
        self.assertEqual(Order.raw_objects.get(business=target).customer_id, customer.pk)
        self.assertEqual(Sale.raw_objects.get(business=target).customer_master_id, customer.pk)
        self.assertIsNone(Sale.raw_objects.get(business=target).items.get().production_batch_id)
        self.assertEqual(StockMovement.raw_objects.get(business=target).location.name, "Main Store")
        self.assertEqual(result["reconstructed_customers"], 1)
        self.assertEqual(InventoryLocation.raw_objects.filter(business=target, name="Main Store").count(), 1)


class PlatformMailingTests(TestCase):
    def _campaign(self):
        from .models import PlatformMailCampaign, PlatformMailRecipient

        campaign = PlatformMailCampaign.objects.create(
            subject="Hello {{ business_name }}",
            heading="An update for {{ business_name }}",
            body_html="<p>Hi <strong>{{ recipient_name }}</strong>.</p>",
            status=PlatformMailCampaign.STATUS_QUEUED,
            total_recipients=1,
            queued_at=timezone.now(),
        )
        recipient = PlatformMailRecipient.objects.create(
            campaign=campaign,
            business_id_snapshot=1,
            business_name="Acme Bakery",
            service="Bakery",
            plan_name="Production",
            recipient_name="Ada",
            email="ada@example.com",
        )
        return campaign, recipient

    def test_composer_sanitises_html_and_rejects_unknown_tokens(self):
        from .forms import PlatformMailComposeForm

        form = PlatformMailComposeForm(data={
            "subject": "Update for {{ business_name }}",
            "heading": "Hello {{ recipient_name }}",
            "body_html": (
                '<p onclick="bad()">Safe {{ business_name }}</p>'
                '<script>alert(1)</script>'
                '<a href="javascript:bad()">Unsafe link</a>'
            ),
            "cta_label": "",
            "cta_url": "",
            "business_ids": "1",
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("script", form.cleaned_data["body_html"])
        self.assertNotIn("onclick", form.cleaned_data["body_html"])
        self.assertNotIn("javascript:", form.cleaned_data["body_html"])
        self.assertIn("{{ business_name }}", form.cleaned_data["body_html"])

        invalid = PlatformMailComposeForm(data={
            "subject": "Hello {{ unknown_contact }}",
            "heading": "Update",
            "body_html": "<p>Message</p>",
            "business_ids": "1",
        })
        self.assertFalse(invalid.is_valid())
        self.assertIn("subject", invalid.errors)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_dispatch_delivers_and_records_each_recipient(self):
        from .mailing import dispatch_queued_platform_mail
        from .models import PlatformMailCampaign, PlatformMailRecipient

        campaign, recipient = self._campaign()

        result = dispatch_queued_platform_mail(campaign_id=campaign.pk)

        self.assertEqual(result["sent"], 1)
        recipient.refresh_from_db()
        campaign.refresh_from_db()
        self.assertEqual(recipient.status, PlatformMailRecipient.STATUS_SENT)
        self.assertEqual(recipient.delivery_attempts, 1)
        self.assertIsNotNone(recipient.sent_at)
        self.assertEqual(campaign.status, PlatformMailCampaign.STATUS_SENT)
        self.assertEqual(campaign.sent_count, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Hello Acme Bakery")

    def test_dispatch_retries_transient_failure_then_marks_terminal_failure(self):
        from unittest.mock import Mock, patch

        from .mailing import dispatch_queued_platform_mail
        from .models import PlatformMailCampaign, PlatformMailRecipient

        campaign, recipient = self._campaign()
        connection = Mock()
        connection.send_messages.side_effect = RuntimeError("provider unavailable")

        with patch("accounts.mailing.get_connection", return_value=connection):
            first = dispatch_queued_platform_mail(campaign_id=campaign.pk)
            second = dispatch_queued_platform_mail(campaign_id=campaign.pk)
            third = dispatch_queued_platform_mail(campaign_id=campaign.pk)

        self.assertEqual(first["retrying"], 1)
        self.assertEqual(second["retrying"], 1)
        self.assertEqual(third["failed"], 1)
        recipient.refresh_from_db()
        campaign.refresh_from_db()
        self.assertEqual(recipient.status, PlatformMailRecipient.STATUS_FAILED)
        self.assertEqual(recipient.delivery_attempts, 3)
        self.assertEqual(campaign.status, PlatformMailCampaign.STATUS_PARTIAL)
        self.assertEqual(campaign.failed_count, 1)
        self.assertEqual(connection.send_messages.call_count, 3)

    def test_mailing_only_user_gets_shared_workspace_and_topic_form(self):
        user = CustomUser.objects.create_user(
            username="project-mailer",
            password="safe-password-123",
            email="mailer@example.com",
            platform_mail_access=True,
        )
        self.client.force_login(user)

        workspace = self.client.get(reverse("platform_mailing_workspace"))
        topic_form = self.client.get(reverse("platform_mail_template_add"))

        self.assertEqual(workspace.status_code, 200)
        self.assertContains(workspace, "Project Mailing")
        self.assertContains(workspace, "workspace.css")
        self.assertContains(workspace, "Send campaign now")
        self.assertEqual(topic_form.status_code, 200)
        self.assertContains(topic_form, "Build a reusable, branded message")


class PayrollAddonPricingVisibilityTests(TestCase):
    """Payroll add-on prices are visible to everyone, including trial and
    Founder-lifetime tenants who currently get payroll free."""

    def setUp(self):
        from .models import PayrollAddonTier
        self.PayrollAddonTier = PayrollAddonTier
        self.plans = ensure_default_plans()
        self.business = Business.objects.create(name="Payroll Plans", slug="payroll-plans", currency_symbol="₦")
        roles = seed_business_roles(self.business)
        self.admin = CustomUser.objects.create_user(username="payroll-admin", password="safe-password-123", fullname="Admin")
        UserBusiness.objects.create(user=self.admin, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        plan = self.plans["business_pro"] if "business_pro" in self.plans else list(self.plans.values())[-1]
        self.plan = plan
        self.PayrollAddonTier.objects.create(plan=plan, staff_limit=5, monthly_price=Decimal("2500.00"))
        self.PayrollAddonTier.objects.create(plan=plan, staff_limit=20, monthly_price=Decimal("7500.00"))
        self.PayrollAddonTier.objects.create(plan=plan, staff_limit=50, monthly_price=Decimal("99999.00"), active=False)
        self.subscription = BusinessSubscription.objects.create(
            primary_business=self.business, plan=plan, status=BusinessSubscription.STATUS_TRIAL,
            trial_ends_at=timezone.now() + timezone.timedelta(days=30),
        )
        SubscriptionService.objects.create(subscription=self.subscription, business=self.business, is_primary=True)
        apply_subscription_entitlements(self.subscription)

    def test_matrix_row_shows_from_price_and_staff_range_only_for_priced_plans(self):
        from .subscription_services import attach_payroll_tiers
        from .models import SubscriptionPlan
        plans = attach_payroll_tiers(SubscriptionPlan.objects.filter(active=True).prefetch_related("module_entitlements").order_by("monthly_price", "id"))
        row = next(r for r in build_plan_feature_matrix(plans) if r["label"] == "Staff payroll add-on")
        by_plan = dict(zip([p.code for p in plans], row["values"]))
        self.assertEqual(by_plan[self.plan.code]["text"], "From ₦2,500.00 / mo")
        self.assertEqual(by_plan[self.plan.code]["sub"], "5–20 staff")
        unpriced = [v for code, v in by_plan.items() if code != self.plan.code]
        self.assertTrue(all(v["text"] == "Pricing coming soon" for v in unpriced))

    def test_retired_tiers_are_not_exposed(self):
        from .subscription_services import attach_payroll_tiers
        [plan] = attach_payroll_tiers([self.plan])
        self.assertEqual([t.staff_limit for t in plan.payroll_tiers], [5, 20])

    def test_marketing_cards_list_tiers_for_anonymous_visitors(self):
        response = self.client.get(reverse("marketing_home"), {"nocache": "1"})
        self.assertContains(response, "from <span")
        self.assertContains(response, "₦2,500.00")
        self.assertContains(response, "Up to 20 staff")
        self.assertNotContains(response, "99,999.00")

    def test_in_app_plan_card_shows_tiers_to_trial_tenant_with_included_note(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("subscription_plans"))
        self.assertContains(response, "Up to 5 staff")
        self.assertContains(response, "Included free on your current access")
        self.assertNotContains(response, "99,999.00")

    def test_founder_lifetime_tenant_also_sees_prices(self):
        self.subscription.status = BusinessSubscription.STATUS_ACTIVE
        self.subscription.founder_lifetime = True
        self.subscription.save()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("subscription_plans"))
        self.assertContains(response, "₦7,500.00")

    def test_checkout_is_read_only_for_included_tenants_and_blocks_payment(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("payroll_addon_checkout"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "nothing to pay")
        self.assertContains(response, "Included with your current access")
        tier = self.plan.payroll_addon_tiers.get(staff_limit=5)
        before = SubscriptionPayment.objects.count()
        post = self.client.post(reverse("payroll_addon_checkout"), {"tier_id": tier.pk, "billing_cycle": "monthly", "provider": "paystack"})
        self.assertRedirects(post, reverse("payroll_addon_checkout"), fetch_redirect_response=False)
        self.assertEqual(SubscriptionPayment.objects.count(), before)


class PayrollStaffBatchTests(TestCase):
    """Extra-staff batches, unlimited packages, pro-rata and auto-renewal."""

    def setUp(self):
        from .models import PayrollAddonTier, PayrollStaffBatch
        self.plans = ensure_default_plans()
        self.plan = self.plans["business_pro"] if "business_pro" in self.plans else list(self.plans.values())[-1]
        self.business = Business.objects.create(name="Batch Co", slug="batch-co", currency_symbol="₦")
        roles = seed_business_roles(self.business)
        self.admin = CustomUser.objects.create_user(username="batch-admin", password="safe-password-123", fullname="Admin")
        UserBusiness.objects.create(user=self.admin, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.tier = PayrollAddonTier.objects.create(plan=self.plan, staff_limit=2, monthly_price=Decimal("3000.00"))
        self.batch5 = PayrollStaffBatch.objects.create(staff_count=5, monthly_price=Decimal("1200.00"))
        self.batch10 = PayrollStaffBatch.objects.create(staff_count=10, monthly_price=Decimal("2000.00"))
        self.subscription = BusinessSubscription.objects.create(
            primary_business=self.business, plan=self.plan, status=BusinessSubscription.STATUS_ACTIVE,
            paid_until=timezone.now() + timezone.timedelta(days=200),
        )
        SubscriptionService.objects.create(subscription=self.subscription, business=self.business, is_primary=True)
        apply_subscription_entitlements(self.subscription)

    def _pay(self, payment):
        from .subscription_services import mark_payment_paid
        return mark_payment_paid(payment)

    def _buy_primary(self, tier=None, cycle="monthly"):
        from .subscription_services import create_payment_request
        payment = create_payment_request(
            self.subscription, self.plan, months=12 if cycle == "yearly" else 1, billing_cycle=cycle,
            purpose=SubscriptionPayment.PURPOSE_PAYROLL_ADDON, payroll_tier=tier or self.tier,
        )
        self._pay(payment)
        return payment

    def _buy_batch(self, batch, quantity=1):
        from .subscription_services import create_payment_request
        payment = create_payment_request(
            self.subscription, self.plan, purpose=SubscriptionPayment.PURPOSE_PAYROLL_BATCH,
            payroll_batch=batch, batch_quantity=quantity,
        )
        self._pay(payment)
        return payment

    def _state(self):
        from .payroll import payroll_access_state
        return payroll_access_state(self.business)

    def test_batches_require_an_active_primary_package(self):
        from .subscription_services import create_payment_request
        with self.assertRaises(ValidationError):
            create_payment_request(
                self.subscription, self.plan, purpose=SubscriptionPayment.PURPOSE_PAYROLL_BATCH, payroll_batch=self.batch5,
            )

    def test_batch_is_prorated_to_the_primary_period_and_stacks_capacity(self):
        self._buy_primary()
        addon = self.business.payroll_addon
        paid_until = addon.paid_until
        payment = self._buy_batch(self.batch5, quantity=2)
        days = payment.payroll_batch_snapshot[0]["prorated_days"]
        self.assertIn(days, (30, 31))
        expected = (Decimal("1200.00") * 12 * 2 * days / Decimal("365")).quantize(Decimal("0.01"))
        self.assertEqual(payment.amount, expected)
        self.assertLess(payment.amount, Decimal("2400.00"))  # cheaper than two full months
        addon.refresh_from_db()
        self.assertEqual(addon.paid_until, paid_until)  # a batch never extends the period
        state = self._state()
        self.assertEqual((state["base_limit"], state["extra_staff"], state["staff_limit"]), (2, 10, 12))
        self._buy_batch(self.batch5)  # same batch again stacks the quantity
        self.assertEqual(self.business.payroll_addon.batches.get(batch=self.batch5).quantity, 3)
        self.assertEqual(self._state()["staff_limit"], 2 + 15)

    def test_capacity_check_uses_primary_plus_batches(self):
        from .payroll import assert_payroll_capacity
        self._buy_primary()
        for n in range(2):
            PayrollStaffProfile.objects.create(business=self.business, full_name=f"Staff {n}")
        with self.assertRaises(ValidationError) as ctx:
            assert_payroll_capacity(self.business)
        self.assertIn("extra-staff batch", str(ctx.exception))
        self._buy_batch(self.batch5)
        assert_payroll_capacity(self.business)  # now room for 7

    def test_renewal_includes_held_batches_at_current_prices_and_is_automatic(self):
        self._buy_primary()
        self._buy_batch(self.batch5, quantity=2)
        self.batch5.monthly_price = Decimal("1500.00")  # price change is picked up on renewal
        self.batch5.save()
        before = self.business.payroll_addon.paid_until
        renewal = self._buy_primary()
        self.assertEqual(renewal.amount, Decimal("3000.00") + Decimal("1500.00") * 2)
        self.assertEqual(renewal.payroll_batch_snapshot[0]["quantity"], 2)
        addon = self.business.payroll_addon
        addon.refresh_from_db()
        self.assertEqual(addon.paid_until, before + timezone.timedelta(days=30))
        self.assertEqual(addon.batches.get().quantity, 2)  # still held after renewal
        yearly = self._buy_primary(cycle="yearly")
        self.assertEqual(yearly.amount, (Decimal("3000.00") + Decimal("1500.00") * 2) * 12)

    def test_retired_batch_still_renews_for_existing_holders_but_cannot_be_bought(self):
        from .subscription_services import create_payment_request
        self._buy_primary()
        self._buy_batch(self.batch10)
        self.batch10.active = False
        self.batch10.save()
        renewal = self._buy_primary()
        self.assertEqual(renewal.amount, Decimal("3000.00") + Decimal("2000.00"))
        with self.assertRaises(ValidationError):
            create_payment_request(
                self.subscription, self.plan, purpose=SubscriptionPayment.PURPOSE_PAYROLL_BATCH, payroll_batch=self.batch10,
            )

    def test_unlimited_package_has_no_cap_and_refuses_batches(self):
        from .models import PayrollAddonTier
        from .payroll import assert_payroll_capacity
        from .subscription_services import create_payment_request
        unlimited = PayrollAddonTier.objects.create(plan=self.plan, unlimited=True, staff_limit=None, monthly_price=Decimal("9000.00"))
        self._buy_primary(tier=unlimited)
        state = self._state()
        self.assertTrue(state["unlimited"])
        self.assertIsNone(state["staff_limit"])
        for n in range(30):
            PayrollStaffProfile.objects.create(business=self.business, full_name=f"S{n}")
        assert_payroll_capacity(self.business)
        with self.assertRaises(ValidationError):
            create_payment_request(
                self.subscription, self.plan, purpose=SubscriptionPayment.PURPOSE_PAYROLL_BATCH, payroll_batch=self.batch5,
            )

    def test_switching_to_unlimited_drops_batches_and_does_not_bill_them(self):
        from .models import PayrollAddonTier
        self._buy_primary()
        self._buy_batch(self.batch5)
        unlimited = PayrollAddonTier.objects.create(plan=self.plan, unlimited=True, staff_limit=None, monthly_price=Decimal("9000.00"))
        payment = self._buy_primary(tier=unlimited)
        self.assertEqual(payment.amount, Decimal("9000.00"))
        self.assertFalse(self.business.payroll_addon.batches.exists())

    def test_lapsed_primary_package_disables_payroll_including_batches(self):
        self._buy_primary()
        self._buy_batch(self.batch5)
        addon = self.business.payroll_addon
        addon.paid_until = timezone.now() - timezone.timedelta(days=1)
        addon.save()
        state = self._state()
        self.assertFalse(state["enabled"])
        self.assertEqual(state["staff_limit"], 0)
        self.assertEqual(addon.batches.count(), 1)  # kept, so renewing restores them

    def test_tier_constraints_and_form_validation(self):
        from .forms import PayrollAddonTierForm
        from .models import PayrollAddonTier
        PayrollAddonTier.objects.create(plan=self.plan, unlimited=True, staff_limit=None, monthly_price=Decimal("1"))
        dup = PayrollAddonTierForm({"plan": self.plan.pk, "unlimited": "on", "monthly_price": "5", "active": "on"})
        self.assertFalse(dup.is_valid())
        missing = PayrollAddonTierForm({"plan": self.plan.pk, "monthly_price": "5", "active": "on"})
        self.assertFalse(missing.is_valid())
        self.assertIn("staff_limit", missing.errors)

    def test_checkout_page_and_post_flow(self):
        self._buy_primary()
        self.client.force_login(self.admin)
        page = self.client.get(reverse("payroll_addon_checkout"))
        self.assertContains(page, "Add extra staff")
        self.assertContains(page, "+5 staff")
        before = SubscriptionPayment.objects.count()
        post = self.client.post(reverse("payroll_addon_checkout"), {
            "kind": "batch", "batch_id": self.batch5.pk, "quantity": "0", "provider": "paystack",
        })
        self.assertRedirects(post, reverse("payroll_addon_checkout"), fetch_redirect_response=False)
        self.assertEqual(SubscriptionPayment.objects.count(), before)  # invalid quantity rejected

    def test_workspace_badge_shows_base_plus_extra(self):
        self._buy_primary()
        self._buy_batch(self.batch5)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("payroll_workspace"))
        self.assertContains(response, "Up to 7 active staff")
        self.assertContains(response, "2 + 5 extra")

    def test_plan_card_and_matrix_mention_batches_and_unlimited(self):
        from .models import PayrollAddonTier
        from .subscription_services import attach_payroll_tiers
        [plan] = attach_payroll_tiers([self.plan])
        row = next(r for r in build_plan_feature_matrix([plan]) if r["label"] == "Staff payroll add-on")
        self.assertIn("extra batches", row["values"][0]["sub"])
        PayrollAddonTier.objects.create(plan=self.plan, unlimited=True, staff_limit=None, monthly_price=Decimal("9000"))
        [plan] = attach_payroll_tiers([self.plan])
        self.assertEqual([t.capacity_label for t in plan.payroll_tiers], ["Up to 2 staff", "Unlimited staff"])
        row = next(r for r in build_plan_feature_matrix([plan]) if r["label"] == "Staff payroll add-on")
        self.assertIn("or unlimited", row["values"][0]["sub"])


    def test_any_active_batch_can_be_bought_on_any_plan(self):
        """Batches are a shared catalogue, not tied to the plan the business is on."""
        from .models import PayrollAddonTier
        from .subscription_services import attach_payroll_tiers, create_payment_request
        other = next(p for p in self.plans.values() if p.pk != self.plan.pk)
        self.assertFalse(hasattr(self.batch5, "plan_id"))
        self._buy_primary()
        payment = create_payment_request(
            self.subscription, self.plan, purpose=SubscriptionPayment.PURPOSE_PAYROLL_BATCH, payroll_batch=self.batch5,
        )
        self.assertEqual(payment.payroll_batch_id, self.batch5.pk)
        PayrollAddonTier.objects.create(plan=other, staff_limit=3, monthly_price=Decimal("1000.00"))
        plans = attach_payroll_tiers([self.plan, other])
        self.assertEqual([b.pk for b in plans[0].payroll_batches], [self.batch5.pk, self.batch10.pk])
        self.assertEqual([b.pk for b in plans[1].payroll_batches], [self.batch5.pk, self.batch10.pk])

    def test_only_one_active_batch_per_size_but_retired_size_can_be_reused(self):
        from django.db import IntegrityError, transaction
        from .forms import PayrollStaffBatchForm
        from .models import PayrollStaffBatch
        dup = PayrollStaffBatchForm({"staff_count": "5", "monthly_price": "999", "active": "on"})
        self.assertFalse(dup.is_valid())
        with self.assertRaises(IntegrityError), transaction.atomic():
            PayrollStaffBatch.objects.create(staff_count=5, monthly_price=Decimal("1"))
        self.batch5.active = False
        self.batch5.save()
        again = PayrollStaffBatchForm({"staff_count": "5", "monthly_price": "999", "active": "on"})
        self.assertTrue(again.is_valid(), again.errors)

class FounderPayrollPackageConfigTests(TestCase):
    def setUp(self):
        from .models import PayrollAddonTier, PayrollStaffBatch
        self.PayrollAddonTier, self.PayrollStaffBatch = PayrollAddonTier, PayrollStaffBatch
        self.founder = CustomUser.objects.create_superuser(username="founder-pay", password="safe-password-123", fullname="Founder")
        self.plan = list(ensure_default_plans().values())[-1]
        self.client.force_login(self.founder)
        self.url = reverse("founder_subscriptions")

    def test_founder_can_configure_unlimited_package_and_batches(self):
        self.client.post(self.url, {"action": "add_payroll_tier", "plan": self.plan.pk, "unlimited": "on", "monthly_price": "9000", "active": "on"})
        tier = self.PayrollAddonTier.objects.get(plan=self.plan)
        self.assertTrue(tier.unlimited)
        self.assertIsNone(tier.staff_limit)
        self.client.post(self.url, {"action": "add_payroll_batch", "staff_count": "5", "monthly_price": "1200", "active": "on"})
        batch = self.PayrollStaffBatch.objects.get()
        self.assertEqual(batch.label, "+5 staff")
        page = self.client.get(self.url)
        self.assertContains(page, "Unlimited staff")
        self.assertContains(page, "+5 staff")

    def test_limited_package_needs_a_staff_count_and_zero_batch_is_rejected(self):
        self.client.post(self.url, {"action": "add_payroll_tier", "plan": self.plan.pk, "monthly_price": "9000", "active": "on"})
        self.assertFalse(self.PayrollAddonTier.objects.exists())
        self.client.post(self.url, {"action": "add_payroll_batch", "staff_count": "0", "monthly_price": "10", "active": "on"})
        self.assertFalse(self.PayrollStaffBatch.objects.exists())

    def test_removing_a_held_batch_retires_it(self):
        from .models import BusinessPayrollAddon, BusinessPayrollBatch
        tier = self.PayrollAddonTier.objects.create(plan=self.plan, staff_limit=2, monthly_price=Decimal("1"))
        batch = self.PayrollStaffBatch.objects.create(staff_count=3, monthly_price=Decimal("1"))
        biz = Business.objects.create(name="Holder", slug="holder")
        addon = BusinessPayrollAddon.objects.create(business=biz, tier=tier, paid_until=timezone.now() + timezone.timedelta(days=5))
        BusinessPayrollBatch.objects.create(addon=addon, batch=batch)
        self.client.post(self.url, {"action": "delete_payroll_batch", "payroll_batch_id": batch.pk})
        batch.refresh_from_db()
        self.assertFalse(batch.active)
