from django import forms
from django.forms.models import BaseInlineFormSet
from .models import Business

INPUT_CLS = "w-full rounded-md border border-[#D9CFB4] bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#8f172d]/30 focus:border-[#8f172d]"


class BusinessForm(forms.ModelForm):
    class Meta:
        model = Business
        fields = [
            "name", "slug", "vertical", "currency_symbol", "background_color", "accent_color", "tagline",
            "storefront_logo",
            "restaurant_table_service",
        ]
        widgets = {
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
            "Shown only beside your business name on the public storefront. "
            "A square or compact transparent PNG/WebP works best (maximum 4 MB)."
        )
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
