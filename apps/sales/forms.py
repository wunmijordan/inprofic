from django import forms
from django.forms import inlineformset_factory
from core.forms import ExistingAwareInlineFormSet
from django.db.models import Q
from .models import Customer, Sale, SaleItem, CustomerProductPrice
from core.models import CashAccount
from inventory.models import FinishedGood

INPUT_CLS = "w-full rounded-md border border-[#D9CFB4] bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#8f172d]/30 focus:border-[#8f172d]"


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs["class"] = INPUT_CLS


class CustomerForm(StyledModelForm):
    class Meta:
        model = Customer
        fields = ["name", "phone", "email", "address", "region", "customer_group", "credit_limit", "payment_terms_days", "active", "notes"]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["active"].label = "Available for use"
        self.fields["active"].help_text = "Turn this off to archive the customer without removing their history."

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Customer name is required.")
        return name


class SaleForm(StyledModelForm):
    class Meta:
        model = Sale
        fields = ["date", "customer_master", "customer", "service_mode", "table_reference", "transaction_type", "unpaid_description", "payment_method", "account"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}), "unpaid_description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, business=None, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)
        self.fields["customer_master"].queryset = Customer.objects.filter(active=True).order_by("name")
        self.fields["customer_master"].required = False
        self.fields["customer"].required = False
        self.fields["account"].queryset = CashAccount.objects.filter(active=True).order_by("name")
        self.fields["account"].required = False
        self.fields["transaction_type"].choices = [("paid", "Paid"), ("unpaid", "Unpaid")]
        if business and business.is_wholesale:
            self.fields["customer_master"].required = True
            self.fields["customer_master"].label = "Trade customer"
            self.fields["customer"].required = False
        if not business or not business.is_restaurant:
            self.fields.pop("service_mode")
            self.fields.pop("table_reference")
        else:
            self.fields["service_mode"].required = True
            self.fields["service_mode"].initial = Sale.SERVICE_DINE_IN
            self.fields["table_reference"].required = False
            self.fields["table_reference"].label = "Table / service reference"

    def clean(self):
        cleaned = super().clean()
        master = cleaned.get("customer_master")
        if master:
            cleaned["customer"] = master.name
        elif self.business and self.business.is_wholesale:
            self.add_error("customer_master", "Select the trade customer receiving this stock.")
        elif not (cleaned.get("customer") or "").strip():
            cleaned["customer"] = "Walk-in"
        if cleaned.get("transaction_type") == "unpaid" and not (cleaned.get("unpaid_description") or "").strip():
            self.add_error("unpaid_description", "Add the credit or non-cash reason for this unpaid sale.")
        if cleaned.get("transaction_type") == "paid" and not cleaned.get("account"):
            self.add_error("account", "Select the cash/bank account that received this payment.")
        if self.business and self.business.is_restaurant:
            if (
                cleaned.get("service_mode") == Sale.SERVICE_DINE_IN
                and self.business.restaurant_table_service
                and not (cleaned.get("table_reference") or "").strip()
            ):
                self.add_error("table_reference", "Enter the table number or dine-in reference.")
            if cleaned.get("service_mode") != Sale.SERVICE_DINE_IN:
                cleaned["table_reference"] = (cleaned.get("table_reference") or "").strip()
        return cleaned


class SaleItemForm(StyledModelForm):
    class Meta:
        model = SaleItem
        fields = ["finished_good", "batch_qty", "piece_qty", "discount"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The Sales form is the only place where physical-store products are
        # sold directly. Only finished goods configured for physical-store
        # shelf stock are therefore valid choices here.
        self.fields["finished_good"].queryset = FinishedGood.objects.filter(
            Q(stock__isnull=False, reorder_level__gt=0)
            | Q(stock__isnull=False, business__vertical__in=("wholesale", "retail"))
            | Q(transferred_market_stock__gt=0),
        ).order_by("name")


SaleItemFormSet = inlineformset_factory(Sale, SaleItem, form=SaleItemForm, formset=ExistingAwareInlineFormSet, extra=1, can_delete=True)


class CustomerProductPriceForm(StyledModelForm):
    class Meta:
        model = CustomerProductPrice
        fields = ["finished_good", "channel", "price"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["finished_good"].queryset = FinishedGood.objects.all().order_by("name")
        self.fields["channel"].required = True
        self.fields["price"].min_value = 0


CustomerProductPriceFormSet = inlineformset_factory(
    Customer, CustomerProductPrice, form=CustomerProductPriceForm,
    formset=ExistingAwareInlineFormSet,
    extra=1, can_delete=True,
)
