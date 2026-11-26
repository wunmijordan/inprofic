"""Compensate a staff/gifting issue before recording its replacement."""
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum

from accounts.services import user_has_permission
from core import ledger
from core.services import audit
from .models import FinishedGood, MarketStockLot, MarketStockMovement, StockAdjustment, StockMovement
from .services import _record_market_movement, issue_product_stock, record_finished_good_movement


@transaction.atomic
def correct_product_issue(*, business, issue_id, revision, reason, good, quantity, purpose, recipient, description, user):
    if not user_has_permission(user, business, 'inventory', 'edit'):
        raise PermissionDenied
    issue = StockAdjustment.objects.select_for_update().get(business=business, pk=issue_id)
    if issue.stock_source != StockAdjustment.SOURCE_MARKET or issue.reason not in {'staff', 'charity'} or issue.quantity >= 0 or issue.reversed:
        raise ValidationError('Only an active Market Stock staff/gifting issue can be corrected here.')
    if str(issue.updated_at) != revision:
        raise ValidationError('This issue changed after you opened it. Reload before correcting it.')
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Enter a reason for this correction.')
    ledger.check_scope_date(issue.date)
    backlog = ledger.segment_for(business, issue.date) == ledger.SEGMENT_BACKLOG
    if backlog and not ledger.can_enter_backlog(user, business):
        raise PermissionDenied('Backlog edit access is required.')
    goods = {g.pk: g for g in FinishedGood.raw_objects.select_for_update().filter(
        business=business, pk__in=sorted({issue.finished_good_id, good.pk})).order_by('pk')}
    if good.pk not in goods or issue.finished_good_id not in goods:
        raise ValidationError('Choose a product from this business.')
    # The issue service includes a delimited adjustment reference in every lot
    # movement. Require exact traceable quantities before restoring anything.
    movements = list(MarketStockMovement.objects.filter(business=business, movement_type=MarketStockMovement.NON_STOCK,
        note__regex=rf'(^|; )ADJ-{issue.pk}(;|$)').order_by().values('lot_id').annotate(quantity=Sum('quantity')))
    if sum((row['quantity'] for row in movements), Decimal('0')) != issue.quantity:
        raise ValidationError('The issue does not match its batch history. Review the stock movements before correcting it.')
    lots = {lot.pk: lot for lot in MarketStockLot.objects.select_for_update().filter(
        business=business, pk__in=[row['lot_id'] for row in movements]).order_by('pk')}
    for row in movements:
        lot = lots[row['lot_id']]
        restored = -row['quantity']
        if restored <= 0 or lot.finished_good_id != issue.finished_good_id or (not lot.active and lot.closed_reason not in ('', 'non_stock')):
            raise ValidationError('A source batch has been written off or reversed and cannot be restored.')
        if not backlog:
            lot.quantity_available += restored
            if lot.quantity_available > lot.quantity_received:
                raise ValidationError('The issue no longer matches its batch balances.')
            lot.active, lot.closed_reason = True, ''
            lot.save(update_fields=['quantity_available', 'active', 'closed_reason', 'updated_at'])
        _record_market_movement(lot, date=issue.date, movement_type=MarketStockMovement.NON_STOCK,
            quantity=restored, user=user, note=f'Correction of ADJ-{issue.pk}: {reason}'[:255])
        record_finished_good_movement(goods[issue.finished_good_id], restored, StockMovement.ADJUSTMENT,
            affects_stock=False, unit_value=lot.unit_cost, reference=f'ADJ-{issue.pk}',
            note=f'Issue correction: {reason}', on_date=issue.date)
    issue.reversed = True
    issue.save(update_fields=['reversed', 'updated_at'])
    replacement = issue_product_stock(business=business, good=goods[good.pk], quantity=quantity, date=issue.date,
        stock_source=issue.stock_source, reason=purpose, recipient=recipient, description=description, user=user)
    audit(business, user, 'correct_product_issue', issue, f'Stock issue corrected: {reason}'[:255], {
        'reason': reason, 'original_issue': issue.pk, 'replacement_issue': replacement.pk,
        'before': {'product': issue.finished_good_id, 'quantity': str(-issue.quantity), 'recipient': issue.recipient,
                   'purpose': issue.reason, 'inventory_cost': str(-issue.value)},
        'after': {'product': replacement.finished_good_id, 'quantity': str(-replacement.quantity),
                  'recipient': replacement.recipient, 'purpose': replacement.reason, 'inventory_cost': str(-replacement.value)},
    })
    return replacement
