"""VAT, input VAT, registration monitor and e-invoicing."""
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock
from urllib.error import HTTPError

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser, UserBusiness
from accounts.services import seed_business_roles
from inventory.models import FinishedGood
from sales.models import Sale, SaleItem

from . import tax
from .models import Business, EInvoice, InputVatEntry, TaxSettings

TODAY = timezone.localdate()
D1 = date(2026, 3, 10)
MARCH = (date(2026, 3, 1), date(2026, 3, 31))
PUBLIC_IP = [(2, 1, 6, "", ("93.184.216.34", 443))]


class TaxBase(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Tax Bakery", slug="tax-bakery")
        roles = seed_business_roles(self.business)
        self.admin = CustomUser.objects.create_user(username="tax-admin", password="pw-123456")
        UserBusiness.objects.create(user=self.admin, business=self.business, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        other_key = next(k for k in roles if k not in {CustomUser.ROLE_BUSINESS_ADMIN, "manager", "md_director"})
        self.staff = CustomUser.objects.create_user(username="tax-staff", password="pw-123456")
        UserBusiness.objects.create(user=self.staff, business=self.business, role=roles[other_key])
        self.standard = FinishedGood.raw_objects.create(business=self.business, name="Bread", unit="loaf")
        self.zero = FinishedGood.raw_objects.create(business=self.business, name="Rice", unit="bag", vat_treatment="zero")
        self.exempt = FinishedGood.raw_objects.create(business=self.business, name="Books", unit="book", vat_treatment="exempt")
        self.settings = TaxSettings.raw_objects.create(business=self.business, vat_registered=True, vat_rate=Decimal("7.50"),
                                                       tin="1234567-0001", einvoice_enabled=True)

    def sell(self, product, price, qty=1, day=D1, customer="Ada", kind="paid"):
        sale = Sale.raw_objects.create(business=self.business, date=day, customer=customer, source="walkin", transaction_type=kind)
        SaleItem.objects.create(sale=sale, finished_good=product, piece_qty=Decimal(qty), price=Decimal(price), unit_cost=Decimal("0"))
        return sale


class VatReturnTests(TaxBase):
    def test_vat_is_extracted_from_inclusive_prices(self):
        self.assertEqual(tax.vat_from_gross("107.50", "7.5"), Decimal("7.50"))
        self.assertEqual(tax.vat_from_gross("1000", "7.5"), Decimal("69.77"))

    def test_return_separates_standard_zero_and_exempt(self):
        self.sell(self.standard, "107.50")
        self.sell(self.zero, "200")
        self.sell(self.exempt, "300")
        result = tax.vat_return(self.business, *MARCH)
        self.assertEqual((result["standard_gross"], result["zero_rated"], result["exempt"]),
                         (Decimal("107.50"), Decimal("200.00"), Decimal("300.00")))
        self.assertEqual((result["output_vat"], result["standard_net"]), (Decimal("7.50"), Decimal("100.00")))

    def test_input_vat_reduces_the_amount_payable_and_blocked_vat_is_excluded(self):
        self.sell(self.standard, "1075")  # output 75
        InputVatEntry.raw_objects.create(business=self.business, date=D1, net_amount=Decimal("200"), vat_amount=Decimal("15"))
        InputVatEntry.raw_objects.create(business=self.business, date=D1, net_amount=Decimal("100"), vat_amount=Decimal("7.5"), claimable=False)
        result = tax.vat_return(self.business, *MARCH)
        self.assertEqual((result["output_vat"], result["input_vat"], result["net_vat"]), (Decimal("75.00"), Decimal("15.00"), Decimal("60.00")))
        self.assertEqual(result["input_vat_blocked"], Decimal("7.50"))
        self.assertFalse(result["refundable"])

    def test_more_input_than_output_is_refundable(self):
        InputVatEntry.raw_objects.create(business=self.business, date=D1, net_amount=Decimal("200"), vat_amount=Decimal("15"))
        result = tax.vat_return(self.business, *MARCH)
        self.assertTrue(result["refundable"])
        self.assertEqual(result["net_vat"], Decimal("-15.00"))

    def test_not_registered_or_before_registration_means_no_output_vat(self):
        self.sell(self.standard, "107.50")
        TaxSettings.raw_objects.update(vat_registration_date=date(2026, 3, 20))
        self.assertEqual(tax.vat_return(self.business, *MARCH)["output_vat"], Decimal("0"))
        TaxSettings.raw_objects.update(vat_registered=False, vat_registration_date=None)
        self.assertEqual(tax.vat_return(self.business, *MARCH)["output_vat"], Decimal("0"))

    def test_due_date_is_the_configured_day_of_the_next_month(self):
        self.assertEqual(tax.vat_return(self.business, *MARCH)["due_date"], date(2026, 4, 21))
        december = tax.vat_return(self.business, date(2026, 12, 1), date(2026, 12, 31))
        self.assertEqual(december["due_date"], date(2027, 1, 21))

    def test_register_rows_and_csv(self):
        self.sell(self.standard, "107.50")
        rows = list(tax.vat_register_rows(self.business, *MARCH))
        self.assertEqual((rows[0]["vat"], rows[0]["net"], rows[0]["treatment"]), (Decimal("7.50"), Decimal("100.00"), "standard"))
        self.client.force_login(self.admin)
        body = self.client.get(reverse("vat_return"), {"start": MARCH[0], "end": MARCH[1], "format": "csv"}).content.decode()
        self.assertIn("Bread", body)
        self.assertIn("7.50", body)


class RegistrationMonitorTests(TaxBase):
    def check(self, price, registered=False):
        Sale.raw_objects.all().delete()
        TaxSettings.raw_objects.update(vat_registered=registered, registration_threshold=Decimal("1000"))
        self.sell(self.standard, price, day=TODAY - timedelta(days=10))
        return tax.registration_status(self.business)["state"]

    def test_states(self):
        self.assertEqual(self.check("500"), "below")
        self.assertEqual(self.check("850"), "approaching")
        self.assertEqual(self.check("1000"), "exceeded")
        self.assertEqual(self.check("5000", registered=True), "registered")


class EInvoiceTests(TaxBase):
    def test_numbering_is_sequential_idempotent_and_per_business(self):
        first, second = self.sell(self.standard, "100"), self.sell(self.standard, "50")
        a = tax.prepare_einvoice(self.business, self.admin, first)
        b = tax.prepare_einvoice(self.business, self.admin, second)
        self.assertEqual((a.invoice_number, b.invoice_number), ("INV-000001", "INV-000002"))
        again = tax.prepare_einvoice(self.business, self.admin, first, buyer_tin="999")
        self.assertEqual(again.pk, a.pk)
        self.assertEqual(again.invoice_number, "INV-000001")
        self.assertEqual(again.payload["buyer"]["tin"], "999")
        other = Business.objects.create(name="Other", slug="other")
        TaxSettings.raw_objects.create(business=other, tin="X-1", einvoice_enabled=True)
        stranger = Sale.raw_objects.create(business=other, date=D1, customer="Z")
        SaleItem.objects.create(sale=stranger, finished_good=FinishedGood.raw_objects.create(business=other, name="P", unit="u"),
                                piece_qty=Decimal("1"), price=Decimal("5"))
        self.assertEqual(tax.prepare_einvoice(other, self.admin, stranger).invoice_number, "INV-000001")

    def test_payload_carries_vat_by_line_and_totals(self):
        sale = Sale.raw_objects.create(business=self.business, date=D1, customer="Ada", source="walkin", transaction_type="unpaid")
        SaleItem.objects.create(sale=sale, finished_good=self.standard, piece_qty=Decimal("2"), price=Decimal("53.75"))
        SaleItem.objects.create(sale=sale, finished_good=self.zero, piece_qty=Decimal("1"), price=Decimal("100"))
        payload = tax.prepare_einvoice(self.business, self.admin, sale).payload
        categories = {l["vat_category"]: l for l in payload["lines"]}
        self.assertEqual((categories["standard"]["vat_amount"], categories["standard"]["net_amount"]), ("7.50", "100.00"))
        self.assertEqual(categories["zero"]["vat_amount"], "0.00")
        self.assertEqual(payload["totals"], {"net": "200.00", "vat": "7.50", "gross": "207.50"})
        self.assertEqual((payload["payment_status"], payload["supplier"]["tin"]), ("unpaid", "1234567-0001"))
        self.assertEqual({b["category"] for b in payload["tax_breakdown"]}, {"standard", "zero"})

    def test_unregistered_sales_are_out_of_scope(self):
        TaxSettings.raw_objects.update(vat_registered=False)
        payload = tax.prepare_einvoice(self.business, self.admin, self.sell(self.standard, "100")).payload
        self.assertEqual(payload["lines"][0]["vat_category"], "out_of_scope")
        self.assertEqual(payload["totals"]["vat"], "0.00")

    def test_guards(self):
        sale = self.sell(self.standard, "100")
        TaxSettings.raw_objects.update(tin="")
        with self.assertRaisesMessage(tax.EInvoiceError, "TIN"):
            tax.prepare_einvoice(self.business, self.admin, sale)
        TaxSettings.raw_objects.update(tin="T-1", einvoice_enabled=False)
        with self.assertRaisesMessage(tax.EInvoiceError, "switched off"):
            tax.prepare_einvoice(self.business, self.admin, sale)
        TaxSettings.raw_objects.update(einvoice_enabled=True)
        empty = Sale.raw_objects.create(business=self.business, date=D1, customer="Nobody")
        with self.assertRaisesMessage(tax.EInvoiceError, "no lines"):
            tax.prepare_einvoice(self.business, self.admin, empty)

    def test_a_submitted_invoice_can_no_longer_be_changed(self):
        invoice = tax.prepare_einvoice(self.business, self.admin, self.sell(self.standard, "100"))
        tax.record_clearance(invoice, "IRN-1")
        with self.assertRaisesMessage(tax.EInvoiceError, "already been submitted"):
            tax.prepare_einvoice(self.business, self.admin, invoice.sale)


class ProviderTests(TaxBase):
    def setUp(self):
        super().setUp()
        TaxSettings.raw_objects.update(einvoice_provider="webhook", einvoice_endpoint="https://apps.example/clear")
        self.invoice = tax.prepare_einvoice(self.business, self.admin, self.sell(self.standard, "100"))

    def submit(self, **kwargs):
        with mock.patch("socket.getaddrinfo", return_value=PUBLIC_IP):
            return tax.submit_einvoice(EInvoice.raw_objects.get(pk=self.invoice.pk))

    def test_cleared_response_records_the_irn_stamp_and_qr(self):
        with mock.patch.object(tax, "_post_json", return_value={"irn": "IRN-777", "csid": "STAMP", "qr": "QR-DATA", "status": "cleared"}) as post:
            result = self.submit()
        self.assertEqual((result.status, result.irn, result.csid, result.qr_text), ("cleared", "IRN-777", "STAMP", "QR-DATA"))
        self.assertEqual(post.call_args[0][1]["invoice_number"], self.invoice.invoice_number)
        self.assertIsNotNone(result.cleared_at)

    def test_rejected_failed_and_pending_responses(self):
        with mock.patch.object(tax, "_post_json", return_value={"status": "rejected", "message": "Buyer TIN invalid"}):
            rejected = self.submit()
        self.assertEqual((rejected.status, rejected.error), ("rejected", "Buyer TIN invalid"))
        with mock.patch.object(tax, "_post_json", side_effect=HTTPError("u", 500, "x", {}, None)):
            failed = self.submit()
        self.assertEqual(failed.status, "failed")
        self.assertIn("500", failed.error)
        with mock.patch.object(tax, "_post_json", side_effect=OSError("boom")):
            self.assertEqual(self.submit().status, "failed")
        with mock.patch.object(tax, "_post_json", return_value={"status": "accepted"}):
            self.assertEqual(self.submit().status, "submitted")

    def test_a_cleared_invoice_cannot_be_submitted_twice(self):
        tax.record_clearance(self.invoice, "IRN-1")
        with self.assertRaises(tax.EInvoiceError):
            tax.submit_einvoice(EInvoice.raw_objects.get(pk=self.invoice.pk))

    def test_manual_mode_refuses_to_submit(self):
        TaxSettings.raw_objects.update(einvoice_provider="none")
        with self.assertRaisesMessage(tax.EInvoiceError, "No provider"):
            tax.submit_einvoice(EInvoice.raw_objects.get(pk=self.invoice.pk))

    def test_endpoint_safety(self):
        for url, fragment in (("http://apps.example/x", "https"), ("https://127.0.0.1/x", "internal"),
                              ("https://10.1.2.3/x", "internal"), ("https://169.254.169.254/latest", "internal"),
                              ("https://localhost/x", "internal"), ("", "https")):
            with self.assertRaises(tax.EInvoiceError, msg=url) as ctx:
                tax._check_endpoint(url)
            self.assertIn(fragment, str(ctx.exception))
        with mock.patch("socket.getaddrinfo", return_value=PUBLIC_IP):
            tax._check_endpoint("https://apps.example/clear")  # does not raise

    def test_manual_clearance_needs_an_irn(self):
        with self.assertRaisesMessage(tax.EInvoiceError, "IRN"):
            tax.record_clearance(self.invoice, "  ")


class ScreenTests(TaxBase):
    def test_only_admins_change_settings_and_validation_holds(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(reverse("tax_home"), {"vat_rate": "7.5"}).status_code, 403)
        self.client.force_login(self.admin)
        base = {"vat_rate": "7.5", "registration_threshold": "1000", "filing_due_day": "21", "invoice_prefix": "INV",
                "einvoice_provider": "none", "tin": "T-9"}
        for change, fragment in (({"vat_rate": "150"}, "between 0 and 100"), ({"filing_due_day": "40"}, "between 0 and 100"),
                                 ({"invoice_prefix": "bad prefix!"}, "letters, numbers"), ({"vat_rate": "abc"}, "must be a number"),
                                 ({"einvoice_provider": "webhook", "einvoice_endpoint": "http://x"}, "https://")):
            response = self.client.post(reverse("tax_home"), {**base, **change}, follow=True)
            self.assertContains(response, fragment)
        self.assertEqual(TaxSettings.raw_objects.get().tin, "1234567-0001")  # nothing invalid was saved
        self.client.post(reverse("tax_home"), {**base, "vat_registered": "1", "vat_rate": "5"})
        saved = TaxSettings.raw_objects.get()
        self.assertEqual((saved.vat_rate, saved.tin, saved.vat_registered), (Decimal("5"), "T-9", True))

    def test_without_a_server_secret_the_key_is_refused_loudly_and_nothing_is_saved(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("tax_home"), {
            "vat_rate": "9", "registration_threshold": "1", "filing_due_day": "21", "invoice_prefix": "INV",
            "einvoice_provider": "webhook", "einvoice_endpoint": "https://apps.example/x", "einvoice_api_key": "k"}, follow=True)
        self.assertContains(response, "WHATSAPP_TOKEN_ENCRYPTION_KEY")
        self.assertEqual(TaxSettings.raw_objects.get().vat_rate, Decimal("7.50"))

    def test_an_unreadable_saved_key_fails_cleanly_on_submit(self):
        TaxSettings.raw_objects.update(einvoice_provider="webhook", einvoice_endpoint="https://apps.example/x",
                                      einvoice_api_key_encrypted="garbage")
        invoice = tax.prepare_einvoice(self.business, self.admin, self.sell(self.standard, "100"))
        with mock.patch("socket.getaddrinfo", return_value=PUBLIC_IP), override_settings(WHATSAPP_TOKEN_ENCRYPTION_KEY="k" * 40):
            with self.assertRaisesMessage(tax.EInvoiceError, "cannot be read"):
                tax.submit_einvoice(invoice)

    @override_settings(WHATSAPP_TOKEN_ENCRYPTION_KEY="k" * 40)
    def test_api_key_is_stored_encrypted_and_never_shown_back(self):
        self.client.force_login(self.admin)
        base = {"vat_rate": "7.5", "registration_threshold": "1", "filing_due_day": "21", "invoice_prefix": "INV",
                "einvoice_provider": "webhook", "einvoice_endpoint": "https://apps.example/x", "einvoice_api_key": "super-secret-key"}
        self.client.post(reverse("tax_home"), base)
        stored = TaxSettings.raw_objects.get().einvoice_api_key_encrypted
        self.assertTrue(stored)
        self.assertNotIn("super-secret-key", stored)
        page = self.client.get(reverse("tax_home"))
        self.assertNotContains(page, "super-secret-key")
        self.assertNotContains(page, stored)
        self.client.post(reverse("tax_home"), {**base, "einvoice_api_key": ""})
        self.assertEqual(TaxSettings.raw_objects.get().einvoice_api_key_encrypted, stored)  # blank keeps it

    def test_input_vat_and_product_treatment_screens(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("input_vat"), {"date": D1.isoformat(), "supplier": "Mill", "net_amount": "200", "vat_amount": "15", "claimable": "1"})
        entry = InputVatEntry.raw_objects.get()
        self.assertTrue(entry.claimable)
        bad = self.client.post(reverse("input_vat"), {"date": D1.isoformat(), "net_amount": "x", "vat_amount": "1"}, follow=True)
        self.assertContains(bad, "must be a number")
        negative = self.client.post(reverse("input_vat"), {"date": D1.isoformat(), "net_amount": "5", "vat_amount": "-1"}, follow=True)
        self.assertContains(negative, "cannot be negative")
        self.client.post(reverse("input_vat"), {"action": "delete", "entry": entry.pk})
        self.assertFalse(InputVatEntry.raw_objects.exists())
        self.client.post(reverse("product_vat"), {f"vat_{self.standard.pk}": "exempt", f"vat_{self.zero.pk}": "nonsense"})
        self.assertEqual(FinishedGood.raw_objects.get(pk=self.standard.pk).vat_treatment, "exempt")
        self.assertEqual(FinishedGood.raw_objects.get(pk=self.zero.pk).vat_treatment, "zero")  # invalid value ignored

    def test_einvoice_flow_through_the_screens(self):
        sale = self.sell(self.standard, "107.50")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("einvoices"), {"start": D1, "end": D1}), "Prepare")
        response = self.client.post(reverse("einvoices"), {"action": "prepare", "sale": sale.pk, "buyer_tin": "B-1"}, follow=True)
        self.assertContains(response, "INV-000001")
        invoice = EInvoice.raw_objects.get()
        data = self.client.get(reverse("einvoice_detail", args=[invoice.pk]), {"format": "json"})
        self.assertEqual(json.loads(data.content)["buyer"]["tin"], "B-1")
        self.client.post(reverse("einvoices"), {"action": "record", "invoice": invoice.pk, "irn": "IRN-5", "csid": "S"})
        invoice.refresh_from_db()
        self.assertEqual((invoice.status, invoice.irn), ("cleared", "IRN-5"))
        self.assertContains(self.client.get(reverse("einvoice_detail", args=[invoice.pk])), "IRN-5")

    def test_another_business_cannot_open_an_invoice(self):
        invoice = tax.prepare_einvoice(self.business, self.admin, self.sell(self.standard, "100"))
        other = Business.objects.create(name="Intruder", slug="intruder")
        roles = seed_business_roles(other)
        intruder = CustomUser.objects.create_user(username="tax-intruder", password="pw-123456")
        UserBusiness.objects.create(user=intruder, business=other, role=roles[CustomUser.ROLE_BUSINESS_ADMIN])
        self.client.force_login(intruder)
        self.assertEqual(self.client.get(reverse("einvoice_detail", args=[invoice.pk])).status_code, 404)
