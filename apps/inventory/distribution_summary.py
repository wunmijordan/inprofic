"""Read-only, vertical-aware distribution and recorded-return summaries."""
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from accounts.services import user_has_permission
from core import ledger
from core.snapshot_images import render_distribution_snapshot, render_report_snapshot, snapshot_response
from core.verticals import vertical_config
from sales.models import Customer, CustomerPayment, Sale, SaleItem
from .models import DistributionReturn, FinishedGood, MarketStockLot, MarketStockMovement, StockMovement

ZERO = Decimal('0')


def customer_directory(business):
    rows = list(Customer.raw_objects.filter(business=business).values('pk', 'name', 'region', 'customer_group'))
    return {row['pk']: row for row in rows}, {row['name']: row for row in rows}


def customer_labels(directory, customer_id=None, name=''):
    by_id, by_name = directory
    row = by_id.get(customer_id) if customer_id is not None else by_name.get(name)
    region, group = (row['region'], row['customer_group']) if row else ('', '')
    labels = ' · '.join(value for value in [f'Region: {region}' if region else '', f'Group: {group}' if group else ''] if value)
    return {'customer_id': row['pk'] if row else None, 'region': region, 'customer_group': group, 'customer_labels': labels}


def report_date(request):
    try:
        return date.fromisoformat(request.GET['date']) if request.GET.get('date') else timezone.localdate()
    except ValueError:
        raise Http404('Choose a valid distribution date.')


def distribution_settlement(request, *, directory=None):
    """Read-only invoice review; returns estimate credits, never post them.

    Select invoices in the active ledger but include their lifetime payments and
    returns, so a later-period settlement cannot make an earlier invoice unpaid.
    """
    if not user_has_permission(request.user, request.business, 'finance', 'view'):
        return None
    business = request.business
    directory = directory or customer_directory(business)
    sales = Sale.objects.filter(business=business, source='distribution_order', date=report_date(request)).exclude(
        linked_order__status='reversed')
    if business.uses_production:
        released = MarketStockMovement.objects.filter(
            business=business, movement_type=MarketStockMovement.RELEASE, date=report_date(request)).values('sale_id')
        sales = sales.filter(pk__in=released)
    sales = list(sales.prefetch_related('items__finished_good'))
    ids = [sale.pk for sale in sales]
    payments = dict(CustomerPayment.raw_objects.filter(business=business, sale_id__in=ids)
                    .values('sale_id').annotate(amount=Sum('amount')).values_list('sale_id', 'amount'))
    returns = dict(DistributionReturn.raw_objects.filter(business=business, sale_item__sale_id__in=ids)
                   .values('sale_item_id').annotate(quantity=Sum('quantity')).values_list('sale_item_id', 'quantity'))
    method_rows = CustomerPayment.raw_objects.filter(business=business, sale_id__in=ids).values('sale_id', 'payment_method').annotate(amount=Sum('amount'))
    methods = {}
    for payment in method_rows:
        methods.setdefault(payment['sale_id'], {})[payment['payment_method']] = payment['amount']
    keys = ('gross', 'returned', 'net', 'paid', 'outstanding', 'net_due', 'refund_review')
    totals = dict.fromkeys(keys, ZERO)
    rows = []
    for sale in sales:
        gross, returned = ZERO, ZERO
        for item in sale.items.all():
            gross += item.line_total
            if item.total_units > ZERO:
                returned += item.line_total * min(returns.get(item.pk, ZERO), item.total_units) / item.total_units
        gross, returned = (value.quantize(Decimal('.01')) for value in (gross, returned))
        # Paid sales may have been collected at creation without a CustomerPayment.
        paid = max(payments.get(sale.pk, ZERO), gross if sale.transaction_type == 'paid' else ZERO)
        net = gross - returned
        payment_methods = methods.get(sale.pk, {}).copy()
        direct = paid - sum(payment_methods.values(), ZERO)
        if direct > ZERO:
            method = sale.payment_method or 'Method not recorded'
            payment_methods[method] = payment_methods.get(method, ZERO) + direct
        row = dict(payment_methods=payment_methods, sale=sale.pk, date=sale.date, customer=sale.customer,
                   gross=gross, returned=returned, net=net, paid=paid,
                   outstanding=max(gross - paid, ZERO), net_due=max(net - paid, ZERO),
                   refund_review=max(paid - net, ZERO))
        row.update(customer_labels(directory, sale.customer_master_id, sale.customer))
        rows.append(row)
        for key in keys:
            totals[key] += row[key]
    return {'rows': rows, 'totals': totals}


def distribution_text(*, business, title, day, intro, rows, allocations, settlement, url):
    """WhatsApp-friendly overview, followed by complete product/customer detail."""
    money = lambda amount: f'{business.currency_symbol}{amount:,.2f}'
    quantity = lambda amount, unit: f'{amount:,.2f}'.rstrip('0').rstrip('.') + ' ' + unit
    lines = [f'*{title}*', business.name, f'Distribution date: {day:%a, %d %b %Y}',
             'As of: ' + intro.removeprefix(business.name + ' · '), '']
    if settlement is not None:
        totals = settlement['totals']
        lines += ['*Money summary*',
                  f"• Distributed invoice value: {money(totals['gross'])}",
                  f"• Returned value: {money(totals['returned'])}",
                  f"• Expected value after returns: {money(totals['net'])}",
                  f"• Collected to date: {money(totals['paid'])}",
                  f"• Invoice outstanding: {money(totals['outstanding'])}",
                  f"• Expected balance: {money(totals['net_due'])}",
                  f"• Overpayment / refund review: {money(totals['refund_review'])}", '']
    lines += ['*Stock by product*']
    for row in rows:
        unit = row['unit']
        lines += [f"*{row['product']}*",
                  f"• {'Made' if business.uses_production else 'Received'}: {quantity(row['made'], unit)}",
                  f"• Taken by customers: {quantity(row['distributed'], unit)}",
                  f"• Remaining now: {quantity(row['available'], unit)}"]
        if business.uses_production:
            lines += [f"• To Physical Store: {quantity(row['physical'], unit)}",
                      f"• Redistributable returns: {quantity(row['returned'], unit)}",
                      f"• Expired / blocked: {quantity(row['expired'], unit)}"]
        lines.append('')
    if not rows:
        lines += ['No stock recorded.', '']
    customers, physical = {}, []
    for allocation in allocations:
        if allocation['kind'] == 'store':
            physical.append(allocation)
        else:
            customers.setdefault((allocation['destination'], allocation.get('customer_id')), {'products': [], 'invoices': [], 'labels': allocation.get('customer_labels', '')})['products'].append(allocation)
    if settlement is not None:
        for invoice in settlement['rows']:
            name = invoice['customer'] or 'Customer (name unavailable)'
            customers.setdefault((name, invoice.get('customer_id')), {'products': [], 'invoices': [], 'labels': invoice.get('customer_labels', '')})['invoices'].append(invoice)
    lines += ['*Customer breakdown*']
    method_order = {method: index for index, (method, _) in enumerate(Sale.PAYMENT_CHOICES)}
    for (name, _customer_id), customer in sorted(customers.items(), key=lambda pair: pair[0][0].casefold()):
        lines += [f'*{name}*']
        if customer['labels']:
            lines.append(customer['labels'])
        for product in customer['products']:
            lines.append(f"• {product['product']}: {quantity(product['quantity'], product['unit'])}")
        for invoice in sorted(customer['invoices'], key=lambda row: (row['date'], row['sale'])):
            lines += ['', f"Invoice #{invoice['sale']} · {invoice['date']:%d %b %Y}",
                      f"• Distributed value: {money(invoice['gross'])}",
                      f"• Returned value: {money(invoice['returned'])}",
                      f"• Expected value after returns: {money(invoice['net'])}",
                      f"• Collected to date: {money(invoice['paid'])}",
                      f"• Outstanding: {money(invoice['outstanding'])}",
                      f"• Expected balance: {money(invoice['net_due'])}",
                      f"• Overpayment / refund review: {money(invoice['refund_review'])}"]
            methods = invoice['payment_methods']
            if methods:
                lines.append('Payment methods:')
                lines += [f'• {method}: {money(amount)}' for method, amount in sorted(
                    methods.items(), key=lambda pair: (method_order.get(pair[0], len(method_order)), pair[0]))]
            else:
                lines.append('Payments: none recorded.')
        lines.append('')
    if not customers:
        lines += ['No customer distributions recorded for this date.', '']
    if physical:
        lines += ['*Physical Store transfers*']
        lines += [f"• {row['product']}: {quantity(row['quantity'], row['unit'])}" for row in physical]
        lines.append('')
    lines += ['*Reading this report*',
              '• Made/received and taken quantities are for the selected date.',
              '• Remaining stock is current, not a historical closing balance.']
    if settlement is not None:
        lines += ['• Collections and balances include later payments and returns for these invoices.',
                  '• Returned value uses original selling prices, including discounts. Credits/refunds require review; invoices are unchanged.',
                  '• Net value is before costs. Collections show customer settlement, not end-customer sales or profit.']
        if business.uses_production:
            lines.append('• Physical Store transfers are not sales.')
    lines += ['', '*Open complete distribution summary*', 'Sign-in required.', url]
    return '\n'.join(lines)


def recorded_returns_text(*, business, day, intro, rows, url):
    lines = ['*Recorded distribution returns*', business.name, f'Return date: {day:%a, %d %b %Y}',
             'As of: ' + intro.removeprefix(business.name + ' · '), '']
    for row in rows:
        lines += [f"*{row['customer'] or 'Customer (name unavailable)'} · Invoice #{row['sale']}*",
                  f"• Product: {row['product']}", *([f"• {row['customer_labels']}"] if row.get('customer_labels') else []), f"• Returned: {row['quantity']:,.2f} {row['unit']}",
                  f"• Date: {row['date']:%d %b %Y}", f"• Condition: {row['condition']}",
                  f"• Reason: {row['reason'] or 'Not recorded'}", '']
    if not rows:
        lines += ['No distribution returns recorded for this date.', '']
    lines += ['*Open recorded returns*', 'Sign-in required.', url]
    return '\n'.join(lines)


def distribution_summaries(request, *, lots=None, directory=None):
    business = request.business
    ui = vertical_config(business)
    channel = ui['commerce_channels']['distribution'].replace(' Order', '')
    title = 'Market Stock Distribution' if business.uses_production else f'{channel} stock distribution'
    intro = f"{business.name} · {timezone.localtime():%d %b %Y %H:%M %Z}"
    scope = ledger.report_scope_label(business)
    if scope:
        intro += f' · {scope}'
    day = report_date(request)
    products, destinations = {}, {}
    directory = directory or customer_directory(business)

    def product(pk, name, unit):
        return products.setdefault(pk, {
            'product_id': pk, 'product': name, 'unit': unit or 'unit not set', 'available': ZERO, 'made': ZERO, 'returned': ZERO,
            'expired': ZERO if business.uses_production else None,
            'received': ZERO, 'distributed': ZERO, 'physical': ZERO,
        })

    def destination(pk, name, unit, target, quantity, kind='customer', customer_id=None):
        labels = customer_labels(directory, customer_id, target) if kind == 'customer' else {}
        key = (pk, kind, target, labels.get('customer_id'))
        row = destinations.setdefault(key, {'product_id': pk, 'kind': kind, 'product': name, 'unit': unit or 'unit not set', 'destination': target, 'quantity': ZERO})
        row.update(labels)
        row['quantity'] += quantity

    if business.uses_production:
        source_lots = lots if lots is not None else MarketStockLot.objects.filter(business=business).select_related('finished_good')
        for lot in source_lots:
            good = lot.finished_good
            row = product(good.pk, good.name, good.unit)
            if lot.active and lot.quantity_available > 0:
                row['expired' if lot.is_expired else 'available'] += lot.quantity_available
        # Movement history supplies gross allocations, including exhausted lots.
        # Current stock is a position; movement totals follow the selected ledger scope.
        movements = MarketStockMovement.objects.filter(business=business, date=day).values(
            'lot__finished_good_id', 'lot__finished_good__name', 'lot__finished_good__unit',
            'movement_type', 'sale__customer', 'customer__name', 'sale__customer_master_id', 'customer_id',
        ).annotate(quantity=Sum('quantity'))
        for movement in movements:
            pk, name, unit = (movement['lot__finished_good_id'], movement['lot__finished_good__name'], movement['lot__finished_good__unit'])
            row = product(pk, name, unit)
            kind, quantity = movement['movement_type'], movement['quantity'] or ZERO
            if kind in {MarketStockMovement.PRODUCTION_IN, MarketStockMovement.RETURN_IN}:
                row['received'] += quantity
                row['made' if kind == MarketStockMovement.PRODUCTION_IN else 'returned'] += quantity
            elif kind == MarketStockMovement.RELEASE:
                row['distributed'] -= quantity
                destination(pk, name, unit, movement['sale__customer'] or movement['customer__name'] or 'Customer (name unavailable)', -quantity, customer_id=movement['sale__customer_master_id'] or movement['customer_id'])
            elif kind == MarketStockMovement.TRANSFER_PHYSICAL:
                row['physical'] -= quantity
                destination(pk, name, unit, 'Physical Store', -quantity, 'store')
    else:
        # Purchased stock stays in its registered warehouse/shop, without a production pool.
        for good in FinishedGood.objects.filter(business=business, stock__isnull=False):
            product(good.pk, good.name, good.unit)['available'] = good.stock
        items = SaleItem.objects.filter(sale__business=business, sale__source='distribution_order', sale__date=day).exclude(
            sale__linked_order__status='reversed').select_related('sale', 'finished_good')
        for item in items:
            good = item.finished_good
            product(good.pk, good.name, good.unit)['distributed'] += item.total_units
            destination(good.pk, good.name, good.unit, item.sale.customer or 'Customer (name unavailable)', item.total_units, customer_id=item.sale.customer_master_id)

        arrivals = StockMovement.objects.filter(business=business, movement_type=StockMovement.FG_PURCHASE).filter(Q(business_date=day) | Q(business_date__isnull=True, occurred_at__date=day)).values('finished_good_id').annotate(quantity=Sum('quantity'))
        for arrival in arrivals:
            if arrival['finished_good_id'] in products:
                products[arrival['finished_good_id']]['made'] = arrival['quantity'] or ZERO
    rows = sorted(products.values(), key=lambda row: (not (row['distributed'] or row['made'] or row['physical']), row['product'].casefold()))
    allocations = sorted(destinations.values(), key=lambda row: (row['product'].casefold(), row['destination'].casefold()))
    # Returns are actual records, not maximum return eligibility or a forecast.
    returns = list(DistributionReturn.objects.filter(business=business, date=day).select_related('sale_item__sale', 'sale_item__finished_good'))
    returned_rows = [{
        'sale': r.sale_item.sale_id, 'date': r.date, 'customer': r.sale_item.sale.customer,
        'product': r.sale_item.finished_good.name, 'unit': r.sale_item.finished_good.unit,
        'quantity': r.quantity, 'condition': r.get_condition_display(),
        'reason': r.reason, 'writeoff_value': r.writeoff_value,
        **customer_labels(directory, r.sale_item.sale.customer_master_id, r.sale_item.sale.customer),
    } for r in returns]
    settlement = distribution_settlement(request, directory=directory)
    page_url = request.build_absolute_uri(reverse('market_stock')) + '?' + urlencode({'date': day.isoformat()})
    stock_text = distribution_text(business=business, title=title, day=day, intro=intro,
        rows=rows, allocations=allocations, settlement=settlement, url=page_url + '#distribution-summary')
    returns_text = recorded_returns_text(business=business, day=day, intro=intro,
        rows=returned_rows, url=page_url + '#distribution-returns')
    return {
        'distribution_date': day, 'distribution_title': title, 'distribution_rows': rows, 'distribution_destinations': allocations,
        'recorded_return_rows': returned_rows, 'distribution_settlement': settlement,
        'distribution_image_url': reverse('market_stock_snapshot', args=['stock']) + '?' + urlencode({'date': day.isoformat()}),
        'returns_image_url': reverse('market_stock_snapshot', args=['returns']) + '?' + urlencode({'date': day.isoformat()}),
        'distribution_share_text': stock_text, 'returns_share_text': returns_text,
    }


@login_required
@require_GET
def distribution_snapshot(request, kind):
    if not user_has_permission(request.user, request.business, 'inventory', 'view'):
        raise PermissionDenied
    if kind not in {'stock', 'returns'}:
        raise Http404
    data = distribution_summaries(request)
    day = data['distribution_date']
    url = request.build_absolute_uri(reverse('market_stock')) + '?' + urlencode({'date': day.isoformat()})
    if kind == 'stock':
        products = []
        for row in data['distribution_rows']:
            customers = [destination for destination in data['distribution_destinations']
                         if destination['product_id'] == row['product_id']]
            products.append(dict(row, customers=customers))
        settlement = data['distribution_settlement']
        cards = []
        if settlement is not None:
            totals = settlement['totals']
            cards = [{'label': label, 'value': f"{request.business.currency_symbol}{totals[key]:,.2f}", 'detail': detail}
                     for key, label, detail in [
                         ('gross', 'Distributed value', 'Original invoice selling value'),
                         ('returned', 'Returned value', 'Credit review; selling value'),
                         ('net', 'Expected value', 'Distributed value less returns'),
                         ('paid', 'Collected to date', 'Payments across all methods'),
                         ('net_due', 'Expected balance', 'Expected value less collections'),
                         ('refund_review', 'Refund review', 'Collected above net retained value'),
                     ]]
        image = render_distribution_snapshot(
            business=request.business, title=data['distribution_title'], day=day,
            products=products, cards=cards, url=url + '#distribution-summary',
            scope=ledger.report_scope_label(request.business))
    else:
        rows = [{'label': r['product'], 'value': f"{r['quantity']:,.2f} {r['unit']}",
                 'detail': f"{r['date']:%d %b %Y} · Sale #{r['sale']} · {r['customer']} · {r['condition']}",
                 'secondary_detail': r['customer_labels']}
                for r in data['recorded_return_rows']]
        image = render_report_snapshot(
            business=request.business, title='Recorded distribution returns', subtitle=day.strftime('%d %b %Y'),
            rows=rows, max_rows=14, total_label='Recorded returns', total=str(len(rows)),
            footer='Actual recorded returns only', url=url + '#distribution-returns',
            link_heading='Open INPROFIC for the complete summary',
            scope=ledger.report_scope_label(request.business),
            stamp='As of '+timezone.localtime().strftime('%d %b %H:%M'))
    return snapshot_response(image, 'distribution-'+kind)
