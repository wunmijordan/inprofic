from decimal import Decimal
from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone
from accounts.models import CustomUser
from inventory.models import FinishedGood
from commerce.models import StorefrontProduct
from .models import (
    Branch,
    ProductPromotion,
    CustomerQuotation,
    QuotationLine,
    UserProfile,
    RetailSettings,
)


class Styled:
    def style(self):
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "w-full rounded-md border px-3 py-2")


class BranchForm(Styled, forms.Form):
    catalogue_mode = forms.ChoiceField(
        choices=Branch._meta.get_field("catalogue_mode").choices,
        initial="shared",
        help_text="Shared: headquarters maintains products, recipes and prices. Independent: this branch maintains its own catalogue. Stock and transactions are always separate.",
    )
    manager = forms.ModelChoiceField(
        queryset=CustomUser.objects.none(),
        label="Branch manager",
        help_text="An active staff member who will administer this branch workspace.",
    )
    name = forms.CharField(max_length=100)
    code = forms.SlugField(max_length=30)
    address = forms.CharField(max_length=255, required=False)
    location_type = forms.ChoiceField(
        choices=[("branch", "Branch"), ("warehouse", "Warehouse")]
    )

    def __init__(self, *a, business, **kw):
        super().__init__(*a, **kw)
        self.business = business
        self.fields["manager"].queryset = CustomUser.objects.filter(
            is_active=True,
            business_memberships__business=business,
            business_memberships__active=True,
        ).distinct()
        self.style()

    def clean_code(self):
        value = self.cleaned_data["code"]
        if Branch.raw_objects.filter(business=self.business, code=value).exists():
            raise forms.ValidationError("This branch code is already in use.")
        return value


class TransferForm(Styled, forms.Form):
    destination_product = forms.ModelChoiceField(
        queryset=FinishedGood.raw_objects.none(),
        required=False,
        help_text="For an independent full branch, select its receiving product. Shared catalogues match automatically.",
    )
    source = forms.ModelChoiceField(queryset=Branch.raw_objects.none())
    destination = forms.ModelChoiceField(queryset=Branch.raw_objects.none())
    product = forms.ModelChoiceField(queryset=FinishedGood.raw_objects.none())
    quantity = forms.DecimalField(
        max_digits=16,
        decimal_places=2,
        min_value=Decimal(".01"),
        help_text="Quantity in the product’s registered stock unit.",
    )
    note = forms.CharField(max_length=500, required=False)

    def __init__(self, *a, business, user, **kw):
        from .services import permitted_branches

        super().__init__(*a, **kw)
        self.fields["source"].queryset = permitted_branches(user, business).filter(
            workspace__isnull=True
        )
        self.fields["destination_product"].queryset = FinishedGood.raw_objects.filter(
            business__headquarters=business, stock__isnull=False
        )
        self.fields["destination"].queryset = Branch.raw_objects.filter(
            business=business, active=True
        )
        self.fields["product"].queryset = FinishedGood.raw_objects.filter(
            business=business, stock__isnull=False
        )
        self.style()


class ProfileForm(Styled, forms.ModelForm):
    remove_photo = forms.BooleanField(required=False, label="Remove profile picture")
    fullname = forms.CharField(max_length=160, label="Full name")
    email = forms.EmailField(required=False)
    phone = forms.CharField(max_length=30, required=False)

    class Meta:
        model = UserProfile
        fields = ["photo", "address", "emergency_contact", "emergency_phone"]
        widgets = {
            "photo": forms.FileInput(
                attrs={"accept": "image/jpeg,image/png,image/webp", "capture": "user"}
            )
        }

    def __init__(self, *a, user, **kw):
        super().__init__(*a, **kw)
        self.user = user
        for key in ("fullname", "email", "phone"):
            self.fields[key].initial = getattr(user, key)
        self.style()

    def clean(self):
        data = super().clean()
        if data.get("remove_photo") and self.files.get("photo"):
            self.add_error("photo", "Choose either a new picture or removal.")
        return data

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if self.files.get("photo") and photo.size > 5 * 1024 * 1024:
            raise forms.ValidationError("Choose an image smaller than 5 MB.")
        return photo

    def save(self, commit=True):
        for key in ("fullname", "email", "phone"):
            setattr(self.user, key, self.cleaned_data[key])
        self.user.save(update_fields=["fullname", "email", "phone"])
        if self.cleaned_data.get("remove_photo"):
            self.instance.photo = ""
        old_name = self.initial.get("photo")
        old_name = getattr(old_name, "name", "")
        result = super().save(commit=commit)
        if commit and self.cleaned_data.get("remove_photo") and old_name:
            from django.db import transaction

            storage = self.instance.photo.storage
            transaction.on_commit(lambda: storage.delete(old_name))
        return result


class PolicyForm(Styled, forms.ModelForm):
    class Meta:
        model = RetailSettings
        fields = ["require_approved_devices", "require_cashier_session"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.style()


class PromotionForm(Styled, forms.ModelForm):
    channels = forms.MultipleChoiceField(
        choices=ProductPromotion._meta.get_field("channel").choices,
        required=False,
        help_text="Choose All channels, or select one or more individual channels.",
    )

    class Meta:
        model = ProductPromotion
        fields = [
            "name",
            "products",
            "reward_products",
            "mode",
            "qualifying_quantity",
            "free_quantity",
            "bundle_price",
            "starts_on",
            "ends_on",
            "channels",
            "active",
            "eligibility_rule",
            "pricing_rule",
        ]
        widgets = {
            "eligibility_rule": forms.Textarea(attrs={"rows": 3}),
            "pricing_rule": forms.Textarea(attrs={"rows": 3}),
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *a, business, **kw):
        super().__init__(*a, **kw)
        self.fields["products"].queryset = FinishedGood.raw_objects.filter(
            business=business
        )
        self.fields["mode"].help_text = (
            "Buy/get rewards selected Y portions free. Mix-and-match charges the bundle price for each complete group of X portions; extra portions keep their individual prices."
        )
        self.fields["products"].label = "Buy X products"
        self.fields["reward_products"].label = "Get Y products"
        self.fields["reward_products"].queryset = FinishedGood.raw_objects.filter(
            business=business
        )
        self.fields["reward_products"].help_text = (
            "Buy/get rewards are added automatically as separate free lines. Customer-added reward products remain billable."
        )
        if (
            self.instance.pk
            and self.instance.mode == "buy_get"
            and not self.instance.reward_products.exists()
        ):
            self.initial["reward_products"] = self.instance.products.values_list(
                "pk", flat=True
            )
        self.business = business
        self.initial["channels"] = self.instance.channel_codes
        selected_ids = (
            self._posted_values("products")
            if self.is_bound
            else (
                list(self.instance.products.values_list("pk", flat=True))
                if self.instance.pk
                else []
            )
        )
        selected = list(
            FinishedGood.raw_objects.filter(
                business=business, pk__in=[v for v in selected_ids if str(v).isdigit()]
            ).prefetch_related("channel_prices")
        )
        codes = (
            self._posted_values("channels")
            if self.is_bound
            else self.initial["channels"]
        )
        if not codes:
            codes = [self.data.get("channel", "all")] if self.is_bound else ["all"]
        codes = list(dict.fromkeys(codes))
        if "all" in codes:
            codes = ["physical_store", "online", "distribution"]
        from .promotion_portions import portion_choices, selected_portion

        self.portion_names = []
        self.portion_catalog = {}
        for role, related in (("x", "products"), ("y", "reward_products")):
            role_ids = (
                self._posted_values(related)
                if self.is_bound
                else self.initial.get(
                    related,
                    (
                        list(
                            getattr(self.instance, related).values_list("pk", flat=True)
                        )
                        if self.instance.pk
                        else []
                    ),
                )
            )
            goods = FinishedGood.raw_objects.filter(
                business=business,
                pk__in=[
                    getattr(v, "pk", v)
                    for v in role_ids
                    if str(getattr(v, "pk", v)).isdigit()
                ],
            ).prefetch_related(
                "channel_prices", "bulk_pack_profiles", "individual_sale_options"
            )
            for good in goods:
                options = portion_choices(good)
                self.portion_catalog[good.pk] = options
                name = f"portion_{role}_{good.pk}"
                self.fields[name] = forms.ChoiceField(
                    label=f"{'Buy X' if role == 'x' else 'Get Y'} · {good.name}",
                    choices=[(key, value["label"]) for key, value in options.items()],
                    required=False,
                    initial=selected_portion(
                        self.instance, good.pk, reward=role == "y"
                    ),
                )
                self.portion_names.append(name)
        self.price_names = []
        for product in selected:
            selection = (
                self.data.get(f"portion_x_{product.pk}", "standard")
                if self.is_bound
                else selected_portion(self.instance, product.pk)
            )
            selected_prices = (
                self.portion_catalog[product.pk].get(selection, {}).get("prices", {})
            )
            for code in codes:
                if code not in selected_prices:
                    continue
                if code not in {"physical_store", "online", "distribution"}:
                    continue
                name = f"price_{product.pk}_{code}"
                self.fields[name] = forms.DecimalField(
                    label=f"{product.name} · {dict(self.fields['channels'].choices)[code]}",
                    min_value=Decimal("0.01"),
                    max_digits=14,
                    decimal_places=2,
                    required=False,
                    initial=self.instance.product_prices.get(str(product.pk), {}).get(
                        code, selected_prices[code]
                    ),
                    widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
                )
                self.price_names.append(name)
        self.style()

    def _posted_values(self, name):
        values = (
            self.data.getlist(name)
            if hasattr(self.data, "getlist")
            else self.data.get(name, [])
        )
        return [values] if isinstance(values, str) else values

    @property
    def regular_fields(self):
        return [
            self[name]
            for name in self.fields
            if name not in self.price_names + self.portion_names
        ]

    @property
    def portion_fields(self):
        return [self[name] for name in self.portion_names]

    @property
    def price_fields(self):
        return [self[name] for name in self.price_names]

    def clean_channels(self):
        codes = self.cleaned_data.get("channels") or [self.data.get("channel", "all")]
        if any(code not in dict(self.fields["channels"].choices) for code in codes):
            raise forms.ValidationError("Choose valid sales channels.")
        if "all" in codes and len(codes) > 1:
            raise forms.ValidationError(
                "Choose All channels on its own, or select individual channels."
            )
        return list(dict.fromkeys(codes))

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.channels = self.cleaned_data["channels"]
        instance.channel = (
            instance.channels[0] if len(instance.channels) == 1 else "all"
        )
        prices = {}
        for name in self.price_names:
            value = self.cleaned_data.get(name)
            if value is not None:
                _, product_id, code = name.split("_", 2)
                prices.setdefault(product_id, {})[code] = str(value)
        instance.product_prices = prices
        instance.product_portions = {}
        instance.reward_portions = {}
        for name in self.portion_names:
            _, role, product_id = name.split("_", 2)
            mapping = (
                instance.product_portions if role == "x" else instance.reward_portions
            )
            mapping[product_id] = self.cleaned_data.get(name) or "standard"
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    def clean(self):
        data = super().clean()
        codes = data.get("channels", ["all"])
        for name in self.portion_names:
            selection = data.get(name) or "standard"
            good_id = int(name.split("_")[2])
            available = (
                self.portion_catalog[good_id].get(selection, {}).get("prices", {})
            )
            if (
                available
                and "all" not in codes
                and not set(codes).intersection(available)
            ):
                self.add_error(
                    name, "This portion is not sold through any selected channel."
                )
            if not available and name not in self.errors:
                self.add_error(name, "Choose an available selling portion.")
        if data.get("mode") == "buy_get" and not data.get("reward_products"):
            self.add_error(
                "reward_products", "Choose the product or products offered free."
            )
        if (
            data.get("ends_on")
            and data.get("starts_on")
            and data["ends_on"] < data["starts_on"]
        ):
            self.add_error("ends_on", "End date must follow the start date.")
        if data.get("mode") == "mix_match" and (
            data.get("bundle_price") is None or data["bundle_price"] <= 0
        ):
            self.add_error("bundle_price", "Enter a positive bundle price.")
        return data


class QuotationForm(Styled, forms.ModelForm):
    class Meta:
        model = CustomerQuotation
        fields = [
            "title",
            "customer_name",
            "customer_email",
            "customer_phone",
            "valid_until",
            "channel",
            "notes",
        ]
        widgets = {
            "valid_until": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.style()

    def clean_valid_until(self):
        date = self.cleaned_data["valid_until"]
        if date < timezone.localdate():
            raise forms.ValidationError("Choose today or a later date.")
        return date


class QuotationLineForm(Styled, forms.ModelForm):
    class Meta:
        model = QuotationLine
        fields = ["product", "quantity", "unit_price"]

    def __init__(self, *a, business, **kw):
        super().__init__(*a, **kw)
        self.fields["product"].queryset = (
            StorefrontProduct.raw_objects.filter(business=business)
            .select_related("finished_good")
            .prefetch_related("finished_good__channel_prices")
        )
        self.style()


class QuotationLineSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        products = []
        for form in self.forms:
            data = getattr(form, "cleaned_data", {})
            if data.get("product") and not data.get("DELETE"):
                products.append(data["product"].pk)
        if len(products) != len(set(products)):
            raise forms.ValidationError("Use one line for each product.")


QuotationLines = inlineformset_factory(
    CustomerQuotation,
    QuotationLine,
    form=QuotationLineForm,
    formset=QuotationLineSet,
    extra=0,
    min_num=1,
    validate_min=True,
    can_delete=True,
)
