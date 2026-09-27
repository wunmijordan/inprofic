from decimal import Decimal

from django import forms
from django.db.models import Prefetch, Sum
from .models import CashAccount
from inventory.models import StockAdjustment, RawMaterial, FinishedGood, InventoryLocation
from procurement.models import SupplierPayment, PurchaseOrder
from sales.models import CustomerPayment, Sale, SaleItem
from expenses.models import Expense

CLS = "w-full rounded-md border border-[#D9CFB4] bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#8f172d]/30 focus:border-[#8f172d]"

class Base(forms.ModelForm):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        for f in self.fields.values(): f.widget.attrs["class"] = CLS

class CashAccountForm(Base):
    class Meta: model=CashAccount; fields=["name","account_type","opening_balance","active"]

class SupplierPaymentForm(Base):
    purchase_order = forms.ModelChoiceField(queryset=PurchaseOrder.objects.none(), required=False, label="Purchase order")
    class Meta: model=SupplierPayment; fields=["date","supplier","amount","payment_method","account","reference","purchase_order","notes"]
    widgets={"date":forms.DateInput(attrs={"type":"date"})}
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.fields["account"].queryset=CashAccount.objects.filter(active=True).order_by("name")
        self.fields["purchase_order"].queryset=PurchaseOrder.objects.filter(status="received",payment_status__in=("unpaid","partial")).prefetch_related("items","payments").order_by("-received_date","-id")
        self.fields["purchase_order"].label_from_instance=lambda po: f"PO #{po.pk} — {po.supplier or 'Unnamed'} — outstanding {po.total - sum((p.amount for p in po.payments.all()), 0):,.2f}"
        self.fields["account"].required=True
        self.supplier_payload={str(po.pk): {"supplier": po.supplier or "", "outstanding": float(max(0, po.total - sum((p.amount for p in po.payments.all()), 0)))} for po in self.fields["purchase_order"].queryset}
        self.fields["purchase_order"].widget.attrs["data-outstanding-select"]="supplier"
    def clean(self):
        c=super().clean(); po=c.get("purchase_order"); amount=c.get("amount")
        if po and amount:
            paid=po.payments.aggregate(v=Sum("amount"))["v"] or 0
            outstanding=po.total-paid
            if amount > outstanding: self.add_error("amount", "Payment exceeds the outstanding purchase balance.")
        if po and c.get("supplier") and c["supplier"].strip().lower()!=po.supplier.strip().lower(): self.add_error("supplier","Supplier must match the selected purchase order.")
        return c

class CustomerPaymentForm(Base):
    sale = forms.ModelChoiceField(queryset=Sale.objects.none(), required=False, label="Sale")
    customer = forms.ChoiceField(choices=[], label="Customer")
    class Meta: model=CustomerPayment; fields=["date","customer","amount","payment_method","account","reference","sale","notes"]
    widgets={"date":forms.DateInput(attrs={"type":"date"})}
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        sales=list(
            Sale.objects.filter(
                source__in=("distribution_order", "online_order"),
                transaction_type__in=("unpaid", "partial"),
            )
            .select_related("business")
            .prefetch_related(
                Prefetch("items", queryset=SaleItem.objects.select_related("finished_good")),
                "payments",
            )
            .order_by("customer", "-date", "-id")
        )
        self.fields["sale"].queryset=Sale.objects.filter(pk__in=[s.pk for s in sales])
        customers=sorted({s.customer for s in sales if s.customer})
        self.fields["customer"].choices=[("","Select customer…")]+[(c,c) for c in customers]
        self.fields["account"].queryset=CashAccount.objects.filter(active=True).order_by("name")
        self.fields["account"].required=True
        self.sales_payload={}
        for s in sales:
            # Payments are already prefetched above; aggregating here would
            # issue one extra SQL query for every open sale on form load.
            paid = sum((payment.amount for payment in s.payments.all()), Decimal("0"))
            self.sales_payload.setdefault(s.customer,[]).append({"id":s.pk,"label":f"Sale #{s.pk} — {s.display_source} — outstanding {s.total-paid:,.2f}","outstanding":float(s.total-paid)})
        self.fields["customer"].widget.attrs["data-customer-sales"]="1"
    def clean(self):
        c=super().clean(); sale=c.get("sale"); amount=c.get("amount")
        if not sale: self.add_error("sale","Select the customer sale being paid.")
        if sale and amount:
            paid=sale.payments.aggregate(v=Sum("amount"))["v"] or 0
            if amount > sale.total-paid: self.add_error("amount", "Payment exceeds the outstanding sale balance.")
        if sale and sale.source not in ("distribution_order","online_order"): self.add_error("sale","Only Distribution and Online sales can be paid here.")
        if sale and c.get("customer") != sale.customer: self.add_error("customer","Customer must match the selected sale.")
        return c

class StockAdjustmentForm(Base):
    class Meta: model=StockAdjustment; fields=["date","raw_material","finished_good","quantity","reason","description","unit_value","location"]
    widgets={"date":forms.DateInput(attrs={"type":"date"})}
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # These selects display only names. Avoid transferring full inventory
        # records merely to render the adjustment form. Tenant scoping remains
        # enforced by the BusinessManager behind each queryset.
        self.fields["raw_material"].queryset = RawMaterial.objects.only("id", "name", "stock", "cost_per_unit").order_by("name")
        self.fields["finished_good"].queryset = FinishedGood.objects.only("id", "name", "stock", "source_type", "business_id", "units_per_batch").order_by("name")
        self.fields["location"].queryset = InventoryLocation.objects.only("id", "name").order_by("name")
    def clean(self):
        c=super().clean()
        if bool(c.get("raw_material")) == bool(c.get("finished_good")): raise forms.ValidationError("Choose exactly one raw material or finished good.")
        if not c.get("description"): raise forms.ValidationError("Describe the adjustment.")
        return c
