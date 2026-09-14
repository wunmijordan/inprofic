from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory
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

    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)
        raw_materials = RawMaterial.objects.filter(business=business).order_by("name")
        products = FinishedGood.objects.filter(
            business=business,
            stock__isnull=False,
        )
        if business and business.uses_production:
            # Production businesses may procure only products explicitly
            # classified as bought-in resale stock. Made-in-house goods remain
            # exclusive to recipes / production orders.
            products = products.filter(source_type=FinishedGood.SOURCE_PURCHASED_FOR_RESALE)
        products = products.distinct().order_by("name")
        material_choices = []

        # Same category order as the Dashboard stock movement dropdown.
        for value, label in RawMaterial.CATEGORY_CHOICES:
            items = raw_materials.filter(category=value)
            if items.exists():
                material_choices.append(
                    (
                        label,
                        [
                            (f"raw:{item.pk}", item.name)
                            for item in items
                        ],
                    )
                )
        resale_label = vertical_config(business)["product_sources"]["resale_group"] if business else "Products for resale"
        product_group = (
            resale_label,
            [(f"finished:{product.pk}", product.name) for product in products],
        )
        groups = [product_group, *material_choices]
        if business and business.uses_production:
            groups = [*material_choices, product_group]
        self.fields["item"].choices = [("", "Select an inventory item…"), *groups]
        self.fields["qty"].min_value = Decimal("0.01")
        self.fields["unit_cost"].min_value = Decimal("0")
        if self.instance and self.instance.pk:
            kind, pk = self.instance.item_identity
            self.fields["item"].initial = f"{kind}:{pk}"

    def clean(self):
        cleaned = super().clean()
        value = cleaned.get("item") or ""
        try:
            kind, raw_pk = value.split(":", 1)
            pk = int(raw_pk)
        except (TypeError, ValueError):
            self.add_error("item", "Select a valid inventory item.")
            return cleaned

        if kind == "raw":
            selected = RawMaterial.objects.filter(business=self.business, pk=pk).first()
            if selected:
                self.instance.raw_material = selected
                self.instance.finished_good = None
        elif kind == "finished":
            selected = FinishedGood.objects.filter(
                business=self.business,
                pk=pk,
                stock__isnull=False,
            )
            if self.business and self.business.uses_production:
                selected = selected.filter(source_type=FinishedGood.SOURCE_PURCHASED_FOR_RESALE)
            selected = selected.distinct().first()
            if selected:
                self.instance.raw_material = None
                self.instance.finished_good = selected
        else:
            selected = None
        if not selected:
            self.add_error("item", "Select an item belonging to the active business.")
        return cleaned


PurchaseOrderItemFormSet = inlineformset_factory(PurchaseOrder, PurchaseOrderItem, form=PurchaseOrderItemForm, extra=1, can_delete=True)
