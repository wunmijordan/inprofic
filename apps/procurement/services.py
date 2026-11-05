from django.db import IntegrityError, transaction

from .models import Supplier


def resolve_supplier(business, name, *, created_by=None):
    """Find or safely create this tenant's supplier for a typed name."""
    name = (name or "").strip()
    if not name:
        return None
    supplier = Supplier.raw_objects.filter(business_id=business.pk, name__iexact=name).first()
    if supplier:
        return supplier
    try:
        with transaction.atomic():
            return Supplier.raw_objects.create(
                business=business, created_by=created_by, name=name,
            )
    except IntegrityError:
        # A concurrent request may have created the case-insensitive match.
        supplier = Supplier.raw_objects.filter(business_id=business.pk, name__iexact=name).first()
        if supplier is None:
            raise
        return supplier


def request_orders(po, *, lock=False):
    """All sibling vendor orders, across ledger periods but never tenants."""
    from .models import PurchaseOrder
    qs = PurchaseOrder.raw_objects.filter(business_id=po.business_id)
    qs = qs.filter(request_id=po.request_id) if po.request_id else qs.filter(pk=po.pk)
    if lock:
        qs = qs.select_for_update()
    return qs.order_by('pk')


def request_editable(orders):
    return all(po.status in {'draft', 'ordered'} for po in orders)


def order_audit_state(po):
    from decimal import Decimal
    items = list(po.items.all())
    return {'supplier': po.supplier, 'date': str(po.date), 'status': po.status,
            'payment_status': po.payment_status, 'account_id': po.account_id,
            'payment_method': po.payment_method,
            'total': str(sum((item.qty * item.unit_cost for item in items), Decimal('0'))),
            'items': [{'id': item.pk, 'raw_material_id': item.raw_material_id, 'finished_good_id': item.finished_good_id,
                       'qty': str(item.qty), 'unit_cost': str(item.unit_cost)} for item in items]}


def recorded_vendor_payments(orders):
    """Actual settlement, across periods, independent of the request's label."""
    from decimal import Decimal
    from core.models import AuditLog, FinancialTransaction
    orders = list(orders)
    if not orders:
        return {}, []
    cash = list(FinancialTransaction.raw_objects.filter(
        business_id=orders[0].business_id, reference__in=[f'PO-{po.pk}' for po in orders],
        category='Procurement', transaction_type=FinancialTransaction.OUTFLOW, reversed=False,
    ))
    paid = {po.pk: sum((payment.amount for payment in po.payments.all()), Decimal('0')) for po in orders}
    lookup = {f'PO-{po.pk}': po.pk for po in orders}
    payment_ids = [str(payment.pk) for po in orders for payment in po.payments.all()]
    # User-entered references are not proof that a cash entry was linked.
    links = AuditLog.raw_objects.filter(business_id=orders[0].business_id, model_name='SupplierPayment',
        action='create', object_id__in=payment_ids, description='Linked existing procurement cash to vendor settlement')
    represented = {(log.metadata.get('purchase_order_id'), log.metadata.get('cash_entry_id')) for log in links}
    unmatched = [entry for entry in cash if (lookup[entry.reference], entry.pk) not in represented]
    for entry in unmatched:
        paid[lookup[entry.reference]] += entry.amount
    return paid, unmatched


def link_direct_vendor_payments(orders, user):
    """Link existing cash to settlement without posting a second cash movement."""
    from core import ledger
    from core.services import audit
    from .models import SupplierPayment
    orders = list(orders)
    _paid, cash = recorded_vendor_payments(orders)
    lookup = {f'PO-{po.pk}': po for po in orders}
    for entry in cash:
        po = lookup[entry.reference]
        with ledger.bypass():  # Metadata for an existing historical cash entry.
            payment = SupplierPayment.raw_objects.create(
                business_id=po.business_id, created_by=user, date=entry.date,
                supplier=po.supplier or 'Unnamed supplier', supplier_master=po.supplier_master,
                amount=entry.amount, payment_method=entry.payment_method,
                reference=f'PO-{po.pk}-CASH-{entry.pk}', account=entry.account, purchase_order=po,
                notes=f'Settlement link for existing cash entry #{entry.pk}; original reference {entry.reference}')
            audit(po.business, user, 'create', payment, 'Linked existing procurement cash to vendor settlement',
                  {'cash_entry_id': entry.pk, 'purchase_order_id': po.pk, 'amount': str(entry.amount), 'account_id': entry.account_id})


def retain_deleted_request_payments(orders, user):
    """Deleting an unapproved request never claims that paid cash was refunded."""
    from core.services import audit
    from .models import SupplierPayment
    orders = list(orders)
    link_direct_vendor_payments(orders, user)
    for po in orders:
        # Query again because materializing settlement links invalidates prefetches.
        payments = SupplierPayment.raw_objects.filter(business_id=po.business_id, purchase_order=po)
        for payment in payments:
            audit(po.business, user, 'update', payment, 'Purchase request deleted; payment retained unallocated',
                  {'purchase_order_id': po.pk, 'amount': str(payment.amount), 'reference': payment.reference})
            payment.purchase_order = None
            payment.notes = f'Unallocated after deleting PO #{po.pk}. {payment.notes}'[:255]
            payment.save(update_fields=['purchase_order', 'notes', 'updated_at'])


def describe_request(orders):
    """Attach presentation aggregates without changing vendor accounting records."""
    from decimal import Decimal
    orders = list(orders)
    root = orders[0]
    for vendor in orders:
        vendor.payment_balance = Decimal('0') if vendor.payment_status == 'paid' else max(
            vendor.total - sum((payment.amount for payment in vendor.payments.all()), Decimal('0')), Decimal('0'))
    root.request_orders = orders
    root.request_items = [item for po in orders for item in po.items.all()]
    root.request_total = sum((po.total for po in orders), Decimal('0'))
    root.request_supplier = ' · '.join(po.supplier or 'Unnamed supplier' for po in orders)
    states = {po.status for po in orders if po.total > 0} or {root.status}
    if states <= {'draft', 'ordered'}:
        states = {'draft'}
    root.request_status = next(iter(states)) if len(states) == 1 else 'mixed'
    root.request_status_label = dict(root.STATUS_CHOICES).get(root.request_status, 'Partially received')
    payments = {po.payment_status for po in orders if po.total > 0} or {root.payment_status}
    root.request_payment = next(iter(payments)) if len(payments) == 1 else 'partial'
    root.request_has_payments = any(po.payment_status in {'paid', 'partial'} or po.payments.exists() for po in orders)
    root.request_editable = request_editable(orders)
    root.request_lock_reason = 'Approved or received requests are locked.' if not root.request_editable else ''
    return root


def request_count(queryset):
    """Count approval envelopes, leaving vendor invoice totals untouched."""
    return (queryset.filter(request__isnull=True).count()
            + queryset.filter(request__isnull=False).values('request_id').distinct().count())
