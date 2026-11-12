"""Audited corrections of standalone customer releases; cash receipts stay fixed."""
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Sum

from accounts.services import user_has_permission
from core import ledger
from core.models import EInvoice, FinancialTransaction
from core.quantities import product_portion, product_choice_label, plural_unit
from core.services import audit
from sales.models import Sale, Customer, CustomerPayment
from .models import DistributionReturn, FinishedGood, MarketStockLot, MarketStockMovement, StockMovement
from .services import _allocate_market_stock_line, _record_market_movement, record_finished_good_movement

ZERO = Decimal('0')


def can_correct_release(user, business):
    return all(user_has_permission(user, business, module, 'edit')
               for module in ('inventory', 'sales', 'finance'))


def release_correction_error(sale):
    if sale.source != 'distribution_order' or sale.linked_order_id:
        return 'Only standalone Market Stock customer releases can be edited here.'
    if DistributionReturn.raw_objects.filter(business=sale.business, sale_item__sale=sale).exists():
        return 'This release already has recorded returns. Its original product lines must remain linked to those returns.'
    if EInvoice.raw_objects.filter(business=sale.business, sale=sale).filter(
        Q(status__in=[EInvoice.SUBMITTED, EInvoice.CLEARED]) |
        Q(submitted_at__isnull=False) |
        ~Q(irn='')).exists():
        return 'This release has a submitted e-invoice. Use a credit/debit adjustment rather than changing it.'
    if not MarketStockMovement.objects.filter(business=sale.business, sale=sale,
                                              movement_type=MarketStockMovement.RELEASE).exists():
        return 'This sale was not released from Market Stock.'
    return ''


def include_original_quantities(available, sale):
    """Availability includes this release, which is restored inside the transaction."""
    for item in sale.items.select_related('finished_good__portion_profile'):
        good = item.finished_good
        key = str(good.pk)
        unit, factor = product_portion(good)
        row = available.setdefault(key, {
            'available': '0', 'unit': good.unit, 'name': good.name,
            'unit_singular': plural_unit(good.unit, 1), 'unit_plural': plural_unit(good.unit, 2),
            'portion_multiplier': str(factor), 'selling_unit_singular': plural_unit(unit, 1),
            'selling_unit_plural': plural_unit(unit, 2), 'choice_label': product_choice_label(good),
        })
        row['available'] = str(Decimal(row['available']) + item.total_units)
    return available


@transaction.atomic
def correct_market_release(*, business, sale_id, items, customer, reason, revision, user):
    if not can_correct_release(user, business):
        raise PermissionDenied('Inventory, Sales and Finance edit access are required.')
    sale = Sale.objects.select_for_update().get(business=business, pk=sale_id)
    if str(sale.updated_at) != revision:
        raise ValidationError('This release changed after you opened it. Reload and review the current record.')
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Enter a reason for this correction.')
    error = release_correction_error(sale)
    if error:
        raise ValidationError(error)
    if not items or len({row['finished_good'].pk for row in items}) != len(items):
        raise ValidationError('Add at least one product, with one row per product.')
    try:
        customer = Customer.raw_objects.select_for_update().get(business=business, pk=customer.pk, active=True)
    except Customer.DoesNotExist:
        raise ValidationError('Choose an active customer from this business.')
    segment = ledger.segment_for(business, sale.date)
    ledger.check_scope_date(sale.date)
    if segment == ledger.SEGMENT_BACKLOG and not ledger.can_enter_backlog(user, business):
        raise PermissionDenied('Backlog edit access is required.')
    old_items = list(sale.items.select_for_update().order_by('pk'))
    ids = {item.finished_good_id for item in old_items} | {row['finished_good'].pk for row in items}
    goods = {g.pk: g for g in FinishedGood.raw_objects.select_for_update().filter(
        business=business, pk__in=ids).order_by('pk')}
    if set(goods) != ids:
        raise ValidationError('Choose products from this business.')
    payments = list(CustomerPayment.raw_objects.select_for_update().filter(business=business, sale=sale))
    invoice = EInvoice.raw_objects.select_for_update().filter(business=business, sale=sale).first()
    # Recheck dependencies after line/payment locks, before replacing any lines.
    error = release_correction_error(sale)
    if error:
        raise ValidationError(error)
    original_prices = {}
    for item in old_items:
        if item.commercial_quantity is not None:
            raise ValidationError('This release has a saved commercial package calculation and needs a separate invoice correction.')
        snapshot = {'price': item.price, 'discount': item.discount,
                    'price_unit_quantity': item.price_unit_quantity, 'price_unit': item.price_unit}
        if item.finished_good_id in original_prices and original_prices[item.finished_good_id] != snapshot:
            raise ValidationError('This release has several prices for the same product and needs a separate invoice correction.')
        original_prices[item.finished_good_id] = snapshot
    old_total = sale.total
    paid = sum((p.amount for p in payments), ZERO)
    if not payments and sale.transaction_type == 'paid':
        cash = FinancialTransaction.raw_objects.filter(business=business, reference=f'SALE-{sale.pk}',
            transaction_type=FinancialTransaction.INCOME).aggregate(total=Sum('amount'))['total']
        paid = cash if cash is not None else old_total
    before = [{'id': i.pk, 'product': i.finished_good_id, 'quantity': str(i.total_units),
               'price': str(i.price), 'price_unit_quantity': str(i.price_unit_quantity),
               'unit_cost': str(i.unit_cost), 'discount': str(i.discount),
               'production_batch': i.production_batch_id, 'price_unit': i.price_unit, 'value': str(i.line_total)} for i in old_items]
    old_customer = {'id': sale.customer_master_id, 'name': sale.customer}
    movement_totals = list(MarketStockMovement.objects.filter(business=business, sale=sale,
        movement_type=MarketStockMovement.RELEASE).order_by().values('lot_id').annotate(total=Sum('quantity')))
    lots = {lot.pk: lot for lot in MarketStockLot.objects.select_for_update().filter(
        business=business, pk__in=[m['lot_id'] for m in movement_totals]).order_by('pk')}
    restored = {}
    for row in movement_totals:
        quantity = -row['total']
        if not quantity:
            continue
        lot = lots[row['lot_id']]
        if quantity < 0 or (not lot.active and lot.closed_reason not in ('', 'released')):
            raise ValidationError('A source batch has been written off or reversed and cannot be restored by editing this release.')
        restored[lot.finished_good_id] = restored.get(lot.finished_good_id, ZERO) + quantity
        if segment != ledger.SEGMENT_BACKLOG:
            lot.quantity_available += quantity
            if lot.quantity_available > lot.quantity_received:
                raise ValidationError('The original release no longer matches its batch balances. Review its stock history.')
            lot.active, lot.closed_reason = True, ''
            lot.save(update_fields=['quantity_available', 'active', 'closed_reason', 'updated_at'])
        _record_market_movement(lot, date=sale.date, movement_type=MarketStockMovement.RELEASE,
            quantity=quantity, user=user, customer=sale.customer_master, sale=sale,
            note=f'Release correction: {reason}')
        record_finished_good_movement(goods[lot.finished_good_id], quantity, StockMovement.FG_MARKET_RELEASE,
            note=f'Release correction: {reason}', reference=f'SALE-{sale.pk}', affects_stock=False,
            unit_value=lot.unit_cost, on_date=sale.date)
    expected = {}
    for item in old_items:
        expected[item.finished_good_id] = expected.get(item.finished_good_id, ZERO) + item.total_units
    if restored != expected:
        raise ValidationError('The release lines do not match their stock movements. Review the stock history before correcting it.')
    if segment != ledger.SEGMENT_BACKLOG:
        for pk, quantity in restored.items():
            goods[pk].total_delivered_to_customers -= quantity
            goods[pk].save(update_fields=['total_delivered_to_customers', 'updated_at'])
    # Explicit compensating movements above reverse the allocation. No stock or
    # payment reversal depends on deleting these replaceable invoice lines.
    sale.items.all().delete()
    sale.customer_master, sale.customer = customer, customer.name
    sale.save(update_fields=['customer_master', 'customer', 'updated_at'])
    for row in items:
        _allocate_market_stock_line(business=business, good=goods[row['finished_good'].pk], customer=customer,
            quantity=row['quantity'], date=sale.date, user=user, note=f'Corrected release: {reason}', sale=sale,
            price_snapshot=original_prices.get(row['finished_good'].pk) if old_customer['id'] == customer.pk else None)
    if not payments and paid > 0:
        CustomerPayment.raw_objects.create(business=business, created_by=user, sale=sale, date=sale.date,
            customer=customer.name, customer_master=customer, amount=paid, payment_method=sale.payment_method,
            account=sale.account, reference=f'RELEASE-OPEN-{sale.pk}',
            notes='Original receipt preserved during release correction. No additional cash received.')
    elif old_customer['id'] != customer.pk or old_customer['name'] != customer.name:
        CustomerPayment.raw_objects.filter(business=business, sale=sale).update(customer_master=customer, customer=customer.name)
    new_total = sale.total
    sale.transaction_type = 'paid' if paid >= new_total else ('partial' if paid > 0 else 'unpaid')
    sale.save(update_fields=['transaction_type', 'updated_at'])
    if invoice:
        invoice.status, invoice.payload = EInvoice.DRAFT, {}
        invoice.error = 'Release corrected; prepare this invoice again before submission.'
        invoice.save(update_fields=['status', 'payload', 'error', 'updated_at'])
    audit(business, user, 'correct_market_release', sale, f'Customer release corrected: {reason}', {
        'reason': reason, 'old_customer': old_customer, 'new_customer': {'id': customer.pk, 'name': customer.name},
        'before': before, 'after': [{'product': i.finished_good_id, 'quantity': str(i.total_units),
            'price': str(i.price), 'discount': str(i.discount), 'unit_cost': str(i.unit_cost),
            'production_batch': i.production_batch_id, 'value': str(i.line_total)} for i in sale.items.all()],
        'old_total': str(old_total), 'new_total': str(new_total), 'cash_preserved': str(paid),
        'balance_due': str(max(new_total-paid, ZERO)), 'overpayment': str(max(paid-new_total, ZERO)),
    })
    return sale
