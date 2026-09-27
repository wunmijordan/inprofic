"""Cache invalidation for finance state that is polled by the alert tray."""

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from expenses.models import Expense, ExpensePayment
from procurement.models import PurchaseOrder, SupplierPayment
from sales.models import CustomerPayment, Sale

from .finance_cache import invalidate_finance_alert_cache
from .models import CashAccount, FinancialTransaction

@receiver(
    [post_save, post_delete],
    sender=Sale,
    dispatch_uid="finance_alert_sale_cache",
)
@receiver(
    [post_save, post_delete],
    sender=CustomerPayment,
    dispatch_uid="finance_alert_customer_payment_cache",
)
@receiver(
    [post_save, post_delete],
    sender=PurchaseOrder,
    dispatch_uid="finance_alert_purchase_order_cache",
)
@receiver(
    [post_save, post_delete],
    sender=SupplierPayment,
    dispatch_uid="finance_alert_supplier_payment_cache",
)
@receiver(
    [post_save, post_delete],
    sender=Expense,
    dispatch_uid="finance_alert_expense_cache",
)
@receiver(
    [post_save, post_delete],
    sender=ExpensePayment,
    dispatch_uid="finance_alert_expense_payment_cache",
)
@receiver(
    [post_save, post_delete],
    sender=FinancialTransaction,
    dispatch_uid="finance_alert_financial_transaction_cache",
)
@receiver(
    [post_save, post_delete],
    sender=CashAccount,
    dispatch_uid="finance_alert_cash_account_cache",
)
def _invalidate_finance_alerts(sender, instance, **kwargs):
    business_id = getattr(instance, "business_id", None)
    if business_id:
        # Do not let an in-flight transaction repopulate a snapshot between a
        # pre-commit delete and the actual finance mutation becoming visible.
        transaction.on_commit(
            lambda business_id=business_id: invalidate_finance_alert_cache(business_id)
        )
