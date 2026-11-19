"""Branch operations extend the existing stock and payment ledgers."""

import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator
from core.models import BusinessOwnedModel


class Branch(BusinessOwnedModel):
    catalogue_mode = models.CharField(
        max_length=12,
        choices=[
            ("independent", "Independent branch catalogue"),
            ("shared", "Shared headquarters catalogue"),
        ],
        default="independent",
    )
    catalogue_map = models.JSONField(default=dict, blank=True, editable=False)
    catalogue_signature = models.CharField(max_length=64, blank=True, editable=False)
    workspace = models.OneToOneField(
        "core.Business",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="location_branch",
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="managed_branches",
    )
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=30)
    address = models.CharField(max_length=255, blank=True)
    location = models.OneToOneField(
        "inventory.InventoryLocation", on_delete=models.PROTECT
    )
    is_main = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_main", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "code"], name="retail_branch_code"
            ),
            models.UniqueConstraint(
                fields=["business"],
                condition=models.Q(is_main=True),
                name="retail_one_main_branch",
            ),
        ]

    def __str__(self):
        return self.name


class BranchAccess(models.Model):
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="staff_access"
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "user"], name="retail_branch_staff"
            )
        ]


class BranchStock(BusinessOwnedModel):
    """Allocated secondary-branch stock; main is the unallocated global balance."""

    branch = models.ForeignKey(
        Branch, on_delete=models.PROTECT, related_name="stock_rows"
    )
    product = models.ForeignKey("inventory.FinishedGood", on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "product"], name="retail_branch_product"
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=0), name="retail_branch_nonnegative"
            ),
        ]


class StockTransfer(BusinessOwnedModel):
    destination_product = models.ForeignKey(
        "inventory.FinishedGood",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="incoming_workspace_transfers",
    )
    source = models.ForeignKey(
        Branch, on_delete=models.PROTECT, related_name="outgoing_transfers"
    )
    destination = models.ForeignKey(
        Branch, on_delete=models.PROTECT, related_name="incoming_transfers"
    )
    product = models.ForeignKey("inventory.FinishedGood", on_delete=models.PROTECT)
    quantity = models.DecimalField(
        max_digits=16, decimal_places=2, validators=[MinValueValidator(Decimal(".01"))]
    )
    received_quantity = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True
    )
    status = models.CharField(
        max_length=12,
        choices=[("dispatched", "Dispatched"), ("received", "Received")],
        default="dispatched",
    )
    note = models.CharField(max_length=500, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="received_branch_transfers",
    )
    received_at = models.DateTimeField(null=True, blank=True)


class RetailSettings(BusinessOwnedModel):
    business = models.OneToOneField(
        "core.Business", on_delete=models.CASCADE, related_name="retail_settings"
    )
    require_approved_devices = models.BooleanField(default=False)
    require_cashier_session = models.BooleanField(default=False)


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="personal_profile",
    )
    photo = models.ImageField(upload_to="staff-profiles/%Y/%m/", blank=True)
    address = models.CharField(max_length=255, blank=True)
    emergency_contact = models.CharField(max_length=160, blank=True)
    emergency_phone = models.CharField(max_length=40, blank=True)


class RegisteredDevice(BusinessOwnedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="retail_devices",
    )
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.PROTECT)
    name = models.CharField(max_length=100)
    token_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=12,
        choices=[
            ("pending", "Pending approval"),
            ("approved", "Approved"),
            ("revoked", "Revoked"),
        ],
        default="pending",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reviewed_retail_devices",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)


class CashierSession(BusinessOwnedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    device = models.ForeignKey(
        RegisteredDevice, null=True, blank=True, on_delete=models.PROTECT
    )
    opening_float = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    expected_cash = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    counted_cash = models.DecimalField(
        max_digits=16, decimal_places=2, null=True, blank=True
    )
    variance = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    close_note = models.CharField(max_length=500, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reviewed_cashier_sessions",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "user"],
                condition=models.Q(closed_at__isnull=True),
                name="retail_one_open_session",
            )
        ]


class CashierCheckout(models.Model):
    session = models.ForeignKey(
        CashierSession, on_delete=models.PROTECT, related_name="checkouts"
    )
    checkout = models.OneToOneField(
        "commerce.CommerceCheckoutSession",
        on_delete=models.PROTECT,
        related_name="cashier_record",
    )


class ProductPromotion(BusinessOwnedModel):
    eligibility_rule = models.TextField(
        max_length=1000,
        blank=True,
        default="",
        help_text="Customer-facing eligibility wording. Leave blank to hide this rule. Product, portion and quantity settings above control eligibility.",
    )
    pricing_rule = models.TextField(
        max_length=1000,
        blank=True,
        default="",
        help_text="Customer-facing pricing wording. Leave blank to hide this rule. Configured prices and promotion mode still control billing.",
    )
    product_portions = models.JSONField(default=dict, blank=True, editable=False)
    reward_portions = models.JSONField(default=dict, blank=True, editable=False)
    tooltip_title = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Optional heading for this promotion’s help tip; defaults to the promotion name.",
    )
    tooltip_text = models.TextField(
        max_length=1000,
        blank=True,
        default="",
        help_text="Describe this offer in your own words. Each non-empty line becomes a list item. Eligibility, dates and pricing rules are also shown.",
    )
    channels = models.JSONField(default=list, blank=True)
    product_prices = models.JSONField(default=dict, blank=True, editable=False)

    @property
    def channel_codes(self):
        return self.channels or ([self.channel] if self.channel != "all" else ["all"])

    def get_channels_display(self):
        labels = dict(self._meta.get_field("channel").choices)
        return ", ".join(labels.get(code, code) for code in self.channel_codes)

    name = models.CharField(max_length=100)
    products = models.ManyToManyField(
        "inventory.FinishedGood", related_name="retail_promotions"
    )
    reward_products = models.ManyToManyField(
        "inventory.FinishedGood", related_name="reward_promotions", blank=True
    )
    mode = models.CharField(
        max_length=15,
        choices=[
            ("buy_get", "Buy X, get Y free"),
            ("mix_match", "Mix-and-match bundle"),
        ],
    )
    qualifying_quantity = models.PositiveIntegerField(
        default=2, validators=[MinValueValidator(1)]
    )
    free_quantity = models.PositiveIntegerField(
        default=1, validators=[MinValueValidator(1)]
    )
    bundle_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    starts_on = models.DateField()
    ends_on = models.DateField()
    active = models.BooleanField(default=True)
    channel = models.CharField(
        max_length=20,
        choices=[
            ("all", "All channels"),
            ("physical_store", "Physical Store"),
            ("online", "Online"),
            ("distribution", "Distribution"),
        ],
        default="all",
    )


class CustomerQuotation(BusinessOwnedModel):
    title = models.CharField(max_length=160, default="")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    customer_name = models.CharField(max_length=160)
    customer_email = models.EmailField(blank=True)
    customer_phone = models.CharField(max_length=40, blank=True)
    valid_until = models.DateField()
    channel = models.CharField(
        max_length=20,
        choices=[
            ("online", "Online"),
            ("physical_store", "Physical Store"),
            ("distribution", "Distribution"),
        ],
        default="online",
    )
    status = models.CharField(
        max_length=12,
        choices=[
            ("draft", "Draft"),
            ("sent", "Sent"),
            ("accepted", "Accepted"),
            ("converted", "Awaiting payment"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
    )
    notes = models.TextField(blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    checkout = models.OneToOneField(
        "commerce.CommerceCheckoutSession",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )

    @property
    def total(self):
        return sum((line.line_total for line in self.lines.all()), Decimal("0.00"))


class QuotationLine(models.Model):
    channel_unit_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, editable=False
    )
    quotation = models.ForeignKey(
        CustomerQuotation, on_delete=models.CASCADE, related_name="lines"
    )
    product = models.ForeignKey("commerce.StorefrontProduct", on_delete=models.PROTECT)
    quantity = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal(".01"))]
    )
    unit_price = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal(".01"))]
    )

    @property
    def line_total(self):
        return (self.quantity * self.unit_price).quantize(Decimal(".01"))


class ParkedBasket(BusinessOwnedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    label = models.CharField(max_length=100)
    contents = models.JSONField(default=dict)
    resumed_at = models.DateTimeField(null=True, blank=True)


class OfflineEntry(BusinessOwnedModel):
    public_id = models.UUIDField(default=uuid.uuid4)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    device = models.ForeignKey(
        RegisteredDevice, null=True, blank=True, on_delete=models.PROTECT
    )
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20)
    payload = models.JSONField(default=dict)
    result = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "user", "public_id"],
                name="retail_offline_idempotency",
            )
        ]
