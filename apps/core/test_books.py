"""Double-entry books: balance by construction, tie to cash, VAT, manual journals, scope independence."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from expenses.models import Expense
from inventory.models import FinishedGood, RawMaterial, StockAdjustment
from procurement.models import PurchaseOrder, PurchaseOrderItem
from sales.models import Sale, SaleItem

from . import books
from .context import set_current_business, set_ledger_context
from .models import (BooksState, Business, CashAccount, FinancialTransaction, JournalEntry, JournalLine, LedgerAccount,
                     TaxSettings)

TODAY = timezone.localdate()
D1 = TODAY - timedelta(days=5)


class BooksBase(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Books Bakery", slug="books-bakery")
        roles = seed_business_roles(self.business)
        self.admin = CustomUser.objects.create_user(username="books-admin", password="pw-123456")
        UserBusiness.objects.create(user=self.admin, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.till = CashAccount.raw_objects.create(business=self.business, name="Till", account_type="cash", opening_balance=Decimal("1000"))
        self.flour = RawMaterial.raw_objects.create(business=self.business, name="Flour", usage_unit="kg", stock=Decimal("10"), cost_per_unit=Decimal("2"))
        self.bread = FinishedGood.raw_objects.create(business=self.business, name="Bread", unit="loaf", stock=Decimal("0"))

    def cash(self, kind, amount, category, ref, day=D1, desc="x", account=None):
        return FinancialTransaction.raw_objects.create(
            business=self.business, account=account or self.till, date=day, transaction_type=kind, amount=Decimal(amount),
            category=category, reference=ref, description=desc)

    def sale(self, qty, price, cost="0", day=D1, source="walkin", kind="paid"):
        sale = Sale.raw_objects.create(business=self.business, date=day, customer="Ada", source=source, transaction_type=kind)
        SaleItem.objects.create(sale=sale, finished_good=self.bread, piece_qty=Decimal(qty), price=Decimal(price), unit_cost=Decimal(cost))
        return sale

    def balance(self, key):
        account = LedgerAccount.raw_objects.get(business=self.business, system_key=key)
        return books.account_balance(self.business, account)


class PostingTests(BooksBase):
    def test_paid_sale_with_cost_posts_revenue_cogs_and_clears_receivable(self):
        sale = self.sale(10, 10, cost="4")
        self.cash(FinancialTransaction.INCOME, "100", "Sales revenue", f"SALE-{sale.pk}")
        books.rebuild_books(self.business)
        self.assertEqual(self.balance("revenue"), Decimal("-100.00"))   # credit
        self.assertEqual(self.balance("cogs"), Decimal("40.00"))
        self.assertEqual(self.balance("inventory"), Decimal("-40.00"))
        self.assertEqual(self.balance("ar"), Decimal("0.00"))
        self.assertEqual(self.balance(f"cash_{self.till.pk}"), Decimal("1100.00"))

    def test_every_entry_balances_and_so_does_the_trial_balance(self):
        sale = self.sale(10, 10, cost="4")
        self.cash(FinancialTransaction.INCOME, "100", "Sales revenue", f"SALE-{sale.pk}")
        po = PurchaseOrder.raw_objects.create(business=self.business, date=D1, supplier="Mill", status="received", received_date=D1)
        PurchaseOrderItem.objects.create(purchase_order=po, raw_material=self.flour, qty=Decimal("5"), unit_cost=Decimal("2"))
        self.cash(FinancialTransaction.OUTFLOW, "10", "Supplier payment", f"SUPPAY-{po.pk}")
        Expense.raw_objects.create(business=self.business, date=D1, category="utilities", description="Power", amount=Decimal("30"), payment_status="paid")
        self.cash(FinancialTransaction.OUTFLOW, "30", "Utilities", "EXP-1")
        StockAdjustment.raw_objects.create(business=self.business, date=D1, raw_material=self.flour, quantity=Decimal("-2"),
                                           unit_value=Decimal("2"), reason="spoilage")
        books.rebuild_books(self.business)
        for entry in JournalEntry.raw_objects.filter(business=self.business):
            debit = sum(l.debit for l in entry.lines.all())
            credit = sum(l.credit for l in entry.lines.all())
            self.assertEqual(debit, credit, f"{entry.source_type} {entry.source_id}")
        tb = books.trial_balance(self.business)
        self.assertTrue(tb["balanced"])
        self.assertGreater(tb["debit_total"], 0)

    def test_purchase_expense_and_adjustment_postings(self):
        po = PurchaseOrder.raw_objects.create(business=self.business, date=D1, supplier="Mill", status="received", received_date=D1)
        PurchaseOrderItem.objects.create(purchase_order=po, raw_material=self.flour, qty=Decimal("5"), unit_cost=Decimal("2"))
        Expense.raw_objects.create(business=self.business, date=D1, category="utilities", description="Power", amount=Decimal("30"))
        StockAdjustment.raw_objects.create(business=self.business, date=D1, raw_material=self.flour, quantity=Decimal("-2"),
                                           unit_value=Decimal("2"), reason="spoilage")
        books.rebuild_books(self.business)
        self.assertEqual(self.balance("ap"), Decimal("-10.00"))
        self.assertEqual(self.balance("accrued"), Decimal("-30.00"))
        self.assertEqual(self.balance("adjustments"), Decimal("4.00"))
        self.assertEqual(self.balance("inventory"), Decimal("6.00"))  # +10 purchased, -4 written off
        self.assertEqual(self.balance("expense_utilities"), Decimal("30.00"))

    def test_unrecognised_cash_lands_in_suspense_and_is_reported(self):
        self.cash(FinancialTransaction.INCOME, "250", "Mystery deposit", "")
        books.rebuild_books(self.business)
        self.assertEqual(self.balance("suspense"), Decimal("-250.00"))
        self.assertEqual(BooksState.raw_objects.get(business=self.business).suspense_total, Decimal("-250.00"))

    def test_a_reversal_is_classified_like_the_original(self):
        sale = self.sale(1, 100)
        original = self.cash(FinancialTransaction.INCOME, "100", "Sales revenue", f"SALE-{sale.pk}")
        FinancialTransaction.raw_objects.create(
            business=self.business, account=self.till, date=D1, transaction_type=FinancialTransaction.OUTFLOW, amount=Decimal("100"),
            category="Reversal", reference="REV", description="undo", reversal_of=original)
        books.rebuild_books(self.business)
        self.assertEqual(self.balance("ar"), Decimal("100.00"))  # receipt reversed, receivable open again
        self.assertEqual(self.balance("suspense"), Decimal("0.00"))

    def test_opening_balance_posts_against_opening_equity(self):
        books.rebuild_books(self.business)
        self.assertEqual(self.balance(f"cash_{self.till.pk}"), Decimal("1000.00"))
        self.assertEqual(self.balance("opening_equity"), Decimal("-1000.00"))


class VatPostingTests(BooksBase):
    def setUp(self):
        super().setUp()
        TaxSettings.raw_objects.create(business=self.business, vat_registered=True, vat_rate=Decimal("7.50"))

    def test_vat_is_extracted_from_inclusive_prices_for_standard_rated_goods_only(self):
        self.sale(1, "107.50")
        exempt = FinishedGood.raw_objects.create(business=self.business, name="Bread (zero)", unit="loaf", vat_treatment="zero")
        sale = Sale.raw_objects.create(business=self.business, date=D1, customer="B", source="walkin", transaction_type="paid")
        SaleItem.objects.create(sale=sale, finished_good=exempt, piece_qty=Decimal("1"), price=Decimal("50"), unit_cost=Decimal("0"))
        books.rebuild_books(self.business)
        self.assertEqual(self.balance("vat_output"), Decimal("-7.50"))
        self.assertEqual(self.balance("revenue"), Decimal("-150.00"))  # 100 net + 50 zero-rated
        self.assertEqual(self.balance("ar"), Decimal("157.50"))

    def test_no_vat_before_registration_or_when_not_registered(self):
        TaxSettings.raw_objects.update(vat_registration_date=TODAY)
        self.sale(1, "107.50", day=D1)
        books.rebuild_books(self.business)
        self.assertEqual(self.balance("vat_output"), Decimal("0"))
        TaxSettings.raw_objects.update(vat_registered=False, vat_registration_date=None)
        books.rebuild_books(self.business)
        self.assertEqual(self.balance("vat_output"), Decimal("0"))


class RebuildAndManualTests(BooksBase):
    def lines(self, a, b, amount="500"):
        return [(a.pk, amount, 0, "stock b/f"), (b.pk, 0, amount, "")]

    def accounts(self):
        books.ensure_chart(self.business)
        return {a.system_key: a for a in LedgerAccount.raw_objects.filter(business=self.business)}

    def test_rebuild_is_idempotent_and_keeps_manual_entries(self):
        self.sale(2, 10)
        acc = self.accounts()
        manual = books.post_manual(self.business, self.admin, D1, "Opening stock", self.lines(acc["inventory"], acc["opening_equity"]))
        first = books.rebuild_books(self.business)
        snapshot = {r["account"].code: r["net"] for r in books.trial_balance(self.business)["rows"]}
        second = books.rebuild_books(self.business)
        self.assertEqual(first, second)
        self.assertEqual({r["account"].code: r["net"] for r in books.trial_balance(self.business)["rows"]}, snapshot)
        self.assertTrue(JournalEntry.raw_objects.filter(pk=manual.pk).exists())
        self.assertEqual(self.balance("inventory"), Decimal("500.00"))

    def test_manual_journal_validation(self):
        acc = self.accounts()
        for lines, fragment in (
            ([(acc["ar"].pk, 100, 0, ""), (acc["revenue"].pk, 0, 90, "")], "does not balance"),
            ([(acc["ar"].pk, 100, 0, "")], "at least two"),
            ([(acc["ar"].pk, 100, 50, ""), (acc["revenue"].pk, 0, 50, "")], "not both"),
            ([(acc["ar"].pk, -5, 0, ""), (acc["revenue"].pk, 0, 5, "")], "negative"),
            ([(999999, 10, 0, ""), (acc["revenue"].pk, 0, 10, "")], "not available"),
        ):
            with self.assertRaises(books.JournalError, msg=fragment) as ctx:
                books.post_manual(self.business, self.admin, D1, "bad", lines)
            self.assertIn(fragment, str(ctx.exception))
        self.assertFalse(JournalEntry.raw_objects.filter(source_type="manual").exists())

    def test_another_business_account_is_refused(self):
        other = Business.objects.create(name="Other", slug="other")
        foreign = books.ensure_chart(other)["ar"]
        acc = self.accounts()
        with self.assertRaises(books.JournalError):
            books.post_manual(self.business, self.admin, D1, "x", [(foreign.pk, 10, 0, ""), (acc["revenue"].pk, 0, 10, "")])

    def test_reversal_rules(self):
        acc = self.accounts()
        manual = books.post_manual(self.business, self.admin, D1, "Opening", self.lines(acc["inventory"], acc["opening_equity"]))
        reversal = books.reverse_manual(manual, self.admin)
        self.assertEqual(books.account_balance(self.business, acc["inventory"]), Decimal("0"))
        with self.assertRaises(books.JournalError):
            books.reverse_manual(manual, self.admin)       # already reversed
        with self.assertRaises(books.JournalError):
            books.reverse_manual(reversal, self.admin)     # a reversal cannot be reversed
        books.rebuild_books(self.business)
        system_entry = JournalEntry.raw_objects.filter(business=self.business, is_system=True).first()
        with self.assertRaises(books.JournalError):
            books.reverse_manual(system_entry, self.admin)

    def test_books_span_both_ledger_periods(self):
        self.sale(1, 100, day=TODAY - timedelta(days=30))
        self.sale(1, 100, day=D1)
        set_current_business(self.business)
        set_ledger_context("live", TODAY - timedelta(days=10), "open")
        try:
            books.rebuild_books(self.business)
        finally:
            set_ledger_context(None, None, None)
            set_current_business(None)
        self.assertEqual(self.balance("revenue"), Decimal("-200.00"))


class ReportTests(BooksBase):
    def test_statements_agree_with_each_other(self):
        sale = self.sale(10, 10, cost="4")
        self.cash(FinancialTransaction.INCOME, "100", "Sales revenue", f"SALE-{sale.pk}")
        Expense.raw_objects.create(business=self.business, date=D1, category="utilities", description="Power", amount=Decimal("30"))
        books.rebuild_books(self.business)
        income = books.income_statement(self.business, D1 - timedelta(days=1), TODAY)
        self.assertEqual((income["total_income"], income["total_expenses"], income["net_profit"]),
                         (Decimal("100.00"), Decimal("70.00"), Decimal("30.00")))
        sheet = books.balance_sheet(self.business, TODAY)
        self.assertTrue(sheet["balanced"])
        self.assertEqual(sheet["current_earnings"], Decimal("30.00"))

    def test_account_register_has_a_running_balance_and_opening_for_a_later_start(self):
        self.cash(FinancialTransaction.INCOME, "100", "Customer payment", "CUSTPAY-1", day=D1)
        self.cash(FinancialTransaction.OUTFLOW, "40", "Utilities", "EXP-2", day=D1 + timedelta(days=2))
        books.rebuild_books(self.business)
        account = LedgerAccount.raw_objects.get(business=self.business, system_key=f"cash_{self.till.pk}")
        full = books.account_register(self.business, account)
        self.assertEqual(full["closing"], Decimal("1060.00"))
        later = books.account_register(self.business, account, start=D1 + timedelta(days=1))
        self.assertEqual(later["opening"], Decimal("1100.00"))
        self.assertEqual(later["closing"], Decimal("1060.00"))

    def test_tie_out_cash_matches_exactly(self):
        self.cash(FinancialTransaction.INCOME, "100", "Customer payment", "CUSTPAY-1")
        self.cash(FinancialTransaction.OUTFLOW, "40", "Utilities", "EXP-2")
        books.rebuild_books(self.business)
        row = next(r for r in books.tie_out(self.business) if r["name"] == "Cash: Till")
        self.assertTrue(row["ties"])
        self.assertEqual(row["ledger"], Decimal("1060.00"))

    def test_the_database_refuses_a_line_that_is_both_debit_and_credit(self):
        from django.db import IntegrityError, transaction
        books.rebuild_books(self.business)
        entry = JournalEntry.raw_objects.filter(business=self.business).first()
        account = LedgerAccount.raw_objects.filter(business=self.business).first()
        with self.assertRaises(IntegrityError), transaction.atomic():
            JournalLine.objects.create(entry=entry, account=account, debit=Decimal("5"), credit=Decimal("5"))


class BooksScreenTests(BooksBase):
    def setUp(self):
        super().setUp()
        from django.urls import reverse
        self.reverse = reverse
        roles = seed_business_roles(self.business)
        other_key = next(k for k in roles if k not in {CustomUser.ROLE_BUSINESS_ADMIN, "manager", "md_director"})
        self.staff = CustomUser.objects.create_user(username="books-staff", password="pw-123456")
        UserBusiness.objects.create(user=self.staff, business=self.business, role=roles[other_key])

    def test_build_then_view_trial_balance_tie_out_and_csv(self):
        sale = self.sale(10, 10, cost="4")
        self.cash(FinancialTransaction.INCOME, "100", "Sales revenue", f"SALE-{sale.pk}")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(self.reverse("books_home")), "Build books")
        self.client.post(self.reverse("books_home"))
        page = self.client.get(self.reverse("books_home"))
        self.assertContains(page, "Trial balance")
        self.assertContains(page, "balanced")
        self.assertContains(page, "Checked against your records")
        csv_text = self.client.get(self.reverse("books_home"), {"format": "csv"}).content.decode()
        self.assertIn("Sales revenue", csv_text)

    def test_manual_journal_flow_and_error_message(self):
        books.rebuild_books(self.business)
        accounts = {a.system_key: a for a in LedgerAccount.raw_objects.filter(business=self.business)}
        self.client.force_login(self.admin)
        good = {"date": D1.isoformat(), "memo": "Opening stock", "account_0": accounts["inventory"].pk, "debit_0": "500",
                "account_1": accounts["opening_equity"].pk, "credit_1": "500"}
        self.client.post(self.reverse("books_journal_new"), good)
        self.assertTrue(JournalEntry.raw_objects.filter(source_type="manual", memo="Opening stock").exists())
        bad = {**good, "credit_1": "400"}
        response = self.client.post(self.reverse("books_journal_new"), bad)
        self.assertContains(response, "does not balance")
        self.assertEqual(JournalEntry.raw_objects.filter(source_type="manual").count(), 1)
        manual = JournalEntry.raw_objects.get(source_type="manual")
        self.client.post(self.reverse("books_journal_reverse", args=[manual.pk]))
        self.assertEqual(JournalEntry.raw_objects.filter(source_type="manual").count(), 2)

    def test_only_admins_change_the_books_structure(self):
        books.rebuild_books(self.business)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.reverse("books_journal_new")).status_code, 403)
        self.assertEqual(self.client.post(self.reverse("books_accounts"), {"action": "add", "code": "7000", "name": "X", "account_type": "expense"}).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(self.reverse("books_accounts"), {"action": "add", "code": "7000", "name": "Bank charges", "account_type": "expense"})
        self.assertTrue(LedgerAccount.raw_objects.filter(business=self.business, code="7000").exists())
        duplicate = self.client.post(self.reverse("books_accounts"), {"action": "add", "code": "7000", "name": "Again", "account_type": "expense"}, follow=True)
        self.assertContains(duplicate, "already used")
        system = LedgerAccount.raw_objects.get(business=self.business, system_key="ar")
        refused = self.client.post(self.reverse("books_accounts"), {"action": "toggle", "account": system.pk}, follow=True)
        self.assertContains(refused, "cannot be switched off")

    def test_registers_statements_and_journal_pages_render(self):
        self.cash(FinancialTransaction.INCOME, "100", "Customer payment", "CUSTPAY-1")
        books.rebuild_books(self.business)
        self.client.force_login(self.admin)
        account = LedgerAccount.raw_objects.get(business=self.business, system_key=f"cash_{self.till.pk}")
        self.assertContains(self.client.get(self.reverse("books_ledger", args=[account.pk])), "Closing balance")
        self.assertContains(self.client.get(self.reverse("books_statements")), "Balance sheet")
        self.assertContains(self.client.get(self.reverse("books_journal")), "Opening balance: Till")
        self.assertIn("Opening balance", self.client.get(self.reverse("books_journal"), {"format": "csv"}).content.decode())

    def test_another_business_cannot_open_an_account_register(self):
        books.rebuild_books(self.business)
        account = LedgerAccount.raw_objects.filter(business=self.business).first()
        other = Business.objects.create(name="Intruder Co", slug="intruder-co")
        roles = seed_business_roles(other)
        intruder = CustomUser.objects.create_user(username="books-intruder", password="pw-123456")
        UserBusiness.objects.create(user=intruder, business=other, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.client.force_login(intruder)
        self.assertEqual(self.client.get(self.reverse("books_ledger", args=[account.pk])).status_code, 404)
