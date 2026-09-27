from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory
from core.forms import ExistingAwareInlineFormSet
from .models import PurchaseOrder, PurchaseOrderItem
from inventory.models import FinishedGood, RawMaterial
from core.models import CashAccount
from core.verticals import vertical_config

INPUT_CLS = "w-full rounded-md border border-[#D9CFB4] bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#8f172d]/30 focus:border-[#8f172d]"


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs["class"] = INPUT_CLS


class PurchaseOrderForm(StyledModelForm):
    amount_paid = forms.DecimalField(
        max_digits=16,
        decimal_places=2,
        required=False,
        min_value=0,
        label="Amount paid now",
        help_text="For Partially Paid orders only. The remaining balance stays payable in Finance.",
    )

    class Meta:
        model = PurchaseOrder
        fields = ["date", "supplier", "payment_status", "payment_method", "account"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = CashAccount.objects.filter(active=True).order_by("name")
        self.fields["account"].required = False

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("payment_status")
        amount_paid = cleaned.get("amount_paid") or 0

        if status in ("paid", "partial") and not cleaned.get("account"):
            self.add_error("account", "Select the cash/bank account used for this payment.")

        if status == "partial" and not self.instance.pk and amount_paid <= 0:
            self.add_error("amount_paid", "Enter the amount paid now for a partially paid order.")
        elif status == "unpaid" and amount_paid > 0:
            self.add_error("amount_paid", "An unpaid order cannot have an initial payment. Choose Partially Paid instead.")

        return cleaned


class PurchaseOrderItemForm(StyledModelForm):
    item = forms.ChoiceField(label="Inventory item")

    class Meta:
        model = PurchaseOrderItem
        fields = ["item", "qty", "unit_cost"]

    def __init__(self, *args, business=None, inventory_catalog=None, **kwargs):
        self.business = business
        self.inventory_catalog = inventory_catalog
        super().__init__(*args, **kwargs)

        if inventory_catalog is None:
            raw_materials = list(RawMaterial.objects.filter(business=business).order_by("name"))
            products_qs = FinishedGood.objects.filter(business=business, stock__isnull=False)
            if business and business.uses_production:
                products_qs = products_qs.filter(source_type=FinishedGood.SOURCE_PURCHASED_FOR_RESALE)
            products = list(products_qs.distinct().order_by("name"))
            inventory_catalog = {
                "raw": {item.pk: item for item in raw_materials},
                "finished": {item.pk: item for item in products},
            }
            self.inventory_catalog = inventory_catalog

        raw_materials = list(inventory_catalog.get("raw", {}).values())
        products = list(inventory_catalog.get("finished", {}).values())
        material_choices = []
        for value, label in RawMaterial.CATEGORY_CHOICES:
            items = [item for item in raw_materials if item.category == value]
            if items:
                material_choices.append((label, [(f"raw:{item.pk}", item.name) for item in items]))

        resale_label = vertical_config(business)["product_sources"]["resale_group"] if business else "Products for resale"
        product_group = (resale_label, [(f"finished:{product.pk}", product.name) for product in products])
        groups = [*material_choices, product_group] if business and business.uses_production else [product_group, *material_choices]
        self.fields["item"].choices = [("", "Select an inventory item…"), *groups]
        self.fields["qty"].min_value = Decimal("0.01")
        # The entry field captures the amount actually paid for this line.
        # The model continues to store normalized cost per whole purchase unit,
        # so downstream stock valuation and production costing stay unchanged.
        self.fields["unit_cost"].min_value = Decimal("0")
        self.fields["unit_cost"].label = "Total purchase cost"
        self.fields["unit_cost"].help_text = (
            "Enter the total amount paid for the quantity on this row. "
            "Fractional quantities are supported; the cost for one whole purchase unit is calculated automatically."
        )
        if self.instance and self.instance.pk:
            kind, pk = self.instance.item_identity
            self.fields["item"].initial = f"{kind}:{pk}"
            # Show the original line amount when editing. The stored unit_cost
            # remains normalized per whole purchase unit.
            self.initial["unit_cost"] = (self.instance.qty * self.instance.unit_cost).quantize(Decimal("0.01"))

    def clean(self):
        cleaned = super().clean()
        value = cleaned.get("item") or ""
        try:
            kind, raw_pk = value.split(":", 1)
            pk = int(raw_pk)
        except (TypeError, ValueError):
            self.add_error("item", "Select a valid inventory item.")
            return cleaned

        catalog = self.inventory_catalog or {}
        if kind == "raw":
            selected = catalog.get("raw", {}).get(pk)
            if selected is None:
                selected = RawMaterial.objects.filter(business=self.business, pk=pk).first()
            if selected:
                self.instance.raw_material = selected
                self.instance.finished_good = None
        elif kind == "finished":
            selected = catalog.get("finished", {}).get(pk)
            if selected is None:
                selected_qs = FinishedGood.objects.filter(business=self.business, pk=pk, stock__isnull=False)
                if self.business and self.business.uses_production:
                    selected_qs = selected_qs.filter(source_type=FinishedGood.SOURCE_PURCHASED_FOR_RESALE)
                selected = selected_qs.distinct().first()
            if selected:
                self.instance.raw_material = None
                self.instance.finished_good = selected
        else:
            selected = None
        if not selected:
            self.add_error("item", "Select an item belonging to the current business.")

        qty = cleaned.get("qty")
        purchase_total = cleaned.get("unit_cost")
        if qty and qty > 0 and purchase_total is not None:
            # Normalize the amount paid for this row back to a cost per one
            # whole purchase/stock unit. This keeps the model contract intact:
            # line_total == qty * unit_cost, and receiving can continue to
            # convert that normalized cost into the material's usage unit.
            normalized_unit_cost = (purchase_total / qty).quantize(Decimal("0.01"))
            cleaned["unit_cost"] = normalized_unit_cost
            self.instance.unit_cost = normalized_unit_cost
        return cleaned


PurchaseOrderItemFormSet = inlineformset_factory(PurchaseOrder, PurchaseOrderItem, form=PurchaseOrderItemForm, formset=ExistingAwareInlineFormSet, extra=1, can_delete=True)
