from datetime import date
from decimal import Decimal

from django.test import TestCase

from accounts.models import PayrollRun, PayrollStaffProfile, Payslip
from core.accounting import daily_components, fiscal_period, period_buckets, profit_and_loss, shift_period
from core.models import Business
from expenses.models import Expense
from inventory.models import FinishedGood, OperationalSupplyDispense, RawMaterial, StockAdjustment
from sales.models import Sale, SaleItem


class FiscalPeriodTests(TestCase):
    def _business(self, **fields):
        return Business(name="Period Store", slug="period-store", **fields)

    def test_calendar_year_quarter_and_half(self):
        quarter = fiscal_period(self._business(profit_period="quarter"), date(2026, 5, 20))
        self.assertEqual((quarter["start"], quarter["end"], quarter["short"]), (date(2026, 4, 1), date(2026, 6, 30), "Q2"))
        half = fiscal_period(self._business(profit_period="half"), date(2026, 8, 1))
        self.assertEqual((half["start"], half["end"], half["short"]), (date(2026, 7, 1), date(2026, 12, 31), "H2"))
        year = fiscal_period(self._business(profit_period="year"), date(2026, 8, 1))
        self.assertEqual((year["start"], year["end"]), (date(2026, 1, 1), date(2026, 12, 31)))

    def test_business_year_that_starts_mid_calendar_year(self):
        business = self._business(profit_period="quarter", fiscal_year_start_month=7)
        first = fiscal_period(business, date(2026, 7, 15))
        self.assertEqual((first["start"], first["end"], first["short"]), (date(2026, 7, 1), date(2026, 9, 30), "Q1"))
        spanning = fiscal_period(business, date(2027, 2, 1))
        self.assertEqual((spanning["start"], spanning["end"], spanning["short"]), (date(2027, 1, 1), date(2027, 3, 31), "Q3"))
        year = fiscal_period(self._business(profit_period="year", fiscal_year_start_month=7), date(2026, 3, 1))
        self.assertEqual((year["start"], year["end"]), (date(2025, 7, 1), date(2026, 6, 30)))

    def test_shift_period_walks_back_and_forward(self):
        business = self._business(profit_period="quarter", fiscal_year_start_month=7)
        current = fiscal_period(business, date(2026, 8, 1))
        self.assertEqual(shift_period(business, current, -1)["start"], date(2026, 4, 1))
        self.assertEqual(shift_period(business, current, 1)["start"], date(2026, 10, 1))


class ProfitAndLossTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="PL Bakery", slug="pl-bakery", profit_period="quarter")
        self.other = Business.objects.create(name="Other PL", slug="other-pl")
        self.day = date(2026, 5, 10)
        self.start, self.end = date(2026, 4, 1), date(2026, 6, 30)

    def _sale(self, business, *, paid=True, price="100", cost="40", qty=2):
        product = FinishedGood.raw_objects.create(business=business, name=f"Cake {Sale.raw_objects.count()}", unit="piece")
        sale = Sale.raw_objects.create(business=business, date=self.day, transaction_type="paid" if paid else "unpaid")
        SaleItem.objects.create(sale=sale, finished_good=product, piece_qty=qty, price=Decimal(price), unit_cost=Decimal(cost))
        return sale

    def test_every_cost_reduces_net_profit_and_other_businesses_are_excluded(self):
        self._sale(self.business)                       # revenue 200, cogs 80
        self._sale(self.business, paid=False)           # receivable memo only
        self._sale(self.other, price="900")             # another tenant
        Expense.raw_objects.create(business=self.business, date=self.day, category="rent", description="Rent", amount=Decimal("30"))
        Expense.raw_objects.create(business=self.business, date=self.day, category="utilities", description="Power", amount=Decimal("10"), payment_status="unpaid")
        Expense.raw_objects.create(business=self.other, date=self.day, category="rent", description="Other rent", amount=Decimal("999"))
        staff = PayrollStaffProfile.raw_objects.create(business=self.business, full_name="Ada", base_pay=Decimal("50"))
        run = PayrollRun.raw_objects.create(business=self.business, period_start=date(2026, 5, 1), period_end=date(2026, 5, 31), pay_date=self.day)
        Payslip.raw_objects.create(
            business=self.business, staff=staff, payroll_run=run, period_start=date(2026, 5, 1), period_end=date(2026, 5, 31),
            gross_pay=Decimal("50"), employer_cost=Decimal("55"), net_pay=Decimal("48"),
        )
        material = RawMaterial.raw_objects.create(business=self.business, name="Soap", stock=Decimal("10"), cost_per_unit=Decimal("2"), reorder_level=Decimal("1"))
        OperationalSupplyDispense.raw_objects.create(business=self.business, date=self.day, raw_material=material, quantity=Decimal("3"), description="Cleaning")
        StockAdjustment.raw_objects.create(
            business=self.business, date=self.day, raw_material=material, quantity=Decimal("-2"), reason="wastage",
            description="Spoiled", unit_value=Decimal("2"),
        )
        StockAdjustment.raw_objects.create(  # supplier returns are not a loss
            business=self.business, date=self.day, raw_material=material, quantity=Decimal("-5"), reason="return_supplier",
            description="Back to supplier", unit_value=Decimal("2"),
        )

        pl = profit_and_loss(self.business, self.start, self.end)
        self.assertEqual(pl["revenue"], Decimal("200.00"))
        self.assertEqual(pl["cogs"], Decimal("80.00"))
        self.assertEqual(pl["gross_profit"], Decimal("120.00"))
        self.assertEqual(pl["operating_expenses"], Decimal("40.00"))   # incurred, paid or not
        self.assertEqual(pl["unpaid_expenses"], Decimal("10.00"))
        self.assertEqual(pl["payroll"], Decimal("55.00"))
        self.assertEqual(pl["supplies"], Decimal("6.00"))
        self.assertEqual(pl["write_offs"], Decimal("4.00"))
        self.assertEqual(pl["total_costs"], Decimal("185.00"))         # 80 + 40 + 55 + 6 + 4
        self.assertEqual(pl["net_profit"], Decimal("15.00"))
        self.assertEqual(pl["unpaid_sales"], Decimal("200.00"))
        self.assertEqual([line["key"] for line in pl["expense_lines"]], ["rent", "utilities"])

    def test_chart_buckets_add_up_to_the_period_statement(self):
        self._sale(self.business)
        Expense.raw_objects.create(business=self.business, date=date(2026, 6, 2), category="rent", description="Rent", amount=Decimal("30"))
        components = daily_components(self.business, self.start, self.end)
        buckets = period_buckets(components, self.start, self.end)
        self.assertEqual(len(buckets), 3)
        self.assertAlmostEqual(sum(row["profit"] for row in buckets), float(profit_and_loss(self.business, self.start, self.end, components)["net_profit"]))


class ProfitLossScreenTests(TestCase):
    def setUp(self):
        from accounts.models import CustomUser
        from django.utils import timezone
        self.business = Business.objects.create(name="Screen Bakery", slug="screen-bakery", profit_period="quarter")
        self.user = CustomUser.objects.create_superuser(username="pl-admin", password="safe-password-123", fullname="Admin")
        today = timezone.localdate()
        product = FinishedGood.raw_objects.create(business=self.business, name="Loaf", unit="piece")
        sale = Sale.raw_objects.create(business=self.business, date=today, transaction_type="paid")
        SaleItem.objects.create(sale=sale, finished_good=product, piece_qty=3, price=Decimal("100"), unit_cost=Decimal("30"))
        Expense.raw_objects.create(business=self.business, date=today, category="rent", description="Rent", amount=Decimal("50"))
        self.client.force_login(self.user)

    def test_profit_loss_page_shows_the_statement_and_period_navigation(self):
        from django.urls import reverse
        response = self.client.get(reverse("finance_profit_loss"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Net profit")
        self.assertContains(response, "Rent / Premises")
        self.assertEqual(response.context["statement"]["revenue"], Decimal("300.00"))
        self.assertEqual(response.context["statement"]["net_profit"], Decimal("160.00"))   # 300 - 90 - 50
        self.assertEqual(response.context["period"]["kind"], "quarter")

    def test_profit_loss_csv_and_past_period(self):
        from django.urls import reverse
        response = self.client.get(reverse("finance_profit_loss_csv"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("Net profit", response.content.decode("utf-8-sig"))
        past = self.client.get(reverse("finance_profit_loss"), {"start": "2020-01-15"})
        self.assertEqual(past.status_code, 200)
        self.assertEqual(past.context["statement"]["revenue"], Decimal("0.00"))

    def test_dashboard_chart_embeds_profit_and_business_period_data(self):
        from django.urls import reverse
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="profit-data"')
        self.assertContains(response, 'id="fiscal-data"')
        self.assertContains(response, 'data-mode="profit"')
        fiscal_row = response.context["profit_json"]["fiscal"][-1]
        self.assertGreaterEqual(fiscal_row["sales"], 0)
        self.assertIn("fiscal", response.context["financial_json"])

    def test_business_settings_accept_the_fiscal_period(self):
        from core.forms import BusinessForm
        form = BusinessForm(instance=self.business, data={
            "name": "Screen Bakery", "slug": "screen-bakery", "vertical": "bakery", "currency_symbol": "₦",
            "background_color": "#050733", "accent_color": "#D14900", "fiscal_year_start_month": "7", "profit_period": "half",
        })
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.business.refresh_from_db()
        self.assertEqual((self.business.fiscal_year_start_month, self.business.profit_period), (7, "half"))


class ComparisonAndProductTests(TestCase):
    def setUp(self):
        from core.accounting import compare_periods, margin_trend, product_monthly_trend, product_profitability
        self.compare_periods, self.margin_trend = compare_periods, margin_trend
        self.product_profitability, self.product_monthly_trend = product_profitability, product_monthly_trend
        self.business = Business.objects.create(name="Compare Bakery", slug="compare-bakery")
        self.other = Business.objects.create(name="Other Compare", slug="other-compare")

    def _sell(self, product, day, *, qty, price, cost, business=None):
        sale = Sale.raw_objects.create(business=business or self.business, date=day, transaction_type="paid")
        SaleItem.objects.create(sale=sale, finished_good=product, piece_qty=qty, price=Decimal(price), unit_cost=Decimal(cost) if cost is not None else None)

    def test_month_comparison_shows_margin_shift_and_where_costs_moved(self):
        product = FinishedGood.raw_objects.create(business=self.business, name="Loaf", unit="piece")
        self._sell(product, date(2026, 9, 10), qty=10, price="100", cost="40")     # Sept: 1000 revenue, 400 cogs
        self._sell(product, date(2026, 10, 10), qty=10, price="100", cost="60")    # Oct: 1000 revenue, 600 cogs
        Expense.raw_objects.create(business=self.business, date=date(2026, 9, 12), category="rent", description="Rent", amount=Decimal("100"))
        Expense.raw_objects.create(business=self.business, date=date(2026, 10, 12), category="rent", description="Rent", amount=Decimal("100"))
        result = self.compare_periods(self.business, "month", date(2026, 10, 15), date(2026, 9, 15))
        self.assertEqual(result["a"]["label"], "October 2026")
        self.assertEqual(result["statement_a"]["net_profit"], Decimal("300.00"))
        self.assertEqual(result["statement_b"]["net_profit"], Decimal("500.00"))
        self.assertEqual(result["net_margin_delta"], -20.0)
        self.assertEqual(result["movers"][0]["label"], "Cost of goods sold")
        self.assertEqual(result["movers"][0]["share_delta"], 20.0)

    def test_margin_trend_runs_oldest_to_newest(self):
        trend = self.margin_trend(self.business, "month", date(2026, 10, 15), count=4)
        self.assertEqual([row["label"] for row in trend], ["Jul 26", "Aug 26", "Sep 26", "Oct 26"])

    def test_products_are_ranked_flagged_and_scoped_to_the_business(self):
        names = ["Best", "Earner", "Thin", "Loser", "Quiet", "Idle"]
        goods = {n: FinishedGood.raw_objects.create(business=self.business, name=n, unit="piece") for n in names}
        day = date(2026, 10, 5)
        self._sell(goods["Best"], day, qty=50, price="10", cost="6")        # 500 rev, 200 profit, most units
        self._sell(goods["Earner"], day, qty=10, price="100", cost="40")    # 1000 rev, 600 profit
        self._sell(goods["Thin"], day, qty=8, price="100", cost="90")       # 10% margin
        self._sell(goods["Loser"], day, qty=6, price="50", cost="70")       # loses money
        self._sell(goods["Quiet"], day, qty=1, price="100", cost=None)      # cost never recorded
        self._sell(FinishedGood.raw_objects.create(business=self.other, name="Foreign", unit="piece"), day, qty=999, price="1", cost="0", business=self.other)
        result = self.product_profitability(self.business, date(2026, 10, 1), date(2026, 10, 31))
        rows = {row["name"]: row for row in result["rows"]}
        self.assertNotIn("Foreign", rows)
        self.assertEqual(result["best_seller"]["name"], "Best")
        self.assertEqual(result["top_earner"]["name"], "Earner")
        self.assertIn("Best seller", rows["Best"]["badges"])
        self.assertIn("Top earner", rows["Earner"]["badges"])
        self.assertIn("Low margin", rows["Thin"]["badges"])
        self.assertIn("Loss-making", rows["Loser"]["badges"])
        self.assertIn("Cost not recorded", rows["Quiet"]["badges"])
        self.assertEqual(rows["Idle"]["badges"], ["No sales"])
        self.assertEqual(rows["Earner"]["margin"], 60.0)
        self.assertEqual(result["weakest"]["name"], "Loser")

    def test_cost_build_up_splits_ingredients_from_production_materials(self):
        from inventory.models import ProductionMaterial, RecipeItem
        good = FinishedGood.raw_objects.create(business=self.business, name="Cake", unit="piece", units_per_batch=Decimal("10"))
        flour = RawMaterial.raw_objects.create(business=self.business, name="Flour", cost_per_unit=Decimal("5"), stock=Decimal("1"), reorder_level=Decimal("1"))
        box = RawMaterial.raw_objects.create(business=self.business, name="Box", cost_per_unit=Decimal("2"), stock=Decimal("1"), reorder_level=Decimal("1"))
        RecipeItem.objects.create(finished_good=good, raw_material=flour, qty_per_batch=Decimal("20"))        # 100 / 10 = 10 per unit
        ProductionMaterial.objects.create(finished_good=good, raw_material=box, qty_per_batch=Decimal("10"))  # 20 / 10 = 2 per unit
        self._sell(good, date(2026, 10, 5), qty=1, price="30", cost="12")
        row = next(r for r in self.product_profitability(self.business, date(2026, 10, 1), date(2026, 10, 31))["rows"] if r["name"] == "Cake")
        self.assertEqual((row["build"]["raw_material"], row["build"]["production"], row["build"]["total"]), (Decimal("10.00"), Decimal("2.00"), Decimal("12.00")))
        trend = self.product_monthly_trend(self.business, good, months=3, anchor=date(2026, 10, 20))
        self.assertEqual([t["label"] for t in trend], ["Aug", "Sep", "Oct"])
        self.assertEqual(trend[-1]["profit"], 18.0)


class ComparisonScreensTests(TestCase):
    def setUp(self):
        from accounts.models import CustomUser
        from django.utils import timezone
        self.today = timezone.localdate()
        self.business = Business.objects.create(name="Screens Two", slug="screens-two")
        self.user = CustomUser.objects.create_superuser(username="cmp-admin", password="safe-password-123", fullname="Admin")
        self.best = FinishedGood.raw_objects.create(business=self.business, name="Best Loaf", unit="piece")
        self.idle = FinishedGood.raw_objects.create(business=self.business, name="Idle Cake", unit="piece")
        for qty in (5, 7):
            sale = Sale.raw_objects.create(business=self.business, date=self.today, transaction_type="paid")
            SaleItem.objects.create(sale=sale, finished_good=self.best, piece_qty=qty, price=Decimal("100"), unit_cost=Decimal("60"))
        self.client.force_login(self.user)

    def test_compare_page_defaults_to_this_and_last_month_and_accepts_quarter_and_year(self):
        from django.urls import reverse
        for kind in ("month", "quarter", "year"):
            response = self.client.get(reverse("finance_profit_compare"), {"kind": kind})
            self.assertEqual(response.status_code, 200, kind)
            self.assertEqual(response.context["result"]["a"]["kind"], kind)
            self.assertNotEqual(response.context["result"]["a"]["start"], response.context["result"]["b"]["start"])
        self.assertContains(self.client.get(reverse("finance_profit_compare")), "Net margin")

    def test_compare_page_ignores_bad_input(self):
        from django.urls import reverse
        response = self.client.get(reverse("finance_profit_compare"), {"kind": "decade", "a": "nonsense", "b": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["kind"], "month")

    def test_product_page_filters_sorts_and_exports(self):
        from django.urls import reverse
        page = self.client.get(reverse("finance_product_profit"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Best Loaf")
        self.assertEqual(page.context["result"]["best_seller"]["name"], "Best Loaf")
        idle = self.client.get(reverse("finance_product_profit"), {"show": "none"})
        self.assertEqual([r["name"] for r in idle.context["rows"]], ["Idle Cake"])
        csv_response = self.client.get(reverse("finance_product_profit_csv"))
        body = csv_response.content.decode("utf-8-sig")
        self.assertIn("Best Loaf", body)
        self.assertIn("Raw material cost/unit (current)", body)

    def test_finished_good_search_detail_carries_profitability(self):
        from django.urls import reverse
        response = self.client.get(reverse("dashboard_search_detail"), {"type": "fg", "id": self.best.pk})
        self.assertEqual(response.status_code, 200)
        data = response.json()["profitability"]
        self.assertEqual([w["label"] for w in data["windows"]], ["This month", "Last month", "This quarter", "This year"])
        month = data["windows"][0]
        self.assertEqual((month["units"], month["revenue"], month["profit"]), (12.0, 1200.0, 480.0))
        self.assertEqual(month["rank_units"], 1)
        self.assertIn("Best seller", month["badges"])
        self.assertEqual(len(data["trend"]), 6)
        idle = self.client.get(reverse("dashboard_search_detail"), {"type": "fg", "id": self.idle.pk}).json()["profitability"]
        self.assertEqual(idle["windows"][0]["badges"], ["No sales"])

    def test_dashboard_search_ui_offers_the_profitability_tab_and_theme_helper(self):
        from django.urls import reverse
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "Profitability")
        self.assertContains(page, "inproficChartTheme")
        self.assertContains(page, reverse("finance_profit_compare"))


class SearchDetailCompactListTests(TestCase):
    def test_long_lists_use_the_help_tip_and_the_search_results_use_theme_tokens(self):
        from accounts.models import CustomUser
        from django.urls import reverse
        business = Business.objects.create(name="Compact Store", slug="compact-store")
        user = CustomUser.objects.create_superuser(username="compact-admin", password="safe-password-123", fullname="Admin")
        self.client.force_login(user)
        page = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("detailLists()", page)
        self.assertIn('data-form-help-portal="true"', page)
        self.assertIn("dashboard-search-result", page)
        self.assertNotIn("hover:bg-[#FFF8F3]", page)           # fixed cream hover is gone
        for retired in ("Recent purchase history</h4>", "Completed sales</h4>", "Order history</h4>"):
            self.assertNotIn(retired, page)                      # now rendered by the compact cards


class BusinessFormCompatibilityTests(TestCase):
    def test_form_without_fiscal_fields_keeps_the_saved_values(self):
        from core.forms import BusinessForm
        business = Business.objects.create(name="Old Form", slug="old-form", fiscal_year_start_month=7, profit_period="half")
        form = BusinessForm(instance=business, data={
            "name": "Old Form", "slug": "old-form", "vertical": "bakery", "currency_symbol": "₦",
            "background_color": "#050733", "accent_color": "#D14900",
        })
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual((saved.fiscal_year_start_month, saved.profit_period), (7, "half"))


class SearchDetailPurchaseItemsTests(TestCase):
    def test_vendor_purchase_lines_are_formatted_not_dumped_as_objects(self):
        from accounts.models import CustomUser
        from django.urls import reverse
        Business.objects.create(name="Items Store", slug="items-store")
        user = CustomUser.objects.create_superuser(username="items-admin", password="safe-password-123", fullname="Admin")
        self.client.force_login(user)
        page = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("i.name + ' × '", page)           # items are objects: name × qty
        self.assertNotIn("' · ' + r.items :", page)      # the raw-object concatenation is gone
        self.assertIn("excessLines(row)", page)          # activity-tab surplus uses the help tip
