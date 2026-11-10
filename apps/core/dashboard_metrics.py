"""Read-only, business-scoped selectors for the Basic dashboard cards."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Prefetch, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET

from accounts.services import user_has_permission
from .accounting import _sale_expressions, add_months, profit_and_loss, settled_sales
from .ledger import report_scope_label

ZERO = Decimal('0')


def selected_period(params, today):
    """Resolve the calendar period containing an anchor, capped at today."""
    kind = params.get('period', 'day')
    try:
        anchor = date.fromisoformat(params.get('date') or today.isoformat())
        if anchor > today:
            raise ValueError('Choose a date on or before today.')
        if kind == 'day':
            start = end = anchor
            label = anchor.strftime('%d %b %Y')
        elif kind == 'week':
            start = anchor - timedelta(days=anchor.weekday())
            end = start + timedelta(days=6)
            label = f'{start:%d %b} – {end:%d %b %Y}'
        elif kind in {'month', 'quarter'}:
            month = ((anchor.month - 1) // 3) * 3 + 1 if kind == 'quarter' else anchor.month
            start = anchor.replace(day=1, month=month)
            end = add_months(start, 3 if kind == 'quarter' else 1) - timedelta(days=1)
            label = f'Q{(anchor.month-1)//3+1} {anchor.year}' if kind == 'quarter' else anchor.strftime('%B %Y')
        elif kind == 'year':
            start, end = anchor.replace(month=1, day=1), anchor.replace(month=12, day=31)
            label = str(anchor.year)
        else:
            raise ValueError('Choose day, week, month, quarter or year.')
    except (ValueError, OverflowError) as exc:
        raise ValidationError(str(exc)) from exc
    return {'period': kind, 'date': anchor, 'start': start, 'end': min(end, today),
            'label': label, 'in_progress': end > today}


@login_required
@require_GET
def dashboard_metric(request):
    business = request.business
    kind = request.GET.get('metric', 'sales')
    if kind not in {'sales', 'revenue', 'profit', 'product', 'customer'}:
        return JsonResponse({'error': 'Unknown dashboard metric.'}, status=400)
    module = 'finance' if kind == 'profit' else 'sales'
    if not all(user_has_permission(request.user, business, m, 'view') for m in ('dashboard', module)):
        return JsonResponse({'error': 'This metric is not available to your role.'}, status=403)
    if kind in {'product', 'customer'}:
        period = {'period': 'all', 'date': None, 'start': None, 'end': timezone.localdate(),
                  'label': 'All time', 'in_progress': False}
    else:
        try:
            period = selected_period(request.GET, timezone.localdate())
        except ValidationError as exc:
            return JsonResponse({'error': ' '.join(exc.messages)}, status=400)
    from sales.models import Sale, SaleItem, CustomerPayment, Customer, return_prefetch
    subject = request.GET.get('subject', '')
    if kind in {'product', 'customer'} and subject and not (kind == 'customer' and subject.startswith('name:')):
        if not subject.isascii() or not subject.isdigit() or len(subject) > 19 or not 0 < int(subject) <= 2**63-1:
            return JsonResponse({'error': 'Choose a valid product or customer.'}, status=400)
    payload = {**period, 'currency': business.currency_symbol, 'metric': kind, 'scope': report_scope_label(business)}
    if kind == 'profit':
        statement = profit_and_loss(business, period['start'], period['end'])
        payload.update(value=statement['net_profit'], revenue=statement['revenue'], costs=statement['total_costs'])
        return JsonResponse(payload)
    sales = Sale.objects.filter(business=business, date__lte=period['end']).select_related('business')
    if period['start'] is not None:
        sales = sales.filter(date__gte=period['start'])
    if kind == 'customer' and request.GET.get('subject'):
        subject = request.GET['subject']
        if subject.startswith('name:'):
            sales = sales.filter(customer_master__isnull=True, customer=subject[5:])
        else:
            customer = get_object_or_404(Customer.objects.filter(business=business), pk=subject)
            sales = sales.filter(Q(customer_master=customer) | Q(customer_master__isnull=True, customer=customer.name))
            payload['subject_label'] = customer.name
    if kind == 'sales':
        # Settlement belongs to the original sale, even when cash or a return
        # was recorded later or in a different ledger segment.
        invoices = sales.prefetch_related(
            'items__finished_good', return_prefetch('items__distribution_returns'),
            Prefetch('payments', queryset=CustomerPayment.raw_objects.filter(business=business)),
        )
        total, count, channels = ZERO, 0, {}
        for sale in invoices.iterator(chunk_size=500):
            value = sale.received_value
            total += value
            count += 1
            channels.setdefault(sale.display_source, [ZERO, 0])
            channels[sale.display_source][0] += value
            channels[sale.display_source][1] += 1
        payload.update(value=total, count=count, channels=[
            {'label': name, 'value': values[0], 'count': values[1]} for name, values in channels.items()
        ])
        return JsonResponse(payload)
    items = SaleItem.objects.filter(sale__in=sales)
    if kind == 'product' and request.GET.get('subject'):
        from inventory.models import FinishedGood
        product = get_object_or_404(FinishedGood.objects.filter(business=business), pk=subject)
        items = items.filter(finished_good=product)
        payload['subject_label'] = product.name
    field, line, _cogs, _units = _sale_expressions(prefix='')
    recorded = items.aggregate(value=Sum(line, output_field=field), count=Count('sale_id', distinct=True))
    paid = items.filter(sale__in=settled_sales(sales)).aggregate(value=Sum(line, output_field=field))['value'] or ZERO
    payload.update(value=paid, recorded=recorded['value'] or ZERO, count=recorded['count'])
    return JsonResponse(payload)


def metric_choices(business):
    from inventory.models import FinishedGood
    from sales.models import Customer, Sale
    products = [{'value': str(pk), 'label': name} for pk, name in FinishedGood.objects.filter(
        business=business).order_by('name').values_list('pk', 'name')]
    customers = [{'value': str(pk), 'label': name} for pk, name in Customer.objects.filter(
        business=business).order_by('name').values_list('pk', 'name')]
    saved_names = {row['label'] for row in customers}
    legacy_names = Sale.objects.filter(business=business, customer_master__isnull=True).order_by(
        'customer').values_list('customer', flat=True).distinct()
    customers += [{'value': 'name:' + name, 'label': name or 'Walk-in (unnamed)'}
                  for name in legacy_names if name not in saved_names]
    return products, customers
