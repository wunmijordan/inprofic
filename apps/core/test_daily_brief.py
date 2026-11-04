"""Morning brief: content, WhatsApp link, page, opt-in and once-a-day email."""
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal

from unittest import mock

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from expenses.models import Expense
from inventory.models import FinishedGood, RawMaterial
from production.models import Order
from sales.models import Sale, SaleItem

from . import daily_brief
from .jobs import SCHEDULED_COMMANDS
from .models import Business, DailyBriefPreference

TODAY = timezone.localdate()
YESTERDAY = TODAY - timedelta(days=1)


class BriefBase(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Brief Bakery", slug="brief-bakery")
        roles = seed_business_roles(self.business)
        self.admin = CustomUser.objects.create_user(username="brief-admin", password="pw-123456",
                                                    email="owner@example.com", phone="+234 801 234 5678")
        UserBusiness.objects.create(user=self.admin, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.bread = FinishedGood.objects.create(business=self.business, name="Bread", unit="loaf", stock=Decimal("5"),
                                                 reorder_level=Decimal("10"))

    def sell(self, qty, price, paid=True, customer="Ada"):
        # A customer order left unpaid is a receivable; a paid walk-in is not.
        sale = Sale.objects.create(business=self.business, date=YESTERDAY, customer=customer,
                                   source="walkin" if paid else "distribution_order",
                                   transaction_type="paid" if paid else "unpaid")
        SaleItem.objects.create(sale=sale, finished_good=self.bread, piece_qty=Decimal(qty), price=Decimal(price), unit_cost=Decimal("1"))


class ContentTests(BriefBase):
    def test_quiet_day_reads_cleanly(self):
        brief = daily_brief.build_brief(self.business)
        text = daily_brief.render_text(brief)
        self.assertIn(f"{daily_brief.time_greeting()}, Brief Bakery", text)
        self.assertIn("0 sales", text)
        self.assertIn("1 item low on stock", text)  # the seeded bread is below its reorder level

    def test_greeting_uses_current_lagos_time_instead_of_the_report_date(self):
        brief = daily_brief.build_brief(self.business)
        with timezone.override('Africa/Lagos'):
            for hour, minute, expected in [(7,0,'Good morning'), (10,59,'Good morning'),
                                           (11,0,'Good afternoon'), (15,59,'Good afternoon'),
                                           (16,0,'Good evening'), (22,59,'Good evening')]:
                now = datetime(TODAY.year, TODAY.month, TODAY.day, hour, minute, tzinfo=datetime_timezone.utc)
                with self.subTest(now=now):
                    text = daily_brief.render_text(brief, today=TODAY, now=now)
                    self.assertTrue(text.startswith(f'*{expected}, Brief Bakery*'))
                    self.assertIn('Yesterday', text)

    def test_selected_report_sections_keep_the_time_aware_greeting(self):
        from .executive_brief import enrich
        brief = daily_brief.build_brief(self.business)
        enrich(brief, YESTERDAY, YESTERDAY, user=self.admin, selected_sections=['products'])
        evening = datetime(TODAY.year, TODAY.month, TODAY.day, 18, tzinfo=datetime_timezone.utc)
        text = daily_brief.render_text(brief, today=TODAY, now=evening)
        self.assertTrue(text.startswith('*Good evening, Brief Bakery*'))
        self.assertIn('Product stock', text)
        self.assertNotIn('Cash on hand:', text)

    def test_figures_and_attention_items(self):
        self.sell(10, 5)
        self.sell(4, 5, paid=False, customer="Ola")
        Expense.objects.create(business=self.business, date=YESTERDAY, category="utilities", description="Power", amount=Decimal("30"))
        Order.objects.create(business=self.business, date=TODAY, status="pending", order_type="physical_store")
        RawMaterial.objects.create(business=self.business, name="Flour", usage_unit="kg", stock=Decimal("1"), reorder_level=Decimal("9"))
        text = daily_brief.render_text(daily_brief.build_brief(self.business))
        sym = self.business.currency_symbol
        self.assertIn(f"Sales: {sym}70.00 from 2 sales", text)
        self.assertIn(f"Expenses: {sym}30.00", text)
        self.assertIn("Ola", text)
        self.assertIn("2 items low on stock", text)
        self.assertIn("1 order awaiting approval", text)

    def test_whatsapp_link_with_and_without_a_number(self):
        with_number = daily_brief.whatsapp_link("Hi there", "+234 801 234 5678")
        self.assertTrue(with_number.startswith("https://wa.me/2348012345678?text=Hi%20there"))
        self.assertTrue(daily_brief.whatsapp_link("Hi").startswith("https://wa.me/?text="))


class PageAndOptInTests(BriefBase):
    def test_login_is_required(self):
        self.assertEqual(self.client.get(reverse("daily_brief")).status_code, 302)

    def test_page_shows_the_brief_and_a_tap_to_send_link(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("daily_brief"))
        self.assertContains(page, f"{daily_brief.time_greeting()}, Brief Bakery")
        self.assertContains(page, "https://wa.me/2348012345678")
        self.assertContains(page, "Nothing is sent automatically")

    def test_opt_in_and_out(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("daily_brief"), {"send_email": "1"})
        self.assertTrue(DailyBriefPreference.raw_objects.get().send_email)
        self.client.post(reverse("daily_brief"), {})
        self.assertFalse(DailyBriefPreference.raw_objects.get().send_email)


class EmailTests(BriefBase):
    def opt_in(self, user=None):
        return DailyBriefPreference.raw_objects.create(business=self.business, user=user or self.admin, send_email=True)

    def at(self, hour):
        return timezone.make_aware(datetime(TODAY.year, TODAY.month, TODAY.day, hour, 0))

    def test_sends_once_per_day_after_seven(self):
        pref = self.opt_in()
        self.assertEqual(daily_brief.send_due_briefs(now=self.at(6)), 0)  # too early
        self.assertEqual(daily_brief.send_due_briefs(now=self.at(8)), 1)
        self.assertEqual(daily_brief.send_due_briefs(now=self.at(9)), 0)  # already sent today
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["owner@example.com"])
        self.assertNotIn("*", mail.outbox[0].body)
        pref.refresh_from_db()
        self.assertEqual(pref.last_sent_on, TODAY)

    def test_skips_people_without_email_or_access(self):
        roles = seed_business_roles(self.business)
        no_email = CustomUser.objects.create_user(username="noemail", password="pw-123456")
        UserBusiness.objects.create(user=no_email, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.opt_in(no_email)
        former = CustomUser.objects.create_user(username="former", password="pw-123456", email="former@example.com")
        membership = UserBusiness.objects.create(user=former, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.opt_in(former)
        UserBusiness.objects.filter(pk=membership.pk).update(active=False)
        self.assertEqual(daily_brief.send_due_briefs(now=self.at(8)), 0)
        self.assertEqual(mail.outbox, [])

    def test_opted_out_members_get_nothing(self):
        DailyBriefPreference.raw_objects.create(business=self.business, user=self.admin, send_email=False)
        self.assertEqual(daily_brief.send_due_briefs(now=self.at(8)), 0)

    def test_the_job_is_registered(self):
        self.assertIn("send_daily_brief", SCHEDULED_COMMANDS)


class WhatsAppTemplateTests(BriefBase):
    """Automated delivery through an approved template, with consent, one attempt a day and email fallback."""

    def setUp(self):
        super().setUp()
        from whatsapp_storefront.models import WhatsAppConnection
        self.connection = WhatsAppConnection.raw_objects.create(
            business=self.business, waba_id="W1", phone_number_id="P1", encrypted_access_token="enc",
            status=WhatsAppConnection.STATUS_CONNECTED, brief_template_name="daily_brief", brief_template_language="en")
        self.pref = DailyBriefPreference.raw_objects.create(
            business=self.business, user=self.admin, whatsapp_enabled=True, whatsapp_number="2348012345678",
            whatsapp_consent_at=timezone.now())

    def at(self, hour=8):
        return timezone.make_aware(datetime(TODAY.year, TODAY.month, TODAY.day, hour, 0))

    def run_job(self, hour=8, **patches):
        with mock.patch("whatsapp_storefront.meta.decrypt_access_token", return_value="tok"), \
             mock.patch("whatsapp_storefront.meta.graph_request", **patches) as graph:
            return daily_brief.send_due_briefs(now=self.at(hour)), graph

    def test_parameters_match_the_template_and_are_single_line(self):
        self.sell(10, 5)
        params = daily_brief.template_parameters(daily_brief.build_brief(self.business))
        self.assertEqual(len(params), daily_brief.TEMPLATE_PARAM_COUNT)
        self.assertEqual(daily_brief.TEMPLATE_BODY.count("{{"), daily_brief.TEMPLATE_PARAM_COUNT)
        self.assertEqual(params[0], "Brief Bakery")
        self.assertTrue(all("\n" not in p and "\t" not in p and p for p in params))

    def test_cleaner_removes_what_whatsapp_rejects(self):
        from whatsapp_storefront.meta import clean_template_param
        self.assertEqual(clean_template_param("a\nb\tc        d"), "a · b c   d")
        self.assertEqual(clean_template_param(""), "-")
        self.assertEqual(len(clean_template_param("x" * 5000)), 1000)

    def test_sends_a_template_message_once_a_day(self):
        sent, graph = self.run_job(return_value={"messages": [{"id": "wamid"}]})
        self.assertEqual(sent, 1)
        body = graph.call_args.kwargs["body"]
        self.assertEqual((body["type"], body["to"], body["template"]["name"]), ("template", "2348012345678", "daily_brief"))
        self.assertEqual(len(body["template"]["components"][0]["parameters"]), 8)
        self.assertEqual(graph.call_args.args[0], "P1/messages")
        self.pref.refresh_from_db()
        self.assertEqual((self.pref.last_whatsapp_status, self.pref.last_whatsapp_on), ("sent", TODAY))
        self.assertEqual(self.run_job(return_value={})[0], 0)  # not again today
        self.assertEqual(mail.outbox, [])  # email was not opted into

    def test_failure_is_recorded_not_retried_and_falls_back_to_email(self):
        from whatsapp_storefront.meta import MetaWhatsAppError
        sent, graph = self.run_job(side_effect=MetaWhatsAppError("Template not approved"))
        self.assertEqual(sent, 1)  # the email fallback
        self.pref.refresh_from_db()
        self.assertEqual((self.pref.last_whatsapp_status, self.pref.last_whatsapp_error), ("failed", "Template not approved"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.run_job(side_effect=MetaWhatsAppError("again"))[1].call_count, 0)  # one attempt a day

    def test_no_consent_no_number_no_template_no_connection_send_nothing(self):
        for field, value, status in (("whatsapp_consent_at", None, "no_consent"), ("whatsapp_number", "", "no_number")):
            DailyBriefPreference.raw_objects.filter(pk=self.pref.pk).update(**{field: value}, last_whatsapp_on=None)
            sent, graph = self.run_job(return_value={})
            self.assertEqual(graph.call_count, 0, status)
            self.pref.refresh_from_db()
            self.assertEqual(self.pref.last_whatsapp_status, status)
            DailyBriefPreference.raw_objects.filter(pk=self.pref.pk).update(
                whatsapp_consent_at=timezone.now(), whatsapp_number="2348012345678", last_whatsapp_on=None)
        type(self.connection).raw_objects.filter(pk=self.connection.pk).update(brief_template_name="")
        self.run_job(return_value={})
        self.pref.refresh_from_db()
        self.assertEqual(self.pref.last_whatsapp_status, "no_template")
        type(self.connection).raw_objects.filter(pk=self.connection.pk).update(status="disconnected", brief_template_name="daily_brief")
        DailyBriefPreference.raw_objects.filter(pk=self.pref.pk).update(last_whatsapp_on=None)
        self.run_job(return_value={})
        self.pref.refresh_from_db()
        self.assertEqual(self.pref.last_whatsapp_status, "no_connection")

    def test_email_and_whatsapp_both_deliver_when_both_are_on(self):
        DailyBriefPreference.raw_objects.filter(pk=self.pref.pk).update(send_email=True)
        sent, _ = self.run_job(return_value={"messages": [{}]})
        self.assertEqual(sent, 2)
        self.assertEqual(len(mail.outbox), 1)

    def test_send_template_builds_the_documented_cloud_api_payload(self):
        from whatsapp_storefront.meta import send_template, MetaWhatsAppError
        with mock.patch("whatsapp_storefront.meta.decrypt_access_token", return_value="tok"), \
             mock.patch("whatsapp_storefront.meta.graph_request", return_value={}) as graph:
            send_template(self.connection, "234801", "t", "en_US", ["a\nb", "c"])
            body = graph.call_args.kwargs["body"]
            self.assertEqual(body["template"]["language"], {"code": "en_US"})
            self.assertEqual(body["template"]["components"][0]["parameters"][0], {"type": "text", "text": "a · b"})
            self.assertEqual(graph.call_args.kwargs["token"], "tok")
            with self.assertRaises(MetaWhatsAppError):
                send_template(self.connection, "234801", "", "en", [])


class WhatsAppPageTests(BriefBase):
    def setUp(self):
        super().setUp()
        from whatsapp_storefront.models import WhatsAppConnection
        self.connection = WhatsAppConnection.raw_objects.create(
            business=self.business, waba_id="W1", phone_number_id="P1", encrypted_access_token="enc",
            status=WhatsAppConnection.STATUS_CONNECTED)
        self.client.force_login(self.admin)

    def post(self, **data):
        return self.client.post(reverse("daily_brief"), data, follow=True)

    def test_page_guides_through_template_then_number(self):
        self.assertContains(self.client.get(reverse("daily_brief")), "approved message template first")
        self.post(action="template", brief_template_name="daily_brief", brief_template_language="en")
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.brief_template_name, "daily_brief")
        page = self.client.get(reverse("daily_brief"))
        self.assertContains(page, "Send it to me every morning")
        self.assertContains(page, "Good morning {{1}}")

    def test_template_settings_are_validated_and_admin_only(self):
        for name, language, fragment in (("Bad Name!", "en", "lowercase"), ("ok_name", "english!", "language code")):
            self.assertContains(self.post(action="template", brief_template_name=name, brief_template_language=language), fragment)
        self.assertEqual(type(self.connection).raw_objects.get().brief_template_name, "")
        roles = seed_business_roles(self.business)
        other_key = next(k for k in roles if k != CustomUser.ROLE_BUSINESS_ADMIN)
        staff = CustomUser.objects.create_user(username="brief-staff", password="pw-123456")
        UserBusiness.objects.create(user=staff, business=self.business, role=roles[other_key])
        self.client.force_login(staff)
        self.assertEqual(self.client.post(reverse("daily_brief"), {"action": "template", "brief_template_name": "x"}).status_code, 403)

    def test_consent_and_number_rules(self):
        self.post(action="template", brief_template_name="daily_brief")
        self.assertContains(self.post(action="whatsapp", whatsapp_enabled="1", whatsapp_number="12"), "country code")
        self.assertContains(self.post(action="whatsapp", whatsapp_enabled="1", whatsapp_number="2348012345678"), "consent")
        self.post(action="whatsapp", whatsapp_enabled="1", whatsapp_consent="1", whatsapp_number="+234 801 234 5678")
        pref = DailyBriefPreference.raw_objects.get()
        self.assertEqual((pref.whatsapp_enabled, pref.whatsapp_number), (True, "2348012345678"))
        self.assertIsNotNone(pref.whatsapp_consent_at)
        self.post(action="whatsapp", whatsapp_number="2348012345678")  # consent withdrawn
        pref.refresh_from_db()
        self.assertEqual((pref.whatsapp_enabled, pref.whatsapp_consent_at), (False, None))

    def test_test_message_is_capped_per_day(self):
        from django.core.cache import cache
        cache.clear()
        self.post(action="template", brief_template_name="daily_brief")
        self.post(action="whatsapp", whatsapp_enabled="1", whatsapp_consent="1", whatsapp_number="2348012345678")
        with mock.patch("whatsapp_storefront.meta.decrypt_access_token", return_value="tok"), \
             mock.patch("whatsapp_storefront.meta.graph_request", return_value={}) as graph:
            for _ in range(3):
                self.assertContains(self.post(action="test"), "Test sent")
            self.assertContains(self.post(action="test"), "up to 3 test messages")
        self.assertEqual(graph.call_count, 3)


class DateSelectionTests(BriefBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_parse_day_defaults_clamps_and_rejects_bad_input(self):
        self.assertEqual(daily_brief.parse_day("", TODAY), (YESTERDAY, ""))
        self.assertEqual(daily_brief.parse_day("2026-01-05", TODAY)[0].isoformat(), "2026-01-05")
        day, note = daily_brief.parse_day("not-a-date", TODAY)
        self.assertEqual(day, YESTERDAY)
        self.assertTrue(note)
        day, note = daily_brief.parse_day((TODAY + timedelta(days=3)).isoformat(), TODAY)
        self.assertEqual(day, TODAY)
        self.assertIn("Future", note)

    def test_chosen_date_drives_figures_text_and_whatsapp_link(self):
        older = TODAY - timedelta(days=5)
        sale = Sale.objects.create(business=self.business, date=older, customer="Ada", source="walkin", transaction_type="paid")
        SaleItem.objects.create(sale=sale, finished_good=self.bread, piece_qty=Decimal("4"), price=Decimal("5"), unit_cost=Decimal("1"))
        page = self.client.get(reverse("daily_brief"), {"date": older.isoformat()})
        self.assertContains(page, f"{older:%a %d %b %Y}")
        self.assertContains(page, "1 sale")
        self.assertContains(page, "cash, owed, owing and stock are current")
        self.assertContains(page, "wa.me/2348012345678?text=")
        self.assertContains(page, f'max="{TODAY.isoformat()}"')
        default = self.client.get(reverse("daily_brief"))
        self.assertContains(default, f"{daily_brief.time_greeting()}, Brief Bakery")
        self.assertNotContains(default, "cash, owed, owing and stock are current")

    def test_bad_and_future_dates_fall_back_with_a_note(self):
        page = self.client.get(reverse("daily_brief"), {"date": "garbage"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "was not understood")
        page = self.client.get(reverse("daily_brief"), {"date": (TODAY + timedelta(days=9)).isoformat()})
        self.assertContains(page, "Future dates")

    def test_post_returns_to_the_chosen_date(self):
        url = reverse("daily_brief") + "?view=weekly&date=2026-01-05"
        response = self.client.post(url, {"action": "email", "send_email": "1"})
        self.assertRedirects(response, url, fetch_redirect_response=False)

    def test_email_me_this_sends_the_selected_date_and_is_capped(self):
        url = reverse("daily_brief") + f"?date={YESTERDAY.isoformat()}"
        self.client.post(url, {"action": "email_now"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["owner@example.com"])
        self.assertIn("Brief Bakery", mail.outbox[0].subject)
        self.assertNotIn("*", mail.outbox[0].body)
        from .brief_views import EMAIL_SENDS_PER_DAY
        for _ in range(EMAIL_SENDS_PER_DAY + 2):
            self.client.post(url, {"action": "email_now"})
        self.assertEqual(len(mail.outbox), EMAIL_SENDS_PER_DAY)


class WeeklySummaryTests(BriefBase):
    LAST_MONDAY = daily_brief.default_week_start(TODAY)

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def sell_on(self, day, qty, price, paid=True):
        sale = Sale.objects.create(business=self.business, date=day, customer="Ada",
                                   source="walkin" if paid else "distribution_order",
                                   transaction_type="paid" if paid else "unpaid")
        SaleItem.objects.create(sale=sale, finished_good=self.bread, piece_qty=Decimal(qty), price=Decimal(price), unit_cost=Decimal("1"))

    def test_week_helpers(self):
        monday = daily_brief.week_start_of(TODAY)
        self.assertEqual(monday.weekday(), 0)
        self.assertEqual(daily_brief.default_week_start(TODAY), monday - timedelta(days=7))
        start, _ = daily_brief.parse_week((monday + timedelta(days=4)).isoformat(), TODAY)
        self.assertEqual(start, monday)
        start, note = daily_brief.parse_week((TODAY + timedelta(days=30)).isoformat(), TODAY)
        self.assertEqual((start, bool(note)), (monday, True))

    def test_weekly_pl_matches_finance_and_compares_with_the_week_before(self):
        from .accounting import profit_and_loss
        mon = self.LAST_MONDAY
        self.sell_on(mon + timedelta(days=1), 10, 10)           # 100 revenue, 10 cost
        self.sell_on(mon + timedelta(days=3), 20, 10)           # 200 revenue, 20 cost -> best day
        self.sell_on(mon + timedelta(days=2), 5, 10, paid=False)  # unpaid: not revenue
        self.sell_on(mon - timedelta(days=5), 10, 10)           # previous week: 100
        Expense.objects.create(business=self.business, date=mon + timedelta(days=2), category="utilities",
                               description="Power", amount=Decimal("50"), payment_status="unpaid")
        brief = daily_brief.build_weekly_brief(self.business, mon, TODAY)
        finance = profit_and_loss(self.business, mon, mon + timedelta(days=6))
        self.assertEqual(brief["pl"]["revenue"], Decimal("300"))
        self.assertEqual(brief["pl"]["net_profit"], finance["net_profit"])
        self.assertEqual(brief["pl"]["net_profit"], Decimal("220"))   # 300 - 30 cogs - 50 expenses
        self.assertEqual(brief["pl"]["unpaid_sales"], Decimal("50"))
        self.assertEqual(brief["previous"]["revenue"], Decimal("100"))
        self.assertEqual(brief["revenue_change"]["pct"], 200.0)
        self.assertEqual(brief["best_day"][0], mon + timedelta(days=3))
        self.assertEqual(brief["sales_count"], 3)
        self.assertFalse(brief["partial"])
        text = daily_brief.render_weekly_text(brief)
        self.assertIn("Revenue (paid sales)", text)
        self.assertIn("Net profit", text)
        self.assertIn("▲ 200%", text)
        self.assertIn("unpaid sales", text)
        self.assertIn("is still unpaid", text)

    def test_current_week_is_partial_and_compared_over_the_same_days(self):
        monday = daily_brief.week_start_of(TODAY)
        brief = daily_brief.build_weekly_brief(self.business, monday, TODAY)
        self.assertTrue(brief["partial"])
        self.assertEqual(brief["through"], TODAY)
        self.assertEqual(brief["prev_through"], monday - timedelta(days=7) + timedelta(days=(TODAY - monday).days))
        self.assertIn("so far", daily_brief.render_weekly_text(brief))
        self.assertIn("the same days last week", daily_brief.render_weekly_text(brief))

    def test_a_loss_is_labelled_as_a_loss(self):
        Expense.objects.create(business=self.business, date=self.LAST_MONDAY, category="rent", description="Rent", amount=Decimal("80"))
        text = daily_brief.render_weekly_text(daily_brief.build_weekly_brief(self.business, self.LAST_MONDAY, TODAY))
        self.assertIn("Net loss", text)
        self.assertNotIn("-", text.split("Net loss:")[1].split("\n")[0].split("(")[0])

    def test_weekly_page_renders_table_navigation_and_share_actions(self):
        self.sell_on(self.LAST_MONDAY + timedelta(days=1), 10, 10)
        page = self.client.get(reverse("daily_brief"), {"view": "weekly"})
        self.assertContains(page, "Last week in numbers")
        self.assertContains(page, "Revenue (paid sales)")
        self.assertContains(page, "Net profit")
        self.assertContains(page, 'aria-current="page"')
        self.assertContains(page, "Previous week")
        self.assertContains(page, "Email me this")
        self.assertContains(page, "wa.me/2348012345678?text=")
        self.assertContains(page, "Books income statement recognises sales when recorded")
        any_day = (self.LAST_MONDAY + timedelta(days=3)).isoformat()
        picked = self.client.get(reverse("daily_brief"), {"view": "weekly", "date": any_day})
        self.assertContains(picked, daily_brief.week_label(self.LAST_MONDAY, self.LAST_MONDAY + timedelta(days=6)))
        this_week = self.client.get(reverse("daily_brief"), {"view": "weekly", "date": TODAY.isoformat()})
        self.assertContains(this_week, "This week so far")

    def test_weekly_opt_in_waits_for_next_monday_then_sends_once(self):
        self.client.post(reverse("daily_brief"), {"action": "weekly", "send_weekly_email": "1"})
        pref = DailyBriefPreference.raw_objects.get()
        self.assertTrue(pref.send_weekly_email)
        self.assertEqual(pref.last_weekly_for, self.LAST_MONDAY)
        at = timezone.make_aware(datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0))
        self.assertEqual(daily_brief.send_due_briefs(now=at), 0)      # nothing until a new week has ended
        DailyBriefPreference.raw_objects.filter(pk=pref.pk).update(last_weekly_for=self.LAST_MONDAY - timedelta(days=7))
        self.assertEqual(daily_brief.send_due_briefs(now=at), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("weekly summary", mail.outbox[0].subject)
        self.assertEqual(daily_brief.send_due_briefs(now=at), 0)      # once per week
        self.assertEqual(DailyBriefPreference.raw_objects.get().last_weekly_for, self.LAST_MONDAY)
        self.client.post(reverse("daily_brief"), {"action": "weekly"})
        self.assertFalse(DailyBriefPreference.raw_objects.get().send_weekly_email)

    def test_weekly_email_is_not_sent_before_seven(self):
        DailyBriefPreference.raw_objects.create(business=self.business, user=self.admin, send_weekly_email=True,
                                                last_weekly_for=self.LAST_MONDAY - timedelta(days=7))
        early = timezone.make_aware(datetime(TODAY.year, TODAY.month, TODAY.day, 5, 0))
        self.assertEqual(daily_brief.send_due_briefs(now=early), 0)


class ViewOnlyMemberTests(BriefBase):
    """A member who can read the Dashboard but not edit may manage their own brief delivery."""

    def setUp(self):
        super().setUp()
        from accounts.services import user_has_permission
        roles = seed_business_roles(self.business)
        self.viewer = CustomUser.objects.create_user(username="brief-viewer", password="pw-123456", email="viewer@example.com")
        UserBusiness.objects.create(user=self.viewer, business=self.business, role=roles[CustomUser.ROLE_MD_DIRECTOR])
        self.assertTrue(user_has_permission(self.viewer, self.business, "dashboard", "view"))
        self.assertFalse(user_has_permission(self.viewer, self.business, "dashboard", "edit"))
        self.client.force_login(self.viewer)

    def test_can_save_weekly_preference_and_email_themselves(self):
        url = reverse("daily_brief")
        self.assertEqual(self.client.post(url, {"action": "weekly", "send_weekly_email": "1"}).status_code, 302)
        self.assertTrue(DailyBriefPreference.raw_objects.get(user=self.viewer).send_weekly_email)
        self.assertEqual(self.client.post(url + "?view=weekly", {"action": "email_now"}).status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["viewer@example.com"])


class SnapshotLinkTests(BriefBase):
    def test_daily_and_weekly_share_links_keep_the_report_period(self):
        self.client.force_login(self.admin)
        for query, section in [('?date=' + str(YESTERDAY), 'indicators'), ('?view=weekly', 'profit_loss')]:
            response = self.client.get(reverse('daily_brief') + query)
            self.assertContains(response, 'data-snapshot-share')
            text = response.context['share_text']
            self.assertIn(reverse('complete_dashboard') + '?', text)
            self.assertIn('section=' + section, text)
            self.assertIn('start=', text)
            self.assertIn('end=', text)

    def test_daily_and_weekly_snapshots_use_the_shared_paper_layout(self):
        import io
        from PIL import Image
        from core.snapshot_images import PAPER, ACCENT
        self.client.force_login(self.admin)
        for params in ({'date':str(YESTERDAY)}, {'view':'weekly'}):
            response = self.client.get(reverse('daily_brief_snapshot'), params)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            image = Image.open(io.BytesIO(response.content))
            self.assertEqual(image.getpixel((5,30)), PAPER)
            self.assertEqual(image.getpixel((5,5)), ACCENT)
            self.assertGreater(image.height, 600)
