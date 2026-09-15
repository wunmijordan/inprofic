from django.contrib.auth import get_user_model
from django import forms

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
        if business:
            self.fields["default_provider_account"].queryset = DeliveryProviderAccount.objects.filter(business=business, active=True)
        else:
            self.fields["default_provider_account"].queryset = DeliveryProviderAccount.objects.none()

    def clean(self):
        cleaned = super().clean()
        provider = cleaned.get("default_provider")
        account = cleaned.get("default_provider_account")
        if provider == DeliverySettings.PROVIDER_INHOUSE and account:
            self.add_error("default_provider_account", "Provider plug-in accounts are only used for external or hybrid dispatch.")
        if provider in {DeliverySettings.PROVIDER_THIRD_PARTY, DeliverySettings.PROVIDER_HYBRID} and account and account.provider_code == DeliveryProviderAccount.PROVIDER_GLOVO and not account.active:
            self.add_error("default_provider_account", "Choose an active Glovo provider account.")
        if provider == DeliverySettings.PROVIDER_THIRD_PARTY and not account:
            self.add_error("default_provider_account", "Choose the external provider account used for delivery.")
        if provider == DeliverySettings.PROVIDER_HYBRID:
            routing = cleaned.get("hybrid_routing_policy")
            if routing in {DeliverySettings.HYBRID_ROUTE_GLOVO_FIRST, DeliverySettings.HYBRID_ROUTE_CUSTOMER, DeliverySettings.HYBRID_ROUTE_LOWEST, DeliverySettings.HYBRID_ROUTE_FASTEST} and not account:
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
        ]


class DeliveryProviderAccountForm(StyledModelForm):
    class Meta:
        model = DeliveryProviderAccount
        fields = ["name", "provider_code", "active", "sandbox", "auto_dispatch", "use_live_quotes", "base_url", "auth_endpoint", "quote_endpoint", "order_endpoint", "cancel_endpoint", "api_key", "api_secret", "address_book_id", "store_id", "webhook_secret", "tracking_base_url", "status_mapping", "metadata"]
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
        elif cleaned.get("auto_dispatch") and not (cleaned.get("api_key") or cleaned.get("api_secret")):
            self.add_error("api_key", "Auto-dispatch needs at least an API key or secret from the provider portal.")
        return cleaned


class DeliveryOriginForm(StyledModelForm):
    class Meta:
        model = DeliveryOrigin
        fields = ["name", "address", "area", "latitude", "longitude", "is_default", "active", "notes"]

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("latitude") is None) != (cleaned.get("longitude") is None):
            raise forms.ValidationError("Enter both latitude and longitude for a delivery origin.")
        return cleaned


class DeliveryRateBandForm(StyledModelForm):
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
        fields = ["name", "code", "latitude", "longitude", "rate_band", "active", "notes"]

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        if business:
            self.fields["rate_band"].queryset = DeliveryRateBand.objects.filter(business=business, active=True)

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("latitude") is None) != (cleaned.get("longitude") is None):
            raise forms.ValidationError("Enter both latitude and longitude for a delivery area.")
        return cleaned


class DeliveryDriverForm(StyledModelForm):
    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
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

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("provider") == DeliveryDriver.PROVIDER_THIRD_PARTY and cleaned.get("user"):
            self.add_error("user", "Staff rider login is only available for in-house drivers. External couriers are tracked through provider references.")
        return cleaned

    class Meta:
        model = DeliveryDriver
        fields = ["user", "name", "phone", "email", "provider", "vehicle_type", "vehicle_registration", "active"]
