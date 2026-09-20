from django import forms

from core.models import CashAccount
from inventory.models import FinishedGood
from .models import (
    CommerceIntegration,
    CommercePaymentConfiguration,
    CommerceSettings,
    StorefrontProduct,
)

CLS = "w-full rounded-md border border-[#D9CFB4] bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#8f172d]/30 focus:border-[#8f172d]"


class CommerceSettingsForm(forms.ModelForm):
    class Meta:
        model = CommerceSettings
        fields = [
            "enabled", "hosted_storefront_enabled", "order_now_link_enabled", "api_enabled",
            "connector_enabled", "storefront_headline", "storefront_hero_image",
            "storefront_hero_image_position", "public_note",
            "notifications_enabled", "notify_order_activity",
            "notify_payment_activity", "notify_delivery_activity", "notification_sound_enabled",
            "notification_sound_repeat_minutes", "notification_sound_tune", "notification_desktop_enabled", "insufficient_stock_policy",
            "checkout_reservation_minutes",
        ]
        widgets = {
            "public_note": forms.Textarea(attrs={"rows": 2}),
            "storefront_hero_image": forms.ClearableFileInput(
                attrs={"accept": "image/avif,image/gif,image/jpeg,image/png,image/webp"}
            ),
        }
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        labels = {
            "enabled": "Commerce master switch",
            "hosted_storefront_enabled": "Hosted storefront",
            "order_now_link_enabled": "Order Now link",
            "api_enabled": "Connected website access",
            "connector_enabled": "Connected sales platforms",
            "storefront_headline": "Storefront headline",
            "storefront_hero_image": "Storefront header image",
            "storefront_hero_image_position": "Image focal point",
            "notifications_enabled": "Commerce activity alerts",
            "notify_order_activity": "New checkout and order alerts",
            "notify_payment_activity": "Payment activity alerts",
            "notify_delivery_activity": "Delivery activity & exception alerts",
            "notification_sound_enabled": "Notification sound",
            "notification_sound_repeat_minutes": "Repeat sound every (minutes)",
            "notification_sound_tune": "Alert tune",
            "notification_desktop_enabled": "Browser & PWA alerts",
        }
        self.fields["public_note"].label = "Storefront supporting message"
        self.fields["storefront_hero_image"].help_text = (
            "Use a wide landscape image, ideally around 1600 × 700 pixels (maximum 8 MB)."
        )
        self.fields["notification_sound_repeat_minutes"].widget.attrs.update({"min": 0, "max": 1440})
        self.fields["notification_sound_repeat_minutes"].help_text = (
            "While unread Commerce alerts remain, repeat the in-app sound at this interval. "
            "Use 0 to sound only when a new alert first appears."
        )
        for name, f in self.fields.items():
            if name in labels: f.label = labels[name]
            if isinstance(f.widget, forms.CheckboxInput): f.widget.attrs["class"]="sr-only peer"
            else: f.widget.attrs["class"] = CLS


    def clean_notification_sound_repeat_minutes(self):
        value = self.cleaned_data.get("notification_sound_repeat_minutes")
        if value is not None and value > 1440:
            raise forms.ValidationError("Use 1,440 minutes (24 hours) or less.")
        return value

    def clean_storefront_hero_image(self):
        image = self.cleaned_data.get("storefront_hero_image")
        if image and getattr(image, "size", 0) > 8 * 1024 * 1024:
            raise forms.ValidationError("Upload a header image no larger than 8 MB.")
        return image


class StorefrontProductForm(forms.ModelForm):
    class Meta:
        model = StorefrontProduct
        fields = ["published", "public_name", "description", "image", "allow_stock_order", "allow_online_order", "allow_distribution_order", "min_quantity", "preorder_min_quantity", "distribution_min_quantity", "max_quantity", "preorder_lead_time"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "image": forms.ClearableFileInput(attrs={"accept": "image/avif,image/gif,image/jpeg,image/png,image/webp"}),
        }
    def __init__(self,*args,business=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["allow_stock_order"].label = "Offer Physical Store / direct mode"
        self.fields["allow_online_order"].label = "Offer Online mode"
        self.fields["allow_distribution_order"].label = "Offer Distribution / bulk mode"
        self.fields["image"].label = "Storefront product image"
        self.fields["image"].help_text = "Upload AVIF, GIF, JPEG, PNG or WebP (maximum 5 MB). The image is also available to connected sales channels."
        self.fields["min_quantity"].label = "Physical Store / direct minimum"
        self.fields["preorder_min_quantity"].label = "Online minimum"
        self.fields["distribution_min_quantity"].label = "Distribution / bulk minimum"
        self.fields["published"].help_text = (
            "Publish this finished/procured good as its own customer-buyable catalogue item. "
            "Raw and packaging materials used inside composed products are not published automatically."
        )
        self.fields["preorder_min_quantity"].help_text = "Minimum standard customer portions for the Online channel."
        self.fields["distribution_min_quantity"].help_text = (
            "Minimum standard customer portions when no Bulk Pack is selected. Each configured Bulk Pack can have its own minimum."
        )
        if business and not business.uses_production:
            self.fields.pop("preorder_lead_time")
        for f in self.fields.values():
            if isinstance(f.widget, forms.CheckboxInput): f.widget.attrs["class"]="h-4 w-4 accent-[#8f172d]"
            else: f.widget.attrs["class"] = CLS

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if image and getattr(image, "size", 0) > 5 * 1024 * 1024:
            raise forms.ValidationError("Upload an image no larger than 5 MB.")
        return image


class CommerceIntegrationForm(forms.ModelForm):
    class Meta:
        model = CommerceIntegration
        fields = ["name", "integration_type", "allowed_origin", "active"]
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["active"].label = "Connection enabled"
        self.fields["active"].help_text = "Turn this off to pause this connection without deleting its settings."
        for f in self.fields.values():
            if isinstance(f.widget, forms.CheckboxInput): f.widget.attrs["class"]="h-4 w-4 accent-[#8f172d]"
            else: f.widget.attrs["class"] = CLS


class CommercePaymentConfigurationForm(forms.ModelForm):
    SECRET_FIELDS = ("paystack_secret_key", "monnify_api_key", "monnify_secret_key")

    class Meta:
        model = CommercePaymentConfiguration
        fields = [
            "currency",
            "paystack_enabled", "paystack_secret_key", "paystack_account",
            "transfer_enabled", "bank_name", "bank_account_name", "bank_account_number", "bank_instructions", "transfer_account",
            "bank_transfer_enabled", "bank_transfer_provider", "monnify_transfer_bank_code", "bank_cash_account",
            "paystack_terminal_enabled", "paystack_terminal_id",
            "paystack_terminal_customer_email", "paystack_terminal_account",
            "monnify_enabled", "monnify_api_key", "monnify_secret_key",
            "monnify_contract_code", "monnify_base_url", "monnify_account",
            "cash_enabled", "cash_instructions", "cash_account",
        ]
        widgets = {
            "paystack_secret_key": forms.PasswordInput(render_value=False),
            "monnify_api_key": forms.PasswordInput(render_value=False),
            "monnify_secret_key": forms.PasswordInput(render_value=False),
            "bank_instructions": forms.Textarea(attrs={"rows": 2}),
            "cash_instructions": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["paystack_enabled"].label = "Paystack"
        self.fields["monnify_enabled"].label = "Monnify"
        self.fields["transfer_enabled"].label = "Transfer (no gateway)"
        self.fields["bank_transfer_enabled"].label = "Instant bank transfer (gateway)"
        self.fields["paystack_terminal_enabled"].label = "Paystack Terminal"
        self.fields["cash_enabled"].label = "Cash at the in-premise POS"
        accounts = CashAccount.raw_objects.filter(business=business, active=True).order_by("name")
        for name in ("paystack_account", "monnify_account", "transfer_account", "bank_cash_account", "paystack_terminal_account", "cash_account"):
            self.fields[name].queryset = accounts
            self.fields[name].required = False
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "sr-only peer"
            else:
                field.widget.attrs["class"] = CLS
        for name in self.SECRET_FIELDS:
            self.fields[name].required = False
            if self.instance.pk and getattr(self.instance, name):
                self.fields[name].help_text = "A credential is saved. Leave blank to keep it unchanged."

    def clean_currency(self):
        value = (self.cleaned_data["currency"] or "").strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise forms.ValidationError("Use a three-letter currency code such as NGN.")
        return value

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk:
            for name in self.SECRET_FIELDS:
                if not cleaned.get(name):
                    cleaned[name] = getattr(self.instance, name)
        if cleaned.get("paystack_enabled"):
            if not cleaned.get("paystack_secret_key"):
                self.add_error("paystack_secret_key", "Add the Paystack secret key before enabling Paystack.")
            if not cleaned.get("paystack_account"):
                self.add_error("paystack_account", "Choose the INPROFIC settlement account before enabling Paystack.")
        monnify_required = ("monnify_api_key", "monnify_secret_key", "monnify_contract_code")
        if cleaned.get("monnify_enabled"):
            for name in monnify_required:
                if not cleaned.get(name):
                    self.add_error(name, "This information is required when Monnify is enabled.")
            if not cleaned.get("monnify_account"):
                self.add_error("monnify_account", "Choose the INPROFIC settlement account before enabling Monnify.")
        if cleaned.get("transfer_enabled"):
            for field_name, message in (
                ("bank_name", "Enter the bank name customers should transfer to."),
                ("bank_account_name", "Enter the account name customers should see."),
                ("bank_account_number", "Enter the account number customers should transfer to."),
            ):
                if not (cleaned.get(field_name) or "").strip():
                    self.add_error(field_name, message)
            if not cleaned.get("transfer_account"):
                self.add_error("transfer_account", "Choose the INPROFIC Finance account that receives direct transfers.")
        if cleaned.get("bank_transfer_enabled"):
            provider = cleaned.get("bank_transfer_provider")
            if provider == CommercePaymentConfiguration.BANK_TRANSFER_PROVIDER_PAYSTACK:
                if not cleaned.get("paystack_enabled") or not cleaned.get("paystack_secret_key"):
                    self.add_error(
                        "bank_transfer_provider",
                        "Paystack transfer requires Paystack to be enabled with a saved secret key.",
                    )
            elif provider == CommercePaymentConfiguration.BANK_TRANSFER_PROVIDER_MONNIFY:
                if not cleaned.get("monnify_enabled") or any(not cleaned.get(name) for name in monnify_required):
                    self.add_error(
                        "bank_transfer_provider",
                        "Monnify transfer requires Monnify to be enabled with its API key, secret key and contract code.",
                    )
                if not (cleaned.get("monnify_transfer_bank_code") or "").strip():
                    self.add_error(
                        "monnify_transfer_bank_code",
                        "Enter the Monnify bank code used to issue the temporary transfer account.",
                    )
            else:
                self.add_error("bank_transfer_provider", "Choose the payment provider that will confirm bank transfers.")
            if not cleaned.get("bank_cash_account"):
                self.add_error("bank_cash_account", "Choose the INPROFIC bank account that receives verified transfers.")
        if cleaned.get("paystack_terminal_enabled"):
            if not cleaned.get("paystack_enabled") or not cleaned.get("paystack_secret_key"):
                self.add_error("paystack_terminal_enabled", "Paystack Terminal requires Paystack to be enabled with a saved secret key.")
            if not cleaned.get("paystack_terminal_id"):
                self.add_error("paystack_terminal_id", "Enter the Paystack Terminal ID.")
            if not cleaned.get("paystack_terminal_customer_email"):
                self.add_error("paystack_terminal_customer_email", "Add a fallback email for walk-in Terminal payment requests.")
            if not cleaned.get("paystack_terminal_account"):
                self.add_error("paystack_terminal_account", "Choose the Finance account that receives Terminal card payments.")
        if cleaned.get("cash_enabled") and not cleaned.get("cash_account"):
            self.add_error("cash_account", "Choose the INPROFIC cash account before enabling cash.")
        return cleaned
