from decimal import Decimal, InvalidOperation

from django import forms
from django.forms import inlineformset_factory
from django.db.models import Q
from django.utils import timezone
from core.models import CashAccount
from core.verticals import vertical_config
from sales.models import Customer, SaleItem
from .models import (
    DistributionReturn,
    FinishedGood,
    FinishedGoodChannelPrice,
    ProductionMaterial,
    RawMaterial,
    RecipeItem,
)

INPUT_CLS = "w-full rounded-md border border-[#D9CFB4] bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#8f172d]/30 focus:border-[#8f172d]"


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs["class"] = INPUT_CLS


class StyledForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = INPUT_CLS


class RawMaterialForm(StyledModelForm):
    """Stock and cost are entered here in the material's PURCHASE unit
    (e.g. '3 bags', 'cost 9000/bag') — the more natural way to count what's
    on hand and what it cost. Internally, RawMaterial.stock and
    .cost_per_unit are always stored in the fine USAGE unit (e.g. grams,
    spoons), because that's what recipes and every stock deduction are
    computed against. This form converts between the two on load and on
    save, via total_conversion_factor = package_qty x usage_conversion_factor."""

    package_qty = forms.DecimalField(
        max_digits=12, decimal_places=2, initial=1,
        label="Package quantity",
        help_text="How much is inside ONE purchase unit, e.g. 1 bag = 50 → 50.",
    )
    usage_conversion_factor = forms.DecimalField(
        max_digits=16, decimal_places=6, initial=1,
        label="Usage conversion",
        help_text="How many usage units in ONE package unit. Standard: kg→g is 1000. "
                   "Non-standard (spoon, cap…): count it yourself.",
    )
    reorder_level_purchase_units = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        initial=0,
        label="Reorder level (purchase units)",
        help_text="How many purchase units should trigger a reorder, e.g. 2 bags.",
    )
    stock_purchase_units = forms.DecimalField(
        label="Stock (in purchase units)", max_digits=18, decimal_places=6,
        required=False, initial=0,
        help_text=(
            "How many purchase units you currently have, including a calculated fraction such as "
            "0.750133 carton. This opening/manual balance is converted to the fine usage unit on save."
        ),
    )
    cost_per_purchase_unit = forms.DecimalField(
        label="Cost (per purchase unit)", max_digits=14, decimal_places=2,
        required=False, initial=0,
        help_text="What one purchase unit costs, e.g. price per bag.",
    )

    class Meta:
        model = RawMaterial
        # stock & cost_per_unit deliberately excluded — captured above in
        # purchase-unit terms and converted in save().
        fields = ["name", "category", "purchase_unit", "package_qty", "package_unit",
                  "usage_unit", "usage_conversion_factor", "reorder_level_purchase_units"]

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        if business:
            vocabulary = vertical_config(business)
            self.fields["usage_unit"].help_text = (
                f"The fine unit the {vocabulary['recipe_label'].lower()} consumes — kg, g, "
                "mL, spoon, cap…"
                if vocabulary["uses_production"]
                else "The fine unit used when an internal supply is dispensed — kg, g, mL, piece…"
            )
            if business.vertical == business.VERTICAL_GENERAL:
                self.fields["category"].choices = [
                    (value, "Component material" if value == RawMaterial.CATEGORY_INGREDIENT else label)
                    for value, label in self.fields["category"].choices
                ]
            elif not vocabulary["uses_production"]:
                category_labels = {
                    RawMaterial.CATEGORY_INGREDIENT: "Consumable supply",
                    RawMaterial.CATEGORY_PACKAGING: "Packaging supply",
                    RawMaterial.CATEGORY_PRODUCTION_SUPPLY: "Handling / storage supply",
                    RawMaterial.CATEGORY_OPERATIONAL_SUPPLY: "Operational supply",
                }
                self.fields["category"].choices = [
                    (value, category_labels.get(value, label))
                    for value, label in self.fields["category"].choices
                ]
                self.fields["category"].help_text = (
                    "Classifies supporting stock used by the business. Products bought for resale "
                    "are created under Stock Products instead."
                )
        if self.instance and self.instance.pk:
            factor = self.instance.total_conversion_factor or Decimal("1")
            # The temporary purchase-unit entry supports 6dp so a measured
            # three-decimal usage balance can survive division by a large pack
            # size. Stored RawMaterial stock remains the authoritative 3dp
            # value; cost and reorder fields retain their existing precision.
            self.fields["stock_purchase_units"].initial = (self.instance.stock / factor).quantize(Decimal("0.000001"))
            self.fields["cost_per_purchase_unit"].initial = (self.instance.cost_per_unit * factor).quantize(Decimal("0.01"))
            self.fields["reorder_level_purchase_units"].initial = (self.instance.reorder_level / factor).quantize(Decimal("0.01"))

    def clean_package_qty(self):
        v = self.cleaned_data.get("package_qty")
        if v is not None and v <= 0:
            raise forms.ValidationError("Must be greater than zero.")
        return v

    def clean_usage_conversion_factor(self):
        v = self.cleaned_data.get("usage_conversion_factor")
        if v is not None and v <= 0:
            raise forms.ValidationError("Must be greater than zero.")
        return v

    def save(self, commit=True):
        instance = super().save(commit=False)
        package_qty = self.cleaned_data.get("package_qty") or Decimal("1")
        usage_conv = self.cleaned_data.get("usage_conversion_factor") or Decimal("1")
        factor = package_qty * usage_conv
        purchase_stock = self.cleaned_data.get("stock_purchase_units") or Decimal("0")
        purchase_cost = self.cleaned_data.get("cost_per_purchase_unit") or Decimal("0")
        purchase_reorder = (self.cleaned_data.get("reorder_level_purchase_units") or Decimal("0"))
        instance.stock = (purchase_stock * factor).quantize(Decimal("0.001"))
        instance.cost_per_unit = (purchase_cost / factor).quantize(Decimal("0.000001"))
        instance.reorder_level = (purchase_reorder * factor).quantize(Decimal("0.01"))
        if commit:
            instance.save()
        return instance


class FinishedGoodForm(StyledModelForm):
    class Meta:
        model = FinishedGood
        fields = ["source_type", "name", "unit", "units_per_batch", "stock", "reorder_level", "selling_price"]

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.fields["stock"].required = False
        self.fields["reorder_level"].required = False
        self.fields["selling_price"].required = False
        if business:
            labels = vertical_config(business)["product_sources"]
            if business.uses_production:
                self.fields["source_type"].label = "Product source"
                self.fields["source_type"].choices = [
                    (FinishedGood.SOURCE_MADE_IN_HOUSE, labels["made_in_house"]),
                    (FinishedGood.SOURCE_PURCHASED_FOR_RESALE, labels["purchased_for_resale"]),
                ]
                self.fields["source_type"].help_text = "Purchased-for-resale products are stocked through Procurement and never sent into recipes or production orders."
            else:
                self.fields.pop("source_type")
                self.instance.source_type = FinishedGood.SOURCE_PURCHASED_FOR_RESALE
        if business and not business.uses_production:
            self.fields.pop("units_per_batch")
            self.fields["unit"].label = "Stock / selling unit"
            self.fields["stock"].label = "Opening stock"
            self.fields["stock"].help_text = "Use this only for the opening balance. Record later arrivals by receiving a purchase order."

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("source_type") or getattr(self.instance, "source_type", FinishedGood.SOURCE_MADE_IN_HOUSE)
        stock_tracked = bool(
            self.business
            and (not self.business.uses_production or source == FinishedGood.SOURCE_PURCHASED_FOR_RESALE)
        )
        if stock_tracked and cleaned.get("stock") is None:
            cleaned["stock"] = Decimal("0")
        if stock_tracked and cleaned.get("reorder_level") is None:
            cleaned["reorder_level"] = Decimal("0")
        return cleaned


class FinishedGoodChannelPriceForm(StyledModelForm):
    class Meta:
        model = FinishedGoodChannelPrice
        fields = ["channel", "price"]

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["price"].required = False
        if business:
            labels = vertical_config(business)["commerce_channels"]
            self.fields["channel"].choices = [
                (code, labels.get(code, label))
                for code, label in FinishedGoodChannelPrice.CHANNEL_CHOICES
            ]


FinishedGoodChannelPriceFormSet = inlineformset_factory(
    FinishedGood, FinishedGoodChannelPrice, form=FinishedGoodChannelPriceForm, extra=3,
    can_delete=True, max_num=3, validate_max=True
)


class RecipeItemForm(StyledModelForm):
    class Meta:
        model = RecipeItem
        fields = ["raw_material", "qty_per_batch", "flexible_usage"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["raw_material"].queryset = RawMaterial.objects.filter(
            category=RawMaterial.CATEGORY_INGREDIENT
        )
        self.fields["flexible_usage"].widget.attrs["class"] = "h-4 w-4 rounded border-[#D9CFB4] text-[#8f172d]"


class ProductionMaterialForm(StyledModelForm):
    class Meta:
        model = ProductionMaterial
        fields = ["raw_material", "qty_per_batch"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Operational supplies (gloves, head nets, cleaning products, etc.)
        # are intentionally not attached to individual productions.
        self.fields["raw_material"].queryset = RawMaterial.objects.filter(
            category__in=[
                RawMaterial.CATEGORY_PACKAGING,
                RawMaterial.CATEGORY_PRODUCTION_SUPPLY,
            ]
        )


RecipeItemFormSet = inlineformset_factory(
    FinishedGood, RecipeItem, form=RecipeItemForm, extra=1, can_delete=True
)
ProductionMaterialFormSet = inlineformset_factory(
    FinishedGood, ProductionMaterial, form=ProductionMaterialForm, extra=1, can_delete=True
)


class MarketStockReleaseForm(StyledForm):
    PAYMENT_CHOICES = [("unpaid", "Receivable / pay later"), ("paid", "Payment received")]

    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    customer = forms.ModelChoiceField(queryset=Customer.objects.none())
    finished_good = forms.ModelChoiceField(queryset=FinishedGood.objects.none(), label="Product")
    quantity = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    payment_status = forms.ChoiceField(choices=PAYMENT_CHOICES, initial="unpaid")
    payment_method = forms.ChoiceField(
        choices=[("Transfer", "Transfer"), ("Cash", "Cash"), ("Card", "Card")],
        required=False,
    )
    account = forms.ModelChoiceField(queryset=CashAccount.objects.none(), required=False)
    note = forms.CharField(max_length=255, required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.filter(
            business=business, active=True
        ).order_by("name")
        self.fields["finished_good"].queryset = FinishedGood.objects.filter(
            business=business,
            market_stock_lots__quantity_available__gt=0,
            market_stock_lots__active=True,
        ).filter(
            Q(market_stock_lots__expiry_date__isnull=True)
            | Q(market_stock_lots__expiry_date__gte=timezone.localdate())
        ).distinct().order_by("name")
        self.fields["account"].queryset = CashAccount.objects.filter(
            business=business, active=True
        ).order_by("name")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("payment_status") == "paid" and not cleaned.get("account"):
            self.add_error("account", "Select the account that received this payment.")
        if cleaned.get("payment_status") != "paid":
            cleaned["account"] = None
            cleaned["payment_method"] = "Transfer"
        return cleaned


class MarketStockTransferForm(StyledForm):
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    finished_good = forms.ModelChoiceField(queryset=FinishedGood.objects.none(), label="Product")
    quantity = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    reason = forms.CharField(max_length=255, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)
        self.fields["finished_good"].queryset = FinishedGood.objects.filter(
            business=business,
            market_stock_lots__quantity_available__gt=0,
            market_stock_lots__active=True,
        ).filter(
            Q(market_stock_lots__expiry_date__isnull=True)
            | Q(market_stock_lots__expiry_date__gte=timezone.localdate())
        ).distinct().order_by("name")


class DistributionReturnForm(StyledModelForm):
    class Meta:
        model = DistributionReturn
        fields = ["date", "sale_item", "quantity", "condition", "reason"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "reason": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)
        queryset = SaleItem.objects.filter(
            sale__business=business,
            sale__source="distribution_order",
        ).select_related("sale", "finished_good").order_by("-sale__date", "-sale_id", "id")
        self.fields["sale_item"].queryset = queryset
        self.fields["sale_item"].label = "Original Distribution sale line"
        self.fields["sale_item"].label_from_instance = lambda item: (
            f"Sale #{item.sale_id} · {item.sale.customer} · {item.finished_good.name} · "
            f"{item.total_units:.2f} {item.finished_good.unit}"
        )
