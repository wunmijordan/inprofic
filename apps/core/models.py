from django.conf import settings
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from decimal import Decimal
from pathlib import Path
import uuid
from .context import get_current_business_id, get_ledger_cutover_date, get_ledger_scope


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
    contact_phone = models.CharField(max_length=40, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    contact_address = models.CharField(max_length=255, blank=True, default="")
    contact_website = models.URLField(max_length=255, blank=True, default="")
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
    PROFIT_PERIOD_YEAR = "year"
    PROFIT_PERIOD_HALF = "half"
    PROFIT_PERIOD_QUARTER = "quarter"
    PROFIT_PERIOD_CHOICES = [
        (PROFIT_PERIOD_YEAR, "Business year"),
        (PROFIT_PERIOD_HALF, "Half year"),
        (PROFIT_PERIOD_QUARTER, "Quarter"),
    ]
    fiscal_year_start_month = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
        help_text="Month your business year begins (1 = January). Profit and loss periods are counted from here.",
    )
    profit_period = models.CharField(
        max_length=10, choices=PROFIT_PERIOD_CHOICES, default=PROFIT_PERIOD_YEAR,
        help_text="How long each profit and loss reporting period runs.",
    )
    assistant_enabled = models.BooleanField(
        default=False,
        help_text="Pilot switch (set by platform staff): lets this business's admins use the read-only staff assistant.",
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
        """Return a readable neutral foreground for the configured fill."""
        try:
            channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        except (TypeError, ValueError):
            return "#FFFFFF"

        def luminance(rgb):
            linear = [
                channel / 12.92
                if channel <= 0.04045
                else ((channel + 0.055) / 1.055) ** 2.4
                for channel in rgb
            ]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        background = luminance(channels)
        # Keep medium and deep brand fills on the light-label treatment.
        return "#182433" if background >= 0.6 else "#FFFFFF"

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


class ScheduledJobLease(models.Model):
    """Platform-level lease for preventing overlapping maintenance runs."""

    STATUS_IDLE = "idle"
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_IDLE, "Idle"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
    ]

    name = models.CharField(max_length=80, unique=True)
    owner_token = models.CharField(max_length=64, blank=True, default="")
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_finished_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_IDLE)
    last_error = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "scheduled job lease"
        verbose_name_plural = "scheduled job leases"

    def __str__(self):
        return self.name


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
        date_field = getattr(self.model, "ledger_date_field", None)
        scope = get_ledger_scope()
        cutover_date = get_ledger_cutover_date()
        if not (scope in {"live", "backlog"} and cutover_date):
            return qs
        if date_field:
            if scope == "backlog":
                return qs.filter(**{f"{date_field}__lt": cutover_date})
            live = models.Q(**{f"{date_field}__gte": cutover_date})
            if self.model._meta.get_field(date_field).null:
                live |= models.Q(**{f"{date_field}__isnull": True})
            return qs.filter(live)
        if hasattr(self.model, "ledger_scope_q"):
            # Models whose segment is not a plain date (e.g. market stock lots).
            return qs.filter(self.model.ledger_scope_q(scope, cutover_date))
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


class LedgerCutover(BusinessOwnedModel):
    STATUS_OPEN = "open"
    STATUS_COMPLETE = "complete"
    STATUS_MERGED = "merged"
    STATUS_ARCHIVED = "archived"
    STATUS_REVERTED = "reverted"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"), (STATUS_COMPLETE, "Complete"),
        (STATUS_MERGED, "Merged"), (STATUS_ARCHIVED, "Archived"),
        (STATUS_REVERTED, "Reverted to combined mode"),
    ]
    cutover_date = models.DateField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_OPEN)
    backlog_complete_at = models.DateTimeField(null=True, blank=True)
    merged_at = models.DateTimeField(null=True, blank=True)
    merge_snapshot = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["business"], condition=~models.Q(status__in=["archived", "reverted"]),
            name="one_active_ledger_cutover_per_business",
        )]


class OpeningPosition(BusinessOwnedModel):
    SEGMENT_BACKLOG = "backlog"
    SEGMENT_LIVE = "live"
    KIND_CHOICES = [(key, label) for key, label in (
        ("raw", "Raw material"), ("fg", "Finished good"), ("cash", "Cash account"),
        ("receivable", "Receivable"), ("payable", "Payable"),
    )]
    SOURCE_CHOICES = [(key, label) for key, label in (
        ("derived", "Derived"), ("counted", "Counted"), ("merged", "Merged"),
    )]
    cutover = models.ForeignKey(LedgerCutover, on_delete=models.CASCADE, related_name="opening_positions")
    segment = models.CharField(max_length=10, choices=[(SEGMENT_BACKLOG, "Backlog"), (SEGMENT_LIVE, "Live")])
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    object_id = models.PositiveBigIntegerField()
    quantity = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default="counted")
    note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["cutover", "segment", "kind", "object_id"], name="unique_ledger_opening_position",
        )]

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
        indexes = [
            models.Index(fields=["business", "active", "account_type"], name="cash_biz_active_type_idx"),
        ]
    def __str__(self): return self.name
    @property
    def balance(self):
        calculated = getattr(self, "_calculated_balance", None)
        if calculated is not None:
            return calculated
        from . import ledger
        _segment, floor = ledger.cash_scope(self.business_id)
        transactions = self.transactions.filter(date__gte=floor) if floor else self.transactions.all()
        return ledger.cash_opening(self) + sum((t.signed_amount for t in transactions), Decimal("0"))


class FinancialTransaction(BusinessOwnedModel):
    """Immutable-style cash ledger entry. Positive income, negative outflow."""
    ledger_date_field = "date"
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
        indexes = [
            models.Index(fields=["business", "date", "transaction_type"], name="fin_tx_biz_date_type_idx"),
            models.Index(fields=["business", "transaction_type", "date"], name="fin_tx_biz_type_date_idx"),
        ]
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
        indexes = [
            models.Index(fields=["business", "model_name", "created_at"], name="audit_biz_model_time_idx"),
        ]


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


class BankStatement(BusinessOwnedModel):
    """A bank/card/transfer statement loaded to reconcile one CashAccount."""
    STATUS_OPEN = "open"
    STATUS_RECONCILED = "reconciled"
    STATUS_CHOICES = [(STATUS_OPEN, "In progress"), (STATUS_RECONCILED, "Reconciled")]

    account = models.ForeignKey(CashAccount, on_delete=models.PROTECT, related_name="statements")
    label = models.CharField(max_length=120, blank=True, default="")
    period_start = models.DateField()
    period_end = models.DateField()
    closing_balance = models.DecimalField(max_digits=16, decimal_places=2)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_OPEN)
    reconciled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-period_end", "-id"]
        indexes = [models.Index(fields=["business", "account", "period_end"], name="bankstmt_biz_acct_end_idx")]

    def __str__(self):
        return f"{self.account.name} statement to {self.period_end}"


class BankStatementLine(TimestampedModel):
    """One line of a statement; signed amount (+ money in, - money out)."""
    statement = models.ForeignKey(BankStatement, on_delete=models.CASCADE, related_name="lines")
    date = models.DateField()
    description = models.CharField(max_length=255, blank=True, default="")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    matched_transaction = models.ForeignKey(
        FinancialTransaction, null=True, blank=True, on_delete=models.SET_NULL, related_name="statement_lines"
    )
    matched_at = models.DateTimeField(null=True, blank=True)
    matched_automatically = models.BooleanField(default=False)

    class Meta:
        ordering = ["date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["matched_transaction"], condition=models.Q(matched_transaction__isnull=False),
                name="unique_statement_line_per_transaction",
            ),
        ]


class FinancialPackShare(BusinessOwnedModel):
    """A revocable, expiring, read-only link to the business's financial pack."""
    token_hash = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=120, blank=True, default="")
    period_start = models.DateField()
    period_end = models.DateField()
    include_backlog = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_viewed_at = models.DateTimeField(null=True, blank=True)
    view_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_active(self):
        from django.utils import timezone
        return self.revoked_at is None and self.expires_at > timezone.now()


class DailyBriefPreference(BusinessOwnedModel):
    """A member's opt-in to the morning business brief by email."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="daily_brief_preferences")
    report_sections = models.JSONField(default=dict, blank=True, help_text="Selected report sections per daily/weekly mode; absent modes include all permitted sections.")
    send_email = models.BooleanField(default=False)
    last_sent_on = models.DateField(null=True, blank=True)
    whatsapp_enabled = models.BooleanField(default=False)
    whatsapp_number = models.CharField(max_length=20, blank=True, default="", help_text="Digits only, with country code.")
    whatsapp_consent_at = models.DateTimeField(null=True, blank=True)
    last_whatsapp_on = models.DateField(null=True, blank=True)
    last_whatsapp_status = models.CharField(max_length=20, blank=True, default="")
    last_whatsapp_error = models.CharField(max_length=255, blank=True, default="")
    send_weekly_email = models.BooleanField(default=False)
    last_weekly_for = models.DateField(null=True, blank=True, help_text="Monday of the last week already emailed.")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "user"], name="unique_daily_brief_pref")]


# ---------------------------------------------------------------------------
# Books (double-entry), VAT and e-invoicing
# ---------------------------------------------------------------------------

class LedgerAccount(BusinessOwnedModel):
    """A line in the chart of accounts."""
    ASSET, LIABILITY, EQUITY, INCOME, EXPENSE = "asset", "liability", "equity", "income", "expense"
    TYPE_CHOICES = [(ASSET, "Asset"), (LIABILITY, "Liability"), (EQUITY, "Equity"), (INCOME, "Income"), (EXPENSE, "Expense")]
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=120)
    account_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    system_key = models.CharField(max_length=40, blank=True, default="", help_text="Set on accounts the posting rules rely on.")
    cash_account = models.OneToOneField(CashAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name="books_account")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["business", "code"], name="unique_ledger_account_code")]

    @property
    def normal_side(self):
        return "debit" if self.account_type in (self.ASSET, self.EXPENSE) else "credit"

    def __str__(self):
        return f"{self.code} {self.name}"


class JournalEntry(BusinessOwnedModel):
    """One balanced posting. System entries are rebuilt from records; manual ones are kept."""
    date = models.DateField()
    memo = models.CharField(max_length=255, blank=True, default="")
    source_type = models.CharField(max_length=30)
    source_id = models.CharField(max_length=40, blank=True, default="")
    is_system = models.BooleanField(default=True)
    reverses = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by")

    class Meta:
        ordering = ["date", "id"]
        verbose_name_plural = "journal entries"
        indexes = [
            models.Index(fields=["business", "date"], name="journal_biz_date_idx"),
            models.Index(fields=["business", "source_type", "source_id"], name="journal_biz_source_idx"),
        ]


class JournalLine(TimestampedModel):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="lines")
    debit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    memo = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["account", "entry"], name="journalline_acct_entry_idx")]
        constraints = [
            models.CheckConstraint(
                check=models.Q(debit__gte=0, credit__gte=0) & ~models.Q(debit=0, credit=0) & (models.Q(debit=0) | models.Q(credit=0)),
                name="journal_line_one_side_positive",
            ),
        ]


class BooksState(BusinessOwnedModel):
    rebuilt_at = models.DateTimeField(null=True, blank=True)
    entry_count = models.PositiveIntegerField(default=0)
    suspense_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business"], name="unique_books_state_per_business")]


class TaxSettings(BusinessOwnedModel):
    """VAT and e-invoicing configuration. Rates and thresholds are settings, not law."""
    PROVIDER_NONE, PROVIDER_WEBHOOK = "none", "webhook"
    vat_registered = models.BooleanField(default=False)
    vat_registration_date = models.DateField(null=True, blank=True)
    tin = models.CharField("Tax identification number (TIN)", max_length=30, blank=True, default="")
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("7.50"))
    registration_threshold = models.DecimalField(
        max_digits=16, decimal_places=2, default=Decimal("25000000"),
        help_text="Annual turnover above which you may need to register. Confirm the current figure with the tax authority.",
    )
    filing_due_day = models.PositiveSmallIntegerField(default=21, help_text="Day of the following month the VAT return is due.")
    einvoice_enabled = models.BooleanField(default=False)
    einvoice_provider = models.CharField(max_length=10, choices=[(PROVIDER_NONE, "None (manual)"), (PROVIDER_WEBHOOK, "HTTPS provider")], default=PROVIDER_NONE)
    einvoice_endpoint = models.URLField(blank=True, default="")
    einvoice_api_key_encrypted = models.TextField(blank=True, default="")
    invoice_prefix = models.CharField(max_length=12, default="INV")
    next_invoice_number = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business"], name="unique_tax_settings_per_business")]


class InputVatEntry(BusinessOwnedModel):
    """VAT you paid on purchases or expenses, taken from supplier invoices."""
    KIND_PURCHASE, KIND_EXPENSE = "purchase", "expense"
    date = models.DateField()
    supplier = models.CharField(max_length=160, blank=True, default="")
    reference = models.CharField("Supplier invoice no.", max_length=80, blank=True, default="")
    kind = models.CharField(max_length=10, choices=[(KIND_PURCHASE, "Stock purchase"), (KIND_EXPENSE, "Expense")], default=KIND_PURCHASE)
    net_amount = models.DecimalField(max_digits=16, decimal_places=2)
    vat_amount = models.DecimalField(max_digits=16, decimal_places=2)
    claimable = models.BooleanField(default=True, help_text="Untick for VAT you cannot recover, such as on VAT-exempt supplies.")
    note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-date", "-id"]


class EInvoice(BusinessOwnedModel):
    """Structured invoice data and its clearance status for one sale."""
    DRAFT, READY, SUBMITTED, CLEARED, REJECTED, FAILED = "draft", "ready", "submitted", "cleared", "rejected", "failed"
    STATUS_CHOICES = [(DRAFT, "Draft"), (READY, "Ready to submit"), (SUBMITTED, "Submitted"), (CLEARED, "Cleared"),
                      (REJECTED, "Rejected"), (FAILED, "Failed, retry")]
    sale = models.OneToOneField("sales.Sale", on_delete=models.PROTECT, related_name="einvoice")
    invoice_number = models.CharField(max_length=40)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=READY)
    buyer_tin = models.CharField(max_length=30, blank=True, default="")
    irn = models.CharField("Invoice reference number (IRN)", max_length=120, blank=True, default="")
    csid = models.CharField(max_length=255, blank=True, default="")
    qr_text = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    response = models.JSONField(default=dict, blank=True)
    error = models.CharField(max_length=255, blank=True, default="")
    submitted_at = models.DateTimeField(null=True, blank=True)
    cleared_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        constraints = [models.UniqueConstraint(fields=["business", "invoice_number"], name="unique_einvoice_number_per_business")]
