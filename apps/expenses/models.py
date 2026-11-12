from decimal import Decimal

from django.conf import settings
from django.db import models
from core.models import BusinessOwnedModel, TimestampedModel


class Expense(BusinessOwnedModel):
    ledger_date_field = "date"
    PAYMENT_STATUS_CHOICES = [("paid", "Paid"), ("unpaid", "Unpaid")]
    PAYMENT_METHOD_CHOICES = [("Cash", "Cash"), ("Card", "Card"), ("Transfer", "Transfer")]

    CATEGORY_CHOICES = [
        ("utilities", "Utilities"),
        ("rent", "Rent / Premises"),
        ("maintenance", "Maintenance / Repairs"),
        ("transport", "Transport / Delivery"),
        ("labour", "Labour / Wages"),
        ("marketing", "Marketing"),
        ("bank", "Bank / Payment Fees"),
        ("other", "Other"),
    ]

    date = models.DateField()
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="other")
    description = models.CharField(max_length=180)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    payment_status = models.CharField(max_length=10, choices=PAYMENT_STATUS_CHOICES, default="paid")
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES, default="Transfer")
    account = models.ForeignKey("core.CashAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="expenses")
    vendor = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="expenses_created",
    )

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["business", "payment_status", "date"], name="expense_biz_pay_date_idx"),
            models.Index(fields=["business", "date"], name="expense_biz_date_idx"),
        ]

    def __str__(self):
        return f"{self.description} — {self.amount}"


class ExpensePayment(BusinessOwnedModel):
    ledger_date_field = "date"
    date = models.DateField()
    expense = models.ForeignKey(Expense, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    payment_method = models.CharField(max_length=10, choices=Expense.PAYMENT_METHOD_CHOICES, default="Transfer")
    reference = models.CharField(max_length=80, blank=True, default="")
    notes = models.CharField(max_length=255, blank=True, default="")
    account = models.ForeignKey("core.CashAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="expense_payments")

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["business", "date"], name="exp_pay_biz_date_idx"),
        ]

    def __str__(self):
        return f"Expense #{self.expense_id} payment — {self.amount}"


class ExpenseRequest(BusinessOwnedModel):
    """Daily planning envelope; only approval creates operational expenses."""
    ledger_date_field = 'date'
    date = models.DateField()
    status = models.CharField(max_length=10, choices=[('pending','Awaiting approval'),('approved','Approved'),('declined','Removed')], default='pending')
    notes = models.TextField(blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='approved_expense_requests')
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-date','-pk']
        constraints = [models.UniqueConstraint(fields=['business','date'], condition=models.Q(status='pending'), name='expense_request_pending_day')]

    @property
    def total(self):
        return sum((line.amount for line in self.lines.all() if not line.removed), Decimal('0'))


class ExpenseRequestLine(TimestampedModel):
    request = models.ForeignKey(ExpenseRequest, on_delete=models.CASCADE, related_name='lines')
    category = models.CharField(max_length=20, choices=Expense.CATEGORY_CHOICES, default='other')
    description = models.CharField(max_length=180)
    requested_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    vendor = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    payment_status = models.CharField(max_length=10, choices=Expense.PAYMENT_STATUS_CHOICES, default='unpaid')
    payment_method = models.CharField(max_length=10, choices=Expense.PAYMENT_METHOD_CHOICES, default='Transfer')
    account = models.ForeignKey('core.CashAccount', null=True, blank=True, on_delete=models.PROTECT)
    approval_reason = models.TextField(blank=True)
    removed = models.BooleanField(default=False)
    expense = models.OneToOneField(Expense, null=True, blank=True, on_delete=models.PROTECT, related_name='request_line')
