from decimal import Decimal

from django.test import TestCase

from .models import SubscriptionPayment, SubscriptionPlan
from .subscription_services import attach_active_promotions, ensure_default_plans, payment_amount


class BillingCycleTests(TestCase):
    def setUp(self):
        ensure_default_plans()
        self.plan = SubscriptionPlan.objects.get(code=SubscriptionPlan.CODE_PRODUCTION)
        self.plan.monthly_price = Decimal("10000")
        self.plan.quarterly_discount_percent = Decimal("5")
        self.plan.biannual_discount_percent = Decimal("10")
        self.plan.yearly_discount_percent = Decimal("20")
        self.plan.save()

    def test_each_cycle_applies_its_own_discount_to_the_months_it_covers(self):
        self.assertEqual(payment_amount(self.plan, 1, billing_cycle=SubscriptionPayment.CYCLE_MONTHLY, promotion=False), Decimal("10000.00"))
        self.assertEqual(payment_amount(self.plan, 1, billing_cycle=SubscriptionPayment.CYCLE_QUARTERLY, promotion=False), Decimal("28500.00"))
        self.assertEqual(payment_amount(self.plan, 1, billing_cycle=SubscriptionPayment.CYCLE_BIANNUAL, promotion=False), Decimal("54000.00"))
        self.assertEqual(payment_amount(self.plan, 1, billing_cycle=SubscriptionPayment.CYCLE_YEARLY, promotion=False), Decimal("96000.00"))

    def test_additional_services_are_included_in_the_cycle_total(self):
        addon = self.plan.additional_service_monthly_price
        expected = ((Decimal("10000") + addon) * 3 * Decimal("0.95")).quantize(Decimal("0.01"))
        self.assertEqual(payment_amount(self.plan, 2, billing_cycle="quarterly", promotion=False), expected)

    def test_plan_exposes_ordered_cycle_options_for_both_switch_panels(self):
        plan = attach_active_promotions([self.plan])[0]
        self.assertEqual([o["key"] for o in plan.cycle_options], ["monthly", "quarterly", "biannual", "yearly"])
        quarterly = plan.cycle_options[1]
        self.assertEqual(quarterly["total"], Decimal("28500.00"))
        self.assertEqual(quarterly["per_month"], Decimal("9500.00"))

    def test_marketing_page_has_the_billing_switch(self):
        page = self.client.get("/").content.decode()
        self.assertIn("data-billing-switch", page)
        for cycle in ("monthly", "quarterly", "biannual", "yearly"):
            self.assertIn(f'data-billing-cycle="{cycle}"', page)
        self.assertIn('data-cycle-price="quarterly"', page)


class DetailedMatrixTests(TestCase):
    def setUp(self):
        ensure_default_plans()

    def test_matrix_is_grouped_and_every_plan_gets_a_value_per_feature(self):
        from .subscription_services import build_plan_feature_matrix
        plans = list(SubscriptionPlan.objects.filter(active=True).order_by("monthly_price", "id"))
        rows = build_plan_feature_matrix(plans)
        groups = [r["group"] for r in rows if r.get("group")]
        for expected in ("Capacity & billing", "Workspace modules", "Production", "Selling & commerce", "Delivery"):
            self.assertIn(expected, groups)
        features = [r for r in rows if not r.get("group")]
        self.assertGreater(len(features), 40)
        self.assertTrue(all(len(r["values"]) == len(plans) for r in features))
        labels = {r["label"] for r in features}
        self.assertIn("Targeted QR codes for social selling", labels)
        self.assertIn("Production-floor entry roles", labels)

    def test_every_row_is_tagged_with_its_group_for_the_accordion(self):
        from .subscription_services import build_plan_feature_matrix
        plans = list(SubscriptionPlan.objects.filter(active=True).order_by("monthly_price", "id"))
        rows = build_plan_feature_matrix(plans)
        self.assertTrue(all(r.get("section") for r in rows))
        for index, row in enumerate(rows):
            if row.get("group"):
                self.assertEqual(row["section"], row["group"])
            else:
                previous_group = next(r["group"] for r in reversed(rows[:index]) if r.get("group"))
                self.assertEqual(row["section"], previous_group)

    def test_workspace_features_follow_the_entitlement_of_the_module_they_sit_in(self):
        from .subscription_services import build_plan_feature_matrix
        plans = list(SubscriptionPlan.objects.filter(active=True).order_by("monthly_price", "id"))
        rows = {r["label"]: [v["state"] for v in r["values"]] for r in build_plan_feature_matrix(plans) if not r.get("group")}
        # In-premise POS is part of Commerce, Production Entry is part of Production.
        self.assertEqual(rows["In-premise POS"], rows["Hosted storefront & Order Now links"])
        self.assertEqual(rows["Assign a production entry to channels"], rows["Production orders & batches"])
        self.assertEqual(rows["Production-floor entry roles"], rows["Production orders & batches"])
        pro_index = [p.code for p in plans].index(SubscriptionPlan.CODE_BUSINESS_PRO)
        self.assertEqual(rows["In-premise POS"][pro_index], "included")

    def test_matrix_renders_one_exclusive_accordion_panel_per_group(self):
        from .subscription_services import build_plan_feature_matrix
        plans = list(SubscriptionPlan.objects.filter(active=True).order_by("monthly_price", "id"))
        rows = build_plan_feature_matrix(plans)
        group_count = len([r for r in rows if r.get("group")])
        from django.template.loader import render_to_string
        for theme, marker in (("", 'data-matrix-theme="app"'), ("dark", 'data-matrix-theme="dark"')):
            html = render_to_string("accounts/_plan_feature_matrix.html", {"plans": plans, "plan_feature_matrix": rows, "matrix_theme": theme})
            self.assertIn(marker, html)
            self.assertEqual(html.count('name="pfm-group"'), group_count)
            self.assertEqual(html.count("<details class=\"pfm__group\""), group_count)
            self.assertEqual(html.count("data-pfm-group open>"), 1)  # only the first group starts open

    def test_pages_explain_features_with_help_tips(self):
        page = self.client.get("/").content.decode()
        self.assertIn("data-form-help-tip", page)
        self.assertIn("form-experience.js", page)


class PlanCardTests(TestCase):
    def test_cards_no_longer_hard_code_a_yearly_line_under_the_monthly_price(self):
        ensure_default_plans()
        page = self.client.get("/").content.decode()
        self.assertIn('data-cycle-price="yearly"', page)       # yearly lives in the switch now
        self.assertNotIn("yearly discount</p>", page)           # the old fixed yearly caption is gone