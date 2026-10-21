"""Founder-only, business-scoped operational reset helpers."""

from django.db import transaction
from django.db.models import ForeignKey, OneToOneField
from django.db.models.deletion import PROTECT


def reset_groups():
    from accounts.models import (
        PayrollCalculationRule, PayrollRun, PayrollRunFunding, PayrollStaffProfile,
        PayrollRecurringAdjustment, Payslip, PayslipAdjustmentLine,
        PayslipCalculationLine, PayslipRevision,
    )
    from commerce.models import (
        StorefrontAttributionVisit, CommerceCheckoutItem, CommerceCheckoutSession,
        CommerceGatewayEvent, CommerceIntake, CommerceIntakeItem,
        CommerceNotification, CommerceNotificationRead, CommerceOrderNumberSequence,
        CommercePayment, CommercePaymentAllocation, CommercePaymentClaim,
        CommercePaymentReceipt, CommercePushDelivery, DeliveryAssignment,
        DeliveryBatch, DeliveryEvent, DeliveryIssue, DeliveryMessage, DeliveryQuote,
    )
    from core.models import FinancialTransaction
    from expenses.models import Expense, ExpensePayment
    from inventory.models import (
        DistributionReturn, InventoryAlertState, MarketStockLot, MarketStockMovement,
        OperationalSupplyDispense, RawMaterialMeasurementChange, StockAdjustment,
        StockMovement,
    )
    from procurement.models import PurchaseOrder, PurchaseOrderItem, RawMaterialCostSnapshot, SupplierPayment
    from production.models import (
        Order, OrderAllocation, OrderAllocationLine, OrderFlexibleMaterialReport,
        OrderItem, OrderMaterialUsage, OrderNumberSequence, ProductionBatch,
        ProductionBatchReconciliation, ProductionCostLine, ProductionCostSnapshot,
        ProductionOffcutAllocation, ProductionQualityCheck, ProductionRun,
        ProductionRunMaterial, ProductionRunOrder,
    )
    from sales.models import CustomerPayment, Sale, SaleItem
    from whatsapp_storefront.models import (
        WhatsAppConversation, WhatsAppMessage, WhatsAppUsageEvent, WhatsAppWebhookEvent,
    )

    # Each list contains transactional records only. Master data and setup are
    # deliberately absent: Business, members, subscriptions, materials,
    # recipes, finished goods, customers, suppliers, cash accounts and settings.
    return {
        "production": [
            Order, OrderFlexibleMaterialReport, OrderNumberSequence, OrderItem,
            OrderAllocation, OrderAllocationLine, OrderMaterialUsage, ProductionRun,
            ProductionRunOrder, ProductionRunMaterial, ProductionBatch,
            ProductionOffcutAllocation, ProductionBatchReconciliation,
            ProductionQualityCheck, ProductionCostSnapshot, ProductionCostLine,
        ],
        "procurement": [PurchaseOrder, PurchaseOrderItem, RawMaterialCostSnapshot, SupplierPayment],
        "sales": [Sale, SaleItem, CustomerPayment],
        "expenses": [Expense, ExpensePayment],
        "inventory": [
            RawMaterialMeasurementChange, StockAdjustment, OperationalSupplyDispense,
            MarketStockLot, MarketStockMovement, DistributionReturn, StockMovement,
            InventoryAlertState,
        ],
        # These rows have PROTECT references to finance transactions. Clear
        # only the attached evidence when the rest of Payroll/Commerce is kept.
        "finance": [FinancialTransaction, CommercePaymentReceipt, PayrollRunFunding, PayslipRevision],
        "commerce": [
            DeliveryQuote, DeliveryAssignment, DeliveryBatch, DeliveryEvent,
            DeliveryMessage, DeliveryIssue, CommerceIntake, CommerceOrderNumberSequence,
            CommerceIntakeItem, CommerceCheckoutSession, CommerceCheckoutItem,
            CommercePayment, CommercePaymentClaim, CommercePaymentReceipt,
            CommercePaymentAllocation, CommerceGatewayEvent, CommerceNotification,
            CommerceNotificationRead, CommercePushDelivery, StorefrontAttributionVisit,
        ],
        "payroll": [
            PayrollRecurringAdjustment, PayrollRunFunding, PayslipAdjustmentLine,
            PayslipCalculationLine, PayslipRevision, Payslip, PayrollRun,
            PayrollStaffProfile, PayrollCalculationRule,
        ],
        "whatsapp": [WhatsAppMessage, WhatsAppConversation, WhatsAppUsageEvent, WhatsAppWebhookEvent],
    }


GROUP_LABELS = {
    "production": "Production orders, runs, batches and cost snapshots",
    "procurement": "Purchase orders, supplier payments and procurement cost snapshots",
    "sales": "Sales and customer payments",
    "expenses": "Expenses and expense payments",
    "inventory": "Stock movements, adjustments, market lots and alert acknowledgements",
    "finance": "Financial transaction log and attached payment evidence",
    "commerce": "Storefront checkouts, payments, notifications and deliveries",
    "payroll": "Payroll staff, rules, runs, payslips and funding records",
    "whatsapp": "WhatsApp conversations, messages, usage and pending webhooks",
}


def _manager(model):
    return getattr(model, "raw_objects", model.objects)


def _business_lookup(model, seen=None):
    """Return a relation lookup from model to its owning Business."""
    seen = set(seen or ())
    if model in seen:
        return None
    seen.add(model)
    if any(field.name == "business" for field in model._meta.get_fields() if getattr(field, "concrete", False)):
        return "business_id"
    for field in model._meta.get_fields():
        if isinstance(field, (ForeignKey, OneToOneField)):
            parent_lookup = _business_lookup(field.remote_field.model, seen.copy())
            if parent_lookup:
                return f"{field.name}__{parent_lookup}"
    return None


def _scoped_queryset(model, business):
    lookup = _business_lookup(model)
    if not lookup:
        return None
    return _manager(model).filter(**{lookup: business.pk})


def _children_first(models):
    pending = set(models)
    result = []
    while pending:
        leaves = []
        for parent in pending:
            referenced_by_pending = any(
                child is not parent and any(
                    isinstance(field, (ForeignKey, OneToOneField))
                    and field.remote_field.model is parent
                    for field in child._meta.get_fields()
                )
                for child in pending
            )
            if not referenced_by_pending:
                leaves.append(parent)
        if not leaves:  # Break a cycle defensively; the database remains authoritative.
            leaves = [next(iter(pending))]
        result.extend(sorted(leaves, key=lambda model: model._meta.label_lower))
        pending.difference_update(leaves)
    return result


def _whatsapp_webhook_queryset(model, business):
    from whatsapp_storefront.models import WhatsAppConnection

    phone_ids = list(
        WhatsAppConnection.raw_objects.filter(business_id=business.pk).values_list("phone_number_id", flat=True)
    )
    if not phone_ids:
        return model.objects.none()
    return model.objects.filter(payload__phone_number_id__in=phone_ids)


def _model_queryset(model, business):
    if model._meta.label_lower == "whatsapp_storefront.whatsappwebhookevent":
        return _whatsapp_webhook_queryset(model, business)
    return _scoped_queryset(model, business)


def _delete_queryset(queryset):
    """Delete rows while honoring protected self-references within one model."""
    model = queryset.model
    protected_self_fields = [
        field for field in model._meta.get_fields()
        if isinstance(field, (ForeignKey, OneToOneField))
        and field.remote_field.model is model
        and field.remote_field.on_delete is PROTECT
    ]
    if not protected_self_fields:
        return queryset.delete()

    pending = set(queryset.values_list("pk", flat=True))
    deleted = (0, {})
    while pending:
        referenced = set()
        for field in protected_self_fields:
            referenced.update(
                queryset.filter(pk__in=pending, **{f"{field.name}__in": pending})
                .values_list(field.attname, flat=True)
            )
        leaves = pending - referenced
        if not leaves:
            raise ValueError(f"Cannot safely delete cyclic protected references in {model._meta.label}.")
        result = queryset.filter(pk__in=leaves).delete()
        deleted = (deleted[0] + result[0], _merge_delete_counts(deleted[1], result[1]))
        pending.difference_update(leaves)
    return deleted


def _merge_delete_counts(left, right):
    merged = dict(left)
    for label, count in right.items():
        merged[label] = merged.get(label, 0) + count
    return merged


def reset_group_counts(business):
    """Return row counts for every selectable group and the always-cleared audit log."""
    counts = {}
    for key, models in reset_groups().items():
        total = 0
        for model in dict.fromkeys(models):
            queryset = _model_queryset(model, business)
            if queryset is not None:
                total += queryset.count()
        counts[key] = total
    from core.models import AuditLog, AuditQuery
    counts["audit_log"] = _scoped_queryset(AuditLog, business).count()
    counts["audit_queries"] = _scoped_queryset(AuditQuery, business).count()
    return counts


def _reset_models(business, selected):
    from core.models import AuditLog, AuditQuery

    groups = reset_groups()
    models = list(dict.fromkeys(model for key in selected for model in groups[key]))
    models.extend(model for model in (AuditQuery, AuditLog) if model not in models)
    return models


def _capture_reset_snapshot(*, business, selected, actor, counts):
    import gzip
    import hashlib
    from datetime import timedelta
    from itertools import chain

    from django.core import serializers
    from django.utils import timezone

    from accounts.models import BusinessResetSnapshot
    from inventory.models import FinishedGood, RawMaterial

    models = _reset_models(business, selected)
    querysets = []
    for model in models:
        queryset = _model_queryset(model, business)
        if queryset is not None:
            querysets.append(queryset.order_by("pk"))
    serialized = serializers.serialize("json", chain.from_iterable(querysets))
    compressed = gzip.compress(serialized.encode("utf-8"), compresslevel=6)
    stock_before_reset = {
        "raw_materials": {
            str(pk): str(stock)
            for pk, stock in RawMaterial.raw_objects.filter(business_id=business.pk).values_list("pk", "stock")
        },
        "finished_goods": {
            str(pk): str(stock)
            for pk, stock in FinishedGood.raw_objects.filter(
                business_id=business.pk, stock__isnull=False
            ).values_list("pk", "stock")
        },
    }
    now = timezone.now()
    BusinessResetSnapshot.raw_objects.filter(
        business_id=business.pk, expires_at__lte=now
    ).delete()
    return BusinessResetSnapshot.raw_objects.create(
        business=business,
        created_by=actor,
        groups=sorted(selected),
        record_counts=counts,
        stock_before_reset=stock_before_reset,
        compressed_payload=compressed,
        payload_sha256=hashlib.sha256(compressed).hexdigest(),
        expires_at=now + timedelta(days=7),
    )


def reset_preview(business, selected_groups):
    groups = reset_groups()
    selected = set(selected_groups) & set(groups)
    # Inventory activity is required to set stock to zero coherently. The old
    # audit trail is always cleared and replaced by the reset event.
    selected.add("inventory")
    models = list(dict.fromkeys(model for key in selected for model in groups[key]))
    from core.models import AuditLog, AuditQuery
    models.extend(model for model in (AuditQuery, AuditLog) if model not in models)
    counts = {}
    for model in models:
        qs = _scoped_queryset(model, business)
        if model._meta.label_lower == "whatsapp_storefront.whatsappwebhookevent":
            qs = _whatsapp_webhook_queryset(model, business)
        if qs is not None:
            count = qs.count()
            if count:
                counts[f"{model._meta.app_config.verbose_name}: {model._meta.verbose_name_plural}"] = count
    return selected, counts


@transaction.atomic
def execute_business_reset(*, business, selected_groups, actor, preview_counts):
    from core.models import AuditLog, AuditQuery
    from inventory.models import FinishedGood, RawMaterial

    selected, _current_counts = reset_preview(business, selected_groups)
    models = _reset_models(business, selected)
    snapshot = _capture_reset_snapshot(
        business=business, selected=selected, actor=actor, counts=preview_counts,
    )
    deleted_counts = {}
    for model in _children_first(models):
        qs = _scoped_queryset(model, business)
        if model._meta.label_lower == "whatsapp_storefront.whatsappwebhookevent":
            qs = _whatsapp_webhook_queryset(model, business)
        if qs is None:
            continue
        count = qs.count()
        if count:
            _delete_queryset(qs)
            deleted_counts[f"{model._meta.app_config.verbose_name}: {model._meta.verbose_name_plural}"] = count

    # Reset opening inventory without changing any recipe or product setup.
    RawMaterial.raw_objects.filter(business_id=business.pk).update(stock=0)
    FinishedGood.raw_objects.filter(business_id=business.pk, stock__isnull=False).update(stock=0)

    AuditLog.raw_objects.create(
        business=business,
        created_by=actor,
        action="reset",
        model_name="Business",
        object_id=str(business.pk),
        description=f"Founder reset operational data for {business.name}; stock set to zero.",
        metadata={
            "groups": sorted(selected),
            "deleted_counts": deleted_counts,
            "preview_counts": preview_counts,
            "stock_reset_to_zero": True,
            "snapshot_id": snapshot.pk,
            "snapshot_expires_at": snapshot.expires_at.isoformat(),
        },
    )
    return deleted_counts, snapshot


@transaction.atomic
def restore_business_reset_snapshot(*, snapshot, actor):
    import gzip
    import hashlib
    import hmac
    from django.core import serializers
    from django.utils import timezone

    from accounts.models import BusinessResetSnapshot
    from core.models import AuditLog, AuditQuery
    from inventory.models import FinishedGood, RawMaterial

    snapshot = BusinessResetSnapshot.raw_objects.select_for_update().get(pk=snapshot.pk)
    now = timezone.now()
    if snapshot.restored_at:
        raise ValueError("This reset snapshot has already been restored.")
    if snapshot.expires_at <= now:
        raise ValueError("This reset snapshot has expired.")
    compressed = bytes(snapshot.compressed_payload)
    if not hmac.compare_digest(hashlib.sha256(compressed).hexdigest(), snapshot.payload_sha256):
        raise ValueError("The saved reset snapshot failed its integrity check.")
    payload = gzip.decompress(compressed).decode("utf-8")
    restored_objects = list(serializers.deserialize("json", payload))

    selected = set(snapshot.groups) & set(reset_groups())
    models = _reset_models(snapshot.business, selected)
    # Keep the reset audit record and append a restore audit record. Restore
    # older audit evidence with its original identifiers alongside them.
    models = [model for model in models if model is not AuditLog]
    for model in _children_first(models):
        queryset = _model_queryset(model, snapshot.business)
        if queryset is not None:
            _delete_queryset(queryset)

    # Sort individual objects by their serialized foreign-key dependencies.
    # This also handles self-referencing financial reversals whose primary-key
    # order may not reflect their relationship order.
    rows_by_key = {
        (row.object.__class__, row.object.pk): row for row in restored_objects
    }
    dependencies = {key: set() for key in rows_by_key}
    for key, row in rows_by_key.items():
        for field in row.object._meta.get_fields():
            if not isinstance(field, (ForeignKey, OneToOneField)):
                continue
            related_pk = getattr(row.object, field.attname, None)
            related_key = (field.remote_field.model, related_pk)
            if related_pk is not None and related_key in rows_by_key:
                dependencies[key].add(related_key)
    restore_order = []
    pending = set(rows_by_key)
    while pending:
        ready = [key for key in pending if not (dependencies[key] & pending)]
        if not ready:
            raise ValueError("The saved reset snapshot contains cyclic foreign-key references.")
        ready.sort(key=lambda key: (key[0]._meta.label_lower, key[1] or 0))
        restore_order.extend(rows_by_key[key] for key in ready)
        pending.difference_update(ready)
    database_alias = snapshot._state.db or "default"
    for row in restore_order:
        row.save(using=database_alias, force_insert=True, save_m2m=False)
    for row in restore_order:
        for accessor_name, object_list in (row.m2m_data or {}).items():
            getattr(row.object, accessor_name).set(object_list)
        row.m2m_data = None

    stock = snapshot.stock_before_reset or {}
    for model, values in (
        (RawMaterial, stock.get("raw_materials", {})),
        (FinishedGood, stock.get("finished_goods", {})),
    ):
        for pk, quantity in values.items():
            model.raw_objects.filter(business_id=snapshot.business_id, pk=pk).update(stock=quantity)

    snapshot.restored_at = now
    snapshot.restored_by = actor
    snapshot.save(update_fields=["restored_at", "restored_by", "updated_at"])
    AuditLog.raw_objects.create(
        business=snapshot.business,
        created_by=actor,
        action="restore",
        model_name="BusinessResetSnapshot",
        object_id=str(snapshot.pk),
        description=f"Founder restored operational data for {snapshot.business.name}.",
        metadata={"groups": sorted(selected), "snapshot_id": snapshot.pk},
    )
    return len(restored_objects)
