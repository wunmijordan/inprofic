from django import forms

from .opening_hours import DEFAULT_CLOSE, DEFAULT_OPEN, WEEKDAYS

from core.models import CashAccount
from inventory.models import FinishedGood
from .models import (
    CommerceIntegration,
    CommercePaymentConfiguration,
    CommerceDirectTransferRoute,
    CommerceSettings,
    StorefrontProduct,
)

CLS = "w-full rounded-md border border-[#D9CFB4] bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#8f172d]/30 focus:border-[#8f172d]"


class CommerceSettingsForm(forms.ModelForm):
    class Meta:
        model = CommerceSettings
        fields = [
            "enabled", "hosted_storefront_enabled", "order_now_link_enabled", "api_enabled",
            "connector_enabled", "storefront_headline", "basket_dock_heading", "basket_dock_subheading", "storefront_hero_image",
            "storefront_hero_image_position", "public_note",
            "notifications_enabled", "notify_order_activity",
            "notify_payment_activity", "notify_delivery_activity", "notification_sound_enabled",
            "notification_sound_repeat_minutes", "notification_sound_tune", "notification_desktop_enabled", "insufficient_stock_policy",
            "checkout_reservation_minutes",
            "opening_hours_enabled", "closed_scheduling_enabled", "closed_scheduling_window_minutes",
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
            "basket_dock_heading": "Basket dock heading",
            "basket_dock_subheading": "Basket dock subheading",
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
        labels.update({
            "opening_hours_enabled": "Show opening hours",
            "closed_scheduling_enabled": "Allow scheduling for the next opening day",
            "closed_scheduling_window_minutes": "Earliest scheduled time after opening (minutes)",
        })
        self.fields["closed_scheduling_window_minutes"].widget.attrs.update({"min": 0, "max": 1440})
        self.fields["closed_scheduling_window_minutes"].help_text = (
            "Scheduled and preferred times cannot be earlier than this many minutes after opening, and cannot be later "
            "than closing. For example, with 60 and a 9:00 AM opening, the earliest scheduled time is 10:00 AM. "
            "Use 0 to allow scheduling from opening time."
        )
        saved_hours = (self.instance.opening_hours or {}) if self.instance else {}
        self.hours_rows = []
        for key, short, day_name in WEEKDAYS:
            entry = saved_hours.get(key) or {}
            on = forms.BooleanField(required=False, initial=bool(entry), label=f"{day_name} open")
            opens = forms.TimeField(required=False, initial=entry.get("open") or DEFAULT_OPEN,
                                    widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"), label=f"{day_name} opens")
            closes = forms.TimeField(required=False, initial=entry.get("close") or DEFAULT_CLOSE,
                                     widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"), label=f"{day_name} closes")
            self.fields[f"hours_{short}_on"], self.fields[f"hours_{short}_open"], self.fields[f"hours_{short}_close"] = on, opens, closes
        for name, f in self.fields.items():
            if name in labels: f.label = labels[name]
            if isinstance(f.widget, forms.CheckboxInput): f.widget.attrs["class"]="sr-only peer"
            else: f.widget.attrs["class"] = CLS
        for key, short, day_name in WEEKDAYS:
            self.fields[f"hours_{short}_on"].widget.attrs["class"] = "h-4 w-4 rounded border-stone-300 accent-[#8f172d]"
            self.hours_rows.append({
                "name": day_name,
                "on": self[f"hours_{short}_on"], "open": self[f"hours_{short}_open"], "close": self[f"hours_{short}_close"],
            })

    def clean(self):
        cleaned = super().clean()
        hours = {}
        for key, short, day_name in WEEKDAYS:
            if not cleaned.get(f"hours_{short}_on"):
                continue
            opens, closes = cleaned.get(f"hours_{short}_open"), cleaned.get(f"hours_{short}_close")
            if not opens or not closes:
                self.add_error(f"hours_{short}_open", f"{day_name}: set both an opening and a closing time.")
                continue
            if opens == closes:
                self.add_error(f"hours_{short}_close", f"{day_name}: opening and closing time cannot be the same.")
                continue
            hours[key] = {"open": opens.strftime("%H:%M"), "close": closes.strftime("%H:%M")}
        if cleaned.get("opening_hours_enabled") and not hours and not self.errors:
            self.add_error("opening_hours_enabled", "Tick at least one day as open, or turn opening hours off.")
        self._cleaned_hours = hours
        return cleaned

    def save(self, commit=True):
        self.instance.opening_hours = getattr(self, "_cleaned_hours", self.instance.opening_hours or {})
        return super().save(commit=commit)


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
        fields = ["published", "public_name", "description", "image", "allow_stock_order", "allow_online_order", "allow_distribution_order", "min_quantity", "preorder_min_quantity", "distribution_min_quantity", "max_quantity", "preorder_lead_time", "estimated_ready_minutes"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "image": forms.ClearableFileInput(attrs={"accept": "image/avif,image/gif,image/jpeg,image/png,image/webp"}),
        }
    def __init__(self,*args,business=None,**kwargs):
        super().__init__(*args,**kwargs)
        active_bulk_packs = []
        if self.instance.pk and self.instance.finished_good_id:
            active_bulk_packs = list(
                self.instance.finished_good.bulk_pack_profiles.filter(active=True).order_by("sort_order", "name", "id")
            )
        self.fields["allow_stock_order"].label = "Offer Physical Store / direct mode"
        self.fields["allow_online_order"].label = "Offer Online mode"
        self.fields["allow_distribution_order"].label = "Offer Distribution / bulk mode"
        self.fields["image"].label = "Storefront product image"
        self.fields["image"].help_text = "Upload AVIF, GIF, JPEG, PNG or WebP (maximum 5 MB). The image is also available to connected sales channels."
        self.fields["min_quantity"].label = "Physical Store / direct minimum"
        self.fields["preorder_min_quantity"].label = "Online minimum"
        self.fields["distribution_min_quantity"].label = "Distribution / bulk minimum"
        self.fields["estimated_ready_minutes"].label = "Typical made-to-order readiness (minutes)"
        self.fields["estimated_ready_minutes"].help_text = "Used for checkout readiness estimates and the earliest valid delivery time; it is not shown as a catalogue-card tag."
        self.fields["published"].help_text = (
            "Publish this finished/procured good as its own customer-buyable catalogue item. "
            "Raw and packaging materials used inside composed products are not published automatically."
        )
        self.fields["preorder_min_quantity"].help_text = "Minimum standard customer portions for the Online channel."
        if active_bulk_packs:
            derived_minimum = min(pack.min_order_quantity for pack in active_bulk_packs)
            self.fields["distribution_min_quantity"].disabled = True
            self.fields["distribution_min_quantity"].initial = derived_minimum
            self.initial["distribution_min_quantity"] = derived_minimum
            self.fields["distribution_min_quantity"].label = "Distribution / bulk minimum (from bulk options)"
            self.fields["distribution_min_quantity"].help_text = (
                "Read-only because this product has active Bulk / Distribution options. "
                "Each bulk option keeps its own minimum; this field shows the lowest configured bulk minimum as a summary."
            )
        else:
            self.fields["distribution_min_quantity"].help_text = (
                "Editable when Distribution / bulk uses the explicit Distribution channel price. "
                "If bulk options are added to the product, their own minimums become authoritative."
            )
        if business and not business.uses_production:
            self.fields.pop("preorder_lead_time")
        for f in self.fields.values():
            if isinstance(f.widget, forms.CheckboxInput): f.widget.attrs["class"]="h-4 w-4 accent-[#8f172d]"
            else: f.widget.attrs["class"] = CLS

    def clean_distribution_min_quantity(self):
        if self.instance.pk and self.instance.finished_good_id:
            bulk_packs = list(
                self.instance.finished_good.bulk_pack_profiles.filter(active=True).only("min_order_quantity")
            )
            if bulk_packs:
                return min(pack.min_order_quantity for pack in bulk_packs)
        return self.cleaned_data.get("distribution_min_quantity")

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
    DEFAULT_CURRENCY = "NGN"
    MONNIFY_DEFAULT_BASE_URL = "https://api.monnify.com"
    CONDITIONAL_FIELDS = (
        "paystack_secret_key", "paystack_account",
        "bank_transfer_provider", "monnify_transfer_bank_code", "bank_cash_account",
        "paystack_terminal_id", "paystack_terminal_customer_email", "paystack_terminal_account",
        "monnify_api_key", "monnify_secret_key", "monnify_contract_code", "monnify_base_url", "monnify_account",
        "cash_instructions", "cash_account",
    )
    PRESERVE_WHEN_DISABLED = {
        "paystack_enabled": ("paystack_account",),
        "monnify_enabled": ("monnify_contract_code", "monnify_base_url", "monnify_account"),
        "bank_transfer_enabled": ("bank_transfer_provider", "monnify_transfer_bank_code", "bank_cash_account"),
        "paystack_terminal_enabled": ("paystack_terminal_id", "paystack_terminal_customer_email", "paystack_terminal_account"),
        "cash_enabled": ("cash_instructions", "cash_account"),
    }

    class Meta:
        model = CommercePaymentConfiguration
        fields = [
            "currency",
            "paystack_enabled", "paystack_secret_key", "paystack_account",
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
            "cash_instructions": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, business, **kwargs):
        self.business = business
        instance = kwargs.get("instance")
        saved_currency = (
            (getattr(instance, "currency", "") or "").strip().upper()
            or self.DEFAULT_CURRENCY
        )
        # A bound ModelForm normally renders the raw POST value even when
        # clean_currency() later normalizes it. Normalize a blank submitted
        # currency before binding so the visible field remains populated on a
        # validation error and after a save/re-render.
        if args and args[0] is not None:
            bound_data = args[0].copy()
            if not (bound_data.get("currency") or "").strip():
                bound_data["currency"] = saved_currency
            args = (bound_data, *args[1:])
        elif kwargs.get("data") is not None:
            bound_data = kwargs["data"].copy()
            if not (bound_data.get("currency") or "").strip():
                bound_data["currency"] = saved_currency
            kwargs["data"] = bound_data
        super().__init__(*args, **kwargs)
        self.fields["paystack_enabled"].label = "Paystack"
        self.fields["monnify_enabled"].label = "Monnify"
        self.fields["bank_transfer_enabled"].label = "Instant bank transfer (gateway)"
        self.fields["paystack_terminal_enabled"].label = "Paystack Terminal"
        self.fields["cash_enabled"].label = "Cash at the in-premise POS"

        # These values are conditionally required by the switches below, not by
        # HTML/model defaults. Keeping the baseline fields optional prevents an
        # OFF provider section from blocking an otherwise valid settings save.
        for name in self.CONDITIONAL_FIELDS:
            self.fields[name].required = False
        # Currency always has a safe business-level default. Treat an omitted or
        # accidentally blank POST as "keep the saved/default currency" rather
        # than letting browser/model requiredness erase or block the settings.
        self.fields["currency"].required = False

        # ModelForm stores instance values in ``form.initial``. Setting only
        # ``field.initial`` does not override an existing blank instance value,
        # which is why older rows could continue to render an empty currency
        # even though the field/model default is NGN. Normalize the form-level
        # initial values explicitly so what the admin sees matches what saves.
        if not self.is_bound:
            self.initial["currency"] = (
                (getattr(self.instance, "currency", "") or "").strip().upper()
                or self.DEFAULT_CURRENCY
            )
            self.initial["monnify_base_url"] = (
                (getattr(self.instance, "monnify_base_url", "") or "").strip()
                or self.MONNIFY_DEFAULT_BASE_URL
            )
            self.initial["bank_transfer_provider"] = (
                (getattr(self.instance, "bank_transfer_provider", "") or "").strip()
                or CommercePaymentConfiguration.BANK_TRANSFER_PROVIDER_PAYSTACK
            )

        accounts = CashAccount.raw_objects.filter(business=business, active=True).order_by("name")
        account_rows = list(accounts.only("id", "name")) if not self.is_bound else None
        for name in ("paystack_account", "monnify_account", "bank_cash_account", "paystack_terminal_account", "cash_account"):
            field = self.fields[name]
            field.queryset = accounts
            if account_rows is not None:
                # All settlement-account selects share the same tenant-owned
                # choices. Render from one loaded list instead of repeating the
                # same account query for each select. POST validation still uses
                # the scoped ModelChoiceField queryset.
                field.choices = [("", field.empty_label), *[(str(row.pk), row.name) for row in account_rows]]
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "sr-only peer"
            else:
                field.widget.attrs["class"] = CLS
        self.fields["currency"].widget.attrs.update({
            "autocomplete": "off",
            "maxlength": "3",
            "spellcheck": "false",
            "class": f"{CLS} font-semibold uppercase text-stone-900",
        })
        for name in self.SECRET_FIELDS:
            if self.instance.pk and getattr(self.instance, name):
                self.fields[name].help_text = "A credential is saved. Leave blank to keep it unchanged."

    def clean_currency(self):
        value = (
            self.cleaned_data.get("currency")
            or getattr(self.instance, "currency", "")
            or self.DEFAULT_CURRENCY
        ).strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise forms.ValidationError("Use a three-letter currency code such as NGN.")
        return value

    def _preserve_disabled_section_values(self, cleaned):
        if not self.instance.pk:
            return
        for switch_name, field_names in self.PRESERVE_WHEN_DISABLED.items():
            if cleaned.get(switch_name):
                continue
            for name in field_names:
                # An OFF section may still be preconfigured for later use. Keep
                # any submitted non-empty value, but do not let an omitted/blank
                # field erase a previously saved setting.
                if cleaned.get(name) in (None, ""):
                    cleaned[name] = getattr(self.instance, name)

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk:
            for name in self.SECRET_FIELDS:
                if not cleaned.get(name):
                    cleaned[name] = getattr(self.instance, name)
        self._preserve_disabled_section_values(cleaned)

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
            base_url = (cleaned.get("monnify_base_url") or getattr(self.instance, "monnify_base_url", "") or self.MONNIFY_DEFAULT_BASE_URL).strip()
            cleaned["monnify_base_url"] = base_url
            if not cleaned.get("monnify_account"):
                self.add_error("monnify_account", "Choose the INPROFIC settlement account before enabling Monnify.")

        if cleaned.get("bank_transfer_enabled"):
            provider = cleaned.get("bank_transfer_provider") or getattr(self.instance, "bank_transfer_provider", "") or CommercePaymentConfiguration.BANK_TRANSFER_PROVIDER_PAYSTACK
            cleaned["bank_transfer_provider"] = provider
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


class CommerceManualTransferSettingsForm(forms.ModelForm):
    class Meta:
        model = CommercePaymentConfiguration
        fields = ["transfer_enabled"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["transfer_enabled"].label = "Manual transfer routes (no gateway)"
        self.fields["transfer_enabled"].help_text = (
            "Customers choose one of the active routes below, transfer manually, and upload proof for staff verification."
        )
        self.fields["transfer_enabled"].widget.attrs["class"] = "sr-only peer"


class CommerceDirectTransferRouteForm(forms.ModelForm):
    class Meta:
        model = CommerceDirectTransferRoute
        fields = ["name", "bank_name", "account_name", "account_number", "transfer_account", "instructions", "sort_order", "active"]
        widgets = {"instructions": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["transfer_account"].queryset = CashAccount.raw_objects.filter(business=business, active=True).order_by("name")
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "h-4 w-4 accent-[#8f172d]"
            else:
                field.widget.attrs["class"] = CLS