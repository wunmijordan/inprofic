from decimal import Decimal, InvalidOperation

from django import forms
from django.forms import inlineformset_factory
from django.db.models import Q
from django.utils import timezone
from core.models import CashAccount
from core.forms import ExistingAwareInlineFormSet
from core.verticals import vertical_config, bulk_package_type_choices
from sales.models import Customer, SaleItem
from .models import (
    DistributionReturn,
    FinishedGood,
    FinishedGoodChannelPrice,
    BulkPackProfile,
    IndividualSaleOption,
    ProductCompositionItem,
    ProductPortionProfile,
    InventoryAlertSettings,
    ProductionMaterial,
    ProductCategory,
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
    measurement_change_confirm = forms.BooleanField(
        required=False,
        label="Apply the controlled measurement conversion",
        help_text="Required when editing how this material is measured. Current stock and live recipe/input quantities are converted consistently; completed historical records stay frozen and the change is logged.",
    )
    measurement_change_reason = forms.CharField(
        required=False, max_length=255, label="Reason for measurement change",
        help_text="Required for traceability, e.g. supplier pack change, standardisation, or corrected unit setup.",
    )
    usage_unit_change_factor = forms.DecimalField(
        required=False, max_digits=20, decimal_places=8, min_value=Decimal("0.00000001"),
        label="Old-to-new usage unit ratio",
        help_text="Only needed when the usage unit changes. Enter how many NEW usage units equal 1 OLD usage unit; e.g. kg → g = 1000, g → kg = 0.001.",
    )

    class Meta:
        model = RawMaterial
        # stock & cost_per_unit deliberately excluded — captured above in
        # purchase-unit terms and converted in save().
        fields = ["name", "category", "purchase_unit", "package_qty", "package_unit",
                  "usage_unit", "usage_conversion_factor", "reorder_level_purchase_units"]

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.original_measurement = None
        if self.instance and self.instance.pk:
            current = RawMaterial.raw_objects.get(pk=self.instance.pk, business_id=self.instance.business_id)
            self.original_measurement = {
                "purchase_unit": current.purchase_unit,
                "package_qty": current.package_qty,
                "package_unit": current.package_unit,
                "usage_unit": current.usage_unit,
                "usage_conversion_factor": current.usage_conversion_factor,
            }
        else:
            self.fields.pop("measurement_change_confirm", None)
            self.fields.pop("measurement_change_reason", None)
            self.fields.pop("usage_unit_change_factor", None)
        if business:
            vocabulary = vertical_config(business)
            self.fields["usage_unit"].help_text = (
                f"The fine unit the {vocabulary['recipe_label'].lower()} consumes — kg, g, "
                "mL, spoon, cap…"
                if vocabulary["uses_production"]
                else "The fine unit used when an operational supply is dispensed — kg, g, mL, piece…"
            )
            if business.vertical == business.VERTICAL_GENERAL:
                self.fields["category"].choices = [
                    (value, "Component material" if value == RawMaterial.CATEGORY_INGREDIENT else label)
                    for value, label in self.fields["category"].choices
                ]
            elif not vocabulary["uses_production"]:
                self.fields["package_qty"].label = "Units per purchase pack"
                self.fields["package_qty"].help_text = "How many package units are inside one supplier purchase unit. Example: 1 carton = 24 bottles."
                self.fields["usage_conversion_factor"].label = "Issue / stock conversion"
                self.fields["usage_conversion_factor"].help_text = "How many fine issue units are in one package unit. Keep 1 when the package and issue unit are the same."
                self.fields["reorder_level_purchase_units"].label = "Reorder level (supplier units)"
                self.fields["reorder_level_purchase_units"].help_text = "How many supplier purchase units should trigger a restock warning."
                self.fields["stock_purchase_units"].label = "Opening stock (supplier units)"
                self.fields["stock_purchase_units"].help_text = "Opening/manual balance only. Later supply arrivals should be received through Procurement for traceable quantity and cost history."
                self.fields["cost_per_purchase_unit"].label = "Cost per supplier unit"
                if "measurement_change_confirm" in self.fields:
                    self.fields["measurement_change_confirm"].help_text = "Required when editing how this supply is measured. Current stock is converted consistently; completed historical records stay frozen and the change is logged."
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

    def clean(self):
        cleaned = super().clean()
        if not self.original_measurement:
            return cleaned
        fields = ("purchase_unit", "package_qty", "package_unit", "usage_unit", "usage_conversion_factor")
        changed = any(cleaned.get(field) != self.original_measurement.get(field) for field in fields)
        self.measurement_definition_changed = changed
        if not changed:
            return cleaned
        if not cleaned.get("measurement_change_confirm"):
            self.add_error("measurement_change_confirm", "Confirm the controlled conversion before changing a material measurement basis.")
        if not (cleaned.get("measurement_change_reason") or "").strip():
            self.add_error("measurement_change_reason", "Give a reason for this measurement change.")
        usage_changed = cleaned.get("usage_unit") != self.original_measurement.get("usage_unit")
        if usage_changed and not cleaned.get("usage_unit_change_factor"):
            self.add_error("usage_unit_change_factor", "Enter how many new usage units equal one old usage unit.")
        if not usage_changed:
            cleaned["usage_unit_change_factor"] = Decimal("1")
        return cleaned

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
        fields = ["source_type", "name", "product_category", "unit", "units_per_batch", "base_material", "stock", "reorder_level", "selling_price"]

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.fields["stock"].required = False
        self.fields["reorder_level"].required = False
        self.fields["selling_price"].required = False
        self.fields["product_category"].required = False
        if business:
            category_qs = ProductCategory.raw_objects.filter(business=business)
            current_category_id = getattr(self.instance, "product_category_id", None)
            if current_category_id:
                category_qs = category_qs.filter(Q(active=True) | Q(pk=current_category_id))
            else:
                category_qs = category_qs.filter(active=True)
            self.fields["product_category"].queryset = category_qs.order_by("sort_order", "name")
        else:
            self.fields["product_category"].queryset = ProductCategory.objects.none()
        self.fields["product_category"].help_text = "Optional. Categories are business-defined and used to group products in both storefronts; product source remains separate."
        if business and business.uses_production:
            self.fields["unit"].label = "Base production / stock unit"
            self.fields["unit"].help_text = (
                "The internal output unit used by production and stock, e.g. piece, loaf, bottle, metre, scoop or ml. "
                "Use the customer selling-unit section below when the public unit differs from the operational unit."
            )
            self.fields["units_per_batch"].label = "Base units per production batch"
            self.fields["units_per_batch"].help_text = (
                "How many base units one normal production batch yields. Example: 120 portions, 48 bottles, 30 pieces or 12 assembled units."
            )
        if "base_material" in self.fields:
            self.fields["base_material"].required = False
            self.fields["base_material"].queryset = RawMaterial.objects.filter(
                category=RawMaterial.CATEGORY_INGREDIENT
            ).order_by("name")
            self.fields["base_material"].label = "Base material"
            self.fields["base_material"].help_text = (
                "Optional. Choose a main recipe material if you sometimes size production by how much of that material you want to use. "
                "It must also appear in the recipe below."
            )
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
                self.fields.pop("base_material", None)
                self.instance.source_type = FinishedGood.SOURCE_PURCHASED_FOR_RESALE
        if business and not business.uses_production:
            self.fields.pop("units_per_batch")
            self.fields["unit"].label = "Stock / selling unit"
            self.fields["unit"].help_text = (
                "The unit counted in stock and normally sold to customers, e.g. piece, pair, bottle, carton or case. "
                "Use customer/bulk selling options below only when customers buy a different pack size."
            )
            self.fields["stock"].label = "Opening stock"
            self.fields["stock"].help_text = "Use this only for the opening balance. Record later supplier arrivals through Procurement so quantity and cost history remain traceable."
            self.fields["reorder_level"].label = "Reorder level"
            self.fields["reorder_level"].help_text = "Restock warning threshold in the stock / selling unit above."
            self.fields["selling_price"].help_text = "Default selling price. Channel-specific prices below can override it where needed."

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
        if source == FinishedGood.SOURCE_PURCHASED_FOR_RESALE:
            cleaned["base_material"] = None
        return cleaned


class ProductPortionProfileForm(StyledModelForm):
    class Meta:
        model = ProductPortionProfile
        fields = ["active", "customer_quantity", "customer_unit", "base_quantity", "public_note"]

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        if business and not vertical_config(business)["uses_production"]:
            self.fields["active"].label = "Use a standard customer selling unit"
            self.fields["customer_quantity"].label = "Displayed quantity"
            self.fields["customer_unit"].label = "Customer selling unit"
            self.fields["base_quantity"].label = "Stock units per selling unit"
            self.fields["public_note"].label = "Customer note"
        else:
            self.fields["active"].label = "Use a standard customer portion"
            self.fields["customer_quantity"].label = "Customer quantity"
            self.fields["customer_unit"].label = "Customer unit"
            self.fields["base_quantity"].label = "Base units per portion"
            self.fields["public_note"].label = "Customer note"
        self.fields["active"].widget.attrs["class"] = "sr-only peer"
        self.fields["customer_quantity"].widget.attrs["data-formset-default"] = "1"
        self.fields["base_quantity"].widget.attrs["data-formset-default"] = "1"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("active"):
            uses_production = bool(self.business and vertical_config(self.business)["uses_production"])
            if not (cleaned.get("customer_unit") or "").strip():
                self.add_error(
                    "customer_unit",
                    "Enter the customer-facing unit, e.g. serving, pack, bottle, set or piece."
                    if uses_production
                    else "Enter the customer selling unit, e.g. piece, pair, pack, carton or case.",
                )
            if (cleaned.get("customer_quantity") or Decimal("0")) <= 0:
                self.add_error("customer_quantity", "Customer quantity must be greater than zero.")
            if (cleaned.get("base_quantity") or Decimal("0")) <= 0:
                self.add_error(
                    "base_quantity",
                    "Base units per portion must be greater than zero."
                    if uses_production
                    else "Stock units per selling unit must be greater than zero.",
                )
        return cleaned


class IndividualSaleOptionForm(StyledModelForm):
    class Meta:
        model = IndividualSaleOption
        fields = [
            "name", "customer_quantity", "customer_unit", "base_quantity", "public_note",
            "physical_store_enabled", "physical_store_price", "physical_store_min_quantity",
            "online_enabled", "online_price", "online_min_quantity",
            "distribution_enabled", "distribution_price", "distribution_min_quantity",
            "active", "sort_order",
        ]

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        vocabulary = vertical_config(business) if business else None
        uses_production = bool(vocabulary and vocabulary["uses_production"])
        self.fields["name"].label = "Individual option name" if uses_production else "Alternative selling option"
        self.fields["customer_quantity"].label = "Customer quantity" if uses_production else "Displayed quantity"
        self.fields["customer_unit"].label = "Customer unit" if uses_production else "Customer selling unit"
        self.fields["base_quantity"].label = "Base units consumed" if uses_production else "Stock units used"
        self.fields["public_note"].label = "Customer note"
        channel_labels = (vocabulary or {}).get("commerce_channels", {})
        labels = {
            "physical_store_enabled": channel_labels.get("physical_store", "Physical Store / in-premise POS"),
            "online_enabled": channel_labels.get("online", "Online / website"),
            "distribution_enabled": channel_labels.get("distribution", "Bulk / distribution"),
            "physical_store_price": f"{channel_labels.get('physical_store', 'Physical Store')} price",
            "online_price": f"{channel_labels.get('online', 'Online')} price",
            "distribution_price": f"{channel_labels.get('distribution', 'Bulk / distribution')} price",
            "physical_store_min_quantity": f"{channel_labels.get('physical_store', 'Physical Store')} minimum",
            "online_min_quantity": f"{channel_labels.get('online', 'Online')} minimum",
            "distribution_min_quantity": f"{channel_labels.get('distribution', 'Bulk / distribution')} minimum",
        }
        for key, label in labels.items():
            self.fields[key].label = label
        for key in ("physical_store_enabled", "online_enabled", "distribution_enabled", "active"):
            self.fields[key].widget.attrs["class"] = "sr-only peer"
        for key in ("customer_quantity", "base_quantity", "physical_store_min_quantity", "online_min_quantity", "distribution_min_quantity"):
            self.fields[key].widget.attrs["data-formset-default"] = "1"
        self.fields["sort_order"].widget.attrs["data-formset-default"] = "0"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE"):
            return cleaned
        populated = bool((cleaned.get("name") or "").strip()) or any(
            cleaned.get(key) is not None for key in ("physical_store_price", "online_price", "distribution_price")
        )
        if not populated:
            return cleaned
        uses_production = bool(self.business and vertical_config(self.business)["uses_production"])
        if not (cleaned.get("name") or "").strip():
            self.add_error("name", "Enter the customer-facing option name.")
        if not (cleaned.get("customer_unit") or "").strip():
            self.add_error(
                "customer_unit",
                "Enter the customer unit, e.g. piece, serving, bottle, pack or set."
                if uses_production
                else "Enter the customer selling unit, e.g. piece, pair, pack, carton or case.",
            )
        if (cleaned.get("base_quantity") or Decimal("0")) <= 0:
            self.add_error(
                "base_quantity",
                "Base units consumed must be greater than zero."
                if uses_production
                else "Stock units used must be greater than zero.",
            )
        for enabled_key, price_key, min_key, label in (
            ("physical_store_enabled", "physical_store_price", "physical_store_min_quantity", "Physical Store"),
            ("online_enabled", "online_price", "online_min_quantity", "Online"),
            ("distribution_enabled", "distribution_price", "distribution_min_quantity", "Bulk / distribution"),
        ):
            if cleaned.get(enabled_key) and cleaned.get(price_key) is None:
                self.add_error(price_key, f"Enter a price when {label} is enabled.")
            if cleaned.get(enabled_key) and (cleaned.get(min_key) or Decimal("0")) <= 0:
                self.add_error(min_key, "Minimum quantity must be greater than zero.")
        return cleaned


class BulkPackProfileForm(StyledModelForm):
    profile_key = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = BulkPackProfile
        fields = [
            "name", "package_type", "customer_quantity", "customer_unit", "base_quantity",
            "price", "min_order_quantity", "active", "sort_order",
        ]

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.fields["profile_key"].initial = (
            f"bulk:{self.instance.public_id}" if getattr(self.instance, "public_id", None) else ""
        )
        self.fields["package_type"].label = "Bulk container / pack type"
        self.fields["package_type"].widget = forms.Select(choices=[("", "Choose type…"), *bulk_package_type_choices(business)] if business else [("", "Choose type…")])
        self.fields["package_type"].widget.attrs["class"] = INPUT_CLS
        self.fields["customer_quantity"].label = "Displayed size"
        self.fields["customer_unit"].label = "Size / measure unit"
        uses_production = bool(business and vertical_config(business)["uses_production"])
        self.fields["base_quantity"].label = "Base units in one bulk option" if uses_production else "Stock units in one bulk option"
        self.fields["min_order_quantity"].label = "Minimum bulk units"
        self.fields["active"].widget.attrs["class"] = "sr-only peer"
        for name in ("customer_quantity", "base_quantity", "min_order_quantity"):
            self.fields[name].widget.attrs["data-formset-default"] = "1"
        self.fields["sort_order"].widget.attrs["data-formset-default"] = "0"

    def clean_profile_key(self):
        value = (self.cleaned_data.get("profile_key") or "").strip()
        if not value and getattr(self.instance, "public_id", None):
            value = f"bulk:{self.instance.public_id}"
        if value and not value.startswith("bulk:"):
            raise forms.ValidationError("Bulk profile key is invalid.")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE"):
            return cleaned
        populated = any(cleaned.get(name) not in (None, "") for name in ("name", "customer_unit", "price"))
        if not populated:
            return cleaned
        if not (cleaned.get("name") or "").strip():
            self.add_error("name", "Enter a customer-facing bulk pack name.")
        uses_production = bool(self.business and vertical_config(self.business)["uses_production"])
        if not (cleaned.get("customer_unit") or "").strip():
            self.add_error(
                "customer_unit",
                "Enter the customer-facing measurement, e.g. litre, kg, piece, pack, tray or set."
                if uses_production
                else "Enter the customer-facing pack unit, e.g. piece, pack, carton, case or pallet.",
            )
        for name, label in (("customer_quantity", "Displayed size"), ("base_quantity", "Base units" if uses_production else "Stock units"), ("min_order_quantity", "Minimum bulk units")):
            if (cleaned.get(name) or Decimal("0")) <= 0:
                self.add_error(name, f"{label} must be greater than zero.")
        if cleaned.get("price") is not None and cleaned["price"] < 0:
            self.add_error("price", "Price cannot be negative.")
        return cleaned


class ProductCompositionItemForm(StyledModelForm):
    TYPE_FINISHED = "finished_good"
    TYPE_RAW = "raw_material"
    component_type = forms.ChoiceField(
        choices=[(TYPE_FINISHED, "Finished / procured product"), (TYPE_RAW, "Raw / packaging material")],
        initial=TYPE_FINISHED,
    )

    class Meta:
        model = ProductCompositionItem
        fields = [
            "profile_key", "component_finished_good", "component_raw_material",
            "quantity", "fulfilment_scope", "include_in_public_contents",
            "public_label", "public_quantity_label",
        ]
        widgets = {"profile_key": forms.Select()}

    def __init__(self, *args, business=None, parent_good=None, profile_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.parent_good = parent_good
        self.fields["profile_key"].label = "Applies to"
        choices = profile_choices or [("standard", "Standard portion")]
        self.fields["profile_key"].widget.choices = choices
        if business:
            goods = FinishedGood.raw_objects.filter(business=business).order_by("name")
            if parent_good and parent_good.pk:
                goods = goods.exclude(pk=parent_good.pk)
            self.fields["component_finished_good"].queryset = goods
            self.fields["component_raw_material"].queryset = RawMaterial.raw_objects.filter(business=business).order_by("category", "name")
        else:
            self.fields["component_finished_good"].queryset = FinishedGood.objects.none()
            self.fields["component_raw_material"].queryset = RawMaterial.objects.none()
        self.fields["component_finished_good"].required = False
        self.fields["component_raw_material"].required = False
        self.fields["include_in_public_contents"].widget.attrs["class"] = "sr-only peer"
        self.fields["quantity"].widget.attrs["data-formset-default"] = "1"
        if self.instance.pk and self.instance.component_raw_material_id:
            self.fields["component_type"].initial = self.TYPE_RAW
        elif self.data:
            prefix = self.prefix or ""
            self.fields["component_type"].initial = self.data.get(f"{prefix}-component_type", self.TYPE_FINISHED)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE"):
            return cleaned
        fg = cleaned.get("component_finished_good")
        raw = cleaned.get("component_raw_material")
        component_type = cleaned.get("component_type")
        if component_type == self.TYPE_FINISHED:
            cleaned["component_raw_material"] = None
            raw = None
            if not fg:
                self.add_error("component_finished_good", "Choose the finished/procured product included in this portion.")
        else:
            cleaned["component_finished_good"] = None
            fg = None
            if not raw:
                self.add_error("component_raw_material", "Choose the raw or packaging material included in this portion.")
        if fg and self.parent_good and self.parent_good.pk and fg.pk == self.parent_good.pk:
            self.add_error("component_finished_good", "The base product is already included automatically; choose only additional contents here.")
        if (cleaned.get("quantity") or Decimal("0")) <= 0:
            self.add_error("quantity", "Quantity must be greater than zero.")
        profile_key = (cleaned.get("profile_key") or "").strip()
        if not profile_key:
            self.add_error("profile_key", "Choose which portion or bulk pack this content belongs to.")
        return cleaned


BulkPackProfileFormSet = inlineformset_factory(
    FinishedGood, BulkPackProfile, form=BulkPackProfileForm, formset=ExistingAwareInlineFormSet, extra=1, can_delete=True
)
IndividualSaleOptionFormSet = inlineformset_factory(
    FinishedGood, IndividualSaleOption, form=IndividualSaleOptionForm, formset=ExistingAwareInlineFormSet, extra=1, can_delete=True
)
ProductCompositionItemFormSet = inlineformset_factory(
    FinishedGood,
    ProductCompositionItem,
    form=ProductCompositionItemForm,
    formset=ExistingAwareInlineFormSet,
    fk_name="finished_good",
    extra=1,
    can_delete=True,
)


class ProductCategoryForm(StyledModelForm):
    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)

    class Meta:
        model = ProductCategory
        fields = ["name", "slug", "sort_order", "active"]
        help_texts = {
            "slug": "Stable storefront/API identifier, e.g. meals, drinks, pastries.",
            "sort_order": "Lower numbers appear first in storefront grouping.",
        }

    def clean_slug(self):
        from django.utils.text import slugify
        value = slugify(self.cleaned_data.get("slug") or self.cleaned_data.get("name") or "")
        if not value:
            raise forms.ValidationError("Enter a category name or slug.")
        if self.business:
            qs = ProductCategory.raw_objects.filter(business=self.business, slug=value)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("Another category already uses this identifier.")
        return value

    def clean_name(self):
        value = (self.cleaned_data.get("name") or "").strip()
        if self.business and value:
            qs = ProductCategory.raw_objects.filter(business=self.business, name__iexact=value)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("A category with this name already exists.")
        return value


class FinishedGoodChannelPriceForm(StyledModelForm):
    class Meta:
        model = FinishedGoodChannelPrice
        fields = ["channel", "price"]

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["price"].required = False
        self.fields["channel"].error_messages["required"] = "Select a channel."
        if business:
            labels = vertical_config(business)["commerce_channels"]
            # Keep an explicit blank choice. These rows are optional inline
            # forms; without a blank option the browser selects the first
            # channel automatically, making untouched rows look populated and
            # preventing the parent Finished Good form from saving.
            self.fields["channel"].choices = [
                ("", "Select channel"),
                *[
                    (code, labels.get(code, label))
                    for code, label in FinishedGoodChannelPrice.CHANNEL_CHOICES
                ],
            ]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("channel") and cleaned.get("price") is None:
            self.add_error("price", "Enter a price for this channel.")
        return cleaned


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
    FinishedGood, RecipeItem, form=RecipeItemForm, formset=ExistingAwareInlineFormSet, extra=1, can_delete=True
)
ProductionMaterialFormSet = inlineformset_factory(
    FinishedGood, ProductionMaterial, form=ProductionMaterialForm, formset=ExistingAwareInlineFormSet, extra=1, can_delete=True
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


class InventoryAlertSettingsForm(StyledModelForm):
    class Meta:
        model = InventoryAlertSettings
        fields = [
            "enabled",
            "raw_warning_enabled", "raw_warning_repeat_minutes",
            "raw_low_enabled", "raw_low_repeat_minutes",
            "finished_warning_enabled", "finished_warning_repeat_minutes",
            "finished_low_enabled", "finished_low_repeat_minutes",
            "sound_enabled", "sound_repeat_minutes", "sound_tune", "poll_seconds",
        ]
        widgets = {
            "raw_warning_repeat_minutes": forms.NumberInput(attrs={"min": 0, "max": 10080}),
            "raw_low_repeat_minutes": forms.NumberInput(attrs={"min": 0, "max": 10080}),
            "finished_warning_repeat_minutes": forms.NumberInput(attrs={"min": 0, "max": 10080}),
            "finished_low_repeat_minutes": forms.NumberInput(attrs={"min": 0, "max": 10080}),
            "sound_repeat_minutes": forms.NumberInput(attrs={"min": 0, "max": 1440}),
            "poll_seconds": forms.NumberInput(attrs={"min": 15, "max": 300}),
        }

    def clean(self):
        cleaned = super().clean()
        for name in (
            "raw_warning_repeat_minutes", "raw_low_repeat_minutes",
            "finished_warning_repeat_minutes", "finished_low_repeat_minutes",
        ):
            value = cleaned.get(name)
            if value is not None and value > 10080:
                self.add_error(name, "Use 10,080 minutes (7 days) or less.")
        sound_repeat = cleaned.get("sound_repeat_minutes")
        if sound_repeat is not None and sound_repeat > 1440:
            self.add_error("sound_repeat_minutes", "Use 1,440 minutes (24 hours) or less.")
        poll = cleaned.get("poll_seconds")
        if poll is not None and not 15 <= poll <= 300:
            self.add_error("poll_seconds", "Choose a refresh interval from 15 to 300 seconds.")
        return cleaned
