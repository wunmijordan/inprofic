import secrets
import uuid
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from core.models import BusinessOwnedModel, TimestampedModel


def storefront_product_image_upload_to(instance, filename):
    """Keep uploads tenant-partitioned and avoid trusting client filenames."""
    extension = Path(filename or "").suffix.lower()
    if extension not in {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}:
        extension = ".jpg"
    business_id = instance.business_id or "unassigned"
    return f"commerce/products/business-{business_id}/{uuid.uuid4().hex}{extension}"


def storefront_hero_image_upload_to(instance, filename):
    """Keep each tenant's storefront artwork in its own media directory."""
    extension = Path(filename or "").suffix.lower()
    if extension not in {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}:
        extension = ".jpg"
    business_id = instance.business_id or "unassigned"
    return f"commerce/storefronts/business-{business_id}/{uuid.uuid4().hex}{extension}"


class CommerceSettings(BusinessOwnedModel):
    HERO_FIT_COVER = "cover"
    HERO_FIT_CONTAIN = "contain"
    HERO_FIT_CHOICES = [
        (HERO_FIT_COVER, "Fill the header"),
        (HERO_FIT_CONTAIN, "Show the full image"),
    ]
    HERO_POSITION_CHOICES = [
        ("left top", "Top left"),
        ("center top", "Top centre"),
        ("right top", "Top right"),
        ("left center", "Centre left"),
        ("center center", "Centre"),
        ("right center", "Centre right"),
        ("left bottom", "Bottom left"),
        ("center bottom", "Bottom centre"),
        ("right bottom", "Bottom right"),
    ]
    POLICY_REDUCE = "reduce"
    POLICY_REJECT = "reject"
    POLICY_INVITE = "invite_preorder"
    POLICY_SPLIT = "split"
    POLICY_CHOICES = [
        (POLICY_REDUCE, "Reduce to available stock"),
        (POLICY_REJECT, "Reject when stock is insufficient"),
        (POLICY_INVITE, "Invite customer to switch to Pre-order"),
        (POLICY_SPLIT, "Split: fulfil available stock and pre-order the balance"),
    ]

    enabled = models.BooleanField(default=False)
    hosted_storefront_enabled = models.BooleanField(default=True)
    order_now_link_enabled = models.BooleanField(default=True)
    api_enabled = models.BooleanField(default=True)
    connector_enabled = models.BooleanField(default=False)
    storefront_headline = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Optional main storefront message. Leave blank to use wording tailored to your business type.",
    )
    storefront_hero_image = models.ImageField(
        upload_to=storefront_hero_image_upload_to,
        blank=True,
        help_text="Optional wide image displayed in the storefront header.",
    )
    storefront_hero_image_fit = models.CharField(
        max_length=10,
        choices=HERO_FIT_CHOICES,
        default=HERO_FIT_COVER,
        help_text="Fill the header for a crop, or show the full image without zooming in.",
    )
    storefront_hero_image_position = models.CharField(
        max_length=20,
        choices=HERO_POSITION_CHOICES,
        default="center center",
        help_text="Choose the part of the image that should stay in view.",
    )
    storefront_hero_image_scale = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0.50")), MaxValueValidator(Decimal("2.00"))],
        help_text="Resize the rendered image from 50% to 200% while keeping its blend and focal point.",
    )
    notifications_enabled = models.BooleanField(
        default=True,
        help_text="Show persistent in-app alerts for new commerce activity.",
    )
    notify_order_activity = models.BooleanField(
        default=True,
        help_text="Alert when a connected sales channel sends a checkout or order.",
    )
    notify_payment_activity = models.BooleanField(
        default=True,
        help_text="Alert for payment attempts, transfer claims, confirmations, and payment reviews.",
    )
    notify_delivery_activity = models.BooleanField(
        default=True,
        help_text="Alert dispatchers and relevant staff about delivery assignments, status changes, provider exceptions, and rider issues.",
    )
    notification_sound_enabled = models.BooleanField(
        default=True,
        help_text="Play a short sound when new activity arrives while INPROFIC is open.",
    )
    notification_desktop_enabled = models.BooleanField(
        default=True,
        help_text="Use browser desktop alerts when this device has granted permission.",
    )
    insufficient_stock_policy = models.CharField(max_length=20, choices=POLICY_CHOICES, default=POLICY_INVITE)
    public_note = models.CharField(max_length=255, blank=True, default="")
    checkout_reservation_minutes = models.PositiveSmallIntegerField(
        default=15, validators=[MinValueValidator(5), MaxValueValidator(120)],
        help_text="How long Physical Store stock is held while a customer completes payment.",
    )

    class Meta:
        verbose_name_plural = "commerce settings"
        constraints = [models.UniqueConstraint(fields=["business"], name="one_commerce_settings_per_business")]

    def __str__(self):
        return f"{self.business} commerce"


class DeliverySettings(BusinessOwnedModel):
    PROVIDER_INHOUSE = "inhouse"
    PROVIDER_THIRD_PARTY = "third_party"
    PROVIDER_HYBRID = "hybrid"
    PROVIDER_CHOICES = [
        (PROVIDER_INHOUSE, "In-house fleet"),
        (PROVIDER_THIRD_PARTY, "External delivery provider"),
        (PROVIDER_HYBRID, "Hybrid dispatch"),
    ]

    HYBRID_ROUTE_DISPATCHER = "dispatcher_choice"
    HYBRID_ROUTE_CUSTOMER = "customer_choice"
    HYBRID_ROUTE_LOWEST = "lowest_fee"
    HYBRID_ROUTE_FASTEST = "fastest_eta"
    HYBRID_ROUTE_INHOUSE_FIRST = "inhouse_first"
    HYBRID_ROUTE_GLOVO_FIRST = "glovo_first"
    HYBRID_ROUTE_CHOICES = [
        (HYBRID_ROUTE_DISPATCHER, "Dispatcher chooses per order"),
        (HYBRID_ROUTE_CUSTOMER, "Customer chooses at checkout"),
        (HYBRID_ROUTE_LOWEST, "Automatically use the lowest fee"),
        (HYBRID_ROUTE_FASTEST, "Automatically use the fastest ETA"),
        (HYBRID_ROUTE_INHOUSE_FIRST, "Prefer in-house; Glovo remains available"),
        (HYBRID_ROUTE_GLOVO_FIRST, "Prefer Glovo; in-house remains available"),
    ]
    SWITCH_LOCKED = "locked"
    SWITCH_EQUAL_OR_LOWER = "equal_or_lower"
    SWITCH_BUSINESS_ABSORBS = "business_absorbs"
    SWITCH_APPROVAL_ABSORBS = "approval_absorbs"
    SWITCH_POLICY_CHOICES = [
        (SWITCH_LOCKED, "Lock the chosen delivery method after payment"),
        (SWITCH_EQUAL_OR_LOWER, "Allow switches only when the new quote is not higher"),
        (SWITCH_BUSINESS_ABSORBS, "Allow switches; the business absorbs any higher provider cost"),
        (SWITCH_APPROVAL_ABSORBS, "Higher-cost switches need manager approval; the business absorbs the difference"),
    ]

    enabled = models.BooleanField(default=False)
    default_provider = models.CharField(max_length=16, choices=PROVIDER_CHOICES, default=PROVIDER_INHOUSE)
    default_provider_account = models.ForeignKey("commerce.DeliveryProviderAccount", null=True, blank=True, on_delete=models.SET_NULL, related_name="default_for_settings", help_text="Optional configured plug-in provider account, such as Glovo. Leave blank for in-house/manual dispatch.")
    hybrid_routing_policy = models.CharField(max_length=24, choices=HYBRID_ROUTE_CHOICES, default=HYBRID_ROUTE_DISPATCHER, help_text="When Hybrid is enabled, decide who/what chooses between in-house delivery and the configured provider.")
    hybrid_switch_policy = models.CharField(max_length=24, choices=SWITCH_POLICY_CHOICES, default=SWITCH_BUSINESS_ABSORBS, help_text="Controls whether dispatch staff may change the paid order's delivery method before pickup.")
    customer_switch_policy_note = models.CharField(max_length=255, blank=True, default="", help_text="Optional customer-facing clarification shown beside the standard Hybrid switching policy.")
    quote_valid_minutes = models.PositiveSmallIntegerField(default=20, validators=[MinValueValidator(5), MaxValueValidator(120)])
    customer_tracking_enabled = models.BooleanField(default=True)
    require_proof_of_delivery = models.BooleanField(default=False)

    @property
    def customer_switch_policy_text(self):
        messages = {
            self.SWITCH_LOCKED: "The delivery method shown at payment is locked and will not be switched afterwards.",
            self.SWITCH_EQUAL_OR_LOWER: "The business may switch between in-house delivery and its delivery partner only when your paid delivery fee does not increase.",
            self.SWITCH_BUSINESS_ABSORBS: "The business may switch between in-house delivery and its delivery partner after payment. Your paid delivery fee will not increase; the business absorbs any higher provider cost.",
            self.SWITCH_APPROVAL_ABSORBS: "A higher-cost delivery-method switch requires manager approval. Your paid delivery fee will not increase; any approved difference is absorbed by the business.",
        }
        base = messages.get(self.hybrid_switch_policy, messages[self.SWITCH_BUSINESS_ABSORBS])
        return f"{base} {self.customer_switch_policy_note}".strip()

    class Meta:
        verbose_name_plural = "delivery settings"
        constraints = [models.UniqueConstraint(fields=["business"], name="one_delivery_settings_per_business")]


class DeliveryProviderAccount(BusinessOwnedModel):
    """Tenant-owned plug-in credentials for optional third-party dispatch.

    The delivery engine remains provider neutral: INPROFIC owns quotes, order
    totals, dispatch state and customer tracking. Provider accounts only add an
    outbound/inbound bridge for partners such as Glovo.
    """

    PROVIDER_GENERIC = "generic"
    PROVIDER_GLOVO = "glovo"
    PROVIDER_CHOICES = [
        (PROVIDER_GENERIC, "Generic courier / manual API"),
        (PROVIDER_GLOVO, "Glovo"),
    ]

    name = models.CharField(max_length=100, default="Glovo")
    provider_code = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default=PROVIDER_GLOVO)
    active = models.BooleanField(default=True)
    sandbox = models.BooleanField(default=True)
    auto_dispatch = models.BooleanField(default=False, help_text="When enabled, paid delivery checkouts are pushed to this provider automatically.")
    base_url = models.URLField(max_length=255, blank=True, default="", help_text="Provider API base URL issued for this tenant/environment by the provider.")
    auth_endpoint = models.CharField(max_length=160, blank=True, default="/oauth/token", help_text="OAuth token endpoint. Glovo LaaS v2 uses /oauth/token.")
    quote_endpoint = models.CharField(max_length=160, blank=True, default="", help_text="Relative/absolute quote endpoint. Glovo LaaS v2 uses /v2/laas/quotes.")
    use_live_quotes = models.BooleanField(default=True, help_text="Use the provider's live quote/ETA when configured; hybrid mode can fall back to INPROFIC rate bands.")
    order_endpoint = models.CharField(max_length=160, blank=True, default="", help_text="Relative/absolute endpoint used to create a delivery job.")
    cancel_endpoint = models.CharField(max_length=160, blank=True, default="", help_text="Optional endpoint template for cancellation; {external_reference} is replaced when present.")
    api_key = models.CharField(max_length=255, blank=True, default="")
    api_secret = models.CharField(max_length=255, blank=True, default="")
    store_id = models.CharField(max_length=120, blank=True, default="", help_text="Optional legacy/provider store identifier.")
    address_book_id = models.CharField(max_length=120, blank=True, default="", help_text="Glovo LaaS Address Book pickup ID. Required for live Glovo quotes.")
    webhook_secret = models.CharField(max_length=255, blank=True, default="")
    tracking_base_url = models.URLField(max_length=255, blank=True, default="")
    status_mapping = models.JSONField(default=dict, blank=True, help_text='Map provider statuses to INPROFIC statuses. Example: {"delivered": "delivered"}.')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["provider_code", "name", "id"]
        constraints = [models.UniqueConstraint(fields=["business", "name"], name="unique_delivery_provider_account_per_business")]

    def __str__(self):
        return f"{self.get_provider_code_display()} — {self.name}"

    @property
    def is_configured_for_dispatch(self):
        if self.provider_code == self.PROVIDER_GLOVO:
            return bool(
                self.active and self.use_live_quotes and self.is_configured_for_quote
                and self.order_endpoint
            )
        return bool(self.active and self.order_endpoint and (self.api_key or self.api_secret))

    @property
    def is_configured_for_quote(self):
        if self.provider_code == self.PROVIDER_GLOVO:
            return bool(
                self.active and self.use_live_quotes and self.base_url and self.auth_endpoint
                and self.quote_endpoint and self.api_key and self.api_secret and self.address_book_id
            )
        return False

    @property
    def masked_api_key(self):
        if not self.api_key:
            return ""
        return f"{self.api_key[:4]}…{self.api_key[-4:]}" if len(self.api_key) > 8 else "••••"


class DeliveryOrigin(BusinessOwnedModel):
    name = models.CharField(max_length=100)
    address = models.CharField(max_length=255)
    area = models.CharField(max_length=120, blank=True, default="")
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    is_default = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-is_default", "name", "id"]
        constraints = [models.UniqueConstraint(fields=["business", "name"], name="unique_delivery_origin_per_business")]

    def __str__(self):
        return self.name


class DeliveryRateBand(BusinessOwnedModel):
    name = models.CharField(max_length=100)
    min_distance_km = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    max_distance_km = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    base_fee = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    per_km_fee = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    minimum_order = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    eta_min_minutes = models.PositiveSmallIntegerField(default=20)
    eta_max_minutes = models.PositiveSmallIntegerField(default=60)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "min_distance_km", "id"]

    def __str__(self):
        return self.name


class DeliveryArea(BusinessOwnedModel):
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=80, blank=True, default="")
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    radius_km = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("5.00"),
        validators=[MinValueValidator(Decimal("0.10")), MaxValueValidator(Decimal("500.00"))],
        help_text="Maximum distance from this destination centre that an address may be delivered to.",
    )
    extension_ne_km = models.DecimalField(max_digits=8, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(500)])
    extension_se_km = models.DecimalField(max_digits=8, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(500)])
    extension_sw_km = models.DecimalField(max_digits=8, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(500)])
    extension_nw_km = models.DecimalField(max_digits=8, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(500)])
    rate_band = models.ForeignKey(DeliveryRateBand, null=True, blank=True, on_delete=models.PROTECT, related_name="areas")
    active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["name", "id"]
        constraints = [models.UniqueConstraint(fields=["business", "name"], name="unique_delivery_area_per_business")]

    def __str__(self):
        return self.name


class DeliveryDriver(BusinessOwnedModel):
    PROVIDER_INHOUSE = "inhouse"
    PROVIDER_THIRD_PARTY = "third_party"
    PROVIDER_CHOICES = [
        (PROVIDER_INHOUSE, "In-house"),
        (PROVIDER_THIRD_PARTY, "Third-party / courier"),
    ]
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="delivery_driver_profiles",
        help_text="Optional tenant staff login for an in-house rider. Linked riders see only deliveries assigned to this driver profile.",
    )
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=40, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    provider = models.CharField(max_length=16, choices=PROVIDER_CHOICES, default=PROVIDER_INHOUSE)
    vehicle_type = models.CharField(max_length=60, blank=True, default="")
    vehicle_registration = models.CharField(max_length=60, blank=True, default="")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "user"],
                condition=models.Q(user__isnull=False),
                name="unique_delivery_driver_login_per_business",
            ),
        ]

    def __str__(self):
        return self.name


class DeliveryQuote(BusinessOwnedModel):
    STATUS_ACTIVE = "active"
    STATUS_USED = "used"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"), (STATUS_USED, "Used"),
        (STATUS_EXPIRED, "Expired"), (STATUS_CANCELLED, "Cancelled"),
    ]
    SELECT_PLATFORM = "platform"
    SELECT_CUSTOMER = "customer"
    SELECT_DISPATCHER = "dispatcher"
    SELECT_CHOICES = [
        (SELECT_PLATFORM, "Platform routing policy"),
        (SELECT_CUSTOMER, "Customer choice"),
        (SELECT_DISPATCHER, "Dispatcher choice"),
    ]
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    quote_group_id = models.UUIDField(default=uuid.uuid4, db_index=True, editable=False)
    selection_source = models.CharField(max_length=12, choices=SELECT_CHOICES, default=SELECT_PLATFORM)
    origin = models.ForeignKey(DeliveryOrigin, on_delete=models.PROTECT, related_name="quotes")
    area = models.ForeignKey(DeliveryArea, null=True, blank=True, on_delete=models.PROTECT, related_name="quotes")
    destination_address = models.CharField(max_length=255)
    destination_latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    destination_longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    distance_km = models.DecimalField(max_digits=9, decimal_places=2)
    subtotal = models.DecimalField(max_digits=16, decimal_places=2)
    fee = models.DecimalField(max_digits=14, decimal_places=2)
    total = models.DecimalField(max_digits=16, decimal_places=2)
    eta_min_minutes = models.PositiveSmallIntegerField(default=20)
    eta_max_minutes = models.PositiveSmallIntegerField(default=60)
    provider = models.CharField(max_length=16, choices=DeliverySettings.PROVIDER_CHOICES, default=DeliverySettings.PROVIDER_INHOUSE)
    provider_account = models.ForeignKey("commerce.DeliveryProviderAccount", null=True, blank=True, on_delete=models.SET_NULL, related_name="quotes")
    provider_quote_reference = models.CharField(max_length=160, blank=True, default="")
    provider_payload = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_ACTIVE)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["business", "status", "expires_at"], name="delivery_quote_active_idx")]


class DeliveryAssignment(BusinessOwnedModel):
    STATUS_PENDING = "pending"
    STATUS_ASSIGNED = "assigned"
    STATUS_READY = "ready"
    STATUS_PICKED_UP = "picked_up"
    STATUS_OUT_FOR_DELIVERY = "out_for_delivery"
    STATUS_DELIVERED = "delivered"
    STATUS_FAILED = "failed"
    STATUS_RETURNED = "returned"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending dispatch"),
        (STATUS_ASSIGNED, "Driver assigned"),
        (STATUS_READY, "Ready for pickup"),
        (STATUS_PICKED_UP, "Picked up"),
        (STATUS_OUT_FOR_DELIVERY, "Out for delivery"),
        (STATUS_DELIVERED, "Delivered"),
        (STATUS_FAILED, "Delivery failed"),
        (STATUS_RETURNED, "Returned"),
        (STATUS_CANCELLED, "Cancelled"),
    ]
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    intake = models.OneToOneField("CommerceIntake", on_delete=models.PROTECT, related_name="delivery_assignment")
    quote = models.ForeignKey(DeliveryQuote, null=True, blank=True, on_delete=models.PROTECT, related_name="assignments")
    origin = models.ForeignKey(DeliveryOrigin, on_delete=models.PROTECT, related_name="assignments")
    driver = models.ForeignKey(DeliveryDriver, null=True, blank=True, on_delete=models.PROTECT, related_name="assignments")
    provider = models.CharField(max_length=16, choices=DeliverySettings.PROVIDER_CHOICES, default=DeliverySettings.PROVIDER_INHOUSE)
    provider_account = models.ForeignKey("commerce.DeliveryProviderAccount", null=True, blank=True, on_delete=models.SET_NULL, related_name="assignments")
    provider_order_id = models.CharField(max_length=160, blank=True, default="")
    provider_status = models.CharField(max_length=80, blank=True, default="")
    provider_payload = models.JSONField(default=dict, blank=True)
    external_reference = models.CharField(max_length=160, blank=True, default="")
    external_tracking_url = models.URLField(max_length=500, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    status_note = models.CharField(max_length=255, blank=True, default="")
    eta_at = models.DateTimeField(null=True, blank=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    proof_note = models.CharField(max_length=255, blank=True, default="")
    proof_reference = models.CharField(max_length=255, blank=True, default="")
    method_switch_count = models.PositiveSmallIntegerField(default=0)
    last_method_switched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["business", "status", "created_at"], name="delivery_assignment_idx")]


class DeliveryEvent(BusinessOwnedModel):
    assignment = models.ForeignKey(DeliveryAssignment, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=20, choices=DeliveryAssignment.STATUS_CHOICES)
    note = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at", "id"]


class DeliveryIssue(BusinessOwnedModel):
    CATEGORY_DELAY = "delay"
    CATEGORY_CUSTOMER = "customer_unavailable"
    CATEGORY_ADDRESS = "address"
    CATEGORY_VEHICLE = "vehicle"
    CATEGORY_PACKAGE = "package"
    CATEGORY_SAFETY = "safety"
    CATEGORY_OTHER = "other"
    CATEGORY_CHOICES = [
        (CATEGORY_DELAY, "Delay / traffic"),
        (CATEGORY_CUSTOMER, "Customer unavailable"),
        (CATEGORY_ADDRESS, "Address / location problem"),
        (CATEGORY_VEHICLE, "Vehicle / rider problem"),
        (CATEGORY_PACKAGE, "Package / order problem"),
        (CATEGORY_SAFETY, "Safety concern"),
        (CATEGORY_OTHER, "Other issue / complaint"),
    ]
    STATUS_OPEN = "open"
    STATUS_ACKNOWLEDGED = "acknowledged"
    STATUS_RESOLVED = "resolved"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_ACKNOWLEDGED, "Acknowledged"),
        (STATUS_RESOLVED, "Resolved"),
    ]

    assignment = models.ForeignKey(DeliveryAssignment, on_delete=models.CASCADE, related_name="issues")
    reporter_driver = models.ForeignKey(DeliveryDriver, null=True, blank=True, on_delete=models.SET_NULL, related_name="reported_issues")
    category = models.CharField(max_length=28, choices=CATEGORY_CHOICES, default=CATEGORY_OTHER)
    details = models.TextField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_OPEN)
    resolution_note = models.TextField(blank=True, default="")
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["business", "status", "created_at"], name="delivery_issue_open_idx")]

    def __str__(self):
        return f"{self.get_category_display()} — {self.assignment.intake.public_number}"


class StorefrontCustomer(BusinessOwnedModel):
    """Optional customer login scoped to exactly one tenant storefront.

    These accounts are intentionally separate from staff/auth users. The same
    email address can have independent profiles at different businesses, and
    every lookup is constrained by business. Guest checkout remains supported.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    email = models.EmailField()
    name = models.CharField(max_length=160)
    phone = models.CharField(max_length=40, blank=True, default="")
    default_address = models.TextField(blank=True, default="")
    password_hash = models.CharField(max_length=255)
    active = models.BooleanField(default=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["business", "email"], name="unique_storefront_customer_email_per_business"),
        ]
        indexes = [models.Index(fields=["business", "email"], name="storefront_customer_email_idx")]

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} — {self.business}"


class CommerceIntegration(BusinessOwnedModel):
    TYPE_API = "api"
    TYPE_WEBHOOK = "webhook"
    TYPE_CHOICES = [(TYPE_API, "Connected website"), (TYPE_WEBHOOK, "Connected sales platform")]

    name = models.CharField(max_length=100)
    integration_type = models.CharField(max_length=12, choices=TYPE_CHOICES, default=TYPE_API)
    api_key = models.CharField(max_length=80, unique=True, editable=False)
    webhook_secret = models.CharField(max_length=80, blank=True, default="")
    active = models.BooleanField(default=True)
    allowed_origin = models.CharField(max_length=255, blank=True, default="")

    def save(self, *args, **kwargs):
        if not self.api_key:
            self.api_key = secrets.token_urlsafe(32)
        if self.integration_type == self.TYPE_WEBHOOK and not self.webhook_secret:
            self.webhook_secret = secrets.token_urlsafe(32)
        return super().save(*args, **kwargs)


class StorefrontProduct(BusinessOwnedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    finished_good = models.OneToOneField("inventory.FinishedGood", on_delete=models.CASCADE, related_name="storefront_product")
    published = models.BooleanField(default=False)
    public_name = models.CharField(max_length=140, blank=True, default="")
    description = models.TextField(blank=True, default="")
    image = models.ImageField(upload_to=storefront_product_image_upload_to, blank=True)
    # Retained as a read-only fallback for records created before image uploads.
    image_url = models.URLField(blank=True, default="")
    allow_stock_order = models.BooleanField(default=True)
    allow_preorder = models.BooleanField(default=True)
    allow_online_order = models.BooleanField(default=True)
    allow_distribution_order = models.BooleanField(default=True)
    min_quantity = models.DecimalField(max_digits=14, decimal_places=2, default=1)
    max_quantity = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    preorder_min_quantity = models.DecimalField(max_digits=14, decimal_places=2, default=1)
    distribution_min_quantity = models.DecimalField(max_digits=14, decimal_places=2, default=1)
    preorder_lead_time = models.CharField(max_length=80, blank=True, default="")

    class Meta:
        ordering = ["finished_good__name"]

    @property
    def display_name(self):
        return self.public_name.strip() or self.finished_good.name

    @property
    def available_now(self):
        return Decimal(self.finished_good.physical_saleable_stock or 0)

    @property
    def public_image_url(self):
        if self.image:
            try:
                return self.image.url
            except ValueError:
                pass
        return self.image_url

    @property
    def stock_price(self):
        return self.finished_good.selling_price_for("physical_store")

    @property
    def preorder_price(self):
        return self.finished_good.selling_price_for("online")

    @property
    def distribution_price(self):
        return self.finished_good.selling_price_for("distribution")

    def __str__(self):
        return self.display_name


class CommerceIntake(BusinessOwnedModel):
    SOURCE_STOREFRONT = "storefront"
    SOURCE_API = "api"
    SOURCE_CONNECTOR = "connector"
    SOURCE_STAFF_POS = "staff_pos"
    SOURCE_CHOICES = [
        (SOURCE_STOREFRONT, "Hosted storefront"),
        (SOURCE_API, "API"),
        (SOURCE_CONNECTOR, "External connector"),
        (SOURCE_STAFF_POS, "In-premise storefront"),
    ]
    MODE_STOCK = "stock"
    MODE_PREORDER = "preorder"
    MODE_CHOICES = [(MODE_STOCK, "Order"), (MODE_PREORDER, "Pre-order")]
    CHANNEL_PHYSICAL_STORE = "physical_store"
    CHANNEL_ONLINE = "online"
    CHANNEL_DISTRIBUTION = "distribution"
    CHANNEL_CHOICES = [
        (CHANNEL_PHYSICAL_STORE, "Physical Store"),
        (CHANNEL_ONLINE, "Online"),
        (CHANNEL_DISTRIBUTION, "Distribution / Bulk"),
    ]
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_AWAITING_PREORDER = "awaiting_preorder"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"
    STATUS_FULFILLED = "fulfilled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending review"), (STATUS_ACCEPTED, "Accepted"),
        (STATUS_AWAITING_PREORDER, "Awaiting Pre-order confirmation"),
        (STATUS_REJECTED, "Rejected"), (STATUS_CANCELLED, "Cancelled"), (STATUS_FULFILLED, "Fulfilled"),
    ]
    PAYMENT_PENDING = "pending"
    PAYMENT_CONFIRMED = "confirmed"
    PAYMENT_FAILED = "failed"
    PAYMENT_CHOICES = [(PAYMENT_PENDING, "Pending"), (PAYMENT_CONFIRMED, "Confirmed"), (PAYMENT_FAILED, "Failed")]
    FULFIL_PENDING = "pending"
    FULFIL_PARTIAL = "partial"
    FULFIL_COMPLETE = "complete"
    FULFIL_CHOICES = [(FULFIL_PENDING, "Pending"), (FULFIL_PARTIAL, "Partial"), (FULFIL_COMPLETE, "Complete")]

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    public_number = models.CharField(max_length=40, blank=True, default="")
    source = models.CharField(max_length=12, choices=SOURCE_CHOICES, default=SOURCE_STOREFRONT)
    external_order_id = models.CharField(max_length=120, blank=True, default="")
    idempotency_key = models.CharField(max_length=120, blank=True, default="")
    ordering_mode = models.CharField(max_length=12, choices=MODE_CHOICES)
    sales_channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default=CHANNEL_PHYSICAL_STORE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    storefront_customer = models.ForeignKey("commerce.StorefrontCustomer", null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    customer_name = models.CharField(max_length=160)
    customer_phone = models.CharField(max_length=40, blank=True, default="")
    customer_email = models.EmailField(blank=True, default="")
    customer_address = models.TextField(blank=True, default="")
    service_mode = models.CharField(max_length=20, blank=True, default="")
    table_reference = models.CharField(max_length=40, blank=True, default="")
    delivery_quote = models.ForeignKey("DeliveryQuote", null=True, blank=True, on_delete=models.PROTECT, related_name="intakes")
    delivery_fee = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    payment_state = models.CharField(max_length=12, choices=PAYMENT_CHOICES, default=PAYMENT_PENDING)
    fulfilment_state = models.CharField(max_length=12, choices=FULFIL_CHOICES, default=FULFIL_PENDING)
    accepted_order = models.ForeignKey("production.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="commerce_intakes")
    accepted_sale = models.ForeignKey("sales.Sale", null=True, blank=True, on_delete=models.SET_NULL, related_name="commerce_intakes")
    split_order = models.ForeignKey("production.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="split_commerce_intakes")
    rejection_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "public_number"],
                condition=~models.Q(public_number=""),
                name="unique_commerce_number_per_business",
            ),
            models.UniqueConstraint(
                fields=["business", "source", "external_order_id"],
                condition=~models.Q(external_order_id=""),
                name="unique_external_commerce_order",
            ),
            models.UniqueConstraint(
                fields=["business", "source", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_commerce_idempotency_key",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.public_number:
            return super().save(*args, **kwargs)
        if not self.business_id:
            raise ValueError("Commerce intake business must be set before allocating an order number.")
        with transaction.atomic():
            sequence, _ = CommerceOrderNumberSequence.raw_objects.select_for_update().get_or_create(
                business_id=self.business_id,
                defaults={"next_number": 1, "created_by_id": self.created_by_id},
            )
            self.public_number = f"WEB-{sequence.next_number:06d}"
            sequence.next_number += 1
            sequence.save(update_fields=["next_number", "updated_at"])
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"public_number"}
            return super().save(*args, **kwargs)

    @property
    def total(self):
        return sum((row.line_total for row in self.items.all()), Decimal("0")) + Decimal(self.delivery_fee or 0)

    @property
    def display_sales_channel(self):
        from core.verticals import vertical_config
        return vertical_config(self.business)["commerce_channels"].get(
            self.sales_channel, self.get_sales_channel_display()
        )


class CommerceOrderNumberSequence(BusinessOwnedModel):
    """Locked per-business sequence for human-facing commerce order numbers."""

    next_number = models.PositiveBigIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business"], name="unique_commerce_order_sequence_per_business"),
        ]


class CommerceIntakeItem(models.Model):
    intake = models.ForeignKey(CommerceIntake, on_delete=models.CASCADE, related_name="items")
    storefront_product = models.ForeignKey(StorefrontProduct, on_delete=models.PROTECT)
    finished_good = models.ForeignKey("inventory.FinishedGood", on_delete=models.PROTECT)
    requested_quantity = models.DecimalField(max_digits=14, decimal_places=2)
    accepted_stock_quantity = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    production_quantity = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    @property
    def line_total(self):
        return self.requested_quantity * self.unit_price


class CommerceCheckoutSession(BusinessOwnedModel):
    """Validated, tenant-scoped checkout that exists before any CommerceIntake.

    The session snapshots authoritative prices and, for stock orders, holds a
    short-lived reservation. Only a fully verified payment can materialize one
    CommerceIntake.
    """

    SOURCE_API = CommerceIntake.SOURCE_API
    SOURCE_STOREFRONT = CommerceIntake.SOURCE_STOREFRONT
    SOURCE_CONNECTOR = CommerceIntake.SOURCE_CONNECTOR
    SOURCE_STAFF_POS = CommerceIntake.SOURCE_STAFF_POS
    SOURCE_CHOICES = CommerceIntake.SOURCE_CHOICES

    STATUS_AWAITING_PAYMENT = "awaiting_payment"
    STATUS_PAID = "paid"
    STATUS_MATERIALIZED = "materialized"
    STATUS_PAID_REVIEW = "paid_review"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_AWAITING_PAYMENT, "Awaiting payment"),
        (STATUS_PAID, "Paid — creating order"),
        (STATUS_MATERIALIZED, "Order created"),
        (STATUS_PAID_REVIEW, "Paid — needs fulfilment review"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    source = models.CharField(max_length=12, choices=SOURCE_CHOICES, default=SOURCE_API)
    external_order_id = models.CharField(max_length=120, blank=True, default="")
    idempotency_key = models.CharField(max_length=120)
    ordering_mode = models.CharField(max_length=12, choices=CommerceIntake.MODE_CHOICES)
    sales_channel = models.CharField(max_length=20, choices=CommerceIntake.CHANNEL_CHOICES)
    storefront_customer = models.ForeignKey("commerce.StorefrontCustomer", null=True, blank=True, on_delete=models.SET_NULL, related_name="checkouts")
    customer_name = models.CharField(max_length=160)
    customer_phone = models.CharField(max_length=40, blank=True, default="")
    customer_email = models.EmailField(blank=True, default="")
    customer_address = models.TextField(blank=True, default="")
    service_mode = models.CharField(max_length=20, blank=True, default="")
    table_reference = models.CharField(max_length=40, blank=True, default="")
    currency = models.CharField(max_length=3, default="NGN")
    amount = models.DecimalField(max_digits=16, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    delivery_quote = models.ForeignKey("DeliveryQuote", null=True, blank=True, on_delete=models.PROTECT, related_name="checkouts")
    delivery_fee = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_AWAITING_PAYMENT)
    reservation_expires_at = models.DateTimeField(null=True, blank=True)
    reservation_released_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    materialized_intake = models.OneToOneField(
        CommerceIntake, null=True, blank=True, on_delete=models.PROTECT, related_name="checkout_session"
    )
    materialization_error = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["business", "status", "reservation_expires_at"], name="commerce_checkout_res_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "source", "idempotency_key"],
                name="unique_checkout_idempotency_key",
            ),
            models.UniqueConstraint(
                fields=["business", "source", "external_order_id"],
                condition=~models.Q(external_order_id=""),
                name="unique_checkout_external_order",
            ),
        ]

    @property
    def reservation_active(self):
        if self.reservation_released_at:
            return False
        if self.status == self.STATUS_MATERIALIZED:
            return True
        return bool(
            self.status == self.STATUS_AWAITING_PAYMENT
            and self.reservation_expires_at
            and self.reservation_expires_at > timezone.now()
        )


class CommerceCheckoutItem(models.Model):
    checkout = models.ForeignKey(CommerceCheckoutSession, on_delete=models.CASCADE, related_name="items")
    storefront_product = models.ForeignKey(StorefrontProduct, on_delete=models.PROTECT)
    finished_good = models.ForeignKey("inventory.FinishedGood", on_delete=models.PROTECT)
    requested_quantity = models.DecimalField(max_digits=14, decimal_places=2)
    payable_quantity = models.DecimalField(max_digits=14, decimal_places=2)
    reserved_stock_quantity = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    production_quantity = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        indexes = [models.Index(fields=["finished_good", "reserved_stock_quantity"], name="commerce_checkout_good_idx")]

    @property
    def line_total(self):
        return self.payable_quantity * self.unit_price


class CommercePaymentConfiguration(BusinessOwnedModel):
    """Tenant-owned payment credentials and settlement destinations.

    Provider secrets are intentionally never serialized by the public API. The
    form also treats a blank submitted secret as "keep the existing value".
    """

    currency = models.CharField(max_length=3, default="NGN")
    paystack_enabled = models.BooleanField(default=False)
    paystack_secret_key = models.CharField(max_length=255, blank=True, default="")
    paystack_account = models.ForeignKey(
        "core.CashAccount", null=True, blank=True, on_delete=models.PROTECT,
        related_name="commerce_paystack_configurations",
    )
    paystack_terminal_enabled = models.BooleanField(default=False)
    paystack_terminal_id = models.CharField(max_length=80, blank=True, default="")
    paystack_terminal_customer_email = models.EmailField(blank=True, default="")
    paystack_terminal_account = models.ForeignKey(
        "core.CashAccount", null=True, blank=True, on_delete=models.PROTECT,
        related_name="commerce_paystack_terminal_configurations",
    )
    monnify_enabled = models.BooleanField(default=False)
    monnify_api_key = models.CharField(max_length=255, blank=True, default="")
    monnify_secret_key = models.CharField(max_length=255, blank=True, default="")
    monnify_contract_code = models.CharField(max_length=80, blank=True, default="")
    monnify_base_url = models.URLField(default="https://api.monnify.com")
    monnify_account = models.ForeignKey(
        "core.CashAccount", null=True, blank=True, on_delete=models.PROTECT,
        related_name="commerce_monnify_configurations",
    )
    BANK_TRANSFER_PROVIDER_PAYSTACK = "paystack"
    BANK_TRANSFER_PROVIDER_MONNIFY = "monnify"
    BANK_TRANSFER_PROVIDER_CHOICES = [
        (BANK_TRANSFER_PROVIDER_PAYSTACK, "Paystack"),
        (BANK_TRANSFER_PROVIDER_MONNIFY, "Monnify"),
    ]
    bank_transfer_enabled = models.BooleanField(default=False)
    bank_transfer_provider = models.CharField(
        max_length=20,
        choices=BANK_TRANSFER_PROVIDER_CHOICES,
        default=BANK_TRANSFER_PROVIDER_PAYSTACK,
        help_text="Payment service that provides the temporary transfer account and confirms payment automatically.",
    )
    monnify_transfer_bank_code = models.CharField(
        max_length=12,
        blank=True,
        default="",
        help_text="Bank used by Monnify when creating a temporary transfer account. Choose a bank supported by your merchant account.",
    )
    bank_name = models.CharField(max_length=120, blank=True, default="")
    bank_account_name = models.CharField(max_length=160, blank=True, default="")
    bank_account_number = models.CharField(max_length=40, blank=True, default="")
    bank_instructions = models.CharField(max_length=255, blank=True, default="")
    bank_cash_account = models.ForeignKey(
        "core.CashAccount", null=True, blank=True, on_delete=models.PROTECT,
        related_name="commerce_bank_transfer_configurations",
    )
    cash_enabled = models.BooleanField(default=False)
    cash_instructions = models.CharField(max_length=255, blank=True, default="")
    cash_account = models.ForeignKey(
        "core.CashAccount", null=True, blank=True, on_delete=models.PROTECT,
        related_name="commerce_cash_configurations",
    )

    class Meta:
        verbose_name = "commerce payment configuration"
        constraints = [
            models.UniqueConstraint(fields=["business"], name="one_commerce_payment_config_per_business"),
        ]


class CommercePayment(BusinessOwnedModel):
    METHOD_PAYSTACK = "paystack"
    METHOD_MONNIFY = "monnify"
    METHOD_BANK_TRANSFER = "bank_transfer"
    METHOD_CASH = "cash"
    METHOD_POS_CARD = "pos_card"
    METHOD_CHOICES = [
        (METHOD_PAYSTACK, "Paystack secure checkout"),
        (METHOD_MONNIFY, "Monnify secure checkout"),
        (METHOD_BANK_TRANSFER, "Instant bank transfer"),
        (METHOD_CASH, "Cash"),
        (METHOD_POS_CARD, "Card on POS terminal"),
    ]
    GATEWAY_NONE = ""
    GATEWAY_PAYSTACK = "paystack"
    GATEWAY_MONNIFY = "monnify"
    GATEWAY_CHOICES = [
        (GATEWAY_NONE, "No automatic payment provider"),
        (GATEWAY_PAYSTACK, "Paystack"),
        (GATEWAY_MONNIFY, "Monnify"),
    ]

    STATUS_PENDING = "pending"
    STATUS_AWAITING_CUSTOMER = "awaiting_customer"
    STATUS_AWAITING_VERIFICATION = "awaiting_verification"
    STATUS_PARTIALLY_PAID = "partially_paid"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_REFUNDED = "refunded"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_AWAITING_CUSTOMER, "Awaiting customer"),
        (STATUS_AWAITING_VERIFICATION, "Awaiting verification"),
        (STATUS_PARTIALLY_PAID, "Partially paid"),
        (STATUS_PAID, "Paid"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_REFUNDED, "Refunded"),
    ]
    ACTIVE_STATUSES = {
        STATUS_PENDING, STATUS_AWAITING_CUSTOMER,
        STATUS_AWAITING_VERIFICATION, STATUS_PARTIALLY_PAID,
    }

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    intake = models.ForeignKey(CommerceIntake, null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    checkout = models.ForeignKey(
        CommerceCheckoutSession, null=True, blank=True, on_delete=models.PROTECT, related_name="payments"
    )
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    gateway_provider = models.CharField(max_length=20, choices=GATEWAY_CHOICES, blank=True, default="")
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_PENDING)
    amount = models.DecimalField(max_digits=16, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    amount_paid = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="NGN")
    reference = models.CharField(max_length=80, unique=True)
    gateway_reference = models.CharField(max_length=160, blank=True, default="")
    authorization_url = models.URLField(max_length=500, blank=True, default="")
    instructions = models.CharField(max_length=500, blank=True, default="")
    idempotency_key = models.CharField(max_length=160)
    return_url = models.URLField(max_length=500, blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="verified_commerce_payments",
    )
    settled_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True, default="")
    gateway_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "intake", "idempotency_key"],
                condition=models.Q(intake__isnull=False),
                name="unique_commerce_payment_idempotency",
            ),
            models.UniqueConstraint(
                fields=["business", "checkout", "idempotency_key"],
                condition=models.Q(checkout__isnull=False),
                name="unique_checkout_payment_idempotency",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(intake__isnull=False, checkout__isnull=True)
                    | models.Q(intake__isnull=True, checkout__isnull=False)
                ),
                name="commerce_payment_has_one_target",
            ),
        ]

    @property
    def balance(self):
        return max(Decimal("0"), Decimal(self.amount or 0) - Decimal(self.amount_paid or 0))


class CommercePaymentClaim(BusinessOwnedModel):
    STATUS_SUBMITTED = "submitted"
    STATUS_ACCEPTED = "accepted"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = [
        (STATUS_SUBMITTED, "Awaiting verification"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REJECTED, "Rejected"),
    ]

    payment = models.ForeignKey(CommercePayment, on_delete=models.PROTECT, related_name="claims")
    payer_name = models.CharField(max_length=160)
    transfer_reference = models.CharField(max_length=160)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_SUBMITTED)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="reviewed_commerce_payment_claims",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    confirmed_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    review_note = models.CharField(max_length=500, blank=True, default="")
    mismatch_reason = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "transfer_reference"],
                name="unique_commerce_transfer_reference",
            ),
        ]


class CommercePaymentReceipt(BusinessOwnedModel):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    payment = models.ForeignKey(CommercePayment, on_delete=models.PROTECT, related_name="receipts")
    amount = models.DecimalField(max_digits=16, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    external_reference = models.CharField(max_length=160, blank=True, default="")
    idempotency_key = models.CharField(max_length=180)
    account = models.ForeignKey(
        "core.CashAccount", on_delete=models.PROTECT, related_name="commerce_payment_receipts",
    )
    financial_transaction = models.OneToOneField(
        "core.FinancialTransaction", on_delete=models.PROTECT, related_name="commerce_payment_receipt",
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="commerce_payment_receipts",
    )
    verified_at = models.DateTimeField()
    location = models.CharField(max_length=120, blank=True, default="")
    note = models.CharField(max_length=500, blank=True, default="")
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="reversed_commerce_payment_receipts",
    )
    reversal_reason = models.CharField(max_length=500, blank=True, default="")
    reversal_transaction = models.OneToOneField(
        "core.FinancialTransaction", null=True, blank=True, on_delete=models.PROTECT,
        related_name="commerce_payment_receipt_reversal",
    )

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "payment", "idempotency_key"],
                name="unique_commerce_receipt_idempotency",
            ),
            models.UniqueConstraint(
                fields=["business", "external_reference"],
                condition=~models.Q(external_reference=""),
                name="unique_settled_commerce_external_ref",
            ),
        ]


class CommercePaymentAllocation(models.Model):
    receipt = models.ForeignKey(CommercePaymentReceipt, on_delete=models.PROTECT, related_name="allocations")
    sale = models.ForeignKey("sales.Sale", on_delete=models.PROTECT, related_name="commerce_payment_allocations")
    customer_payment = models.OneToOneField(
        "sales.CustomerPayment", on_delete=models.PROTECT, related_name="commerce_payment_allocation",
    )
    reversal_customer_payment = models.OneToOneField(
        "sales.CustomerPayment", null=True, blank=True, on_delete=models.PROTECT,
        related_name="commerce_payment_reversal_allocation",
    )
    amount = models.DecimalField(max_digits=16, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["receipt", "sale"], name="unique_commerce_receipt_sale_allocation"),
        ]


class CommerceGatewayEvent(BusinessOwnedModel):
    PROVIDER_PAYSTACK = CommercePayment.METHOD_PAYSTACK
    PROVIDER_MONNIFY = CommercePayment.METHOD_MONNIFY
    PROVIDER_CHOICES = [
        (PROVIDER_PAYSTACK, "Paystack"),
        (PROVIDER_MONNIFY, "Monnify"),
    ]

    payment = models.ForeignKey(
        CommercePayment, null=True, blank=True, on_delete=models.PROTECT, related_name="gateway_events",
    )
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    event_key = models.CharField(max_length=160)
    event_type = models.CharField(max_length=100, blank=True, default="")
    signature_valid = models.BooleanField(default=False)
    provider_verified = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    error = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["business", "provider", "event_key"], name="unique_commerce_gateway_event"),
        ]


class CommerceNotification(BusinessOwnedModel):
    """Persistent tenant activity that each staff member acknowledges separately."""

    EVENT_CHECKOUT_RECEIVED = "checkout_received"
    EVENT_INTAKE_RECEIVED = "intake_received"
    EVENT_PAYMENT_STARTED = "payment_started"
    EVENT_PAYMENT_CLAIM = "payment_claim"
    EVENT_PAYMENT_CONFIRMED = "payment_confirmed"
    EVENT_PAYMENT_REVIEW = "payment_review"
    EVENT_DELIVERY_CREATED = "delivery_created"
    EVENT_DELIVERY_ASSIGNED = "delivery_assigned"
    EVENT_DELIVERY_STATUS = "delivery_status"
    EVENT_DELIVERY_SWITCH = "delivery_switch"
    EVENT_DELIVERY_ISSUE = "delivery_issue"
    EVENT_DELIVERY_PROVIDER = "delivery_provider"
    EVENT_CHOICES = [
        (EVENT_CHECKOUT_RECEIVED, "Checkout received"),
        (EVENT_INTAKE_RECEIVED, "Order received"),
        (EVENT_PAYMENT_STARTED, "Payment started"),
        (EVENT_PAYMENT_CLAIM, "Payment claim submitted"),
        (EVENT_PAYMENT_CONFIRMED, "Payment confirmed"),
        (EVENT_PAYMENT_REVIEW, "Payment needs review"),
        (EVENT_DELIVERY_CREATED, "Delivery created"),
        (EVENT_DELIVERY_ASSIGNED, "Delivery assigned"),
        (EVENT_DELIVERY_STATUS, "Delivery status changed"),
        (EVENT_DELIVERY_SWITCH, "Delivery method switched"),
        (EVENT_DELIVERY_ISSUE, "Delivery issue raised"),
        (EVENT_DELIVERY_PROVIDER, "Delivery provider update"),
    ]
    ORDER_EVENTS = {EVENT_CHECKOUT_RECEIVED, EVENT_INTAKE_RECEIVED}
    PAYMENT_EVENTS = {
        EVENT_PAYMENT_STARTED,
        EVENT_PAYMENT_CLAIM,
        EVENT_PAYMENT_CONFIRMED,
        EVENT_PAYMENT_REVIEW,
    }
    DELIVERY_EVENTS = {
        EVENT_DELIVERY_CREATED, EVENT_DELIVERY_ASSIGNED, EVENT_DELIVERY_STATUS,
        EVENT_DELIVERY_SWITCH, EVENT_DELIVERY_ISSUE, EVENT_DELIVERY_PROVIDER,
    }

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    event_type = models.CharField(max_length=28, choices=EVENT_CHOICES)
    recipient_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
        related_name="targeted_commerce_notifications",
        help_text="When set, this alert is visible only to that tenant staff user.",
    )
    title = models.CharField(max_length=160)
    message = models.CharField(max_length=500, blank=True, default="")
    target_url = models.CharField(max_length=500, blank=True, default="/commerce/")
    dedupe_key = models.CharField(max_length=180, blank=True, default="")
    push_processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["business", "created_at"], name="commerce_notice_recent_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "dedupe_key"],
                condition=models.Q(recipient_user__isnull=True) & ~models.Q(dedupe_key=""),
                name="unique_commerce_notification_dedupe",
            ),
            models.UniqueConstraint(
                fields=["business", "recipient_user", "dedupe_key"],
                condition=models.Q(recipient_user__isnull=False) & ~models.Q(dedupe_key=""),
                name="unique_targeted_commerce_notification_dedupe",
            ),
        ]

    def __str__(self):
        return self.title


class CommerceNotificationRead(models.Model):
    notification = models.ForeignKey(
        CommerceNotification, on_delete=models.CASCADE, related_name="reads"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="commerce_notification_reads"
    )
    read_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "user"], name="unique_commerce_notification_read"
            ),
        ]


class CommercePushSubscription(TimestampedModel):
    """One browser/PWA Web Push subscription for one user in one tenant.

    Push endpoints are browser-generated secrets. They are never exposed in
    templates or logs; only the owning authenticated user can register/remove
    their current device subscription.
    """

    business = models.ForeignKey(
        "core.Business", on_delete=models.CASCADE, related_name="commerce_push_subscriptions"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="commerce_push_subscriptions"
    )
    endpoint = models.TextField()
    endpoint_hash = models.CharField(max_length=64)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    user_agent = models.CharField(max_length=300, blank=True, default="")
    active = models.BooleanField(default=True)
    failure_count = models.PositiveSmallIntegerField(default=0)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "user", "endpoint_hash"],
                name="unique_commerce_push_device",
            )
        ]
        indexes = [
            models.Index(fields=["business", "active"], name="commerce_push_active_idx"),
        ]


class CommercePushDelivery(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
        (STATUS_EXPIRED, "Subscription expired"),
    ]

    notification = models.ForeignKey(
        CommerceNotification, on_delete=models.CASCADE, related_name="push_deliveries"
    )
    subscription = models.ForeignKey(
        CommercePushSubscription, on_delete=models.CASCADE, related_name="deliveries"
    )
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["next_attempt_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "subscription"],
                name="unique_commerce_push_delivery",
            )
        ]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="commerce_push_pending_idx"),
        ]
