"""Review actual receipts separately from Market Stock quantities and pricing."""
import hashlib
import json
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum

from core import ledger
from core.models import FinancialTransaction
from core.services import audit
from sales.models import CustomerPayment, Sale
from sales.repricing import receipt_review, apply_receipt_corrections
from .models import MarketStockMovement
from .release_corrections import can_correct_release

ZERO = Decimal('0')


def market_payment_review(*, business, sale_id, user, amounts=None, lock=False):
    if not can_correct_release(user, business):
        raise PermissionDenied('Inventory, Sales and Finance edit access are required.')
    sales = Sale.objects.filter(business=business, pk=sale_id,
        source__in=['distribution_order', 'online_order'], linked_order__isnull=True)
    if lock:
        sales = sales.select_for_update()
    sale = sales.get()
    if not MarketStockMovement.objects.filter(business=business, sale=sale,
            movement_type=MarketStockMovement.RELEASE).exists():
        raise ValidationError('Choose a customer sale released from Market Stock.')
    payments = CustomerPayment.raw_objects.filter(business=business, sale=sale).order_by('pk')
    if lock:
        payments = payments.select_for_update()
    payments = list(payments)
    paid = sum((p.amount for p in payments), ZERO)
    if not payments and sale.transaction_type == 'paid':
        cash = FinancialTransaction.raw_objects.filter(business=business, reference=f'SALE-{sale.pk}',
            reversed=False, reversal_of__isnull=True, transaction_type=FinancialTransaction.INCOME).aggregate(total=Sum('amount'))['total']
        paid = cash if cash is not None else sale.total
    net = sale.net_total.quantize(Decimal('.01'))
    state = [sale.pk, str(sale.updated_at), str(net), str(paid),
        [(p.pk, str(p.updated_at), str(p.amount), p.account_id, p.reference) for p in payments]]
    fingerprint = hashlib.sha256(json.dumps(state).encode()).hexdigest()
    defaults = {f'payment-{p.pk}': str(p.amount) for p in payments}
    if not payments and paid > ZERO:
        defaults[f'direct-{sale.pk}'] = str(paid)
    row = {'sale': sale, 'paid': paid, 'net_new': net, 'due': max(net-paid, ZERO),
           'credit': max(paid-net, ZERO), 'payment_rows': payments}
    review = {'rows': [row], 'fingerprint': fingerprint}
    values = defaults if amounts is None else amounts
    result = receipt_review(business, review, values, lock=lock, user=user)
    errors = result['errors']
    if set(values) != set(defaults):
        errors.append('Receipt records changed. Reload the review before correcting them.')
    if not defaults:
        errors.append('No recorded receipts are available to correct. Record new payments through Finance.')
    # Even an unchanged receipt must be uniquely attributable to this sale.
    if any(r['transaction'] is None for r in row['receipts']):
        errors.append('A receipt cannot be matched uniquely to its cash entry. Review it in Finance first.')
    for receipt in row['receipts']:
        if receipt['old'] != receipt['new']:
            ledger.check_scope_date(receipt['date'])
    return {**review, 'sale': sale, 'row': row, 'errors': sorted(set(errors)), 'amounts': values}


@transaction.atomic
def apply_market_payment_review(*, business, sale_id, user, amounts, fingerprint, reason):
    if not (reason or '').strip():
        raise ValidationError('Enter a reason for correcting the recorded payment.')
    review = market_payment_review(business=business, sale_id=sale_id, user=user, amounts=amounts, lock=True)
    if fingerprint != review['fingerprint']:
        raise ValidationError('The sale or receipts changed. Review the current figures before confirming.')
    if review['errors']:
        raise ValidationError(review['errors'])
    row, sale = review['row'], review['sale']
    changes = [{'key': r['key'], 'old': str(r['old']), 'new': str(r['new']),
                'transaction': r['transaction'].pk, 'account': r['transaction'].account_id}
               for r in row['receipts'] if r['old'] != r['new']]
    if not changes:
        raise ValidationError('Enter a corrected amount before confirming.')
    apply_receipt_corrections(business=business, user=user, row=row, context='Market Stock payment correction')
    paid = row['corrected_paid']
    sale.transaction_type = 'paid' if paid >= row['net_new'] else 'partial' if paid > ZERO else 'unpaid'
    sale.save(update_fields=['transaction_type', 'updated_at'])
    audit(business, user, 'correct_market_payment', sale, f'Recorded payment corrected: {reason.strip()}', {
        'reason': reason.strip(), 'receipts': changes, 'old_paid': str(row['paid']), 'new_paid': str(paid),
        'invoice_value': str(row['net_new']), 'balance_due': str(row['corrected_due']),
        'overpayment': str(row['corrected_credit']),
    })
    return sale
