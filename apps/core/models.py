from django.conf import settings
from django.core.validators import FileExtensionValidator, RegexValidator
from django.db import models
from decimal import Decimal
from pathlib import Path
import uuid
from .context import get_current_business_id


def business_storefront_logo_upload_to(instance, filename):
    """Store tenant storefront logos in tenant-partitioned media paths."""
    extension = Path(filename or "").suffix.lower()
    if extension not in {".jpeg", ".jpg", ".png", ".webp"}:
        extension = ".png"
    business_id = instance.pk or "unassigned"
    return f"businesses/business-{business_id}/storefront-logo/{uuid.uuid4().hex}{extension}"


class Business(models.Model):
    """Tenant root shared by every vertical.

    Existing rows default to ``bakery`` so the original workflow and labels
    remain unchanged after the multitenant migration.
    """
    VERTICAL_BAKERY = "bakery"
    VERTICAL_RESTAURANT = "restaurant"
    VERTICAL_GENERAL = "general"
    VERTICAL_WHOLESALE = "wholesale"
    VERTICAL_RETAIL = "retail"
    VERTICAL_CHOICES = [
        (VERTICAL_BAKERY, "Bakery"),
        (VERTICAL_RESTAURANT, "Restaurant / Food Service"),
        (VERTICAL_GENERAL, "General Production"),
        (VERTICAL_WHOLESALE, "Wholesale / Distribution"),
        (VERTICAL_RETAIL, "Retail Store"),
    ]

    name = models.CharField(max_length=120, default="My Business")
    currency_symbol = models.CharField(max_length=5, default="₦")
    slug = models.SlugField(max_length=60, unique=True, default="main")
    vertical = models.CharField(max_length=20, choices=VERTICAL_CHOICES, default=VERTICAL_BAKERY)
    accent_color = models.CharField(
        max_length=7,
        default="#D14900",
        validators=[RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Use a six-digit hex colour such as #D14900.")],
        help_text="Used for primary buttons, links, headings, and action highlights.",
    )
    background_color = models.CharField(
        max_length=7,
        default="#050733",
        validators=[RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Use a six-digit hex colour such as #050733.")],
        help_text="Used for persistent branded backgrounds such as the navigation area.",
    )
    tagline = models.CharField(max_length=100, blank=True, default="")
    storefront_logo = models.ImageField(
        upload_to=business_storefront_logo_upload_to,
        blank=True,
        validators=[FileExtensionValidator(["jpeg", "jpg", "png", "webp"])],
        help_text="Optional logo shown beside the business name on the public storefront only.",
    )
    restaurant_table_service = models.BooleanField(
        default=True,
        help_text="For restaurant businesses, capture a table/reference for dine-in sales.",
    )

    class Meta:
        verbose_name_plural = "businesses"

    def __str__(self):
        return self.name

    @property
    def is_restaurant(self):
        return self.vertical == self.VERTICAL_RESTAURANT

    @property
    def is_wholesale(self):
        return self.vertical == self.VERTICAL_WHOLESALE

    @property
    def is_retail(self):
        return self.vertical == self.VERTICAL_RETAIL

    @property
    def uses_production(self):
        return self.vertical not in {self.VERTICAL_WHOLESALE, self.VERTICAL_RETAIL}

    @staticmethod
    def _contrast_color(value):
        """Return a readable black/white foreground for a configured hex colour."""
        try:
            red, green, blue = (int(value[index:index + 2], 16) for index in (1, 3, 5))
        except (TypeError, ValueError):
            return "#FFFFFF"
        luminance = (red * 299 + green * 587 + blue * 114) / 1000
        return "#211D1A" if luminance > 150 else "#FFFFFF"

    @property
    def button_text_color(self):
        return self._contrast_color(self.accent_color)

    @property
    def background_text_color(self):
        return self._contrast_color(self.background_color)

    @classmethod
    def default(cls):
        """Return the default business for setup and administration only.

        Request tenancy is resolved from memberships in BusinessMiddleware;
        application views must not use this helper to choose a user's tenant.
        """
        obj, _ = cls.objects.get_or_create(slug="main", defaults={"name": "My Business"})
        try:
            from accounts.services import seed_business_modules, seed_business_roles
            seed_business_roles(obj)
            seed_business_modules(obj)
        except Exception:
            # Keep model import/bootstrap safe before migrations are complete.
            pass
        return obj


class BaseModel(models.Model):
    class Meta:
        abstract = True


class TimestampedModel(BaseModel):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BusinessManager(models.Manager):
    """Scoped manager: filters to the business resolved by BusinessMiddleware
    for the current request. With one business today this filter is a no-op
    in practice, but every model using this manager is already structured
    for multi-location — the filtering becomes real the day Business stops
    being a single row."""

    def get_queryset(self):
        qs = super().get_queryset()
        business_id = get_current_business_id()
        if business_id is not None:
            qs = qs.filter(business_id=business_id)
        return qs


class BusinessOwnedModel(TimestampedModel):
    """Base for any model that belongs to a business. Mirrors ChurchOwnedModel:
    a scoped default manager plus an explicit unscoped manager for admin,
    management commands, and provisioning — see rule in CLAUDE.md."""
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="%(app_label)s_%(class)s_set")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="%(app_label)s_%(class)s_created",
        help_text="Person who created this record.",
    )

    objects = BusinessManager()
    raw_objects = models.Manager()

    class Meta:
        abstract = True

class CashAccount(BusinessOwnedModel):
    """A cash/bank/card ledger account used for actual money movement."""
    ACCOUNT_CHOICES = [("cash", "Cash"), ("bank", "Bank / Transfer"), ("card", "Card / POS"), ("other", "Other")]
    name = models.CharField(max_length=80)
    account_type = models.CharField(max_length=10, choices=ACCOUNT_CHOICES, default="cash")
    opening_balance = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    active = models.BooleanField(default=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "name"], name="unique_cash_account_per_business")]
        ordering = ["name"]
    def __str__(self): return self.name
    @property
    def balance(self):
        calculated = getattr(self, "_calculated_balance", None)
        if calculated is not None:
            return calculated
        return self.opening_balance + sum((t.signed_amount for t in self.transactions.all()), Decimal("0"))


class FinancialTransaction(BusinessOwnedModel):
    """Immutable-style cash ledger entry. Positive income, negative outflow."""
    INCOME = "income"
    OUTFLOW = "outflow"
    TYPE_CHOICES = [(INCOME, "Money In"), (OUTFLOW, "Money Out")]
    date = models.DateField()
    transaction_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    category = models.CharField(max_length=80)
    description = models.CharField(max_length=255)
    payment_method = models.CharField(max_length=20, blank=True, default="")
    reference = models.CharField(max_length=80, blank=True, default="")
    account = models.ForeignKey(CashAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="transactions")
    reversed = models.BooleanField(default=False)
    reversal_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal_entries")
    class Meta:
        ordering = ["-date", "-id"]
    @property
    def signed_amount(self): return self.amount if self.transaction_type == self.INCOME else -self.amount


class AuditLog(BusinessOwnedModel):
    """Human-readable audit trail for important business mutations."""
    action = models.CharField(max_length=30)
    model_name = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80, blank=True, default="")
    description = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)
    class Meta:
        ordering = ["-created_at", "-id"]


class AuditQuery(BusinessOwnedModel):
    STATUS_OPEN = "open"
    STATUS_REVIEWING = "reviewing"
    STATUS_ANSWERED = "answered"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_REVIEWING, "Under review"),
        (STATUS_ANSWERED, "Answered"),
        (STATUS_CLOSED, "Closed"),
    ]
    SEVERITY_LOW = "low"
    SEVERITY_MEDIUM = "medium"
    SEVERITY_HIGH = "high"
    SEVERITY_CHOICES = [(SEVERITY_LOW, "Low"), (SEVERITY_MEDIUM, "Medium"), (SEVERITY_HIGH, "High")]

    module = models.CharField(max_length=40)
    record_label = models.CharField(max_length=180, blank=True, default="")
    record_model = models.CharField(max_length=120, blank=True, default="")
    record_id = models.CharField(max_length=80, blank=True, default="")
    subject = models.CharField(max_length=160)
    message = models.TextField()
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default=SEVERITY_MEDIUM)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_OPEN)
    response = models.TextField(blank=True, default="")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_audit_queries")
    answered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="answered_audit_queries")
    answered_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "-created_at", "-id"]
        indexes = [models.Index(fields=["business", "status", "created_at"], name="audit_query_status_idx")]

    def __str__(self):
        return f"{self.subject} — {self.get_status_display()}"
