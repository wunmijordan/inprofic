"""Bank reconciliation: CSV parsing, matching, the balance proof and the screens."""
from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles

from . import bank_reconciliation as recon
from .models import BankStatement, BankStatementLine, Business, CashAccount, FinancialTransaction


class ParsingTests(SimpleTestCase):
    def test_signed_amount_column_and_flexible_dates(self):
        rows = recon.parse_statement_csv("Date,Description,Amount\n2026-03-06,Deposit,\"1,500.00\"\n07/03/2026,Fee,(15.00)\n")
        self.assertEqual(rows, [(date(2026, 3, 6), "Deposit", Decimal("1500.00")), (date(2026, 3, 7), "Fee", Decimal("-15.00"))])

    def test_debit_and_credit_columns(self):
        rows = recon.parse_statement_csv("Date,Narration,Debit,Credit\n2026-03-06,POS sale,,500\n2026-03-07,Supplier,200,\n")
        self.assertEqual([r[2] for r in rows], [Decimal("500.00"), Decimal("-200.00")])

    def test_bad_files_explain_themselves(self):
        for content, fragment in (
            ("Date,Description\n2026-03-06,x\n", "No amount column"),
            ("Description,Amount\nx,5\n", "No date column"),
            ("Date,Amount\nnot-a-date,5\n", "Line 2"),
            ("Date,Amount\n2026-03-06,abc\n", "Line 2"),
            ("Date,Amount\n", "header row"),
            ("Date,Amount\n2026-03-06,0\n", "No transactions"),
        ):
            with self.assertRaises(recon.StatementError, msg=content) as ctx:
                recon.parse_statement_csv(content)
            self.assertIn(fragment, str(ctx.exception))


class ReconBase(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Recon Co", slug="recon-co")
        roles = seed_business_roles(self.business)
        self.admin = CustomUser.objects.create_user(username="recon-admin", password="pw-123456")
        UserBusiness.objects.create(user=self.admin, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.account = CashAccount.raw_objects.create(business=self.business, name="Main bank", account_type="bank",
                                                      opening_balance=Decimal("1000"))

    def tx(self, day, kind, amount, desc, account=None):
        return FinancialTransaction.raw_objects.create(
            business=self.business, account=account or self.account, date=date(2026, 3, day), transaction_type=kind,
            amount=Decimal(amount), category="sales", description=desc)

    def statement(self, lines, closing):
        return recon.create_statement(self.business, self.admin, self.account, Decimal(closing),
                                      [(date(2026, 3, d), t, Decimal(a)) for d, t, a in lines])


class MatchingTests(ReconBase):
    def test_auto_match_uses_exact_amount_within_the_window_and_never_reuses_a_transaction(self):
        sale = self.tx(5, FinancialTransaction.INCOME, "500", "Sale")
        self.tx(7, FinancialTransaction.OUTFLOW, "200", "Supplier")
        stmt = self.statement([(6, "Deposit", "500"), (8, "Cheque", "-200"), (9, "Second deposit", "500")], "1800")
        self.assertEqual(recon.auto_match(stmt), 2)
        deposit = stmt.lines.get(description="Deposit")
        self.assertEqual(deposit.matched_transaction_id, sale.pk)
        self.assertIsNone(stmt.lines.get(description="Second deposit").matched_transaction)

    def test_outside_the_window_or_wrong_amount_does_not_match(self):
        self.tx(1, FinancialTransaction.INCOME, "500", "Early")
        self.tx(6, FinancialTransaction.INCOME, "499", "Close but different")
        stmt = self.statement([(20, "Deposit", "500")], "1500")
        self.assertEqual(recon.auto_match(stmt), 0)

    def test_reversed_entries_are_never_candidates(self):
        tx = self.tx(5, FinancialTransaction.INCOME, "500", "Reversed sale")
        FinancialTransaction.raw_objects.filter(pk=tx.pk).update(reversed=True)
        stmt = self.statement([(6, "Deposit", "500")], "1500")
        self.assertEqual(recon.auto_match(stmt), 0)

    def test_manual_match_rejects_different_amounts_and_unmatch_frees_the_entry(self):
        tx = self.tx(5, FinancialTransaction.INCOME, "500", "Sale")
        stmt = self.statement([(6, "Deposit", "500"), (6, "Other", "300")], "1800")
        other = stmt.lines.get(description="Other")
        with self.assertRaises(recon.StatementError):
            recon.match_line(stmt, other.pk, tx.pk)
        line = stmt.lines.get(description="Deposit")
        recon.match_line(stmt, line.pk, tx.pk)
        recon.unmatch_line(stmt, line.pk)
        self.assertIsNone(BankStatementLine.objects.get(pk=line.pk).matched_transaction)


class ProofTests(ReconBase):
    def test_a_bank_fee_not_yet_in_the_books_still_proves_out(self):
        self.tx(5, FinancialTransaction.INCOME, "500", "Sale")
        self.tx(7, FinancialTransaction.OUTFLOW, "200", "Supplier")
        stmt = self.statement([(6, "Deposit", "500"), (8, "Cheque", "-200"), (9, "Bank fee", "-15")], "1285")
        recon.auto_match(stmt)
        result = recon.summarize(stmt)
        self.assertEqual((result["adjusted_bank"], result["adjusted_book"], result["difference"]),
                         (Decimal("1285"), Decimal("1285.00"), Decimal("0.00")))
        self.assertTrue(result["balanced"])

    def test_a_deposit_in_transit_is_explained_by_the_books(self):
        self.tx(5, FinancialTransaction.INCOME, "500", "Sale")
        self.tx(9, FinancialTransaction.INCOME, "100", "Deposit in transit")
        stmt = self.statement([(6, "Deposit", "500"), (9, "Bank fee", "-15")], "1485")
        recon.auto_match(stmt)
        result = recon.summarize(stmt)
        self.assertEqual([c["tx"].description for c in result["book_only"]], ["Deposit in transit"])
        self.assertTrue(result["balanced"])

    def test_unexplained_difference_blocks_finishing(self):
        self.tx(5, FinancialTransaction.INCOME, "500", "Sale")
        stmt = self.statement([(6, "Deposit", "500")], "1600")  # bank says 100 more than the books explain
        recon.auto_match(stmt)
        self.assertEqual(recon.summarize(stmt)["difference"], Decimal("100.00"))
        with self.assertRaises(recon.StatementError):
            recon.finish(stmt)

    def test_finishing_locks_the_statement(self):
        self.tx(5, FinancialTransaction.INCOME, "500", "Sale")
        stmt = self.statement([(6, "Deposit", "500")], "1500")
        recon.auto_match(stmt)
        recon.finish(stmt)
        stmt.refresh_from_db()
        self.assertEqual(stmt.status, BankStatement.STATUS_RECONCILED)
        with self.assertRaises(recon.StatementError):
            recon.unmatch_line(stmt, stmt.lines.first().pk)
        self.assertEqual(recon.auto_match(stmt), 0)


class ScreenTests(ReconBase):
    def csv_upload(self, text="Date,Description,Amount\n2026-03-06,Deposit,500\n"):
        return SimpleUploadedFile("march.csv", text.encode(), content_type="text/csv")

    def test_upload_matches_and_shows_the_proof(self):
        self.tx(5, FinancialTransaction.INCOME, "500", "Sale")
        self.client.force_login(self.admin)
        response = self.client.post(reverse("reconcile_home"), {
            "account": self.account.pk, "closing_balance": "1500", "statement": self.csv_upload()}, follow=True)
        self.assertContains(response, "Mark as reconciled")
        statement = BankStatement.raw_objects.get()
        self.client.post(reverse("reconcile_detail", args=[statement.pk]), {"action": "finish"})
        statement.refresh_from_db()
        self.assertEqual(statement.status, "reconciled")
        self.assertEqual(FinancialTransaction.raw_objects.count(), 1)  # cash records untouched

    def test_bad_upload_shows_a_message_and_creates_nothing(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("reconcile_home"), {
            "account": self.account.pk, "closing_balance": "10", "statement": self.csv_upload("Date,Memo\nx,y\n")}, follow=True)
        self.assertContains(response, "No amount column")
        self.assertFalse(BankStatement.raw_objects.exists())

    def test_another_business_cannot_open_a_statement(self):
        stmt = self.statement([(6, "Deposit", "500")], "1500")
        other = Business.objects.create(name="Other Co", slug="other-co")
        roles = seed_business_roles(other)
        intruder = CustomUser.objects.create_user(username="intruder", password="pw-123456")
        UserBusiness.objects.create(user=intruder, business=other, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.client.force_login(intruder)
        self.assertEqual(self.client.get(reverse("reconcile_detail", args=[stmt.pk])).status_code, 404)
