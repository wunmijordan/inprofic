from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.core.validators import FileExtensionValidator
import uuid
from core.models import Business, BusinessOwnedModel


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
    ROLE_AUDITOR = "auditor"
    ROLE_DELIVERY_COORDINATOR = "delivery_coordinator"
    ROLE_DELIVERY_RIDER = "delivery_rider"
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
        (ROLE_AUDITOR, "External Auditor"),
        (ROLE_DELIVERY_COORDINATOR, "Delivery Coordinator"),
        (ROLE_DELIVERY_RIDER, "Delivery Rider"),
        (ROLE_LIVE_TESTER, "Demo"),
        (ROLE_SUPERUSER, "Superuser"),
    )

    fullname = models.CharField("Full Name", max_length=160)
    username = models.CharField(max_length=80, unique=True)
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=30, blank=True, default="")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    platform_mail_access = models.BooleanField(default=False, help_text="Project-level access to the INPROFIC mailing workspace only.")
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
        ("delivery", "Delivery"),
        ("delivery_rider", "Delivery Rider"),
        ("audit", "Audit Workspace"),
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
    SOURCE_EXISTING = "existing"
    SOURCE_PLAN = "plan"
    SOURCE_FOUNDER = "founder"
    SOURCE_CHOICES = [
        (SOURCE_DEFAULT, "Service default"),
        (SOURCE_EXISTING, "Existing full access"),
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
    onboarding_tour_version = models.PositiveSmallIntegerField(
        default=0,
        help_text="Latest INPROFIC onboarding-tour version this membership completed or dismissed.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "business"], name="unique_user_business_membership"),
        ]
        indexes = [
            models.Index(fields=["user", "active", "business"], name="userbiz_user_active_idx"),
            models.Index(fields=["business", "active", "user"], name="userbiz_biz_active_idx"),
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
    user_limit = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Maximum unique active users across this subscription. Leave blank for unlimited.",
    )
    additional_service_limit = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Maximum additional business/service profiles beyond the primary business. Leave blank for unlimited.",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["monthly_price", "id"]

    def __str__(self):
        return self.name

    @property
    def is_free_forever(self):
        """Starter can be founder-switched between free-forever and paid.

        Keeping the policy derived from the persisted price avoids a second
        state flag drifting out of sync with billing. Other built-in plans are
        never treated as free merely because their price is temporarily zero.
        """
        from decimal import Decimal
        return self.code == self.CODE_STARTER and Decimal(self.monthly_price or 0) <= 0

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
    content_html = models.TextField(help_text="Promotion text shown to customers on the public offer display.")
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


class MarketingTrustSettings(models.Model):
    """Founder-controlled visibility for the public business-trust strip."""

    enabled = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="marketing_trust_settings_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "marketing trust setting"
        verbose_name_plural = "marketing trust settings"

    @classmethod
    def load(cls):
        row, _ = cls.objects.get_or_create(pk=1)
        return row


class MarketingTrustLogo(models.Model):
    """Founder-approved logo shown alongside paid tenant storefront logos."""

    name = models.CharField(max_length=120)
    logo = models.ImageField(
        upload_to="marketing/trusted-businesses/%Y/%m/",
        validators=[FileExtensionValidator(["png", "jpg", "jpeg", "webp"])],
    )
    active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=50)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="marketing_trust_logos_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name", "id"]

    def __str__(self):
        return self.name


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
        indexes = [
            models.Index(fields=["status", "paid_until"], name="sub_status_paid_idx"),
        ]

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
        if self.plan.is_free_forever and self.status == self.STATUS_ACTIVE:
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
        if self.plan.is_free_forever and self.status == self.STATUS_ACTIVE:
            return "Free"
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


class FounderTrialGrant(models.Model):
    """Auditable Founder-granted trial window for a business subscription."""

    subscription = models.ForeignKey(
        BusinessSubscription, on_delete=models.CASCADE, related_name="founder_trial_grants"
    )
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="founder_trial_grants")
    days = models.PositiveSmallIntegerField()
    previous_ends_at = models.DateTimeField(null=True, blank=True)
    granted_ends_at = models.DateTimeField()
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="founder_trial_grants_made",
    )
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.subscription.primary_business} · +{self.days} trial days"


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


class PaidPlanTrialClaim(models.Model):
    """One-way credential marker preventing repeat paid-plan trial use."""

    KIND_USERNAME = "username"
    KIND_EMAIL = "email"
    KIND_PHONE = "phone"
    KIND_CHOICES = [
        (KIND_USERNAME, "Username"),
        (KIND_EMAIL, "Email"),
        (KIND_PHONE, "Phone"),
    ]

    credential_kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    credential_fingerprint = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="paid_plan_trial_claims",
    )
    subscription = models.ForeignKey(
        BusinessSubscription, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="trial_claims",
    )
    plan = models.ForeignKey(
        SubscriptionPlan, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="trial_claims",
    )
    claimed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-claimed_at", "-id"]


class BusinessFeatureAccess(models.Model):
    """Feature-depth entitlement below a module, e.g. Reports basic vs full."""
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="feature_access")
    feature = models.CharField(max_length=60)
    enabled = models.BooleanField(default=False)
    source = models.CharField(max_length=12, choices=BusinessModuleAccess.SOURCE_CHOICES, default=BusinessModuleAccess.SOURCE_PLAN)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business", "feature"], name="unique_business_feature_access")]
        ordering = ["feature"]


class PayrollAddonTier(models.Model):
    """Founder-priced primary payroll package attached to one commercial plan.

    A tier either covers a fixed number of active staff or is ``unlimited``.
    Businesses on a limited tier can top up capacity with ``PayrollStaffBatch``
    purchases (not plan-specific) instead of upgrading the tier.
    """
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.CASCADE, related_name="payroll_addon_tiers")
    staff_limit = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Maximum active payroll staff covered by this tier. Leave empty when unlimited.",
    )
    unlimited = models.BooleanField(default=False, help_text="Cover unlimited active payroll staff on this plan.")
    monthly_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["plan__monthly_price", models.F("staff_limit").asc(nulls_last=True), "id"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "staff_limit"], name="unique_payroll_tier_plan_staff"),
            models.UniqueConstraint(
                fields=["plan"], condition=models.Q(unlimited=True), name="unique_unlimited_payroll_tier_per_plan",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(unlimited=True, staff_limit__isnull=True)
                    | models.Q(unlimited=False, staff_limit__isnull=False)
                ),
                name="payroll_tier_limit_matches_unlimited",
            ),
        ]

    @property
    def capacity_label(self):
        return "Unlimited staff" if self.unlimited else f"Up to {self.staff_limit} staff"

    def __str__(self):
        return f"{self.plan.name} · {self.capacity_label.lower()}"


class PayrollStaffBatch(models.Model):
    """Founder-priced block of extra payroll staff a business can add on top of
    its primary package without changing plan. Price is per month.

    Batches are a platform-wide catalogue: they are deliberately not attached to
    a commercial plan, so any business holding a limited primary payroll package
    can pick any active batch. (Primary packages, ``PayrollAddonTier``, remain
    priced per plan.)"""
    staff_count = models.PositiveIntegerField(help_text="Extra active payroll staff this batch adds.")
    monthly_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["staff_count", "id"]
        verbose_name_plural = "payroll staff batches"
        constraints = [
            # One offered batch per size; a retired batch may share its size with a new one.
            models.UniqueConstraint(
                fields=["staff_count"], condition=models.Q(active=True), name="unique_active_payroll_batch_staff",
                violation_error_message="An active extra-staff batch of this size already exists. Retire it first to change its price.",
            ),
            models.CheckConstraint(condition=models.Q(staff_count__gte=1), name="payroll_batch_staff_positive"),
        ]

    @property
    def label(self):
        return f"+{self.staff_count} staff"

    def __str__(self):
        return self.label


class BusinessPayrollAddon(models.Model):
    """Paid payroll add-on state for one tenant business.

    Trial and Founder-lifetime access are derived from BusinessSubscription and
    do not need a paid row here. This row only represents post-trial paid access.
    Extra-staff batches (``BusinessPayrollBatch``) are co-terminous with
    ``paid_until`` and renew together with the primary package.
    """
    business = models.OneToOneField(Business, on_delete=models.CASCADE, related_name="payroll_addon")
    tier = models.ForeignKey(PayrollAddonTier, on_delete=models.PROTECT, related_name="business_addons")
    active = models.BooleanField(default=True)
    paid_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["business__name"]

    @property
    def extra_staff(self):
        return sum(h.batch.staff_count * h.quantity for h in self.batches.all())

    @property
    def staff_limit(self):
        """Total active staff covered, or None when the primary tier is unlimited."""
        if self.tier.unlimited:
            return None
        return (self.tier.staff_limit or 0) + self.extra_staff

    def __str__(self):
        return f"{self.business} · {self.tier}"


class BusinessPayrollBatch(models.Model):
    """Extra-staff batches a business holds on top of its primary package."""
    addon = models.ForeignKey(BusinessPayrollAddon, on_delete=models.CASCADE, related_name="batches")
    batch = models.ForeignKey(PayrollStaffBatch, on_delete=models.PROTECT, related_name="holdings")
    quantity = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["batch__staff_count", "id"]
        constraints = [
            models.UniqueConstraint(fields=["addon", "batch"], name="unique_business_payroll_batch"),
            models.CheckConstraint(condition=models.Q(quantity__gte=1), name="business_payroll_batch_qty_positive"),
        ]

    @property
    def staff(self):
        return self.batch.staff_count * self.quantity

    def __str__(self):
        return f"{self.addon.business} · {self.batch.label} × {self.quantity}"


class PayrollStaffProfile(BusinessOwnedModel):
    FREQUENCY_MONTHLY = "monthly"
    FREQUENCY_WEEKLY = "weekly"
    FREQUENCY_CHOICES = [(FREQUENCY_MONTHLY, "Monthly"), (FREQUENCY_WEEKLY, "Weekly")]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="payroll_staff_profiles",
        help_text="Optional existing INPROFIC user linked to this payroll profile.",
    )
    full_name = models.CharField(max_length=160)
    email = models.EmailField(blank=True, default="")
    whatsapp_number = models.CharField(max_length=30, blank=True, default="")
    job_title = models.CharField(max_length=120, blank=True, default="")
    pay_frequency = models.CharField(max_length=12, choices=FREQUENCY_CHOICES, default=FREQUENCY_MONTHLY)
    base_pay = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    recurring_allowances = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    recurring_deductions = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["full_name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "user"], condition=models.Q(user__isnull=False),
                name="unique_payroll_user_per_business",
            )
        ]

    def __str__(self):
        return self.full_name

    @property
    def recurring_net_pay(self):
        from decimal import Decimal
        return max(
            Decimal("0"),
            Decimal(self.base_pay or 0) + Decimal(self.recurring_allowances or 0) - Decimal(self.recurring_deductions or 0),
        )


class PayrollRecurringAdjustment(models.Model):
    """Named recurring allowance or deduction attached to one payroll profile.

    Tenant scope is inherited through ``staff``.  The aggregate legacy fields on
    PayrollStaffProfile are retained as compatibility caches and synchronized
    whenever these rows are changed through the payroll workspace.
    """

    KIND_ALLOWANCE = "allowance"
    KIND_DEDUCTION = "deduction"
    KIND_CHOICES = [
        (KIND_ALLOWANCE, "Allowance / earning"),
        (KIND_DEDUCTION, "Deduction"),
    ]

    staff = models.ForeignKey(
        PayrollStaffProfile, on_delete=models.CASCADE, related_name="recurring_adjustments"
    )
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    name = models.CharField(max_length=120)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "name", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="payroll_recurring_amount_nonnegative",
            ),
        ]

    def __str__(self):
        return f"{self.staff.full_name} · {self.name}"


class PayrollCalculationRule(BusinessOwnedModel):
    """Tenant-defined payroll calculation applied when a payslip is issued.

    Rules are intentionally jurisdiction-neutral.  Business admins can model
    tax, pension and other payroll additions/deductions without INPROFIC
    hard-coding one country's statutory formula.
    """

    CATEGORY_TAX = "tax"
    CATEGORY_PENSION = "pension"
    CATEGORY_INSURANCE = "insurance"
    CATEGORY_LEVY = "levy"
    CATEGORY_ALLOWANCE = "allowance"
    CATEGORY_DEDUCTION = "deduction"
    CATEGORY_EMPLOYER = "employer_contribution"
    CATEGORY_OTHER = "other"
    CATEGORY_CHOICES = [
        (CATEGORY_TAX, "Tax"),
        (CATEGORY_PENSION, "Pension"),
        (CATEGORY_INSURANCE, "Insurance"),
        (CATEGORY_LEVY, "Levy"),
        (CATEGORY_ALLOWANCE, "Allowance / earning"),
        (CATEGORY_DEDUCTION, "Other deduction"),
        (CATEGORY_EMPLOYER, "Employer contribution"),
        (CATEGORY_OTHER, "Other"),
    ]

    EFFECT_EARNING = "earning"
    EFFECT_EMPLOYEE_DEDUCTION = "employee_deduction"
    EFFECT_EMPLOYER_CONTRIBUTION = "employer_contribution"
    EFFECT_CHOICES = [
        (EFFECT_EARNING, "Add to staff gross pay"),
        (EFFECT_EMPLOYEE_DEDUCTION, "Deduct from staff pay"),
        (EFFECT_EMPLOYER_CONTRIBUTION, "Employer contribution only"),
    ]

    METHOD_FIXED = "fixed"
    METHOD_PERCENTAGE = "percentage"
    METHOD_CHOICES = [
        (METHOD_FIXED, "Fixed amount"),
        (METHOD_PERCENTAGE, "Percentage"),
    ]

    BASIS_BASE_PAY = "base_pay"
    BASIS_GROSS_PAY = "gross_pay"
    BASIS_CHOICES = [
        (BASIS_BASE_PAY, "Base pay"),
        (BASIS_GROSS_PAY, "Gross pay"),
    ]

    name = models.CharField(max_length=120)
    category = models.CharField(max_length=24, choices=CATEGORY_CHOICES, default=CATEGORY_OTHER)
    effect = models.CharField(max_length=24, choices=EFFECT_CHOICES, default=EFFECT_EMPLOYEE_DEDUCTION)
    method = models.CharField(max_length=12, choices=METHOD_CHOICES, default=METHOD_PERCENTAGE)
    basis = models.CharField(max_length=12, choices=BASIS_CHOICES, default=BASIS_GROSS_PAY)
    rate = models.DecimalField(max_digits=7, decimal_places=4, default=0, help_text="Percentage rate, e.g. 8 for 8%.")
    fixed_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    threshold_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="Optional exempt threshold. Percentage rules apply only to the basis amount above this value.",
    )
    cap_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="Optional maximum calculated amount. Leave at 0 for no cap.",
    )
    statutory = models.BooleanField(default=False, help_text="Mark tax, pension or another rule that is statutory for this business.")
    active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=50)

    class Meta:
        ordering = ["sort_order", "name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["business", "name"], name="unique_payroll_rule_name_per_business"),
            models.CheckConstraint(condition=models.Q(rate__gte=0) & models.Q(rate__lte=100), name="payroll_rule_rate_0_100"),
            models.CheckConstraint(condition=models.Q(fixed_amount__gte=0), name="payroll_rule_fixed_nonnegative"),
            models.CheckConstraint(condition=models.Q(threshold_amount__gte=0), name="payroll_rule_threshold_nonnegative"),
            models.CheckConstraint(condition=models.Q(cap_amount__gte=0), name="payroll_rule_cap_nonnegative"),
        ]

    def __str__(self):
        return self.name


class PayrollRun(BusinessOwnedModel):
    """One posted wage run that groups issued payslips and Finance outflows."""

    KIND_BULK = "bulk"
    KIND_SINGLE = "single"
    KIND_CHOICES = [
        (KIND_BULK, "Bulk payroll"),
        (KIND_SINGLE, "Single payslip"),
    ]

    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default=KIND_BULK)
    period_start = models.DateField()
    period_end = models.DateField()
    pay_date = models.DateField()
    staff_count = models.PositiveIntegerField(default=0)
    total_gross_pay = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_net_pay = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_employer_cost = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    notes = models.CharField(max_length=500, blank=True, default="")
    posted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pay_date", "-id"]
        indexes = [
            models.Index(fields=["business", "-pay_date"], name="payrun_biz_paydate_idx"),
        ]

    def __str__(self):
        return f"Payroll {self.period_start}–{self.period_end}"


class PayrollRunFunding(models.Model):
    """Finance-account split used to pay one posted payroll run."""

    payroll_run = models.ForeignKey(
        PayrollRun,
        on_delete=models.PROTECT,
        related_name="funding_lines",
    )
    account = models.ForeignKey(
        "core.CashAccount",
        on_delete=models.PROTECT,
        related_name="payroll_funding_lines",
    )
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    finance_transaction = models.OneToOneField(
        "core.FinancialTransaction",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="payroll_funding_line",
    )

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["payroll_run", "account"],
                name="unique_payrun_account",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="payrun_funding_positive",
            ),
        ]

    def __str__(self):
        return f"{self.payroll_run} · {self.account} · {self.amount}"


class Payslip(BusinessOwnedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    staff = models.ForeignKey(PayrollStaffProfile, on_delete=models.PROTECT, related_name="payslips")
    payroll_run = models.ForeignKey(
        PayrollRun,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="payslips",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    base_pay = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    manual_allowances = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    manual_deductions = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    allowances = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gross_pay = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    employer_contributions = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    employer_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    net_pay = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.CharField(max_length=500, blank=True, default="")
    share_enabled = models.BooleanField(default=True)
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_end", "-issued_at", "-id"]
        indexes = [models.Index(fields=["business", "-period_end"], name="payslip_biz_period_idx")]

    def __str__(self):
        return f"{self.staff.full_name} · {self.period_start}–{self.period_end}"

    @property
    def recurring_allowance_lines(self):
        return [
            line for line in self.adjustment_lines.all()
            if line.kind == PayslipAdjustmentLine.KIND_ALLOWANCE
        ]

    @property
    def recurring_deduction_lines(self):
        return [
            line for line in self.adjustment_lines.all()
            if line.kind == PayslipAdjustmentLine.KIND_DEDUCTION
        ]


class PayslipAdjustmentLine(models.Model):
    """Frozen named recurring pay item carried into an issued payslip."""

    KIND_ALLOWANCE = PayrollRecurringAdjustment.KIND_ALLOWANCE
    KIND_DEDUCTION = PayrollRecurringAdjustment.KIND_DEDUCTION
    KIND_CHOICES = PayrollRecurringAdjustment.KIND_CHOICES

    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name="adjustment_lines")
    recurring_adjustment = models.ForeignKey(
        PayrollRecurringAdjustment, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="payslip_lines",
    )
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    name = models.CharField(max_length=120)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="payslip_adjustment_amount_nonnegative",
            ),
        ]

    def __str__(self):
        return f"{self.payslip} · {self.name}"


class PayslipCalculationLine(models.Model):
    """Frozen explanation of one configured calculation on an issued payslip."""
    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name="calculation_lines")
    rule = models.ForeignKey(PayrollCalculationRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="payslip_lines")
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=24, choices=PayrollCalculationRule.CATEGORY_CHOICES)
    effect = models.CharField(max_length=24, choices=PayrollCalculationRule.EFFECT_CHOICES)
    method = models.CharField(max_length=12, choices=PayrollCalculationRule.METHOD_CHOICES)
    basis = models.CharField(max_length=12, choices=PayrollCalculationRule.BASIS_CHOICES)
    rate = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    fixed_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    threshold_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cap_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    calculation_base = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    statutory = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=50)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.payslip} · {self.name}"


class PayslipRevision(models.Model):
    """Audit snapshot captured before an issued payslip is deliberately edited."""

    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name="revisions")
    reason = models.CharField(max_length=500)
    previous_snapshot = models.JSONField(default=dict)
    finance_delta = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    finance_transaction = models.ForeignKey(
        "core.FinancialTransaction",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="payroll_payslip_revisions",
    )
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="payroll_payslip_revisions",
    )
    edited_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-edited_at", "-id"]

    def __str__(self):
        return f"Revision of {self.payslip} · {self.edited_at:%Y-%m-%d %H:%M}"


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
    PURPOSE_SUBSCRIPTION = "subscription"
    PURPOSE_PAYROLL_ADDON = "payroll_addon"
    PURPOSE_PAYROLL_BATCH = "payroll_batch"
    PURPOSE_CHOICES = [
        (PURPOSE_SUBSCRIPTION, "Plan subscription"),
        (PURPOSE_PAYROLL_ADDON, "Payroll add-on"),
        (PURPOSE_PAYROLL_BATCH, "Payroll extra-staff batch"),
    ]

    subscription = models.ForeignKey(BusinessSubscription, on_delete=models.CASCADE, related_name="subscription_payments")
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default=PURPOSE_SUBSCRIPTION)
    payroll_tier = models.ForeignKey(
        PayrollAddonTier, null=True, blank=True, on_delete=models.PROTECT, related_name="subscription_payments"
    )
    payroll_batch = models.ForeignKey(
        PayrollStaffBatch, null=True, blank=True, on_delete=models.PROTECT, related_name="subscription_payments"
    )
    payroll_batch_quantity = models.PositiveSmallIntegerField(default=1)
    # Audit trail of the extra-staff batches priced into this payment: a renewal
    # carries every held batch, a batch purchase carries the one being bought.
    payroll_batch_snapshot = models.JSONField(default=list, blank=True)
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
        indexes = [
            models.Index(fields=["status", "-created_at"], name="subpay_status_time_idx"),
        ]


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


class SubscriptionPolicySettings(models.Model):
    """Founder-controlled platform defaults for free-trial windows."""

    general_trial_days = models.PositiveSmallIntegerField(
        default=30,
        help_text="Default number of days for new eligible INPROFIC free trials.",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="subscription_policy_settings_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "subscription policy setting"
        verbose_name_plural = "subscription policy settings"

    @classmethod
    def load(cls):
        row, _ = cls.objects.get_or_create(pk=1)
        return row


class PlatformIntegrationSettings(models.Model):
    """Founder-level availability gates for optional third-party integrations.

    Turning an integration off hides and blocks it without deleting tenant
    credentials, provider records, delivery history, or synchronized records.
    """

    glovo_enabled = models.BooleanField(
        default=False,
        help_text="Expose and allow the optional Glovo delivery-provider integration across INPROFIC.",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="platform_integration_settings_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "platform integration setting"
        verbose_name_plural = "platform integration settings"

    @classmethod
    def load(cls):
        row, _ = cls.objects.get_or_create(pk=1)
        return row


class PlatformPrivacyPolicy(models.Model):
    """Founder-managed platform privacy policy shown on the public website."""

    RENDER_HTML = "html"
    RENDER_PDF = "pdf"
    RENDER_DOCX = "docx"
    RENDER_MODE_CHOICES = [
        (RENDER_HTML, "Editable web policy"),
        (RENDER_PDF, "Uploaded PDF"),
        (RENDER_DOCX, "Uploaded Word document"),
    ]

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    body_html = models.TextField(blank=True, default="")
    rendered_document_html = models.TextField(
        blank=True, default="",
        help_text="Safe rendered snapshot used for an uploaded Word policy document.",
    )
    render_mode = models.CharField(max_length=12, choices=RENDER_MODE_CHOICES, default=RENDER_HTML)
    effective_date = models.DateField(null=True, blank=True)
    source_file = models.FileField(
        upload_to="platform/privacy/", blank=True,
        validators=[FileExtensionValidator(["html", "htm", "txt", "md", "markdown", "pdf", "docx"])],
        help_text="Optional HTML, TXT, Markdown, PDF or Word (.docx) source retained with the current policy.",
    )
    source_filename = models.CharField(max_length=255, blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="platform_privacy_policy_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "platform privacy policy"
        verbose_name_plural = "platform privacy policy"

    @classmethod
    def load(cls):
        row = cls.objects.select_related("updated_by").filter(pk=1).first()
        if row is not None:
            return row
        return cls.objects.create(pk=1)

    @property
    def published_body_html(self):
        if self.body_html.strip():
            return self.body_html
        from .privacy import DEFAULT_PRIVACY_POLICY_HTML
        return DEFAULT_PRIVACY_POLICY_HTML

    def save(self, *args, **kwargs):
        result = super().save(*args, **kwargs)
        from .privacy import invalidate_privacy_policy_cache
        invalidate_privacy_policy_cache()
        return result

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        from .privacy import invalidate_privacy_policy_cache
        invalidate_privacy_policy_cache()
        return result


class FounderSignupContactState(models.Model):
    """Durable Founder signup-list record keyed by normalized signup email.

    The snapshot survives a tenant hard-delete so the Founder mailing list can show
    which business signed up and that the business was later deleted. The row is
    never marked deleted from the mailing-list UI directly; that state is driven by
    the Founder business hard-delete flow.
    """
    email_key = models.CharField(max_length=254, unique=True)
    signup_email = models.EmailField(blank=True, default="")
    signup_name = models.CharField(max_length=160, blank=True, default="")
    business_name = models.CharField(max_length=180, blank=True, default="")
    business_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    vertical = models.CharField(max_length=40, blank=True, default="")
    service = models.CharField(max_length=120, blank=True, default="")
    signed_up_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    permanently_hidden = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="founder_signup_contact_state_changes",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["email_key"]

    def __str__(self):
        return self.email_key


class BusinessTrialIdentity(models.Model):
    """Original tenant registration credentials used only for free-trial eligibility.

    It is intentionally CASCADE-bound to Business: a Founder hard-delete removes
    this blocklist identity as well, making those credentials eligible again.
    """
    business = models.OneToOneField(Business, on_delete=models.CASCADE, related_name="trial_identity")
    email_key = models.CharField(max_length=254, blank=True, default="", db_index=True)
    phone_key = models.CharField(max_length=40, blank=True, default="", db_index=True)
    business_name_key = models.CharField(max_length=160, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.business_name_key


class PlatformMailTemplate(models.Model):
    """Reusable Founder-authored mailing topic based on the INPROFIC email shell."""
    name = models.CharField(max_length=120, unique=True)
    subject = models.CharField(max_length=180)
    heading = models.CharField(max_length=180)
    body_html = models.TextField(help_text="Email body HTML inside the branded INPROFIC shell.")
    cta_label = models.CharField(max_length=80, blank=True, default="")
    cta_url = models.URLField(blank=True, default="")
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="platform_mail_templates_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PlatformMailCampaign(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_QUEUED = "queued"
    STATUS_SENDING = "sending"
    STATUS_SENT = "sent"
    STATUS_PARTIAL = "partial"
    STATUS_CHOICES = [(STATUS_DRAFT,"Draft"),(STATUS_QUEUED,"Queued"),(STATUS_SENDING,"Sending"),(STATUS_SENT,"Sent"),(STATUS_PARTIAL,"Partially sent")]
    template = models.ForeignKey(PlatformMailTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name="campaigns")
    subject = models.CharField(max_length=180)
    heading = models.CharField(max_length=180)
    body_html = models.TextField()
    cta_label = models.CharField(max_length=80, blank=True, default="")
    cta_url = models.URLField(blank=True, default="")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    total_recipients = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="platform_mail_campaigns_created")
    created_at = models.DateTimeField(auto_now_add=True)
    queued_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class PlatformMailRecipient(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SENDING = "sending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SENDING, "Sending"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
    ]
    campaign = models.ForeignKey(PlatformMailCampaign, on_delete=models.CASCADE, related_name="recipients")
    business_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    business_name = models.CharField(max_length=180)
    service = models.CharField(max_length=120, blank=True, default="")
    plan_name = models.CharField(max_length=120, blank=True, default="")
    recipient_name = models.CharField(max_length=160, blank=True, default="")
    email = models.EmailField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    error = models.CharField(max_length=255, blank=True, default="")
    delivery_attempts = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["campaign", "business_id_snapshot"], name="unique_platform_campaign_business")]
        indexes = [
            models.Index(fields=["status", "last_attempt_at"], name="mailrec_status_try_idx"),
            models.Index(fields=["status", "campaign", "id"], name="mailrec_status_camp_idx"),
        ]


class PlatformEvent(models.Model):
    """First-party Founder analytics event.

    Events intentionally capture product/operational milestones rather than
    arbitrary clickstreams.  They are platform-scoped and never participate in
    tenant authorization or bookkeeping.
    """

    EVENT_MARKETING_VISIT = "marketing_visit"
    EVENT_SIGNUP_VIEW = "signup_view"
    EVENT_REGISTRATION = "registration_completed"
    EVENT_LOGIN = "login"
    EVENT_LOGOUT = "logout"
    EVENT_MODULE_VIEW = "module_view"
    EVENT_SUBSCRIPTION_STARTED = "subscription_started"
    EVENT_SUBSCRIPTION_TRIAL = "subscription_trial_started"
    EVENT_SUBSCRIPTION_CHANGED = "subscription_changed"
    EVENT_SUBSCRIPTION_PAID = "subscription_paid"
    EVENT_SUBSCRIPTION_FOUNDER = "subscription_founder_grant"
    EVENT_CHOICES = [
        (EVENT_MARKETING_VISIT, "Marketing page visit"),
        (EVENT_SIGNUP_VIEW, "Signup viewed"),
        (EVENT_REGISTRATION, "Registration completed"),
        (EVENT_LOGIN, "Login"),
        (EVENT_LOGOUT, "Logout"),
        (EVENT_MODULE_VIEW, "Module viewed"),
        (EVENT_SUBSCRIPTION_STARTED, "Subscription started"),
        (EVENT_SUBSCRIPTION_TRIAL, "Paid-plan trial started"),
        (EVENT_SUBSCRIPTION_CHANGED, "Subscription changed"),
        (EVENT_SUBSCRIPTION_PAID, "Subscription paid"),
        (EVENT_SUBSCRIPTION_FOUNDER, "Founder subscription grant"),
    ]
    SUBSCRIPTION_EVENTS = (
        EVENT_SUBSCRIPTION_STARTED,
        EVENT_SUBSCRIPTION_TRIAL,
        EVENT_SUBSCRIPTION_CHANGED,
        EVENT_SUBSCRIPTION_PAID,
        EVENT_SUBSCRIPTION_FOUNDER,
    )

    event_type = models.CharField(max_length=40, choices=EVENT_CHOICES, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="platform_events",
    )
    business = models.ForeignKey(
        Business, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="platform_events",
    )
    session_key = models.CharField(max_length=64, blank=True, default="", db_index=True)
    module = models.CharField(max_length=40, blank=True, default="", db_index=True)
    route_name = models.CharField(max_length=100, blank=True, default="")
    path = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["event_type", "occurred_at"], name="platform_event_type_time"),
            models.Index(fields=["business", "occurred_at"], name="platform_event_business_time"),
        ]

    def __str__(self):
        return f"{self.event_type} · {self.business or 'platform'} · {self.occurred_at:%Y-%m-%d %H:%M}"


class FounderPushSubscription(models.Model):
    """A founder user's browser or installed-app push destination."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="founder_push_subscriptions",
    )
    endpoint = models.TextField()
    endpoint_hash = models.CharField(max_length=64, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    user_agent = models.CharField(max_length=300, blank=True, default="")
    active = models.BooleanField(default=True)
    failure_count = models.PositiveSmallIntegerField(default=0)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return f"Founder device for {self.user}"
