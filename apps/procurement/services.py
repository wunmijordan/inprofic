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


def request_editable(orders, *, posted_references=None):
    from core.models import FinancialTransaction
    orders = list(orders)
    if any(po.status not in {'draft', 'ordered'} or po.payment_status != 'unpaid' or po.payments.exists() for po in orders):
        return False
    references = [ref for po in orders for ref in (f'PO-{po.pk}', f'PO-{po.pk}-INITIAL')]
    if posted_references is not None:
        return not any(ref in posted_references for ref in references)
    return not FinancialTransaction.raw_objects.filter(
        business_id=orders[0].business_id,
        reference__in=references,
    ).exists()


def describe_request(orders, *, posted_references=None):
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
    root.request_editable = request_editable(orders, posted_references=posted_references)
    return root


def request_count(queryset):
    """Count approval envelopes, leaving vendor invoice totals untouched."""
    return (queryset.filter(request__isnull=True).count()
            + queryset.filter(request__isnull=False).values('request_id').distinct().count())
