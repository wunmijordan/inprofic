from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.core.validators import FileExtensionValidator
from core.models import Business


class CustomUserManager(BaseUserManager):
    def create_user(self, username, password=None, **extra_fields):
        if not username:
            raise ValueError("Username is required.")
        user = self.model(username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(username, password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    # System roles are deliberately fixed identifiers. Their permissions are
    # stored/configurable in the Role tables; businesses may also create roles.
    ROLE_STOCK_KEEPER = "stock_keeper"
    ROLE_MANAGER = "manager"
    ROLE_ACCOUNTANT = "accountant"
    ROLE_MD_DIRECTOR = "md_director"
    ROLE_BUSINESS_ADMIN = "business_admin"
    ROLE_POS_OPERATOR = "pos_operator"
    # Backward-compatible internal key for the fixed, superuser-only Demo role.
    ROLE_LIVE_TESTER = "live_tester"
    ROLE_SUPERUSER = "superuser"
    SYSTEM_ROLE_DEFINITIONS = (
        (ROLE_STOCK_KEEPER, "Stock Keeper"),
        (ROLE_MANAGER, "Manager"),
        (ROLE_ACCOUNTANT, "Accountant"),
        (ROLE_MD_DIRECTOR, "MD / Director"),
        (ROLE_BUSINESS_ADMIN, "Business Admin"),
        (ROLE_POS_OPERATOR, "In-Premise POS"),
        (ROLE_LIVE_TESTER, "Demo"),
        (ROLE_SUPERUSER, "Superuser"),
    )

    fullname = models.CharField(max_length=160)
    username = models.CharField(max_length=80, unique=True)
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=30, blank=True, default="")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["fullname"]
    objects = CustomUserManager()

    class Meta:
        ordering = ["fullname", "username"]

    def __str__(self):
        return self.fullname or self.username

    def get_full_name(self):
        return self.fullname

    def get_short_name(self):
        return self.fullname.split()[0] if self.fullname else self.username


class Role(models.Model):
    """Business-scoped role definition.

    System roles are seeded from CustomUser.SYSTEM_ROLE_DEFINITIONS and cannot
    be deleted. Their permissions remain editable for each business. Custom
    roles are created by a superuser or Business Admin for that business.
    """
    key = models.SlugField(max_length=60)
    name = models.CharField(max_length=80)
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="roles")
    is_system = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    visible_to_admin = models.BooleanField(
        default=True,
        help_text="Uncheck to keep this role visible only to the global superuser — for demo/review roles that aren't part of this business's normal operations. Business Admins won't see it in Roles & Access, can't open it directly, and can't assign it to a user.",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["business", "key"], name="unique_role_key_per_business"),
            models.UniqueConstraint(fields=["business", "name"], name="unique_role_name_per_business"),
        ]

    def __str__(self):
        return self.name


class RoleModulePermission(models.Model):
    MODULE_CHOICES = [
        ("dashboard", "Dashboard"),
        ("inventory", "Inventory"),
        ("procurement", "Procurement"),
        ("production", "Production Orders"),
        ("sales", "Sales"),
        ("expenses", "Expenses"),
        ("finance", "Finance"),
        ("reports", "Reports"),
        ("users", "User Management"),
        ("commerce", "Commerce"),
        ("pos", "In-Premise POS"),
    ]
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="module_permissions")
    module = models.CharField(max_length=30, choices=MODULE_CHOICES)
    can_view = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)

    class Meta:
        ordering = ["module"]
        constraints = [
            models.UniqueConstraint(fields=["role", "module"], name="unique_role_module_permission"),
        ]


class BusinessModuleAccess(models.Model):
    """Business-level module entitlement boundary.

    Subscription plans materialize into these rows without rewriting role or
    per-user permissions. Missing rows intentionally mean enabled for pre-SaaS
    tenants; explicit enabled=False is the commercial hard ceiling.
    """
    SOURCE_DEFAULT = "default"
    SOURCE_LEGACY = "legacy"
    SOURCE_PLAN = "plan"
    SOURCE_FOUNDER = "founder"
    SOURCE_CHOICES = [
        (SOURCE_DEFAULT, "Service default"),
        (SOURCE_LEGACY, "Legacy full access"),
        (SOURCE_PLAN, "Subscription plan"),
        (SOURCE_FOUNDER, "Founder lifetime grant"),
    ]

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="module_access")
    module = models.CharField(max_length=30, choices=RoleModulePermission.MODULE_CHOICES)
    enabled = models.BooleanField(default=True)
    source = models.CharField(max_length=12, choices=SOURCE_CHOICES, default=SOURCE_DEFAULT)

    class Meta:
        ordering = ["module"]
        constraints = [
            models.UniqueConstraint(fields=["business", "module"], name="unique_business_module_access"),
        ]

    def __str__(self):
        return f"{self.business} — {self.get_module_display()}"


class UserBusiness(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="business_memberships")
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="user_memberships")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="memberships")
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "business"], name="unique_user_business_membership"),
        ]

    def __str__(self):
        return f"{self.user} — {self.business} — {self.role}"


class UserModulePermission(models.Model):
    """Optional per-user override of role defaults."""
    membership = models.ForeignKey(UserBusiness, on_delete=models.CASCADE, related_name="module_permissions")
    module = models.CharField(max_length=30, choices=RoleModulePermission.MODULE_CHOICES)
    can_view = models.BooleanField(null=True, blank=True, default=None)
    can_edit = models.BooleanField(null=True, blank=True, default=None)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["membership", "module"], name="unique_user_module_permission"),
        ]
        ordering = ["module"]

    def __str__(self):
        return f"{self.membership.user} — {self.get_module_display()}"


class SubscriptionPlan(models.Model):
    """Commercial plan definition; module entitlements are copied into BusinessModuleAccess."""
    CODE_STARTER = "starter"
    CODE_PRODUCTION = "production"
    CODE_BUSINESS_PRO = "business_pro"
    CODE_CHOICES = [
        (CODE_STARTER, "STARTER"),
        (CODE_PRODUCTION, "PRODUCTION"),
        (CODE_BUSINESS_PRO, "BUSINESS PRO"),
    ]

    code = models.CharField(max_length=30, choices=CODE_CHOICES, unique=True)
    name = models.CharField(max_length=80)
    monthly_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    yearly_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        help_text="Discount applied to 12 months of the plan when paid yearly.",
    )
    additional_service_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=30,
        help_text="Discount from the plan's normal monthly price for each additional service/business profile.",
    )
    trial_days = models.PositiveSmallIntegerField(default=30)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["monthly_price", "id"]

    def __str__(self):
        return self.name

    @property
    def yearly_price(self):
        from decimal import Decimal
        discount = min(max(self.yearly_discount_percent, Decimal("0")), Decimal("100"))
        return (self.monthly_price * Decimal("12") * (Decimal("1") - discount / Decimal("100"))).quantize(Decimal("0.01"))

    @property
    def additional_service_monthly_price(self):
        from decimal import Decimal
        discount = min(max(self.additional_service_discount_percent, Decimal("0")), Decimal("100"))
        return (self.monthly_price * (Decimal("1") - discount / Decimal("100"))).quantize(Decimal("0.01"))


class SubscriptionPromotion(models.Model):
    DISCOUNT_PERCENT = "percent"
    DISCOUNT_AMOUNT = "amount"
    DISCOUNT_CHOICES = [
        (DISCOUNT_PERCENT, "Percentage off"),
        (DISCOUNT_AMOUNT, "Fixed amount off"),
    ]

    CYCLE_BOTH = "both"
    CYCLE_MONTHLY = "monthly"
    CYCLE_YEARLY = "yearly"
    BILLING_CYCLE_CHOICES = [
        (CYCLE_BOTH, "Monthly and yearly"),
        (CYCLE_MONTHLY, "Monthly only"),
        (CYCLE_YEARLY, "Yearly only"),
    ]

    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.CASCADE, related_name="promotions",
        null=True, blank=True,
        help_text="Leave blank when this promotion applies to every active plan.",
    )
    applies_to_all_plans = models.BooleanField(
        default=False,
        help_text="Apply this single promotion to every active subscription plan using each plan's own base price.",
    )
    reason = models.CharField(max_length=140)
    discount_type = models.CharField(max_length=12, choices=DISCOUNT_CHOICES)
    discount_value = models.DecimalField(max_digits=14, decimal_places=2)
    billing_cycle = models.CharField(
        max_length=12, choices=BILLING_CYCLE_CHOICES, default=CYCLE_BOTH,
        help_text="Limit this promotion to monthly payments, yearly payments, or both.",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="subscription_promotions_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-starts_at", "-id"]
        indexes = [models.Index(fields=["plan", "active", "starts_at", "ends_at"], name="plan_promo_active_idx")]

    def __str__(self):
        target = "All plans" if self.applies_to_all_plans else (self.plan.name if self.plan_id else "No plan")
        return f"{target} — {self.reason}"

    @property
    def target_label(self):
        return "All plans" if self.applies_to_all_plans else (self.plan.name if self.plan_id else "No plan")

    def applies_to_plan(self, plan):
        return bool(self.applies_to_all_plans or (self.plan_id and plan and self.plan_id == plan.pk))

    def applies_to_billing_cycle(self, billing_cycle):
        billing_cycle = (billing_cycle or self.CYCLE_MONTHLY).strip().lower()
        return self.billing_cycle == self.CYCLE_BOTH or self.billing_cycle == billing_cycle

    @property
    def billing_cycle_label(self):
        return dict(self.BILLING_CYCLE_CHOICES).get(self.billing_cycle, "Monthly and yearly")

    def is_active_at(self, moment=None):
        from django.utils import timezone
        moment = moment or timezone.now()
        return bool(self.active and self.starts_at <= moment < self.ends_at)

    def discounted_monthly_price_for(self, plan=None):
        from decimal import Decimal
        plan = plan or self.plan
        if not plan:
            return Decimal("0.00")
        base = Decimal(plan.monthly_price or 0)
        value = max(Decimal("0"), Decimal(self.discount_value or 0))
        if self.discount_type == self.DISCOUNT_PERCENT:
            value = min(value, Decimal("100"))
            result = base * (Decimal("1") - value / Decimal("100"))
        else:
            result = base - value
        return max(Decimal("0"), result).quantize(Decimal("0.01"))

    def discounted_additional_service_monthly_price_for(self, plan=None):
        from decimal import Decimal
        plan = plan or self.plan
        if not plan:
            return Decimal("0.00")
        discount = min(max(plan.additional_service_discount_percent, Decimal("0")), Decimal("100"))
        return (self.discounted_monthly_price_for(plan) * (Decimal("1") - discount / Decimal("100"))).quantize(Decimal("0.01"))

    def discounted_yearly_price_for(self, plan=None):
        from decimal import Decimal
        plan = plan or self.plan
        if not plan:
            return Decimal("0.00")
        discount = min(max(plan.yearly_discount_percent, Decimal("0")), Decimal("100"))
        return (self.discounted_monthly_price_for(plan) * Decimal("12") * (Decimal("1") - discount / Decimal("100"))).quantize(Decimal("0.01"))

    @property
    def discounted_monthly_price(self):
        return self.discounted_monthly_price_for()

    @property
    def discounted_additional_service_monthly_price(self):
        return self.discounted_additional_service_monthly_price_for()

    @property
    def discounted_yearly_price(self):
        return self.discounted_yearly_price_for()


class MarketingPromoCampaign(models.Model):
    """Founder-authored creative for an existing subscription promotion."""

    ANIMATION_KINETIC = "kinetic"
    ANIMATION_SPOTLIGHT = "spotlight"
    ANIMATION_PARALLAX = "parallax"
    ANIMATION_MARQUEE = "marquee"
    ANIMATION_CHOICES = [
        (ANIMATION_KINETIC, "Kinetic reveal"),
        (ANIMATION_SPOTLIGHT, "Spotlight sweep"),
        (ANIMATION_PARALLAX, "Parallax float"),
        (ANIMATION_MARQUEE, "Marquee energy"),
    ]
    THEME_MIDNIGHT = "midnight"
    THEME_EMBER = "ember"
    THEME_PAPER = "paper"
    THEME_GOLD = "gold"
    THEME_CHOICES = [
        (THEME_MIDNIGHT, "Midnight"),
        (THEME_EMBER, "Ember"),
        (THEME_PAPER, "Paper"),
        (THEME_GOLD, "Gold"),
    ]

    name = models.CharField(max_length=100, help_text="Founder-only label for this campaign creative.")
    promotion = models.OneToOneField(
        SubscriptionPromotion, on_delete=models.CASCADE, related_name="marketing_campaign"
    )
    content_html = models.TextField(help_text="Sanitised rich text rendered in the public promo stage.")
    cta_label = models.CharField(max_length=80, default="See promotional plans")
    animation_style = models.CharField(max_length=16, choices=ANIMATION_CHOICES, default=ANIMATION_KINETIC)
    theme = models.CharField(max_length=16, choices=THEME_CHOICES, default=THEME_MIDNIGHT)
    priority = models.PositiveSmallIntegerField(default=50, help_text="Higher numbers appear first if several promotions are live.")
    image = models.ImageField(upload_to="marketing/promotions/%Y/%m/", blank=True)
    video = models.FileField(
        upload_to="marketing/promotions/%Y/%m/",
        blank=True,
        validators=[FileExtensionValidator(["mp4", "webm", "mov"])],
    )
    video_poster = models.ImageField(upload_to="marketing/promotions/%Y/%m/", blank=True)
    media_alt = models.CharField(max_length=180, blank=True, default="")
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="marketing_promo_campaigns_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "id"]
        indexes = [models.Index(fields=["active", "priority"], name="marketing_campaign_idx")]

    def __str__(self):
        return f"{self.name} — {self.promotion}"

    def is_active_at(self, moment=None):
        return bool(self.active and self.promotion.is_active_at(moment))

    @property
    def media_kind(self):
        if self.video:
            return "video"
        if self.image:
            return "image"
        return "none"

    def save(self, *args, **kwargs):
        from .marketing_campaigns import sanitize_campaign_html
        self.content_html = sanitize_campaign_html(self.content_html)
        super().save(*args, **kwargs)


class SubscriptionPlanModule(models.Model):
    LEVEL_NONE = "none"
    LEVEL_BASIC = "basic"
    LEVEL_FULL = "full"
    LEVEL_CHOICES = [(LEVEL_NONE, "Not included"), (LEVEL_BASIC, "Basic"), (LEVEL_FULL, "Full")]

    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.CASCADE, related_name="module_entitlements")
    module = models.CharField(max_length=30, choices=RoleModulePermission.MODULE_CHOICES)
    enabled = models.BooleanField(default=False)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default=LEVEL_FULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["plan", "module"], name="unique_plan_module")]
        ordering = ["module"]


class BusinessSubscription(models.Model):
    STATUS_TRIAL = "trial"
    STATUS_ACTIVE = "active"
    STATUS_EXPIRED = "expired"
    STATUS_FOUNDER = "founder"
    STATUS_CHOICES = [
        (STATUS_TRIAL, "Free trial"),
        (STATUS_ACTIVE, "Paid"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_FOUNDER, "Founder lifetime"),
    ]

    primary_business = models.OneToOneField(Business, on_delete=models.CASCADE, related_name="subscription")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_TRIAL)
    started_at = models.DateTimeField(auto_now_add=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    paid_until = models.DateTimeField(null=True, blank=True)
    founder_lifetime = models.BooleanField(default=False)
    founder_granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="founder_grants_made"
    )
    founder_granted_at = models.DateTimeField(null=True, blank=True)
    founder_note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["primary_business__name"]

    def __str__(self):
        return f"{self.primary_business} — {self.plan.name}"

    @property
    def expires_at(self):
        if self.founder_lifetime:
            return None
        return self.trial_ends_at if self.status == self.STATUS_TRIAL else self.paid_until

    @property
    def days_to_expiry(self):
        from django.utils import timezone
        expiry = self.expires_at
        if not expiry:
            return None
        delta = expiry - timezone.now()
        return max(0, delta.days + (1 if delta.seconds else 0))

    @property
    def is_expiring_soon(self):
        days = self.days_to_expiry
        return days is not None and days <= 7

    @property
    def is_effectively_active(self):
        if self.founder_lifetime:
            return True
        from django.utils import timezone
        expiry = self.expires_at
        return bool(expiry and expiry >= timezone.now() and self.status in {self.STATUS_TRIAL, self.STATUS_ACTIVE})

    @property
    def effective_status_label(self):
        if self.founder_lifetime:
            return "Founder lifetime"
        if not self.is_effectively_active:
            return "Expired"
        return self.get_status_display()

    @property
    def trial_status_label(self):
        if self.status == self.STATUS_TRIAL and self.trial_ends_at:
            return f"Trial · ends {self.trial_ends_at:%d %b %Y}"
        return self.effective_status_label

    @property
    def monthly_total(self):
        from decimal import Decimal
        total = Decimal(self.plan.monthly_price or 0)
        extras = max(0, self.services.count() - 1)
        return (total + Decimal(extras) * self.plan.additional_service_monthly_price).quantize(Decimal("0.01"))


class SubscriptionService(models.Model):
    """One service line/business profile billed under a primary subscription."""
    subscription = models.ForeignKey(BusinessSubscription, on_delete=models.CASCADE, related_name="services")
    business = models.OneToOneField(Business, on_delete=models.CASCADE, related_name="subscription_service")
    is_primary = models.BooleanField(default=False)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["subscription"], condition=models.Q(is_primary=True), name="one_primary_service_per_subscription"
            )
        ]
        ordering = ["-is_primary", "business__name"]

    @property
    def service_type(self):
        return self.business.vertical


class BusinessFeatureAccess(models.Model):
    """Feature-depth entitlement below a module, e.g. Reports basic vs full."""
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="feature_access")
    feature = models.CharField(max_length=60)
    enabled = models.BooleanField(default=False)
    source = models.CharField(max_length=12, choices=BusinessModuleAccess.SOURCE_CHOICES, default=BusinessModuleAccess.SOURCE_PLAN)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "feature"], name="unique_business_feature_access")]
        ordering = ["feature"]


class SubscriptionPayment(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"), (STATUS_PAID, "Paid"),
        (STATUS_FAILED, "Failed"), (STATUS_CANCELLED, "Cancelled"),
    ]
    PROVIDER_PAYSTACK = "paystack"
    PROVIDER_MONNIFY = "monnify"
    PROVIDER_MANUAL = "manual"
    PROVIDER_CHOICES = [
        (PROVIDER_PAYSTACK, "Paystack"),
        (PROVIDER_MONNIFY, "Monnify"),
        (PROVIDER_MANUAL, "Manual / founder confirmation"),
    ]
    CYCLE_MONTHLY = "monthly"
    CYCLE_YEARLY = "yearly"
    CYCLE_CHOICES = [(CYCLE_MONTHLY, "Monthly"), (CYCLE_YEARLY, "Yearly")]

    subscription = models.ForeignKey(BusinessSubscription, on_delete=models.CASCADE, related_name="subscription_payments")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    base_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    promotion = models.ForeignKey(
        SubscriptionPromotion, null=True, blank=True, on_delete=models.SET_NULL, related_name="payments"
    )
    promotion_reason = models.CharField(max_length=140, blank=True, default="")
    promotion_discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    service_count = models.PositiveSmallIntegerField(default=1)
    months = models.PositiveSmallIntegerField(default=1)
    billing_cycle = models.CharField(max_length=12, choices=CYCLE_CHOICES, default=CYCLE_MONTHLY)
    provider = models.CharField(max_length=12, choices=PROVIDER_CHOICES, default=PROVIDER_MANUAL)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING)
    reference = models.CharField(max_length=80, unique=True)
    provider_reference = models.CharField(max_length=160, blank=True, default="")
    checkout_url = models.URLField(blank=True, default="")
    provider_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]


class SubscriptionPaymentSettings(models.Model):
    """Founder-controlled availability for new INPROFIC plan checkouts.

    Provider credentials remain environment-owned. Disabling a provider stops
    new payment attempts but deliberately does not invalidate existing ones.
    """

    paystack_enabled = models.BooleanField(default=True)
    monnify_enabled = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="subscription_payment_settings_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "subscription payment setting"
        verbose_name_plural = "subscription payment settings"

    @classmethod
    def load(cls):
        settings_row, _ = cls.objects.get_or_create(pk=1)
        return settings_row

    def provider_enabled(self, provider):
        return {
            SubscriptionPayment.PROVIDER_PAYSTACK: self.paystack_enabled,
            SubscriptionPayment.PROVIDER_MONNIFY: self.monnify_enabled,
        }.get(provider, False)
