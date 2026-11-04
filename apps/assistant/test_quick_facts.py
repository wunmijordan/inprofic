"""Quick-fact coverage and the founder / business configuration layers."""
import json
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse

from accounts.models import CustomUser, UserBusiness, UserModulePermission
from accounts.services import seed_business_roles
from core import daily_brief
from core.accounting import daily_components, summarise
from core.finance_views import _cash_account_balances, _finance_open_items
from core.models import AuditLog, Business, CashAccount
from expenses.models import Expense
from inventory.models import FinishedGood
from procurement.models import PurchaseOrder, PurchaseOrderItem
from production.models import Order
from sales.models import Sale, SaleItem

from . import config, engine, quickfacts
from .models import AssistantBusinessSettings, AssistantMessage, AssistantPlatformSettings
from .tests import Base, FakeProvider, say, TODAY
from .tools import run_tool


class ToolBase(Base):
    def sale(self, qty, price, *, paid=True, day=None, customer="Ada", good=None):
        sale = Sale.objects.create(business=self.biz, date=day or TODAY, customer=customer,
                                   source="walkin" if paid else "distribution_order", transaction_type="paid" if paid else "unpaid")
        SaleItem.objects.create(sale=sale, finished_good=good or self.good, piece_qty=Decimal(qty), price=Decimal(price), unit_cost=Decimal("1"))
        return sale

    def tool(self, tool, **args):
        result, _ = run_tool(tool, args, self.admin, self.biz)
        return result


class NewToolAccuracyTests(ToolBase):
    def test_sales_summary_matches_the_pl_components(self):
        self.sale(10, 10)                       # paid 100
        self.sale(4, 10, paid=False)            # unpaid 40
        self.sale(5, 10, day=TODAY - timedelta(days=1))
        result = self.tool("get_sales_summary", period="today")
        comps = daily_components(self.biz, TODAY, TODAY)
        pl = summarise(comps, TODAY, TODAY)
        self.assertEqual(result.data["sales_count"], 2)
        self.assertEqual(result.data["paid_sales_revenue"], daily_brief._money(self.biz, pl["revenue"]))
        self.assertEqual(result.data["sales_recorded"], daily_brief._money(self.biz, Decimal("140")))
        self.assertIn("unpaid", result.text.lower())
        yesterday = self.tool("get_sales_summary", period="yesterday")
        self.assertEqual(yesterday.data["sales_count"], 1)

    def test_top_products_rank_by_sales_value(self):
        other = FinishedGood.objects.create(business=self.biz, name="Cake", unit="pc", stock=Decimal("5"))
        self.sale(10, 10)                       # Bread 100
        self.sale(3, 50, good=other)            # Cake 150
        result = self.tool("get_top_products", period="this_month")
        self.assertEqual([p["product"] for p in result.data["products"]], ["Cake", "Bread"])
        empty = run_tool("get_top_products", {"period": "last_month"}, self.admin, self.biz)[0]
        self.assertIn("No sales", empty.text)

    def test_profit_and_expenses_match_the_report(self):
        from core.accounting import profit_and_loss
        self.sale(10, 10)
        Expense.objects.create(business=self.biz, date=TODAY, category="utilities", description="Power", amount=Decimal("30"), payment_status="unpaid")
        start = TODAY.replace(day=1)
        pl = profit_and_loss(self.biz, start, TODAY)
        profit = self.tool("get_profit_summary", period="this_month")
        self.assertEqual(profit.data["net_profit"], daily_brief._money(self.biz, pl["net_profit"]))
        expenses = self.tool("get_expenses_summary", period="this_month")
        self.assertEqual(expenses.data["operating_expenses"], daily_brief._money(self.biz, pl["operating_expenses"]))
        self.assertEqual(expenses.data["of_which_unpaid"], daily_brief._money(self.biz, Decimal("30")))
        self.assertIn("cash basis", profit.data["basis"])

    def test_cash_position_equals_the_sum_of_active_accounts(self):
        CashAccount.objects.create(business=self.biz, name="Till", active=True)
        CashAccount.objects.create(business=self.biz, name="Closed", active=False)
        result = self.tool("get_cash_position")
        expected = sum((a._calculated_balance for a in _cash_account_balances(self.biz, active_only=True)), Decimal("0"))
        self.assertEqual(result.data["total_cash_now"], daily_brief._money(self.biz, expected))
        self.assertEqual([a["account"] for a in result.data["accounts"]], ["Till"])

    def test_overdue_buckets_add_up_to_receivables(self):
        self.owed(self.biz, "Ada", 10, 10)
        old = self.owed(self.biz, "Ola", 4, 10)
        Sale.objects.filter(pk=old.pk).update(date=TODAY - timedelta(days=75))
        result = self.tool("get_overdue_receivables")
        self.assertEqual(result.data["total_owed_to_you"], daily_brief._money(self.biz, _finance_open_items(self.biz)["receivables"]))
        self.assertEqual(sum(b["sales"] for b in result.data["by_age"]), 2)

    def test_pending_approvals_count_requests_and_production_orders(self):
        po = PurchaseOrder.raw_objects.create(business=self.biz, date=TODAY, supplier="Main", payment_status="unpaid")
        PurchaseOrderItem.objects.create(purchase_order=po, finished_good=self.good, qty=Decimal("4"), unit_cost=Decimal("5"))
        Order.objects.create(business=self.biz, date=TODAY, status="pending", order_type="physical_store")
        approved = PurchaseOrder.raw_objects.create(business=self.biz, date=TODAY, status="approved")
        result = self.tool("get_pending_approvals")
        self.assertEqual(result.data["purchase_requests_waiting"], 1)
        self.assertEqual(result.data["production_orders_waiting"], 1)
        self.assertNotIn(approved.pk, [r["request_id"] for r in result.data["purchase_requests"]])
        self.assertTrue(any("Request #" in s["label"] for s in result.sources))

    def test_vat_position_says_so_when_not_registered_and_matches_the_return_when_registered(self):
        from core.tax import get_settings, vat_return
        self.assertFalse(self.tool("get_vat_position").data["registered"])
        tax = get_settings(self.biz)
        tax.vat_registered, tax.vat_rate = True, Decimal("7.5")
        tax.save()
        self.sale(10, 10)
        result = self.tool("get_vat_position", period="this_month")
        vat = vat_return(self.biz, TODAY.replace(day=1), TODAY)
        self.assertEqual(result.data["output_vat"], daily_brief._money(self.biz, vat["output_vat"]))
        self.assertIn("confirm with your tax authority", result.data["basis"])

    def test_periods_are_validated(self):
        from .tools import ToolError
        with self.assertRaises(ToolError):
            run_tool("get_sales_summary", {"period": "forever"}, self.admin, self.biz)
        with self.assertRaises(ToolError):
            run_tool("get_vat_position", {"period": "today"}, self.admin, self.biz)

    def test_period_ranges(self):
        from .tools import period_range
        thursday = TODAY - timedelta(days=TODAY.weekday()) + timedelta(days=3)
        self.assertEqual(period_range("this_week", thursday)[0].weekday(), 0)
        start, end = period_range("last_week", thursday)
        self.assertEqual((end - start).days, 6)
        first, last = period_range("last_month", thursday)
        self.assertEqual(first.day, 1)
        self.assertEqual((last + timedelta(days=1)).day, 1)


PHRASES = [
    ("who owes me money?", "owed_to_me"), ("which customers owe me", "owed_to_me"), ("what do I owe my suppliers", "i_owe"),
    ("how much do we owe", "i_owe"), ("how much did we sell today", "sales_today"), ("what were yesterday's sales", "sales_yesterday"),
    ("sales this week", "sales_this_week"), ("sales last week", "sales_last_week"), ("how are sales this month?", "sales_this_month"),
    ("revenue last month", "sales_last_month"), ("what's my profit", "profit_this_month"), ("profit last month", "profit_last_month"),
    ("did we make a loss this week", "profit_this_week"), ("profit last week", "profit_last_week"),
    ("what are my expenses this month", "expenses_this_month"), ("how much did we spend this week", "expenses_this_week"),
    ("how much cash do I have", "cash_position"), ("bank balance", "cash_position"), ("what is running low", "low_stock"),
    ("which ingredients need reordering", "low_stock"), ("what do I need to restock", "low_stock"),
    ("best selling products this week", "top_products_week"), ("top products", "top_products_month"), ("any overdue payments", "overdue"),
    ("how much vat do I owe", "vat_position"), ("anything waiting for my approval", "approvals"),
    ("pending purchase requests", "approvals"), ("recipe variance", "recipe_check"), ("is there waste in the kitchen", "recipe_check"),
    ("pending production orders", "orders_pending"), ("how is the business doing", "brief_yesterday"), ("give me a summary", "brief_yesterday"),
]


class MatcherTests(ToolBase):
    def test_realistic_phrasings_reach_the_right_fact(self):
        for question, expected in PHRASES:
            fact, _ = quickfacts.match(question, quickfacts.QUICK_FACTS)
            self.assertEqual(fact.key if fact else None, expected, question)

    def test_unmatched_questions_get_suggestions_not_a_guess(self):
        for question in ("hello there", "what is the meaning of life", "how do I reset my password"):
            fact, suggestions = quickfacts.match(question, quickfacts.QUICK_FACTS)
            self.assertIsNone(fact)
            self.assertTrue(suggestions)

    def test_only_enabled_facts_can_match(self):
        enabled = [f for f in quickfacts.QUICK_FACTS if f.key != "sales_today"]
        fact, suggestions = quickfacts.match("how much did we sell today", enabled)
        self.assertIsNone(fact)
        self.assertNotIn("sales_today", [f.key for f in suggestions])

    def test_every_fact_references_a_real_tool_with_valid_preset_arguments(self):
        from .tools import TOOLS, validate_arguments
        for fact in quickfacts.QUICK_FACTS:
            self.assertIn(fact.tool, TOOLS, fact.key)
            validate_arguments(TOOLS[fact.tool].parameters, fact.arguments)
            self.assertIn(fact.group, quickfacts.GROUPS)
        self.assertEqual(len({f.key for f in quickfacts.QUICK_FACTS}), len(quickfacts.QUICK_FACTS))
        self.assertGreaterEqual(len(quickfacts.QUICK_FACTS), 20)

    def test_every_fact_runs_against_an_empty_business(self):
        for fact in quickfacts.QUICK_FACTS:
            result, _ = run_tool(fact.tool, fact.arguments, self.admin, self.biz)
            self.assertTrue(result.text.strip(), fact.key)


class DeterministicFlowTests(ToolBase):
    def test_typed_question_is_answered_without_any_model(self):
        self.sale(10, 10)
        response = self.post({"question": "how much did we sell today?"})
        body = response.json()["message"]
        self.assertEqual((body["status"], body["via"]), ("ok", "Sales today"))
        self.assertIn("1 sale", body["content"])
        stored = AssistantMessage.raw_objects.filter(role="assistant").get()
        self.assertEqual(stored.model_calls, 0)

    def test_unmatched_typed_question_returns_clickable_suggestions(self):
        body = self.post({"question": "tell me a joke"}).json()["message"]
        self.assertEqual(body["status"], "refused")
        self.assertTrue(body["suggestions"])
        self.assertTrue(all({"key", "label"} <= set(s) for s in body["suggestions"]))
        follow = self.post({"quick": body["suggestions"][0]["key"]})
        self.assertEqual(follow.status_code, 200)

    def test_page_offers_grouped_facts_and_an_enabled_input_without_a_model(self):
        page = self.client.get(reverse("assistant") + "?panel=1")
        for group in ("Overview", "Sales", "Money", "Stock &amp; production", "Approvals"):
            self.assertContains(page, group)
        self.assertContains(page, 'data-quick="profit_last_month"')
        self.assertContains(page, "Ask in your own words")
        self.assertNotContains(page, "disabled>")

    @override_settings(ASSISTANT_AI_API_KEY="test-key")
    def test_when_the_business_turns_ai_off_the_model_is_never_called(self):
        AssistantBusinessSettings.raw_objects.create(business=self.biz, enabled=True, free_text_enabled=False)
        config.invalidate(self.biz)
        fake = FakeProvider(say("model answer"))
        reply = engine.ask(self.admin, self.biz, self.conversation(), "what is running low", provider=fake)
        self.assertEqual((reply.quick_fact, fake.calls), ("low_stock", []))
        self.assertEqual(reply.model_calls, 0)

    def test_member_without_module_access_does_not_get_that_fact(self):
        limited = self.member(self.biz, seed_business_roles(self.biz), CustomUser.ROLE_BUSINESS_ADMIN, "admin-nofin")
        UserModulePermission.objects.create(membership=UserBusiness.objects.get(user=limited, business=self.biz), module="finance", can_view=False, can_edit=False)
        cache.clear()
        keys = {f.key for f in quickfacts.enabled_facts(limited, self.biz)}
        self.assertNotIn("profit_this_month", keys)
        self.assertNotIn("owed_to_me", keys)
        self.assertIn("low_stock", keys)


class PlatformLayerTests(ToolBase):
    def set_platform(self, **fields):
        row = AssistantPlatformSettings.load()
        for key, value in fields.items():
            setattr(row, key, value)
        row.save()
        config.invalidate()

    def test_main_switch_hides_and_blocks_everything_at_once(self):
        self.assertContains(self.client.get(reverse("daily_brief")), reverse("assistant"))
        self.set_platform(assistant_enabled=False)
        self.assertEqual(self.client.get(reverse("assistant") + "?panel=1").status_code, 403)
        self.assertEqual(self.post({"quick": "low_stock"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("assistant_settings")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("daily_brief")), 'href="/assistant/"')

    def test_open_to_all_businesses_ignores_the_pilot_flag(self):
        Business.objects.filter(pk=self.biz.pk).update(assistant_enabled=False)
        self.assertEqual(self.client.get(reverse("assistant") + "?panel=1").status_code, 403)
        self.set_platform(open_to_all_businesses=True)
        self.assertEqual(self.client.get(reverse("assistant") + "?panel=1").status_code, 200)
        self.set_platform(open_to_all_businesses=True, assistant_enabled=False)
        self.assertEqual(self.client.get(reverse("assistant") + "?panel=1").status_code, 403)

    def test_quick_facts_master_switch(self):
        self.set_platform(quick_facts_enabled=False)
        self.assertEqual(self.post({"quick": "low_stock"}).status_code, 403)
        body = self.post({"question": "what is running low"}).json()["message"]
        self.assertEqual(body["status"], "refused")
        self.assertIn("switched off", body["content"])
        self.assertNotContains(self.client.get(reverse("assistant") + "?panel=1"), 'data-quick=')
        self.assertContains(self.client.get(reverse("assistant") + "?panel=1"), "Questions are switched off")

    @override_settings(ASSISTANT_AI_API_KEY="test-key")
    def test_quick_facts_off_does_not_stop_ai_answers_when_ai_is_on(self):
        self.set_platform(quick_facts_enabled=False)
        reply = engine.ask(self.admin, self.biz, self.conversation(), "anything", provider=FakeProvider(say("From the model.")))
        self.assertEqual((reply.status, reply.content), ("ok", "From the model."))

    @override_settings(ASSISTANT_AI_API_KEY="test-key")
    def test_platform_ai_switch_stops_the_model_not_the_quick_facts(self):
        self.set_platform(free_text_enabled=False)
        fake = FakeProvider(say("model"))
        reply = engine.ask(self.admin, self.biz, self.conversation(), "what is running low", provider=fake)
        self.assertEqual((reply.quick_fact, fake.calls), ("low_stock", []))

    def test_one_fact_switched_off_platform_wide(self):
        self.set_platform(disabled_quick_facts=["sales_today"])
        self.assertEqual(self.post({"quick": "sales_today"}).status_code, 403)
        self.assertNotContains(self.client.get(reverse("assistant") + "?panel=1"), 'data-quick="sales_today"')
        self.assertContains(self.client.get(reverse("assistant") + "?panel=1"), 'data-quick="sales_yesterday"')
        body = self.post({"question": "how much did we sell today"}).json()["message"]
        self.assertNotEqual(body["via"], "Sales today")

    def test_environment_switch_overrides_everything(self):
        with override_settings(ASSISTANT_ENABLED=False):
            self.assertEqual(self.client.get(reverse("assistant") + "?panel=1").status_code, 403)

    def test_a_business_cannot_switch_back_on_what_the_platform_turned_off(self):
        self.set_platform(disabled_quick_facts=["sales_today"])
        response = self.client.post(reverse("assistant_settings"), {"enabled": "1", "free_text_enabled": "1", "quick_fact": [f.key for f in quickfacts.QUICK_FACTS]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.post({"quick": "sales_today"}).status_code, 403)
        page = self.client.get(reverse("assistant_settings"))
        self.assertContains(page, "Switched off by the platform")

    def test_a_platform_disabled_fact_keeps_the_businesss_earlier_choice(self):
        AssistantBusinessSettings.raw_objects.create(business=self.biz, disabled_quick_facts=["sales_today"])
        self.set_platform(disabled_quick_facts=["sales_today"])
        self.client.post(reverse("assistant_settings"), {"enabled": "1", "free_text_enabled": "1", "quick_fact": [f.key for f in quickfacts.QUICK_FACTS]})
        self.assertIn("sales_today", AssistantBusinessSettings.raw_objects.get(business=self.biz).disabled_quick_facts)


class BusinessSettingsTests(ToolBase):
    def test_only_admins_reach_the_settings_page(self):
        self.assertEqual(self.client.get(reverse("assistant_settings")).status_code, 200)
        self.client.force_login(self.keeper)
        self.assertEqual(self.client.get(reverse("assistant_settings")).status_code, 403)
        self.assertEqual(self.client.post(reverse("assistant_settings"), {"enabled": "1"}).status_code, 403)

    def test_saving_turns_individual_facts_off_and_audits_the_change(self):
        keep = [f.key for f in quickfacts.QUICK_FACTS if f.key not in {"profit_this_month", "vat_position"}]
        response = self.client.post(reverse("assistant_settings"), {"enabled": "1", "free_text_enabled": "1", "quick_fact": keep + ["not-a-real-key"]})
        self.assertRedirects(response, reverse("assistant_settings"))
        row = AssistantBusinessSettings.raw_objects.get(business=self.biz)
        self.assertEqual(row.disabled_quick_facts, ["profit_this_month", "vat_position"])
        self.assertEqual(self.post({"quick": "profit_this_month"}).status_code, 403)
        self.assertEqual(self.post({"quick": "low_stock"}).status_code, 200)
        self.assertNotContains(self.client.get(reverse("assistant") + "?panel=1"), 'data-quick="vat_position"')
        self.assertTrue(AuditLog.objects.filter(business=self.biz, description="Assistant settings changed").exists())

    def test_a_business_can_turn_itself_off_and_back_on(self):
        self.client.post(reverse("assistant_settings"), {"quick_fact": [f.key for f in quickfacts.QUICK_FACTS]})   # 'enabled' unchecked
        self.assertEqual(self.client.get(reverse("assistant") + "?panel=1").status_code, 403)
        self.assertEqual(self.post({"quick": "low_stock"}).status_code, 403)
        self.assertNotContains(self.client.get(reverse("daily_brief")), 'href="/assistant/"')
        self.assertEqual(self.client.get(reverse("assistant_settings")).status_code, 200)      # can still reach settings
        self.client.post(reverse("assistant_settings"), {"enabled": "1", "free_text_enabled": "1", "quick_fact": [f.key for f in quickfacts.QUICK_FACTS]})
        self.assertEqual(self.client.get(reverse("assistant") + "?panel=1").status_code, 200)

    def test_settings_are_per_business(self):
        self.client.post(reverse("assistant_settings"), {"quick_fact": []})                     # everything off for Alpha
        other = Client()
        other.force_login(self.admin_b)
        self.assertEqual(other.get(reverse("assistant") + "?panel=1").status_code, 200)
        self.assertEqual(other.post(reverse("assistant_ask"), json.dumps({"quick": "low_stock"}), content_type="application/json").status_code, 200)

    def test_business_settings_page_links_to_assistant_settings_only_when_configurable(self):
        self.assertContains(self.client.get(reverse("business_settings")), reverse("assistant_settings"))
        row = AssistantPlatformSettings.load()
        row.assistant_enabled = False
        row.save()
        config.invalidate()
        self.assertNotContains(self.client.get(reverse("business_settings")), reverse("assistant_settings"))

    def test_defaults_when_no_settings_row_exists(self):
        self.assertFalse(AssistantBusinessSettings.raw_objects.filter(business=self.biz).exists())
        tenant = config.tenant_config(self.biz)
        self.assertTrue(tenant.enabled and tenant.free_text_enabled)
        self.assertEqual(tenant.disabled_facts, frozenset())


class FounderConsoleTests(ToolBase):
    def setUp(self):
        super().setUp()
        self.founder = CustomUser.objects.create_superuser(username="founder", password="pw-123456", email="f@example.com")
        self.founder_client = Client()
        self.founder_client.force_login(self.founder)
        self.url = reverse("founder_subscriptions") + "?workspace=management"

    def save(self, **post):
        data = {"action": "save_platform_assistant", **post}
        return self.founder_client.post(self.url, data)

    def test_page_shows_the_assistant_controls(self):
        page = self.founder_client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="platform-assistant"')
        self.assertContains(page, "Assistant available (main switch)")
        self.assertContains(page, "Sales today")
        self.assertContains(page, "Alpha Bakery")           # current pilot listed

    def test_only_superusers_can_save(self):
        response = self.client.post(self.url, {"action": "save_platform_assistant", "assistant_enabled": "on"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(AssistantPlatformSettings.load().assistant_enabled)

    def test_unticking_the_main_switch_takes_effect_immediately_for_members(self):
        self.assertEqual(self.client.get(reverse("assistant") + "?panel=1").status_code, 200)       # primes the settings cache
        response = self.save(quick_facts_enabled="on", free_text_enabled="on", quick_fact=[f.key for f in quickfacts.QUICK_FACTS],
                             pilot_keep=[self.biz.pk, self.other.pk])
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AssistantPlatformSettings.load().assistant_enabled)
        self.assertEqual(self.client.get(reverse("assistant") + "?panel=1").status_code, 403)
        self.assertEqual(self.post({"quick": "low_stock"}).status_code, 403)

    def test_saving_persists_every_control(self):
        self.save(assistant_enabled="on", free_text_enabled="on", open_to_all_businesses="on",
                  quick_fact=[f.key for f in quickfacts.QUICK_FACTS if f.key != "vat_position"], pilot_keep=[self.biz.pk])
        row = AssistantPlatformSettings.load()
        self.assertEqual((row.assistant_enabled, row.quick_facts_enabled, row.free_text_enabled, row.open_to_all_businesses),
                         (True, False, True, True))
        self.assertEqual(row.disabled_quick_facts, ["vat_position"])
        self.assertEqual(row.updated_by, self.founder)
        self.assertEqual(list(Business.objects.filter(assistant_enabled=True).values_list("slug", flat=True)), ["alpha"])

    def test_pilot_businesses_can_be_added_by_slug_and_unknown_slugs_are_reported(self):
        gamma = Business.objects.create(name="Gamma", slug="gamma")
        response = self.founder_client.post(self.url, {"action": "save_platform_assistant", "assistant_enabled": "on",
                                                       "quick_facts_enabled": "on", "pilot_keep": [self.biz.pk, self.other.pk],
                                                       "pilot_add": "gamma, nope-biz", "quick_fact": [f.key for f in quickfacts.QUICK_FACTS]}, follow=True)
        gamma.refresh_from_db()
        self.assertTrue(gamma.assistant_enabled)
        self.assertContains(response, "No business matched: nope-biz")

    def test_removing_a_pilot_blocks_that_business_only(self):
        self.save(assistant_enabled="on", quick_facts_enabled="on", quick_fact=[f.key for f in quickfacts.QUICK_FACTS], pilot_keep=[self.other.pk])
        self.assertEqual(self.client.get(reverse("assistant") + "?panel=1").status_code, 403)
        other = Client()
        other.force_login(self.admin_b)
        self.assertEqual(other.get(reverse("assistant") + "?panel=1").status_code, 200)


class CustomQuestionTests(ToolBase):
    def configure_question(self, business=None, question='How did our counter do?', report='sales_today'):
        return AssistantBusinessSettings.raw_objects.create(
            business=business or self.biz,
            custom_questions=[{'question': question, 'report': report}])

    def test_custom_question_reads_the_mapped_report_without_a_provider(self):
        self.sale(3, 10)
        self.configure_question()
        body = self.post({'question': 'How did our counter do?'}).json()['message']
        self.assertEqual(body['status'], 'ok')
        stored = AssistantMessage.raw_objects.get(pk=body['id'])
        self.assertEqual(stored.model_calls, 0)
        self.assertEqual(stored.tool_calls, [{'name': 'get_sales_summary', 'arguments': {'period': 'today'}}])

    def test_custom_questions_keep_tenant_and_report_permission_boundaries(self):
        self.configure_question(self.other, question='Private question')
        self.assertFalse(any(f.label == 'Private question' for f in quickfacts.enabled_facts(self.admin, self.biz)))
        row = self.configure_question(report='profit_this_month')
        row.disabled_quick_facts = ['profit_this_month']
        row.save()
        config.invalidate(self.biz)
        self.assertFalse(any(f.key.startswith('custom_') for f in quickfacts.enabled_facts(self.admin, self.biz)))
        self.assertEqual(self.post({'quick': 'custom_missing'}).status_code, 403)

    def test_settings_save_and_reject_an_unregistered_report(self):
        response = self.client.post(reverse('assistant_settings'), {
            'enabled':'1', 'custom_question':['How did our counter do?'], 'custom_report':['sales_today'],
            'quick_fact':['sales_today']})
        self.assertEqual(response.status_code, 302)
        row = AssistantBusinessSettings.raw_objects.get(business=self.biz)
        self.assertEqual(row.custom_questions[0]['report'], 'sales_today')
        self.client.post(reverse('assistant_settings'), {
            'enabled':'1', 'custom_question':['Unsafe'], 'custom_report':['execute_sql']})
        row.refresh_from_db()
        self.assertEqual(row.custom_questions[0]['report'], 'sales_today')

    def test_direct_assistant_entry_opens_the_tray(self):
        self.assertRedirects(self.client.get(reverse('assistant')), '/dashboard/?assistant=1', fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse('daily_brief')), 'data-alert-channel="assistant"')

    def test_conflicting_builtin_question_and_missing_mapping_do_not_overwrite_settings(self):
        row = self.configure_question()
        for payload in [
            {'custom_question':['Sales today'], 'custom_report':['sales_last_month']},
            {'custom_question':['One','Two'], 'custom_report':['sales_today']},
            {'custom_question':['Counter?','Counter!'], 'custom_report':['sales_today','sales_last_month']},
        ]:
            self.client.post(reverse('assistant_settings'), {'enabled':'1', **payload})
            row.refresh_from_db()
            self.assertEqual(row.custom_questions[0]['question'], 'How did our counter do?')
