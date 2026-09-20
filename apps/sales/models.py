from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from core.models import BusinessOwnedModel, TimestampedModel


class Customer(BusinessOwnedModel):
    """Master record for a business customer.

    Orders and sales retain their historical customer-name snapshot so old
    documents remain readable even if the master record is later edited.
    """
    name = models.CharField(max_length=160)
    phone = models.CharField(max_length=40, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    address = models.TextField(blank=True, default="")
    region = models.CharField(max_length=100, blank=True, default="")
    customer_group = models.CharField(max_length=100, blank=True, default="")
    credit_limit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    payment_terms_days = models.PositiveIntegerField(default=0, help_text="Expected payment period in days for credit sales.")
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["business", "name"], name="unique_customer_per_business")
        ]

    def __str__(self):
        return self.name

    @property
    def outstanding_balance(self):
        total = Decimal("0")
        # Customer list pages attach only the relevant open sales to this
        # attribute. Honour that prefetch when present; callers elsewhere keep
        # the existing scoped-query fallback and therefore unchanged behaviour.
        sales = getattr(self, "_open_sales_for_balance", None)
        if sales is None:
            sales = self.sales_records.filter(
                source__in=("distribution_order", "online_order"),
                transaction_type__in=("unpaid", "partial"),
            ).prefetch_related("items__finished_good", "payments")
        for sale in sales:
            total += max(
                Decimal("0"),
                sale.total - sum((payment.amount for payment in sale.payments.all()), Decimal("0")),
            )
        return total


class CustomerProductPrice(BusinessOwnedModel):
    """Customer-specific selling price for a finished good and sales channel.

    This is an override layer above FinishedGood channel pricing. Orders snapshot
    the resolved price into OrderItem/SaleItem so later price changes do not alter
    historical documents or analytics.
    """
    CHANNEL_CHOICES = [
        ("distribution", "Distribution"),
        ("online", "Online"),
    ]

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="product_prices")
    finished_good = models.ForeignKey("inventory.FinishedGood", on_delete=models.PROTECT, related_name="customer_prices")
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES)
    price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["finished_good__name", "channel"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "customer", "finished_good", "channel"],
                name="unique_customer_product_price",
            )
        ]

    def clean(self):
        if self.customer_id and self.finished_good_id:
            if self.customer.business_id != self.finished_good.business_id:
                raise ValidationError("Customer and finished good must belong to the same business.")
        if self.channel not in {"distribution", "online"}:
            raise ValidationError("Customer-specific prices are only available for Distribution and Online channels.")
        if self.price is not None and self.price < 0:
            raise ValidationError("Agreed price cannot be negative.")

    def __str__(self):
        return f"{self.customer.name} — {self.finished_good.name} — {self.get_channel_display()} — {self.price}"


class Sale(BusinessOwnedModel):
    """A completed stock/sales event. Physical-store rows may be paid or
    unpaid product issues; Distribution/Online rows may remain receivables
    until Finance records one or more CustomerPayment entries."""

    PAYMENT_CHOICES = [("Cash", "Cash"), ("Card", "Card"), ("Transfer", "Transfer")]
    TRANSACTION_CHOICES = [("paid", "Paid"), ("partial", "Partially Paid"), ("unpaid", "Unpaid")]
    SOURCE_CHOICES = [("walkin", "Physical Store"), ("distribution_order", "Distribution Order"), ("online_order", "Online Order")]
    SERVICE_DINE_IN = "dine_in"
    SERVICE_TAKEAWAY = "takeaway"
    SERVICE_DELIVERY = "delivery"
    SERVICE_MODE_CHOICES = [
        (SERVICE_DINE_IN, "Dine-in"),
        (SERVICE_TAKEAWAY, "Takeaway / pickup"),
        (SERVICE_DELIVERY, "Delivery"),
    ]

    date = models.DateField()
    customer = models.CharField(max_length=120, blank=True, default="Walk-in")
    customer_master = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="sales_records")
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_CHOICES, default="paid", help_text="Payment state. Physical-store unpaid sales are non-cash issues; customer-order sales are receivables until Finance records payment.")
    unpaid_description = models.CharField(max_length=255, blank=True, default="", help_text="Reason for physical-store unpaid issue, or receivable note for customer orders.")
    account = models.ForeignKey("core.CashAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="sales")
    payment_method = models.CharField(
        max_length=10, choices=PAYMENT_CHOICES, default="Cash", blank=True,
        help_text="Payment method actually received. May be blank while a sale remains an unpaid receivable.",
    )
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="walkin")
    linked_order = models.ForeignKey(
        "production.Order", null=True, blank=True, on_delete=models.SET_NULL,
        help_text="Set automatically if this sale was created from a completed distribution or online order.",
    )
    service_mode = models.CharField(
        max_length=12, choices=SERVICE_MODE_CHOICES, blank=True, default="",
        help_text="Restaurant service context. Leave blank when restaurant service details do not apply.",
    )
    table_reference = models.CharField(
        max_length=40, blank=True, default="",
        help_text="Optional restaurant table number, tab name, or service reference.",
    )

    class Meta:
        ordering = ["-date", "-id"]

    def clean(self):
        super().clean()
        if self.transaction_type == "paid" and not (self.payment_method or "").strip():
            raise ValidationError({"payment_method": "Select the payment method received for a paid sale."})

    def __str__(self):
        return f"Sale #{self.id} — {self.customer}"

    @property
    def total(self):
        return sum((i.line_total for i in self.items.all()), Decimal("0"))

    @property
    def display_source(self):
        if self.source == "walkin" and self.business.is_restaurant:
            return "Restaurant POS"
        if self.source == "distribution_order" and self.business.is_wholesale:
            return "Wholesale"
        if self.source == "walkin" and self.business.is_retail:
            return "Retail POS"
        return self.get_source_display()


class SaleItem(TimestampedModel):
    sale = models.ForeignKey(Sale, related_name="items", on_delete=models.CASCADE)
    finished_good = models.ForeignKey("inventory.FinishedGood", on_delete=models.PROTECT)
    batch_qty = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    piece_qty = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0,
        help_text="Snapshot of the product's selling price at sale time — set automatically.")
    commercial_quantity = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="Optional customer-facing quantity snapshot for commerce portion/bulk sales.",
    )
    commercial_unit = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Customer-facing unit snapshot for commerce portion/bulk sales.",
    )
    commercial_unit_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="Exact customer-facing unit price snapshot for commerce portion/bulk sales.",
    )
    unit_cost = models.DecimalField(max_digits=16, decimal_places=6, null=True, blank=True,
        help_text="Product cost per unit captured at the time of sale.")
    production_batch = models.ForeignKey("production.ProductionBatch", null=True, blank=True, on_delete=models.SET_NULL, related_name="sale_items")

    def __str__(self):
        return f"{self.sale.customer} — {self.finished_good.name} x{self.total_units}"

    @property
    def total_units(self):
        upb = self.finished_good.units_per_batch or Decimal("1")
        return self.batch_qty * upb + self.piece_qty

    @property
    def line_total(self):
        if self.commercial_quantity is not None and self.commercial_unit_price is not None:
            return self.commercial_quantity * self.commercial_unit_price
        return self.total_units * ((self.price or Decimal("0")) - (self.discount or Decimal("0")))


class CustomerPayment(BusinessOwnedModel):
    date = models.DateField()
    customer = models.CharField(max_length=120)
    customer_master = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="payments")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    payment_method = models.CharField(max_length=10, choices=Sale.PAYMENT_CHOICES, default="Cash")
    reference = models.CharField(max_length=80, blank=True, default="")
    notes = models.CharField(max_length=255, blank=True, default="")
    sale = models.ForeignKey(Sale, null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    account = models.ForeignKey("core.CashAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="customer_payments")
    class Meta: ordering = ["-date", "-id"]
