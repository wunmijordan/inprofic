"""Read-only, vertical-aware distribution and recorded-return summaries."""
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from core.share_text import render_lines

from accounts.services import user_has_permission
from core import ledger
from core.quantities import format_quantity
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
    labels = ' · '.join(value for value in [f'Region: {region}' if region else '', f'Area: {group}' if group else ''] if value)
    return {'customer_id': row['pk'] if row else None, 'region': region, 'customer_group': group, 'customer_labels': labels}


def report_date(request):
    try:
        return date.fromisoformat(request.GET['date']) if request.GET.get('date') else timezone.localdate()
    except ValueError:
        raise Http404('Choose a valid distribution date.')


def distribution_settlement(request, *, directory=None):
    """Invoice review using recorded return credits and unchanged cash receipts.

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
        paid = max(payments.get(sale.pk, ZERO), gross if sale.transaction_type == 'paid' and not payments.get(sale.pk, ZERO) else ZERO)
        net = gross - returned
        payment_methods = methods.get(sale.pk, {}).copy()
        direct = paid - sum(payment_methods.values(), ZERO)
        if direct > ZERO:
            method = sale.payment_method or 'Method not recorded'
            payment_methods[method] = payment_methods.get(method, ZERO) + direct
        products = [{'product': item.finished_good.name, 'quantity': format_quantity(item.total_units,item.finished_good.unit),
                     'price': item.price, 'discount': item.discount, 'value': item.line_total} for item in sale.items.all()]
        row = dict(products=products, payment_methods=payment_methods, sale=sale.pk, date=sale.date, customer=sale.customer,
                   gross=gross, returned=returned, net=net, paid=paid,
                   outstanding=max(net - paid, ZERO), net_due=max(net - paid, ZERO),
                   refund_review=max(paid - net, ZERO))
        row.update(customer_labels(directory, sale.customer_master_id, sale.customer))
        rows.append(row)
        for key in keys:
            totals[key] += row[key]
    return {'rows': rows, 'totals': totals}


def distribution_text(*, business, title, day, intro, rows, allocations, settlement, url, issues=()):
    """WhatsApp-friendly overview, followed by complete product/customer detail."""
    money = lambda amount: f'{business.currency_symbol}{amount:,.2f}'
    quantity = format_quantity
    lines = [f'*{title}*', business.name, f'Distribution date: {day:%a, %d %b %Y}',
             'As of: ' + intro.removeprefix(business.name + ' · '), '']
    if settlement is not None and any(settlement['totals'].values()):
        totals = settlement['totals']
        lines += ['*Money summary*']
        for key,label in [('gross','Customer distributions'),('returned','Returns'),('net','Expected value after returns'),
            ('paid','Collected'),('net_due','Balance due'),('refund_review','Refund review')]:
            if totals[key]: lines.append(f"• {label}: {money(totals[key])}")
        lines.append('')
    if rows:
        lines += ['*Stock by product*']
    for row in rows:
        lines.append(f"• *{row['product']}*")
        for key, label in [('opening','Opening balance'),('received','Received'),('returned','Returned to stock'),
            ('distributed','To customers'),('physical_sold','Sold in Physical Store'),('physical','Transferred to Physical Store'),
            ('issued','Staff / gifting'),('write_off','Written off'),('reversal','Production reversed'),('closing','Closing balance'),('expired','Expired stock')]:
            if row.get(key): lines.append(f"  • {label}: {quantity(row[key],row['unit'])}")
        adjustment = row['other_change'] + row.get('write_off',ZERO) - row.get('reversal',ZERO)
        if adjustment: lines.append(f"  • {'Other stock out' if adjustment < 0 else 'Other stock in'}: {quantity(abs(adjustment),row['unit'])}")
        lines.append('')
    customers, physical = {}, []
    for allocation in allocations:
        if allocation['kind'] == 'non_stock':
            continue
        if allocation['kind'] in {'store', 'physical_sale'}:
            physical.append(allocation)
        else:
            customers.setdefault((allocation['destination'], allocation.get('customer_id')), {'products': [], 'invoices': [], 'labels': allocation.get('customer_labels', '')})['products'].append(allocation)
    if settlement is not None:
        for invoice in settlement['rows']:
            name = invoice['customer'] or 'Customer (name unavailable)'
            customers.setdefault((name, invoice.get('customer_id')), {'products': [], 'invoices': [], 'labels': invoice.get('customer_labels', '')})['invoices'].append(invoice)
    if customers: lines += ['*Customer breakdown*']
    method_order = {method: index for index, (method, _) in enumerate(Sale.PAYMENT_CHOICES)}
    for (name, _customer_id), customer in sorted(customers.items(), key=lambda pair: pair[0][0].casefold()):
        lines += [f'• *{name}*']
        if customer['labels']:
            lines.append(customer['labels'])
        for product in customer['products']:
            lines.append(f"  • {product['product']}: {quantity(product['quantity'], product['unit'])}")
        for invoice in sorted(customer['invoices'], key=lambda row: (row['date'], row['sale'])):
            lines += ['', f"Invoice #{invoice['sale']} · {invoice['date']:%d %b %Y}"]
            for item in invoice.get('products',[]):
                discount = f" · discount {money(item['discount'])} per unit" if item['discount'] else ''
                lines.append(f"  • {item['product']}: {item['quantity']} × {money(item['price'])}{discount} = {money(item['value'])}")
            for key,label in [('gross','Value'),('returned','Returns'),('net','After returns'),('paid','Collected'),('net_due','Balance due'),('refund_review','Refund review')]:
                if invoice[key]: lines.append(f"  • {label}: {money(invoice[key])}")
            methods = invoice['payment_methods']
            if methods:
                lines.append('Payment methods:')
                lines += [f'  • {method}: {money(amount)}' for method, amount in sorted(
                    methods.items(), key=lambda pair: (method_order.get(pair[0], len(method_order)), pair[0]))]

        lines.append('')
    if physical:
        lines += ['*Physical Store activity*']
        lines += [f"• {row['product']} · {row['destination']}: {quantity(row['quantity'], row['unit'])}" for row in physical]
        lines.append('')
    if issues:
        lines += ['*Staff and gifting*']
        for issue in issues:
            lines.append(f"• {issue.get_reason_display()} · {issue.recipient or 'Recipient not recorded'}")
            lines.append(f"  • {issue.finished_good.name}: {quantity(-issue.quantity,issue.finished_good.unit)}" +
                (f" · Inventory cost: {money(-issue.value)}" if issue.value else ''))
            if issue.description:
                lines.append(f'  • Note: {issue.description}')
        lines.append('')
    lines += ['', 'Full summary:', url]
    return render_lines(lines)


def recorded_returns_text(*, business, day, intro, rows, url):
    lines = ['*Recorded distribution returns*', business.name, f'Return date: {day:%a, %d %b %Y}',
             'As of: ' + intro.removeprefix(business.name + ' · '), '']
    for row in rows:
        lines += [f"• *{row['customer'] or 'Customer (name unavailable)'} · Invoice #{row['sale']}*",
                  f"  • Product: {row['product']}", *([f"  • {row['customer_labels']}"] if row.get('customer_labels') else []), f"  • Returned: {format_quantity(row['quantity'], row['unit'])}",
                  f"  • Date: {row['date']:%d %b %Y}", f"  • Condition: {row['condition']}",
                  f"  • Reason: {row['reason'] or 'Not recorded'}", '']
    if not rows:
        lines += ['No distribution returns recorded for this date.', '']
    lines += ['*Open recorded returns*', 'Sign-in required.', url]
    return render_lines(lines)


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
            'opening': ZERO, 'closing': ZERO, 'other_change': ZERO,
            'physical_sold': ZERO, 'write_off': ZERO, 'reversal': ZERO, 'issued': ZERO,
        })

    def destination(pk, name, unit, target, quantity, kind='customer', customer_id=None):
        labels = customer_labels(directory, customer_id, target) if kind == 'customer' else {}
        key = (pk, kind, target, labels.get('customer_id'))
        row = destinations.setdefault(key, {'product_id': pk, 'kind': kind, 'product': name, 'unit': unit or 'unit not set', 'destination': target, 'quantity': ZERO})
        row.update(labels)
        row['quantity'] += quantity

    if business.uses_production:
        source_lots = list(lots) if lots is not None else list(MarketStockLot.objects.filter(business=business).select_related('finished_good'))
        # Roll stored balances back through later movements. Exhausted/closed
        # lots still contribute to a historical day before their release.
        balances = {row['lot_id']: row for row in MarketStockMovement.objects.filter(
            business=business, lot_id__in=[lot.pk for lot in source_lots],
        ).values('lot_id').annotate(
            after=Sum('quantity', filter=Q(date__gt=day)),
            on_day=Sum('quantity', filter=Q(date=day)),
            through=Sum('quantity', filter=Q(date__lte=day)),
            receipts=Count('pk', filter=Q(movement_type__in=[MarketStockMovement.PRODUCTION_IN, MarketStockMovement.RETURN_IN])),
        )}
        historical = ledger.segment_for(business, day) == ledger.SEGMENT_BACKLOG
        for lot in source_lots:
            good = lot.finished_good
            row = product(good.pk, good.name, good.unit)
            if lot.received_date > day:
                continue
            totals = balances.get(lot.pk, {})
            if historical or lot.is_backlog_entry:
                baseline = ZERO if totals.get('receipts') else lot.quantity_received
                closing = baseline + (totals.get('through') or ZERO)
            else:
                closing = lot.quantity_available - (totals.get('after') or ZERO)
            opening = closing - (totals.get('on_day') or ZERO) if lot.received_date < day else ZERO
            row['opening'] += opening
            row['closing'] += closing
            row['available'] += closing
            if lot.expiry_date and lot.expiry_date < day:
                row['expired'] += closing
            # Older/imported lots may predate receipt movement recording.
            if lot.received_date == day and not totals.get('receipts'):
                if lot.source == MarketStockLot.SOURCE_RETURN:
                    row['returned'] += lot.quantity_received
                else:
                    row['made'] += lot.quantity_received
                    row['received'] += lot.quantity_received
        movements = MarketStockMovement.objects.filter(business=business, date=day).values(
            'lot__finished_good_id', 'lot__finished_good__name', 'lot__finished_good__unit',
            'movement_type', 'sale__customer', 'sale__source', 'customer__name', 'sale__customer_master_id', 'customer_id',
        ).annotate(quantity=Sum('quantity'))
        for movement in movements:
            pk, name, unit = (movement['lot__finished_good_id'], movement['lot__finished_good__name'], movement['lot__finished_good__unit'])
            row = product(pk, name, unit)
            kind, quantity = movement['movement_type'], movement['quantity'] or ZERO
            if kind in {MarketStockMovement.PRODUCTION_IN, MarketStockMovement.RETURN_IN}:
                row['made' if kind == MarketStockMovement.PRODUCTION_IN else 'returned'] += quantity
                if kind == MarketStockMovement.PRODUCTION_IN:
                    row['received'] += quantity
            elif kind == MarketStockMovement.RELEASE:
                if movement['sale__source'] == 'walkin':
                    row['physical_sold'] -= quantity
                    destination(pk, name, unit, 'Physical Store sales', -quantity, 'physical_sale')
                else:
                    row['distributed'] -= quantity
                    destination(pk, name, unit, movement['sale__customer'] or movement['customer__name'] or 'Customer (name unavailable)', -quantity, customer_id=movement['sale__customer_master_id'] or movement['customer_id'])
            elif kind == MarketStockMovement.TRANSFER_PHYSICAL:
                row['physical'] -= quantity
                destination(pk, name, unit, 'Physical Store', -quantity, 'store')
            elif kind == MarketStockMovement.NON_STOCK:
                row['issued'] -= quantity
            else:
                row['other_change'] += quantity
                if kind == MarketStockMovement.EXPIRY: row['write_off'] -= quantity
                elif kind == MarketStockMovement.REVERSAL: row['reversal'] += quantity
    else:
        # Reconstruct the selected day's balances from all shelf movements,
        # including other channels, adjustments and purchases.
        stock_moves = StockMovement.objects.filter(business=business, affects_stock=True,
            finished_good__isnull=False).annotate(stock_date=Coalesce('business_date', TruncDate('occurred_at')))
        balances = {entry['finished_good_id']: entry for entry in stock_moves.values('finished_good_id').annotate(
            after=Sum('quantity', filter=Q(stock_date__gt=day)),
            on_day=Sum('quantity', filter=Q(stock_date=day)),
        )}
        for good in FinishedGood.objects.filter(business=business, stock__isnull=False):
            row = product(good.pk, good.name, good.unit)
            totals = balances.get(good.pk, {})
            row['closing'] = row['available'] = good.stock - (totals.get('after') or ZERO)
            row['opening'] = row['closing'] - (totals.get('on_day') or ZERO)
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
                products[arrival['finished_good_id']]['received'] = arrival['quantity'] or ZERO
        for row in products.values():
            row['other_change'] = row['closing'] - row['opening'] - row['received'] + row['distributed']
    from .models import StockAdjustment
    from .product_use import product_issues
    issues = list(product_issues(business, day, day, stock_source=StockAdjustment.SOURCE_MARKET if business.uses_production else StockAdjustment.SOURCE_PHYSICAL))
    issue_cost = sum((-issue.value for issue in issues), ZERO)
    for issue in issues:
        good = issue.finished_good
        row = product(good.pk, good.name, good.unit)
        if not business.uses_production:
            row['issued'] -= issue.quantity
            row['other_change'] -= issue.quantity
        destination(good.pk, good.name, good.unit,
            f'{issue.get_reason_display()} · {issue.recipient or "Recipient not recorded"}', -issue.quantity, 'non_stock')
    rows = sorted((row for row in products.values() if any(row[key] for key in ['opening','received','returned','distributed','physical_sold','physical','issued','closing','other_change'])), key=lambda row: (not (row['distributed'] or row['made'] or row['physical']), row['product'].casefold()))
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
    column_specs = [('opening','Opening balance'),('received','Received'),('distributed','To customers'),
        ('physical_sold','Store sales'),('physical','To store'),('issued','Staff / gifting'),('closing','Closing')]
    columns = [(key,label) for key,label in column_specs if key in {'opening','closing'} or any(row[key] for row in rows)]
    for row in rows:
        row['report_cells'] = [format_quantity(row[key],row['unit']) if row[key] else '—' for key,_ in columns]
        row['stock_notes'] = []
        for key,label in [('returned','Returns'),('write_off','Written off'),('reversal','Production reversal'),('expired','Expired')]:
            if row.get(key): row['stock_notes'].append(f"{label}: {format_quantity(abs(row[key]),row['unit'])}")
        residual = row['other_change'] + row['write_off'] - row['reversal']
        if residual:
            label = 'Other stock out' if residual < 0 else 'Other stock in'
            row['stock_notes'].append(f"{label}: {format_quantity(abs(residual),row['unit'])}")
    settlement = distribution_settlement(request, directory=directory)
    page_url = request.build_absolute_uri(reverse('market_stock')) + '?' + urlencode({'date': day.isoformat()})
    physical_sales = Sale.objects.filter(business=business, source='walkin', date=day,
        market_stock_movements__movement_type=MarketStockMovement.RELEASE).distinct().prefetch_related('items')
    physical_sales_value = sum((sale.total for sale in physical_sales),ZERO) if settlement is not None else ZERO
    stock_text = distribution_text(business=business, title=title, day=day, intro=intro,
        rows=rows, allocations=allocations, settlement=settlement, issues=issues, url=page_url + '#distribution-summary')
    if physical_sales_value:
        stock_text = stock_text.replace('\n*Stock by product*', f'\nPhysical Store sales: {business.currency_symbol}{physical_sales_value:,.2f}\n\n*Stock by product*',1)
    returns_text = recorded_returns_text(business=business, day=day, intro=intro,
        rows=returned_rows, url=page_url + '#distribution-returns')
    return {
        'distribution_columns': [label for _,label in columns], 'distribution_colspan': len(columns)+1,
        'physical_sales_value': physical_sales_value,
        'product_issues': issues, 'product_issue_cost': issue_cost,
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
                     ] if totals[key]]
        if data['physical_sales_value']:
            cards.append({'label':'Physical Store sales','value':f"{request.business.currency_symbol}{data['physical_sales_value']:,.2f}",'detail':'Sold directly from Market Stock'})
        if data['product_issue_cost']:
            cards.insert(0, {'label': 'Staff / gifting cost', 'value': f"{request.business.currency_symbol}{data['product_issue_cost']:,.2f}", 'detail': 'Inventory cost; no sale or cash movement'})
        image = render_distribution_snapshot(
            business=request.business, title=data['distribution_title'], day=day,
            products=products, cards=cards, url=url + '#distribution-summary',
            scope=ledger.report_scope_label(request.business))
    else:
        rows = [{'label': r['product'], 'value': format_quantity(r['quantity'],r['unit']),
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
