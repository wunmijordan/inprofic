from datetime import date
from django import forms
from django.forms.models import BaseInlineFormSet
from .models import Business

INPUT_CLS = "w-full rounded-md border border-[#D9CFB4] bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#8f172d]/30 focus:border-[#8f172d]"


class BusinessForm(forms.ModelForm):
    class Meta:
        model = Business
        fields = [
            "name", "slug", "vertical", "currency_symbol", "background_color", "accent_color", "tagline",
            "contact_phone", "contact_email", "contact_address", "contact_website",
            "storefront_logo",
            "restaurant_table_service", "fiscal_year_start_month", "profit_period",
        ]
        widgets = {
            "fiscal_year_start_month": forms.Select(choices=[(m, date(2000, m, 1).strftime("%B")) for m in range(1, 13)]),
            "background_color": forms.TextInput(attrs={"type": "color"}),
            "accent_color": forms.TextInput(attrs={"type": "color"}),
            "storefront_logo": forms.ClearableFileInput(
                attrs={"accept": "image/jpeg,image/png,image/webp"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs["class"] = INPUT_CLS
        self.fields["fiscal_year_start_month"].label = "Business year starts in"
        self.fields["profit_period"].label = "Profit & loss period"
        # Optional so any form that does not post them keeps the saved values.
        self.fields["fiscal_year_start_month"].required = False
        self.fields["profit_period"].required = False
        self.fields["vertical"].label = "Service"
        self.fields["slug"].label = "Public business address"
        self.fields["slug"].help_text = (
            "Choose a short unique address such as sunrisestore. Letters, numbers, hyphens and underscores are allowed. "
            "Changing it also changes storefront and connected-website links."
        )
        self.fields["background_color"].label = "Navigation / background color"
        self.fields["accent_color"].label = "Button / action color"
        self.fields["storefront_logo"].label = "Storefront logo"
        self.fields["storefront_logo"].help_text = (
            "Shown beside your business name on the public storefront and on checkout pages and receipts. "
            "A square or compact transparent PNG/WebP works best (maximum 4 MB)."
        )
        self.fields["contact_phone"].label = "Business phone"
        self.fields["contact_email"].label = "Business email"
        self.fields["contact_address"].label = "Business address"
        self.fields["contact_website"].label = "Business website"
        self.fields["restaurant_table_service"].label = "Table service"
        self.fields["restaurant_table_service"].help_text = "Require a table or service reference for dine-in sales."
        self.fields["restaurant_table_service"].widget.attrs["class"] = "h-4 w-4 accent-[#8f172d]"

    def clean_slug(self):
        return (self.cleaned_data.get("slug") or "").strip().lower()

    def clean_storefront_logo(self):
        image = self.cleaned_data.get("storefront_logo")
        if image and getattr(image, "size", 0) > 4 * 1024 * 1024:
            raise forms.ValidationError("Upload a storefront logo no larger than 4 MB.")
        return image


    def clean_fiscal_year_start_month(self):
        value = self.cleaned_data.get("fiscal_year_start_month")
        return value or getattr(self.instance, "fiscal_year_start_month", None) or 1

    def clean_profit_period(self):
        value = self.cleaned_data.get("profit_period")
        return value or getattr(self.instance, "profit_period", None) or "year"

class ExistingAwareInlineFormSet(BaseInlineFormSet):
    """Keep create-mode starter rows without injecting blank rows on edit.

    Existing records render exactly as saved.  When an existing parent has no
    rows at all, one starter row remains available so the section is still
    discoverable.  Bound POST data remains authoritative.
    """

    starter_extra = 1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound:
            return
        instance = getattr(self, "instance", None)
        if instance is not None and getattr(instance, "pk", None):
            self.extra = 0 if self.get_queryset().exists() else self.starter_extra
