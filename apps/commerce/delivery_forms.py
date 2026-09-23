from django.contrib.auth import get_user_model
from django import forms

from accounts.platform_integrations import glovo_platform_enabled
from .models import DeliveryArea, DeliveryDriver, DeliveryOrigin, DeliveryProviderAccount, DeliveryRateBand, DeliverySettings

INPUT = "w-full rounded-md border border-[#D9CFB4] bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#8f172d]/30 focus:border-[#8f172d]"


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                continue
            field.widget.attrs["class"] = INPUT


class DeliverySettingsForm(StyledModelForm):
    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        glovo_enabled = glovo_platform_enabled()
        if business:
            accounts = DeliveryProviderAccount.objects.filter(business=business, active=True)
            if not glovo_enabled:
                accounts = accounts.exclude(provider_code=DeliveryProviderAccount.PROVIDER_GLOVO)
            self.fields["default_provider_account"].queryset = accounts
        else:
            self.fields["default_provider_account"].queryset = DeliveryProviderAccount.objects.none()
        hints = {
            "enabled": "Turn this on only after the delivery base and pricing are ready. It immediately exposes delivery on eligible hosted, POS and API checkout surfaces.",
            "default_provider": "Choose who normally carries new deliveries. Hybrid keeps in-house and the selected partner available under the routing policy below.",
            "default_provider_account": "Required for external-provider delivery and for Hybrid policies that compare or offer both methods.",
            "hybrid_routing_policy": "Controls whether the customer, dispatcher, or INPROFIC selects the delivery method before payment.",
            "hybrid_switch_policy": "Controls method changes after payment. The customer is never charged more after checkout.",
            "customer_switch_policy_note": "Optional plain-language note shown to customers alongside the standard switch policy.",
            "quote_valid_minutes": "A basket or destination change always requires a fresh quote, even within this time.",
            "customer_tracking_enabled": "Lets customers open the secure delivery timeline from their order or account page.",
            "require_proof_of_delivery": "Use this when a rider or dispatcher must record delivery evidence before completion.",
            "rider_alert_sound_enabled": "Admin-controlled persistent foreground sound for rider-only notification sessions.",
            "rider_alert_sound_repeat_minutes": "Repeat while the rider still has unread assigned-delivery activity. Use 0 for new-alert sound only.",
            "rider_alert_sound_tune": "Choose a synthesized rider alert or bundled audio chime. Background Web Push uses the device/OS notification sound.",
        }
        for name, hint in hints.items():
            self.fields[name].help_text = hint

    def clean(self):
        cleaned = super().clean()
        provider = cleaned.get("default_provider")
        account = cleaned.get("default_provider_account")
        if provider == DeliverySettings.PROVIDER_INHOUSE and account:
            self.add_error("default_provider_account", "Provider plug-in accounts are only used for external or hybrid dispatch.")
        if provider in {DeliverySettings.PROVIDER_THIRD_PARTY, DeliverySettings.PROVIDER_HYBRID} and account and not account.is_configured_for_dispatch:
            self.add_error("default_provider_account", "Choose an active, configured delivery provider account.")
        if provider == DeliverySettings.PROVIDER_THIRD_PARTY and not account:
            self.add_error("default_provider_account", "Choose the external provider account used for delivery.")
        rider_repeat = cleaned.get("rider_alert_sound_repeat_minutes")
        if rider_repeat is not None and rider_repeat > 1440:
            self.add_error("rider_alert_sound_repeat_minutes", "Use 1,440 minutes (24 hours) or less.")
        if provider == DeliverySettings.PROVIDER_HYBRID:
            routing = cleaned.get("hybrid_routing_policy")
            if routing in {DeliverySettings.HYBRID_ROUTE_PROVIDER_FIRST, DeliverySettings.HYBRID_ROUTE_CUSTOMER, DeliverySettings.HYBRID_ROUTE_LOWEST, DeliverySettings.HYBRID_ROUTE_FASTEST} and not account:
                self.add_error("default_provider_account", "Hybrid routing needs an external provider account so both delivery methods can be offered.")
            if routing == DeliverySettings.HYBRID_ROUTE_DISPATCHER and cleaned.get("hybrid_switch_policy") == DeliverySettings.SWITCH_LOCKED:
                self.add_error("hybrid_switch_policy", "Dispatcher-choice Hybrid needs a switching policy that permits the dispatcher to choose the actual delivery method after payment.")
        return cleaned

    class Meta:
        model = DeliverySettings
        fields = [
            "enabled", "default_provider", "default_provider_account",
            "hybrid_routing_policy", "hybrid_switch_policy", "customer_switch_policy_note",
            "quote_valid_minutes", "customer_tracking_enabled", "require_proof_of_delivery",
            "rider_alert_sound_enabled", "rider_alert_sound_repeat_minutes", "rider_alert_sound_tune",
        ]
        widgets = {
            "rider_alert_sound_repeat_minutes": forms.NumberInput(attrs={"min": 0, "max": 1440}),
        }


class DeliveryProviderAccountForm(StyledModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        glovo_enabled = glovo_platform_enabled()
        if not glovo_enabled:
            self.fields["provider_code"].choices = [
                choice for choice in self.fields["provider_code"].choices
                if choice[0] != DeliveryProviderAccount.PROVIDER_GLOVO
            ]
            if not self.instance.pk:
                self.fields["provider_code"].initial = DeliveryProviderAccount.PROVIDER_GENERIC
                self.fields["name"].initial = ""
                self.initial["provider_code"] = DeliveryProviderAccount.PROVIDER_GENERIC
                self.initial["name"] = ""
        if not self.instance.pk:
            # New provider connections remain dormant until their configuration is complete.
            self.fields["active"].initial = False
            self.initial["active"] = False
            self.fields["use_live_quotes"].initial = False
            self.initial["use_live_quotes"] = False
        self.fields["provider_code"].label = "Provider type"
        self.fields["name"].label = "Partner name"
        self.fields["base_url"].label = "API base URL"
        self.fields["order_endpoint"].label = "Dispatch endpoint"
        self.fields["cancel_endpoint"].label = "Cancellation endpoint"
        self.fields["tracking_base_url"].label = "Tracking base URL"
        self.fields["api_key"].label = "API key / client ID"
        self.fields["api_secret"].label = "API secret"
        hints = {
            "name": "Use a recognizable internal name for this courier connection.",
            "provider_code": "Custom delivery partner keeps INPROFIC in control of quoting, routing and tracking. Built-in adapters may add provider-specific automation.",
            "sandbox": "Keep this on while testing provider credentials. Turn it off only when the provider has issued production access.",
            "auto_dispatch": "Optional for custom partners: send a paid delivery to the configured adapter endpoint automatically. Failed sends remain visible for manual dispatch.",
            "use_live_quotes": "Dedicated built-in adapters may use provider live pricing. Custom partners use INPROFIC price bands so checkout remains provider-neutral.",
            "base_url": "Optional for manual partners. For API automation, enter the base URL of the provider or merchant-owned adapter service.",
            "health_endpoint": "Optional non-mutating GET endpoint used by the Test connection action for custom partners.",
            "auth_endpoint": "Authentication endpoint supplied by the delivery provider, when required.",
            "quote_endpoint": "Live-quote endpoint supplied by the delivery provider, when required.",
            "order_endpoint": "For automatic custom-provider dispatch, point this to an endpoint accepting the INPROFIC Delivery Adapter v1 JSON payload.",
            "cancel_endpoint": "Optional cancellation endpoint. You may use {external_reference} in the path.",
            "address_book_id": "Optional provider pickup-location identifier when required by the connector.",
            "store_id": "Optional partner merchant/store/location identifier required by the courier's API.",
            "webhook_secret": "Optional for custom partners, but required for status callbacks. Keep it private; the account-specific callback URL appears after saving.",
            "tracking_base_url": "Optional public tracking URL or template. Use {external_reference} where the courier reference belongs; INPROFIC uses it when the dispatch response does not return a tracking URL.",
            "status_mapping": "Optional JSON mapping from partner statuses to INPROFIC statuses, for example {\"completed\": \"delivered\"}.",
            "metadata": "Optional advanced adapter JSON. Custom adapters may use auth_type (api_key, bearer, basic, none), api_key_header, api_secret_header and headers.",
        }
        if glovo_enabled:
            hints.update({
                "name": "Use a recognizable internal name, such as Glovo Lagos Production or Manual Courier Desk.",
                "provider_code": "Choose Generic for a manually coordinated courier or Glovo for the built-in LaaS v2 connection.",
                "auth_endpoint": "OAuth token endpoint. Glovo LaaS v2 uses /oauth/token.",
                "quote_endpoint": "Glovo LaaS v2 uses /v2/laas/quotes for live delivery quotes.",
                "address_book_id": "For Glovo, this is the pickup location ID created in the LaaS Address Book—not the written office address.",
            })
        for name, hint in hints.items():
            self.fields[name].help_text = hint
        glovo_only_fields = ("auth_endpoint", "quote_endpoint", "use_live_quotes", "address_book_id")
        for name in glovo_only_fields:
            if name in self.fields:
                self.fields[name].widget.attrs["data-provider-scope"] = "glovo"
        if not glovo_enabled:
            for name in glovo_only_fields:
                self.fields.pop(name, None)

    class Meta:
        model = DeliveryProviderAccount
        fields = ["name", "provider_code", "active", "sandbox", "auto_dispatch", "use_live_quotes", "base_url", "health_endpoint", "auth_endpoint", "quote_endpoint", "order_endpoint", "cancel_endpoint", "api_key", "api_secret", "address_book_id", "store_id", "webhook_secret", "tracking_base_url", "status_mapping", "metadata"]
        widgets = {
            "api_key": forms.PasswordInput(render_value=False, attrs={"placeholder": "Leave blank to keep existing key"}),
            "api_secret": forms.PasswordInput(render_value=False, attrs={"placeholder": "Leave blank to keep existing secret"}),
            "webhook_secret": forms.PasswordInput(render_value=False, attrs={"placeholder": "Leave blank to keep existing webhook secret"}),
            "status_mapping": forms.Textarea(attrs={"rows": 4}),
            "metadata": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_api_key(self):
        value = self.cleaned_data.get("api_key")
        return value or (self.instance.api_key if self.instance and self.instance.pk else "")

    def clean_api_secret(self):
        value = self.cleaned_data.get("api_secret")
        return value or (self.instance.api_secret if self.instance and self.instance.pk else "")

    def clean_webhook_secret(self):
        value = self.cleaned_data.get("webhook_secret")
        return value or (self.instance.webhook_secret if self.instance and self.instance.pk else "")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("auto_dispatch") and not cleaned.get("order_endpoint"):
            self.add_error("order_endpoint", "Auto-dispatch needs the provider order endpoint.")
        if cleaned.get("provider_code") == DeliveryProviderAccount.PROVIDER_GLOVO:
            if not glovo_platform_enabled():
                self.add_error("provider_code", "This delivery provider is not currently available.")
                return cleaned
            if cleaned.get("active"):
                required = {
                    "base_url": "Add the API base URL issued during onboarding before activating this provider.",
                    "auth_endpoint": "Add the authentication endpoint before activating this provider.",
                    "quote_endpoint": "Add the live-quote endpoint before activating this provider.",
                    "order_endpoint": "Add the dispatch endpoint before activating this provider.",
                    "api_key": "Add the client ID issued during onboarding before activating this provider.",
                    "api_secret": "Add the client secret issued during onboarding before activating this provider.",
                    "address_book_id": "Add the pickup Address Book ID before activating this provider.",
                    "webhook_secret": "Add a strong webhook secret before activating this provider.",
                }
                for field_name, message in required.items():
                    if not cleaned.get(field_name):
                        self.add_error(field_name, message)
                if not cleaned.get("use_live_quotes"):
                    self.add_error("use_live_quotes", "Keep live quotes enabled for this provider before activating it.")
            if cleaned.get("auto_dispatch") and not cleaned.get("use_live_quotes"):
                self.add_error("use_live_quotes", "Glovo LaaS auto-dispatch requires the live provider quote ID.")
            if cleaned.get("auto_dispatch") or cleaned.get("use_live_quotes"):
                if not cleaned.get("base_url"):
                    self.add_error("base_url", "Enter the Glovo LaaS API base URL issued during onboarding.")
                if not cleaned.get("api_key"):
                    self.add_error("api_key", "Enter the Glovo client ID issued during onboarding.")
                if not cleaned.get("api_secret"):
                    self.add_error("api_secret", "Enter the Glovo client secret issued during onboarding.")
            if cleaned.get("use_live_quotes") and not cleaned.get("address_book_id"):
                self.add_error("address_book_id", "Live Glovo quotes require the LaaS Address Book pickup ID.")
            if cleaned.get("use_live_quotes") and not cleaned.get("quote_endpoint"):
                self.add_error("quote_endpoint", "Live Glovo quotes require the quote endpoint.")
        else:
            if cleaned.get("use_live_quotes"):
                self.add_error("use_live_quotes", "Custom delivery partners use INPROFIC price bands unless a dedicated live-quote adapter is installed.")
            if cleaned.get("auto_dispatch"):
                metadata = cleaned.get("metadata") if isinstance(cleaned.get("metadata"), dict) else {}
                auth_type = str(metadata.get("auth_type") or "api_key").strip().lower()
                has_auth = bool(
                    cleaned.get("api_key") or cleaned.get("api_secret") or metadata.get("headers")
                    or auth_type == "none"
                )
                if not has_auth:
                    self.add_error(
                        "api_key",
                        "Automatic dispatch needs provider credentials, configured static headers, or metadata auth_type set to none.",
                    )
        return cleaned


class DeliveryOriginForm(StyledModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].label = "Delivery / office base name"
        self.fields["name"].widget.attrs["placeholder"] = "e.g. Lekki dispatch base"
        self.fields["address"].label = "Base address"
        self.fields["address"].widget.attrs["placeholder"] = "Street address used for dispatch"
        self.fields["area"].label = "Town / service area"
        self.fields["latitude"].label = "Base latitude"
        self.fields["longitude"].label = "Base longitude"
        self.fields["address"].help_text = "This is the pickup address shared with dispatchers and supported delivery providers."
        self.fields["area"].help_text = "A short locality label for staff, such as Ikeja or Lekki Phase 1."
        self.fields["latitude"].help_text = "Place the pin on the map below; accurate base coordinates are required for distance pricing."
        self.fields["longitude"].help_text = "Filled together with latitude by the map picker."
        self.fields["is_default"].help_text = "New quotes start from the default base. If none is marked, INPROFIC uses the first active base."
        self.fields["active"].help_text = "Inactive bases remain in history but cannot be used for new delivery quotes."

    class Meta:
        model = DeliveryOrigin
        fields = ["name", "address", "area", "latitude", "longitude", "is_default", "active", "notes"]

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("latitude") is None) != (cleaned.get("longitude") is None):
            raise forms.ValidationError("Enter both latitude and longitude for a delivery origin.")
        return cleaned


class DeliveryRateBandForm(StyledModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["min_distance_km"].label = "Fallback minimum distance (km)"
        self.fields["max_distance_km"].label = "Fallback maximum distance (km)"
        hints = {
            "name": "Use a recognizable pricing name, such as Nearby Zone Pricing.",
            "min_distance_km": "Used only for map-only/direct-coordinate quotes that do not select a named destination area. Named areas use their mapped centre and radius for coverage.",
            "max_distance_km": "Used only for map-only/direct-coordinate quotes. A named area's radius (plus optional diagonal extensions) is its authoritative maximum coverage boundary.",
            "base_fee": "Fixed amount charged whenever this pricing band applies.",
            "per_km_fee": "Added for every kilometre from the delivery base to the customer's precise validated destination. Enter 0 for a flat fee.",
            "minimum_order": "Basket subtotal required before delivery can be quoted in this band. Enter 0 for no minimum.",
            "eta_min_minutes": "Best-case customer-facing delivery estimate.",
            "eta_max_minutes": "Latest customer-facing delivery estimate under normal conditions.",
            "sort_order": "Lower numbers win first when active bands overlap. Avoid overlaps where possible.",
            "active": "Inactive bands remain in history and are ignored for new quotes.",
        }
        for name, hint in hints.items():
            self.fields[name].help_text = hint

    class Meta:
        model = DeliveryRateBand
        fields = [
            "name", "min_distance_km", "max_distance_km", "base_fee", "per_km_fee",
            "minimum_order", "eta_min_minutes", "eta_max_minutes", "sort_order", "active",
        ]

    def clean(self):
        cleaned = super().clean()
        minimum = cleaned.get("min_distance_km")
        maximum = cleaned.get("max_distance_km")
        if minimum is not None and minimum < 0:
            self.add_error("min_distance_km", "Distance cannot be negative.")
        if maximum is not None and minimum is not None and maximum < minimum:
            self.add_error("max_distance_km", "Maximum distance must be at least the minimum distance.")
        if cleaned.get("base_fee") is not None and cleaned["base_fee"] < 0:
            self.add_error("base_fee", "Fee cannot be negative.")
        if cleaned.get("per_km_fee") is not None and cleaned["per_km_fee"] < 0:
            self.add_error("per_km_fee", "Fee cannot be negative.")
        if cleaned.get("minimum_order") is not None and cleaned["minimum_order"] < 0:
            self.add_error("minimum_order", "Minimum order cannot be negative.")
        eta_min, eta_max = cleaned.get("eta_min_minutes"), cleaned.get("eta_max_minutes")
        if eta_min is not None and eta_max is not None and eta_max < eta_min:
            self.add_error("eta_max_minutes", "Maximum ETA must be at least the minimum ETA.")
        return cleaned


class DeliveryAreaForm(StyledModelForm):
    class Meta:
        model = DeliveryArea
        fields = [
            "name", "code", "latitude", "longitude", "radius_km",
            "extension_ne_km", "extension_se_km", "extension_sw_km", "extension_nw_km",
            "rate_band", "active", "notes",
        ]

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        if business:
            self.fields["rate_band"].queryset = DeliveryRateBand.objects.filter(business=business, active=True)
        else:
            self.fields["rate_band"].queryset = DeliveryRateBand.objects.none()
        self.fields["name"].label = "Delivery destination / zone name"
        self.fields["name"].widget.attrs["placeholder"] = "e.g. Victoria Island"
        self.fields["latitude"].label = "Destination centre latitude"
        self.fields["longitude"].label = "Destination centre longitude"
        self.fields["radius_km"].label = "Coverage radius (km)"
        extension_labels = {
            "extension_ne_km": "Extra reach north-east (km)",
            "extension_se_km": "Extra reach south-east (km)",
            "extension_sw_km": "Extra reach south-west (km)",
            "extension_nw_km": "Extra reach north-west (km)",
        }
        for name, label in extension_labels.items():
            self.fields[name].label = label
            self.fields[name].help_text = "Optional distance beyond the main radius in this diagonal direction. Leave at 0 for a circle."
        self.fields["rate_band"].label = "Delivery price band"
        self.fields["code"].help_text = "Optional stable short code for website or staff reference, for example victoria-island."
        self.fields["latitude"].help_text = "Place the pin at the centre of this zone. The coverage circle is measured from this point."
        self.fields["longitude"].help_text = "Filled together with latitude by the map picker."
        self.fields["radius_km"].help_text = "Drag the radius handle on the map or enter the maximum distance from the zone centre."
        self.fields["rate_band"].help_text = "Links this named destination to its customer-facing fee, minimum order and ETA rules."
        self.fields["active"].help_text = "Only active destinations appear on hosted, POS and API storefronts."

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("latitude") is None) != (cleaned.get("longitude") is None):
            raise forms.ValidationError("Enter both latitude and longitude for a delivery area.")
        if cleaned.get("active") and (cleaned.get("latitude") is None or cleaned.get("longitude") is None):
            raise forms.ValidationError("An active delivery destination needs a mapped centre point.")
        if cleaned.get("active") and not cleaned.get("rate_band"):
            self.add_error("rate_band", "Choose the pricing band used for this active delivery destination.")
        return cleaned


class DeliveryDriverForm(StyledModelForm):
    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        User = get_user_model()
        if business:
            self.fields["user"].queryset = User.objects.filter(
                business_memberships__business=business,
                business_memberships__active=True,
                is_active=True,
            ).distinct().order_by("fullname", "username")
        else:
            self.fields["user"].queryset = User.objects.none()
        self.fields["user"].label = "Rider login (optional)"
        self.fields["user"].help_text = "Link an in-house rider to a tenant staff login so they can use the lightweight My Deliveries workspace."
        self.fields["name"].label = "Rider / courier name"
        self.fields["phone"].label = "Contact phone"
        self.fields["email"].label = "Contact email"
        self.fields["provider"].help_text = "In-house riders can use My Deliveries. Third-party entries are manual courier contacts, not provider API accounts."
        self.fields["vehicle_type"].help_text = "Optional operational detail, such as Bike, Car or Van."
        self.fields["vehicle_registration"].help_text = "Optional plate or fleet identifier shown to dispatch staff."
        self.fields["active"].help_text = "Inactive riders remain on historical deliveries but cannot receive new assignments."

    def clean(self):
        cleaned = super().clean()
        user = cleaned.get("user")
        if cleaned.get("provider") == DeliveryDriver.PROVIDER_THIRD_PARTY and user:
            self.add_error("user", "Staff rider login is only available for in-house drivers. External couriers are tracked through provider references.")
        if self.business and user:
            existing = DeliveryDriver.objects.filter(business=self.business, user=user)
            if self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                self.add_error("user", "This staff login is already linked to another rider profile.")
        return cleaned

    class Meta:
        model = DeliveryDriver
        fields = ["user", "name", "phone", "email", "provider", "vehicle_type", "vehicle_registration", "active"]
