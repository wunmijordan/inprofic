from django import forms
from django.forms import BaseFormSet, formset_factory
from django.utils import timezone
from .models import Expense, ExpensePayment
from core.models import CashAccount

INPUT_CLS = "w-full rounded-md border border-[#D9CFB4] bg-white px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#8f172d]/30 focus:border-[#8f172d]"


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs["class"] = INPUT_CLS


class ExpenseForm(StyledModelForm):
    class Meta:
        model = Expense
        fields = ["date", "category", "description", "amount", "payment_status", "payment_method", "account", "vendor", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        accounts = CashAccount.objects.filter(active=True)
        if business is not None:
            accounts = accounts.filter(business=business)
        self.fields["account"].queryset = accounts.order_by("name")
        self.fields["account"].required = False
        if not self.is_bound and not getattr(self.instance, "pk", None):
            self.fields["date"].initial = timezone.localdate()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("payment_status") == "paid" and not cleaned.get("account"):
            self.add_error("account", "Select the cash/bank account used for this payment.")
        return cleaned

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is not None and amount <= 0:
            raise forms.ValidationError("Amount must be greater than zero.")
        return amount


class BaseExpenseFormSet(BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        has_record = any(
            form.cleaned_data
            and not form.cleaned_data.get("DELETE")
            and form.has_changed()
            for form in self.forms
        )
        if not has_record:
            raise forms.ValidationError("Add at least one expense before saving.")


ExpenseFormSet = formset_factory(
    ExpenseForm,
    formset=BaseExpenseFormSet,
    extra=1,
    can_delete=True,
    max_num=25,
    validate_max=True,
)


class ExpensePaymentForm(StyledModelForm):
    class Meta:
        model = ExpensePayment
        fields = ["date", "expense", "amount", "payment_method", "account", "reference", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = CashAccount.objects.filter(active=True).order_by("name")
        self.fields["account"].required = True
        qs = Expense.objects.filter(payment_status="unpaid").order_by("-date", "-id")
        self.fields["expense"].queryset = qs
        self.fields["expense"].label_from_instance = lambda e: f"Expense #{e.pk} — {e.description} — outstanding {e.amount:,.2f}"

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if amount is None or amount <= 0:
            raise forms.ValidationError("Amount must be greater than zero.")
        expense = self.cleaned_data.get("expense")
        if expense and amount > expense.amount:
            raise forms.ValidationError("Payment exceeds the outstanding expense amount.")
        return amount
