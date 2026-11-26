"""Period documents use saved commercial lines and dated settlement evidence."""
from decimal import Decimal

from django.db.models import Q, Sum
from django.shortcuts import render
from django.utils.cache import patch_cache_control

from core.models import FinancialTransaction
from core.quantities import format_quantity, sale_quantity_display
from inventory.models import DistributionReturn
from .models import CustomerPayment

ZERO = Decimal('0')
CENT = Decimal('.01')


def _dated_totals(queryset, key, amount, start, end):
    return {row[key]: row for row in queryset.order_by().values(key).annotate(
        before=Sum(amount, filter=Q(date__lt=start)),
        during=Sum(amount, filter=Q(date__range=(start, end))),
        lifetime=Sum(amount))}


def sale_document(business, sales, *, start, end, include_earlier=False):
    sales = list(sales.filter(business=business, date__lte=end).exclude(linked_order__status='reversed')
                 .select_related('linked_order').prefetch_related('items__finished_good__portion_profile'))
    ids = [sale.pk for sale in sales]
    payments = _dated_totals(CustomerPayment.raw_objects.filter(business=business, sale_id__in=ids),
        'sale_id', 'amount', start, end)
    cash = _dated_totals(FinancialTransaction.raw_objects.filter(business=business,
        reference__in=[f'SALE-{pk}' for pk in ids], transaction_type=FinancialTransaction.INCOME),
        'reference', 'amount', start, end)
    returns = _dated_totals(DistributionReturn.raw_objects.filter(business=business, sale_item__sale_id__in=ids),
        'sale_item_id', 'quantity', start, end)
    lines, settlements = [], []
    totals = dict.fromkeys(('gross', 'credit', 'total', 'paid', 'due', 'overpaid', 'brought_forward'), ZERO)
    for sale in sales:
        earlier = sale.date < start
        if earlier and not include_earlier:
            continue
        gross, prior_credit, period_credit = ZERO, ZERO, ZERO
        item_lines = []
        for item in sale.items.all():
            display = sale_quantity_display(item)
            value = item.line_total
            returned = returns.get(item.pk, {})
            before_units = min(returned.get('before') or ZERO, item.total_units)
            during_units = min(returned.get('during') or ZERO, max(item.total_units-before_units, ZERO))
            before_credit = value * before_units / item.total_units if item.total_units else ZERO
            through_credit = value * (before_units+during_units) / item.total_units if item.total_units else ZERO
            if item.price_unit:
                before_credit, through_credit = before_credit.quantize(CENT), through_credit.quantize(CENT)
            during_credit = through_credit-before_credit
            gross += value
            prior_credit += before_credit
            period_credit += during_credit
            quantity = display['quantity'] if display['operator'] == '×' else format_quantity(item.total_units, item.finished_good.unit)
            item_lines.append({'name': item.finished_good.name, 'quantity': quantity,
                'unit_price': display['price'] - display['discount'], 'line_total': value,
                'detail': f'Sale #{sale.pk} · {sale.date:%d %b %Y} · {sale.display_source}',
                'returned': (before_credit+during_credit).quantize(CENT)})
        gross, prior_credit, period_credit = (v.quantize(CENT) for v in (gross, prior_credit, period_credit))
        # Payment rows are the authoritative receipt history when present.
        # Their existence also prevents a later Paid state from being projected
        # backwards into an earlier period before that payment was received.
        receipts = payments.get(sale.pk, cash.get(f'SALE-{sale.pk}'))
        if receipts is None:
            receipts = {'before': gross if earlier and sale.transaction_type == 'paid' else ZERO,
                        'during': gross if not earlier and sale.transaction_type == 'paid' else ZERO}
        prior_paid, paid = receipts.get('before') or ZERO, receipts.get('during') or ZERO
        opening = max(gross-prior_credit-prior_paid, ZERO) if earlier else ZERO
        if earlier:
            if opening == ZERO:
                continue
            lines.append({'name': f'Balance brought forward · Sale #{sale.pk}', 'quantity': '1 balance',
                'unit_price': opening, 'line_total': opening,
                'detail': f'{sale.date:%d %b %Y} · {sale.customer} · {sale.display_source}'})
            invoice_value, credit = opening, min(period_credit, opening)
        else:
            lines.extend(item_lines)
            invoice_value, credit = gross, prior_credit+period_credit
            # Advance receipts attached to these period sales count toward their
            # settlement, but receipts after the period end never do.
            paid += prior_paid
        net = max(invoice_value-credit, ZERO)
        due, overpaid = max(net-paid, ZERO), max(paid-net, ZERO)
        status = 'Paid' if due == ZERO else 'Partially paid' if paid > ZERO else 'Unpaid'
        settlements.append({'sale': sale.pk, 'date': sale.date, 'customer': sale.customer, 'earlier': earlier,
            'channel': sale.display_source, 'total': net, 'paid': paid, 'due': due, 'overpaid': overpaid, 'status': status})
        for key, value in dict(gross=invoice_value, credit=credit, total=net, paid=paid, due=due, overpaid=overpaid,
                               brought_forward=opening).items():
            totals[key] += value
    status = 'No sales' if not settlements else 'Paid' if totals['due'] == ZERO else 'Partially paid' if totals['paid'] else 'Unpaid'
    return {'title': 'Invoice', 'status': status, 'lines': lines, 'settlements': settlements,
            'include_earlier': include_earlier, 'period_sales': totals['gross']-totals['brought_forward'], **totals}


def render_document(request, document, *, name, start, end, reference, customer=None):
    document.update(start=start, end=end, reference=reference)
    response = render(request, 'sales/activity_invoice.html', {
        'store_business': request.business, 'hide_storefront_header': True,
        'activity_document': document, 'receipt_items': document['lines'],
        'customer_name': name, 'customer_phone': customer.phone if customer else '',
        'customer_email': getattr(customer, 'email', '') if customer else '',
    })
    patch_cache_control(response, private=True, no_store=True)
    return response
