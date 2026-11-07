"""Audited invoice corrections, with separately reviewed receipt corrections."""
import hashlib
import json
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q

from accounts.services import user_has_permission
from core import ledger
from core.models import EInvoice, FinancialTransaction
from core.services import audit, record_cash
from .models import Customer, CustomerPayment, CustomerProductPrice, Sale, SaleItem, return_prefetch

ZERO = Decimal('0')
CENT = Decimal('.01')


def require_repricing(user, business):
    if not all(user_has_permission(user, business, module, 'edit') for module in ('sales', 'finance')):
        raise PermissionDenied('Sales and Finance edit access are required to correct earlier invoices.')


def price_review(*, business, customer, start, end, user, lock=False, include_receipts=False):
    """Read saved agreements and invoice/payment snapshots; hash every input."""
    require_repricing(user, business)
    if customer.business_id != business.pk:
        raise PermissionDenied('Customer belongs to a different business.')
    prices = CustomerProductPrice.raw_objects.filter(business=business, customer=customer,
        finished_good__business=business, channel='distribution')
    if lock:
        prices = prices.select_for_update(of=('self',))
    agreements = {row.finished_good_id: row.price for row in prices}
    sales = Sale.raw_objects.filter(business=business, source='distribution_order', date__range=(start, end)).filter(
        Q(customer_master=customer) | Q(customer_master__isnull=True, customer=customer.name)
    ).exclude(linked_order__status='reversed').order_by('pk')
    if lock:
        sales = sales.select_for_update(of=('self',))
    rows, state, errors = [], [str(customer.pk), start.isoformat(), end.isoformat(), sorted((k, str(v)) for k, v in agreements.items())], []
    for sale in sales:
        item_query = SaleItem.objects.filter(sale=sale).select_related('finished_good').prefetch_related(return_prefetch()).order_by('pk')
        if lock:
            item_query = item_query.select_for_update(of=('self',))
        items = list(item_query)
        changes = [item for item in items if item.finished_good_id in agreements and item.price != agreements[item.finished_good_id]]
        if not changes and not (include_receipts and any(item.finished_good_id in agreements for item in items)):
            continue
        payment_query = CustomerPayment.raw_objects.filter(business=business, sale=sale).order_by('pk')
        if lock:
            payment_query = payment_query.select_for_update(of=('self',))
        payments = list(payment_query)
        invoices = EInvoice.raw_objects.filter(business=business, sale=sale)
        if lock:
            invoices = invoices.select_for_update(of=('self',))
        invoice = invoices.first()
        state.append([sale.pk, sale.transaction_type, str(sale.updated_at),
            [(i.pk, str(i.updated_at), str(i.price), str(i.discount), str(i.total_units), str(i.commercial_quantity), str(i.commercial_unit_price), str(i.returned_units)) for i in items],
            [(p.pk, str(p.updated_at), str(p.amount)) for p in payments],
            [invoice.status, str(invoice.updated_at)] if invoice else None])
        old = sum((item.line_total for item in items), ZERO).quantize(CENT)
        new = old
        detail = []
        for item in changes:
            if item.commercial_quantity is not None and item.commercial_unit_price is not None:
                errors.append(f'Sale #{sale.pk}: a commercial portion/bulk price needs a separate correction.')
                continue
            price = agreements[item.finished_good_id]
            if price < item.discount:
                errors.append(f'Sale #{sale.pk}: the agreed price is below the saved discount.')
            new += item.total_units * (price - item.price)
            detail.append({'item': item, 'old_price': item.price, 'new_price': price})
        try:
            if ledger.segment_for(business, sale.date) == ledger.SEGMENT_BACKLOG and not ledger.can_enter_backlog(user, business):
                errors.append(f'Sale #{sale.pk}: permission to edit the earlier ledger period is required.')
        except ValidationError as exc:
            errors.extend(exc.messages)
        if invoice and (invoice.status in (EInvoice.SUBMITTED, EInvoice.CLEARED) or invoice.submitted_at or invoice.irn):
            errors.append(f'Sale #{sale.pk}: the submitted e-invoice needs a credit/debit adjustment instead.')
        paid = sum((payment.amount for payment in payments), ZERO)
        # A paid-at-creation sale can have a cash ledger entry without a payment row.
        paid = max(paid, old if sale.transaction_type == 'paid' and not payments else ZERO)
        new = new.quantize(CENT)
        if not changes and (paid == old or paid == ZERO):
            continue
        returned_credit = sum((i.return_credit for i in items), ZERO)
        returned_credit += sum((d['item'].returned_units * (d['new_price'] - d['old_price']) for d in detail), ZERO)
        net_new = max(new - returned_credit, ZERO).quantize(CENT)
        rows.append({'net_new': net_new, 'sale': sale, 'changes': detail, 'old': old, 'new': new, 'paid': paid,
            'due': max(net_new - paid, ZERO), 'credit': max(paid - net_new, ZERO),
            'payment_rows': payments, 'invoice': invoice})
    fingerprint = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    return {'rows': rows, 'fingerprint': fingerprint, 'errors': sorted(set(errors)),
        'count': len(rows), 'old_total': sum((r['old'] for r in rows), ZERO),
        'new_total': sum((r['new'] for r in rows), ZERO),
        'due_total': sum((r['due'] for r in rows), ZERO),
        'credit_total': sum((r['credit'] for r in rows), ZERO)}


@transaction.atomic
def apply_price_review(*, business, customer, start, end, user, fingerprint, receipt_corrections=None):
    """Recheck under locks; only explicitly reviewed receipts can change cash."""
    from production.models import Order, OrderItem, OrderAllocation, OrderAllocationLine
    require_repricing(user, business)
    customer = Customer.raw_objects.select_for_update().get(pk=customer.pk, business=business)
    review = price_review(business=business, customer=customer, start=start, end=end, user=user, lock=True, include_receipts=receipt_corrections is not None)
    corrections = receipt_review(business, review, receipt_corrections, lock=True) if receipt_corrections is not None else None
    if review['fingerprint'] != fingerprint:
        raise ValidationError('Prices, invoices or payments changed. Review the updated figures before confirming.')
    if review['errors']:
        raise ValidationError(review['errors'])
    if corrections and corrections['errors']:
        raise ValidationError(corrections['errors'])
    # All selected dates have passed period authorization above. This correction
    # can intentionally cover both ledger segments without changing their scope.
    with ledger.bypass():
        for row in review['rows']:
            sale = row['sale']
            paid_before = row['paid']
            recorded = sum((p.amount for p in row['payment_rows']), ZERO)
            baseline = row['paid'] - recorded
            if corrections:
                apply_receipt_corrections(business=business, user=user, row=row)
                baseline = ZERO
                row['paid'] = sum((receipt['new'] for receipt in row['receipts']), ZERO)
                row['due'] = max(row['net_new'] - row['paid'], ZERO)
                row['credit'] = max(row['paid'] - row['net_new'], ZERO)
            if baseline > ZERO:
                CustomerPayment.raw_objects.create(business=business, created_by=user, date=sale.date,
                    customer=sale.customer, customer_master=customer, sale=sale, amount=baseline,
                    payment_method=sale.payment_method or 'Cash', account=sale.account,
                    reference=f'REPRICE-OPEN-{sale.pk}',
                    notes='Original paid-at-creation settlement preserved during price correction. No new cash receipt.')
            metadata = {'start': str(start), 'end': str(end), 'old_value': str(row['old']),
                'new_value': str(row['new']), 'recorded_paid_after': str(row['paid']), 'recorded_paid_before': str(paid_before),
                'balance_due': str(row['due']), 'credit_to_review': str(row['credit']), 'items': [], 'payment_corrections': [
                    {'key': r['key'], 'old_amount': str(r['old']), 'new_amount': str(r['new']), 'transaction': r['transaction'].pk if r['transaction'] else None}
                    for r in row.get('receipts', [])]}
            for change in row['changes']:
                item = change['item']
                item.price = change['new_price']
                item.save(update_fields=['price', 'updated_at'])
                metadata['items'].append({'id': item.pk, 'product': item.finished_good_id,
                    'old_price': str(change['old_price']), 'new_price': str(change['new_price'])})
                # Multi-customer runs retain each customer's own allocation price.
                OrderAllocationLine.objects.filter(allocation__sale=sale, allocation__order__business=business,
                    order_item__finished_good_id=item.finished_good_id).update(price=item.price)
                if sale.linked_order_id:
                    order = Order.raw_objects.select_for_update().get(pk=sale.linked_order_id, business=business)
                    if order.order_type == 'distribution' and not order.is_market_stock and (
                        order.customer_id == customer.pk or (not order.customer_id and order.customer_name == customer.name)):
                        OrderItem.objects.filter(order=order, finished_good_id=item.finished_good_id).update(price=item.price)
            state = 'paid' if row['paid'] >= sale.net_total else ('partial' if row['paid'] > ZERO else 'unpaid')
            sale.transaction_type = state
            sale.save(update_fields=['transaction_type', 'updated_at'])
            OrderAllocation.objects.filter(sale=sale, order__business=business).update(payment_status='paid' if state == 'paid' else 'unpaid')
            if sale.linked_order_id:
                Order.raw_objects.filter(pk=sale.linked_order_id, business=business, order_type='distribution',
                    is_market_stock=False).filter(Q(customer=customer) | Q(customer__isnull=True, customer_name=customer.name)).update(customer_payment_status='paid' if state == 'paid' else 'unpaid')
            if row['invoice']:
                invoice = row['invoice']
                invoice.status, invoice.payload = EInvoice.DRAFT, {}
                invoice.error = 'Prices corrected; prepare this invoice again before submission.'
                invoice.save(update_fields=['status', 'payload', 'error', 'updated_at'])
            audit(business, user, 'reprice', sale, f'Customer price correction for {customer.name}', metadata)
    return review


def receipt_review(business, review, amounts=None, *, lock=False):
    """Match each receipt to exactly one cash entry; never guess across accounts.

    Fully settled invoices initially propose their corrected full value. Partial
    receipts retain their original amount until the reviewer enters the actual
    receipt. Every proposed split is visible before confirmation.
    """
    errors, seen, defaults = [], set(), {}
    for row in review['rows']:
        sale = row['sale']
        receipts = []
        for payment in row['payment_rows']:
            reference = payment.reference or f'CUSTPAY-{payment.pk}'
            if reference == f'REPRICE-OPEN-{sale.pk}' and payment.notes.startswith((
                    'Original paid-at-creation settlement preserved during price correction.',
                    'Paid-at-creation settlement after reviewed receipt correction;')):
                reference = f'ORDER-{sale.linked_order_id}' if sale.linked_order_id else f'SALE-{sale.pk}'
            receipts.append({'key': f'payment-{payment.pk}', 'old': payment.amount, 'payment': payment,
                'date': payment.date, 'method': payment.payment_method, 'account': payment.account_id,
                'reference': reference})
        baseline = row['paid'] - sum((p.amount for p in row['payment_rows']), ZERO)
        if baseline > ZERO:
            receipts.append({'key': f'direct-{sale.pk}', 'old': baseline, 'payment': None,
                'date': sale.date, 'method': sale.payment_method, 'account': sale.account_id,
                'reference': f'ORDER-{sale.linked_order_id}' if sale.linked_order_id else f'SALE-{sale.pk}'})
        remainder = row['net_new']
        for index, receipt in enumerate(receipts):
            proposed = receipt['old']
            if sale.transaction_type == 'paid' and row['paid'] > ZERO:
                proposed = (row['net_new'] * receipt['old'] / row['paid']).quantize(CENT) if index < len(receipts)-1 else remainder
                remainder -= proposed
            key = receipt['key']
            defaults[key] = str(proposed)
            try:
                amount = Decimal(amounts[key]) if amounts is not None else proposed
                if not amount.is_finite() or amount < ZERO or amount > Decimal('99999999999999.99') or amount != amount.quantize(CENT):
                    raise ValueError()
            except (KeyError, ValueError, ArithmeticError):
                errors.append(f'Sale #{sale.pk}: enter a valid receipt amount with at most two decimal places.')
                amount = receipt['old']
            receipt['new'] = amount
            if amount != receipt['old']:
                try:
                    if ledger.segment_for(business, receipt['date']) == ledger.SEGMENT_BACKLOG and not ledger.can_enter_backlog(user, business):
                        errors.append(f'Sale #{sale.pk}: permission to edit the receipt’s earlier ledger period is required.')
                except ValidationError as exc:
                    errors.extend(exc.messages)
            candidates = FinancialTransaction.raw_objects.filter(business=business, reference=receipt['reference'],
                reversed=False, reversal_of__isnull=True, transaction_type=FinancialTransaction.INCOME,
                amount=receipt['old'], date=receipt['date'], payment_method=receipt['method'])
            if receipt['account']:
                candidates = candidates.filter(account_id=receipt['account'])
            if receipt['payment'] and receipt['reference'] == (receipt['payment'].reference or f"CUSTPAY-{receipt['payment'].pk}"):
                candidates = candidates.filter(category='Customer payment', description__endswith=f'Sale #{sale.pk}')
            if lock:
                candidates = candidates.select_for_update()
            matches = list(candidates[:2])
            receipt['transaction'] = matches[0] if len(matches) == 1 else None
            if receipt['transaction']:
                tx = receipt['transaction']
                if amount != receipt['old'] and tx.account_id is None:
                    errors.append(f'Sale #{sale.pk}: the cash account is missing; review it in Finance first.')
                if tx.pk in seen:
                    errors.append(f'Sale #{sale.pk}: a cash entry is shared or ambiguous; review it in Finance first.')
                seen.add(tx.pk)
                receipt['account_name'] = str(tx.account or 'Account not recorded')
                # Include cash changes in the signed review fingerprint.
                review['fingerprint'] += f':{tx.pk}:{tx.updated_at}:{tx.amount}:{tx.reversed}'
            elif amount != receipt['old'] or not receipt['payment']:
                errors.append(f'Sale #{sale.pk}: the original cash entry could not be identified uniquely; review it in Finance first.')
            receipt['original_due'] = row['due']
        row['receipts'] = receipts
        row['corrected_paid'] = sum((r['new'] for r in receipts), ZERO)
        row['corrected_due'] = max(row['net_new'] - row['corrected_paid'], ZERO)
        row['corrected_credit'] = max(row['corrected_paid'] - row['net_new'], ZERO)
    review['due_total'] = sum((row['corrected_due'] for row in review['rows']), ZERO)
    review['credit_total'] = sum((row['corrected_credit'] for row in review['rows']), ZERO)
    return {'errors': sorted(set(errors)), 'amounts': amounts if amounts is not None else defaults}


def apply_receipt_corrections(*, business, user, row):
    """Compensate the mistaken ledger receipt, then post the reviewed amount."""
    sale = row['sale']
    for receipt in row['receipts']:
        tx, payment = receipt['transaction'], receipt['payment']
        if receipt['old'] != receipt['new']:
            FinancialTransaction.raw_objects.create(business=business, created_by=user, date=tx.date, amount=tx.amount,
                transaction_type=FinancialTransaction.OUTFLOW, category=f'Reversal: {tx.category}'[:80],
                description=f'Price correction: reverse mistaken receipt #{tx.pk}', payment_method=tx.payment_method,
                reference=f'REV-REPRICE-{tx.pk}', account=tx.account, reversal_of=tx)
            tx.reversed = True
            tx.save(update_fields=['reversed', 'updated_at'])
            record_cash(business, user, date=tx.date, amount=receipt['new'],
                transaction_type=FinancialTransaction.INCOME, category=tx.category,
                description=tx.description, payment_method=tx.payment_method, reference=tx.reference, account=tx.account)
        if payment:
            if payment.amount != receipt['new']:
                payment.amount = receipt['new']
                payment.save(update_fields=['amount', 'updated_at'])
        else:
            CustomerPayment.raw_objects.create(business=business, created_by=user, date=sale.date,
                customer=sale.customer, customer_master=sale.customer_master, sale=sale, amount=receipt['new'],
                payment_method=receipt['method'], account=tx.account, reference=f'REPRICE-OPEN-{sale.pk}',
                notes='Paid-at-creation settlement after reviewed receipt correction; cash posted separately.')
