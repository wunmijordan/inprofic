from decimal import Decimal
from .models import AuditLog, FinancialTransaction, CashAccount


def audit(business, user, action, obj, description, metadata=None):
    return AuditLog.objects.create(
        business=business, created_by=user, action=action,
        model_name=obj.__class__.__name__ if obj else "system",
        object_id=str(getattr(obj, "pk", "")), description=description,
        metadata=metadata or {},
    )


def record_cash(business, user, *, date, amount, transaction_type, category, description,
                payment_method="", reference="", account=None):
    amount = Decimal(amount or 0)
    if amount <= 0:
        return None
    if account is None:
        account = CashAccount.objects.filter(business=business, active=True).order_by("id").first()
        if account is None:
            account = CashAccount.objects.create(business=business, name="Main Cash", account_type="cash", created_by=user)
    elif account.business_id != business.id:
        raise ValueError("Cash account belongs to a different business.")
    return FinancialTransaction.objects.create(
        business=business, created_by=user, date=date, amount=amount,
        transaction_type=transaction_type, category=category,
        description=description, payment_method=payment_method or "",
        reference=reference or "", account=account,
    )

def tenant_backup_querysets(business):
    """Return explicitly tenant-scoped querysets for the operational backup.

    Imports are intentionally local so the shared core services module does not
    introduce cross-app model-import cycles during Django app loading. Every
    child table without its own business foreign key is scoped through its
    tenant-owned parent.
    """
    from inventory.models import (
        RawMaterial, FinishedGood, FinishedGoodChannelPrice, RecipeItem,
        ProductionMaterial, InventoryLocation, StockAdjustment,
        OperationalSupplyDispense, MarketStockLot, MarketStockMovement,
        DistributionReturn, StockMovement,
    )
    from procurement.models import (
        PurchaseOrder, PurchaseOrderItem, RawMaterialCostSnapshot, SupplierPayment,
    )
    from production.models import (
        Order, OrderNumberSequence, OrderItem, OrderAllocation, OrderAllocationLine, OrderMaterialUsage,
        OrderFlexibleMaterialReport, ProductionRun,
        ProductionRunOrder, ProductionRunMaterial, ProductionBatch,
        ProductionOffcutAllocation, ProductionBatchReconciliation,
        ProductionQualityCheck, ProductionCostSnapshot, ProductionCostLine,
    )
    from sales.models import Customer, CustomerProductPrice, Sale, SaleItem, CustomerPayment
    from expenses.models import Expense, ExpensePayment

    return [
        business.__class__.objects.filter(pk=business.pk),
        CashAccount.raw_objects.filter(business=business),
        FinancialTransaction.raw_objects.filter(business=business),
        AuditLog.raw_objects.filter(business=business),

        RawMaterial.raw_objects.filter(business=business),
        FinishedGood.raw_objects.filter(business=business),
        FinishedGoodChannelPrice.objects.filter(finished_good__business=business),
        RecipeItem.objects.filter(finished_good__business=business),
        ProductionMaterial.objects.filter(finished_good__business=business),
        InventoryLocation.raw_objects.filter(business=business),
        StockAdjustment.raw_objects.filter(business=business),
        OperationalSupplyDispense.raw_objects.filter(business=business),
        MarketStockLot.raw_objects.filter(business=business),
        MarketStockMovement.raw_objects.filter(business=business),
        DistributionReturn.raw_objects.filter(business=business),
        StockMovement.raw_objects.filter(business=business),

        PurchaseOrder.raw_objects.filter(business=business),
        PurchaseOrderItem.objects.filter(purchase_order__business=business),
        RawMaterialCostSnapshot.raw_objects.filter(business=business),
        SupplierPayment.raw_objects.filter(business=business),

        Customer.raw_objects.filter(business=business),
        CustomerProductPrice.raw_objects.filter(business=business),

        Order.raw_objects.filter(business=business),
        OrderNumberSequence.raw_objects.filter(business=business),
        OrderItem.objects.filter(order__business=business),
        OrderMaterialUsage.raw_objects.filter(business=business),
        OrderFlexibleMaterialReport.raw_objects.filter(business=business),
        ProductionRun.raw_objects.filter(business=business),
        ProductionRunOrder.objects.filter(production_run__business=business),
        ProductionRunMaterial.raw_objects.filter(business=business),
        ProductionBatch.raw_objects.filter(business=business),
        ProductionOffcutAllocation.raw_objects.filter(business=business),
        ProductionBatchReconciliation.raw_objects.filter(business=business),
        ProductionQualityCheck.raw_objects.filter(business=business),
        ProductionCostSnapshot.raw_objects.filter(business=business),
        ProductionCostLine.objects.filter(snapshot__business=business),

        Sale.raw_objects.filter(business=business),
        SaleItem.objects.filter(sale__business=business),
        # After Sale: an allocation points at the customer sale it created.
        OrderAllocation.objects.filter(order__business=business),
        OrderAllocationLine.objects.filter(allocation__order__business=business),
        CustomerPayment.raw_objects.filter(business=business),
        Expense.raw_objects.filter(business=business),
        ExpensePayment.raw_objects.filter(business=business),
    ]


def tenant_backup_objects(business):
    """Lazy iterable for Django serialization; never crosses tenant ownership."""
    from itertools import chain
    return chain.from_iterable(tenant_backup_querysets(business))