from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Business, CashAccount
from inventory.models import FinishedGood, FinishedGoodChannelPrice
from .forms import SaleForm
from .models import Customer, CustomerProductPrice, Sale


class CustomerProductPriceTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Test Business", slug="test-business")
        self.customer = Customer.objects.create(business=self.business, name="Customer A")
        self.good = FinishedGood.objects.create(
            business=self.business, name="Product A", unit="piece", selling_price=Decimal("100.00")
        )
        FinishedGoodChannelPrice.objects.create(
            finished_good=self.good, channel="distribution", price=Decimal("90.00")
        )
        FinishedGoodChannelPrice.objects.create(
            finished_good=self.good, channel="online", price=Decimal("95.00")
        )

    def test_customer_price_overrides_channel_price(self):
        CustomerProductPrice.objects.create(
            business=self.business, customer=self.customer, finished_good=self.good,
            channel="distribution", price=Decimal("120.00")
        )
        self.assertEqual(self.good.selling_price_for("distribution", self.customer), Decimal("120.00"))
        self.assertEqual(self.good.selling_price_for("online", self.customer), Decimal("95.00"))

    def test_without_customer_override_channel_price_is_unchanged(self):
        self.assertEqual(self.good.selling_price_for("distribution", self.customer), Decimal("90.00"))

    def test_customer_price_is_channel_specific(self):
        CustomerProductPrice.objects.create(
            business=self.business, customer=self.customer, finished_good=self.good,
            channel="online", price=Decimal("125.00")
        )
        self.assertEqual(self.good.selling_price_for("online", self.customer), Decimal("125.00"))
        self.assertEqual(self.good.selling_price_for("distribution", self.customer), Decimal("90.00"))


class RestaurantSaleFormTests(TestCase):
    def setUp(self):
        self.restaurant = Business.objects.create(
            name="Restaurant", slug="restaurant", vertical=Business.VERTICAL_RESTAURANT,
            restaurant_table_service=True,
        )
        self.account = CashAccount.objects.create(
            business=self.restaurant, name="Till", account_type="cash"
        )

    def data(self, **overrides):
        data = {
            "date": "2026-09-02",
            "customer": "Walk-in",
            "service_mode": Sale.SERVICE_DINE_IN,
            "table_reference": "",
            "transaction_type": "paid",
            "payment_method": "Cash",
            "account": self.account.pk,
        }
        data.update(overrides)
        return data

    def test_dine_in_requires_table_reference_when_table_service_enabled(self):
        form = SaleForm(self.data(), business=self.restaurant)
        self.assertFalse(form.is_valid())
        self.assertIn("table_reference", form.errors)

    def test_takeaway_does_not_require_table_reference(self):
        form = SaleForm(
            self.data(service_mode=Sale.SERVICE_TAKEAWAY), business=self.restaurant
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_non_restaurant_keeps_legacy_sale_form(self):
        bakery = Business.objects.create(name="Bakery", slug="legacy-bakery")
        form = SaleForm(business=bakery)
        self.assertNotIn("service_mode", form.fields)
        self.assertNotIn("table_reference", form.fields)


class SalePaymentMethodValidationTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(name="Receivable Business", slug="receivable-business")

    def test_unpaid_receivable_may_have_no_payment_method_yet(self):
        sale = Sale(
            business=self.business,
            date="2026-09-02",
            customer="Credit Customer",
            transaction_type="unpaid",
            unpaid_description="Customer receivable — payment to be recorded through Finance.",
            payment_method="",
            account=None,
            source="distribution_order",
        )
        sale.full_clean()

    def test_paid_sale_still_requires_payment_method(self):
        sale = Sale(
            business=self.business,
            date="2026-09-02",
            customer="Paid Customer",
            transaction_type="paid",
            payment_method="",
            source="walkin",
        )
        with self.assertRaises(ValidationError) as exc:
            sale.full_clean()
        self.assertIn("payment_method", exc.exception.message_dict)
