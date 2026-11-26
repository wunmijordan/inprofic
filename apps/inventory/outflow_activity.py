"""Activity and storefront-style documents for Market Stock destinations."""
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from accounts.services import user_has_permission
from core.quantities import format_product_quantity
from sales.activity_documents import render_document, sale_document
from sales.customer_activity import ActivityDates
from sales.models import Sale
from .models import MarketStockMovement, StockAdjustment
from .product_use import product_issues

KINDS = {'customer': 'Customers', 'physical_sale': 'Physical Store sales', 'online': 'Online sales', 'store': 'Physical Store', 'non_stock': 'Other outflows'}


def activity_url(kind, day, *, name='', purpose=''):
    return reverse('market_outflow_activity', args=[kind]) + '?' + urlencode({
        'start': day.isoformat(), 'end': day.isoformat(), 'name': name, 'purpose': purpose})


@login_required
@require_GET
def outflow_activity(request, kind, document=False):
    if kind not in KINDS:
        raise Http404
    if not user_has_permission(request.user, request.business, 'inventory', 'view'):
        return render(request, '403.html', status=403)
    if kind in {'customer', 'physical_sale', 'online'} and not user_has_permission(request.user, request.business, 'sales', 'view'):
        return render(request, '403.html', status=403)
    day = timezone.localdate()
    dates = ActivityDates({'start': request.GET.get('start', day.replace(day=1).isoformat()),
                           'end': request.GET.get('end', day.isoformat())})
    name, purpose = request.GET.get('name', ''), request.GET.get('purpose', '')
    context = {'date_form': dates, 'title': KINDS[kind], 'kind': kind, 'target_name': name, 'purpose': purpose,
               'activity_query': urlencode({'name': name, 'purpose': purpose})}
    def visible_rows(queryset):
        if document:
            return queryset
        page = Paginator(queryset, 25).get_page(request.GET.get('page'))
        context['activity_page'] = page
        return page.object_list

    if dates.is_valid():
        start, end = dates.cleaned_data['start'], dates.cleaned_data['end']
        context.update(start=start, end=end)
        include_earlier = request.GET.get('include_outstanding') == '1'
        if kind in {'customer', 'physical_sale', 'online'}:
            released = MarketStockMovement.objects.filter(business=request.business, movement_type='release').values('sale_id')
            sales = Sale.objects.filter(business=request.business, date__lte=end,
                source={'customer': 'distribution_order', 'physical_sale': 'walkin', 'online': 'online_order'}[kind]).exclude(linked_order__status='reversed')
            if request.business.uses_production:
                sales = sales.filter(pk__in=released)
            if not include_earlier:
                sales = sales.filter(date__gte=start)
            if name:
                sales = sales.filter(customer=name, customer_master__isnull=True)
            selected_sales = visible_rows(sales.order_by('date', 'pk'))
            if not document:
                selected_sales = sales.filter(pk__in=[sale.pk for sale in selected_sales]).order_by('date', 'pk')
            data = sale_document(request.business, selected_sales, start=start, end=end, include_earlier=include_earlier)
            context['settlements'] = data['settlements']
        else:
            lines, rows = [], []
            if kind == 'non_stock':
                issues = product_issues(request.business, start, end, stock_source=StockAdjustment.SOURCE_MARKET if request.business.uses_production else StockAdjustment.SOURCE_PHYSICAL)
                if name:
                    issues = issues.filter(recipient=name)
                if purpose:
                    issues = issues.filter(reason=purpose)
                for issue in visible_rows(issues):
                    issue.display_quantity = -issue.quantity
                    rows.append(issue)
                    lines.append({'name': issue.finished_good.name,
                        'quantity': format_product_quantity(-issue.quantity, issue.finished_good),
                        'detail': f'Issue #{issue.pk} · {issue.date:%d %b %Y} · {issue.recipient} · {issue.get_reason_display()}'})
            else:
                movements = MarketStockMovement.objects.filter(business=request.business, date__range=(start, end),
                    movement_type=MarketStockMovement.TRANSFER_PHYSICAL).select_related('lot__finished_good__portion_profile').order_by('date', 'pk')
                for movement in visible_rows(movements):
                    movement.display_quantity = -movement.quantity
                    rows.append(movement)
                    lines.append({'name': movement.lot.finished_good.name,
                        'quantity': format_product_quantity(-movement.quantity, movement.lot.finished_good),
                        'detail': f'{movement.date:%d %b %Y} · {movement.note}'})
            context['outflows'] = rows
            data = {'title': 'Stock issue' if kind == 'non_stock' else 'Stock transfer',
                'status': 'Not applicable', 'non_sale': True, 'lines': lines}
        if document:
            return render_document(request, data, name=name or KINDS[kind], start=start, end=end,
                reference=f'{kind.upper()}-{start:%Y%m%d}-{end:%Y%m%d}')
    return render(request, 'inventory/outflow_activity.html', context, status=400 if document else 200)


@login_required
@require_http_methods(['GET', 'POST'])
def outflow_edit(request, pk):
    from django import forms
    from django.contrib import messages
    from django.core.exceptions import ValidationError
    from django.shortcuts import get_object_or_404, redirect
    from core.quantities import product_portion
    from .forms import ProductStockIssueForm, INPUT_CLS
    from .outflow_corrections import correct_product_issue
    if not user_has_permission(request.user, request.business, 'inventory', 'edit'):
        return render(request, '403.html', status=403)
    issue = get_object_or_404(StockAdjustment.objects.select_related('finished_good__portion_profile').filter(
        business=request.business, stock_source=StockAdjustment.SOURCE_MARKET, reason__in=['staff', 'charity'],
        quantity__lt=0, reversed=False), pk=pk)
    form = ProductStockIssueForm(request.POST or None, business=request.business, stock_source=issue.stock_source,
        initial={'date': issue.date, 'finished_good': issue.finished_good_id,
            'quantity': -issue.quantity / product_portion(issue.finished_good)[1], 'reason': issue.reason,
            'recipient': issue.recipient, 'description': issue.description, 'revision': str(issue.updated_at)})
    form.fields['date'].disabled = True
    form.fields['correction_reason'] = forms.CharField(max_length=255, label='Reason for correction',
        widget=forms.Textarea(attrs={'rows': 2, 'class': INPUT_CLS}))
    form.fields['revision'] = forms.CharField(widget=forms.HiddenInput)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        try:
            replacement = correct_product_issue(business=request.business, issue_id=issue.pk,
                revision=data['revision'], reason=data['correction_reason'], good=data['finished_good'], quantity=data['quantity'],
                purpose=data['reason'], recipient=data['recipient'], description=data['description'], user=request.user)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Stock issue corrected. The original issue and compensating movements remain in the audit history.')
            return redirect(activity_url('non_stock', replacement.date, name=replacement.recipient, purpose=replacement.reason))
    return render(request, 'inventory/product_stock_issue.html', {'form': form, 'editing': True,
        'stock_source_label': 'Market Stock', 'back_url': activity_url('non_stock', issue.date, name=issue.recipient, purpose=issue.reason)})
