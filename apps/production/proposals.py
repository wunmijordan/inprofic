"""Non-operational production proposals and reviewed seeds for the real order form."""
from collections import OrderedDict
from datetime import date
from decimal import Decimal, InvalidOperation
import secrets
import time

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from core.share_text import render_lines
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from accounts.services import is_kitchen_restricted, user_has_permission
from core.forms import ProductChoiceLabelsMixin, SellingPortionEntryMixin
from core.seo import absolute_url
from core.services import audit
from core.snapshot_images import render_report_snapshot, snapshot_response
from inventory.models import FinishedGood
from core.quantities import format_product_quantity, product_portion
from sales.models import Customer
from .forms import INPUT_CLS
from .models import OrderProposal, OrderProposalLine, ProposalOrderLink


def require_access(request, edit=False):
    if (not request.business.uses_production or is_kitchen_restricted(request.user, request.business)
            or not user_has_permission(request.user, request.business, 'production', 'edit' if edit else 'view')):
        raise PermissionDenied


class ProposalForm(forms.ModelForm):
    class Meta:
        model = OrderProposal
        fields = ['title', 'planned_date', 'notes']
        widgets = {'planned_date': forms.DateInput(attrs={'type': 'date'}), 'notes': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = INPUT_CLS


class ProposalLineForm(SellingPortionEntryMixin, ProductChoiceLabelsMixin, forms.ModelForm):
    class Meta:
        model = OrderProposalLine
        fields = ['channel', 'customer', 'customer_name', 'finished_good', 'quantity']

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['customer'].queryset = Customer.objects.filter(business=business, active=True)
        self.fields['finished_good'].queryset = FinishedGood.objects.filter(
            business=business, source_type=FinishedGood.SOURCE_MADE_IN_HOUSE)
        self.fields['customer_name'].label = 'Online buyer name (optional)'
        self.fields['quantity'].widget.attrs.update(min='0.01', step='0.01')
        for field in self.fields.values():
            field.widget.attrs['class'] = INPUT_CLS

    def clean(self):
        data = super().clean()
        if data.get('DELETE'):
            return data
        channel, customer = data.get('channel'), data.get('customer')
        if channel == 'distribution' and not customer:
            self.add_error('customer', 'Choose the distribution customer, or choose Market Stock for unassigned production.')
        if channel in ('physical_store', 'market_stock'):
            data['customer'], data['customer_name'] = None, ''
        elif customer:
            data['customer_name'] = ''
        if data.get('quantity') is not None and data['quantity'] <= 0:
            self.add_error('quantity', 'Enter a quantity greater than zero.')
        return data


class ProposalLineSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        keys = {form.instance.destination_key for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get('DELETE')}
        if len(keys) > 20:
            raise forms.ValidationError('Use at most 20 customer/channel destinations per proposal.')


ProposalLines = inlineformset_factory(OrderProposal, OrderProposalLine, form=ProposalLineForm, formset=ProposalLineSet,
    fields=['channel', 'customer', 'customer_name', 'finished_good', 'quantity'], extra=0,
    can_delete=True, min_num=1, validate_min=True, max_num=100, validate_max=True, absolute_max=120)


def proposal_query(request):
    return OrderProposal.objects.filter(business=request.business).prefetch_related(
        'lines__finished_good__portion_profile', 'lines__customer', 'order_links__order')


def grouped(proposal, data=None):
    groups = OrderedDict()
    for line in proposal.lines.all():
        line.review_quantity = data.get(f'quantity-{line.pk}', str(line.quantity / product_portion(line.finished_good)[1])) if data is not None else str(line.quantity / product_portion(line.finished_good)[1])
        group = groups.setdefault(line.destination_key, {'key': line.destination_key,
            'label': line.destination_label, 'channel': line.channel, 'customer': line.customer,
            'customer_name': line.customer_name, 'lines': []})
        group['lines'].append(line)
    return list(groups.values())


def share_data(request, proposal, groups):
    url = absolute_url(request, reverse('proposal_detail', args=[proposal.pk]))
    lines = [f'*Production proposal · {proposal.title}*', request.business.name,
             f'Planned for {proposal.planned_date:%d %b %Y}', 'Planning only · no stock reserved or released.']
    for group in groups:
        lines += ['', f'• *{group["label"]}*']
        lines += [f'  • {line.finished_good.name}: {format_product_quantity(line.quantity, line.finished_good)}' for line in group['lines']]
    if proposal.notes:
        lines += ['', proposal.notes]
    lines += ['', 'Review and use this proposal (sign-in required):', url]
    return {'proposal_share_text': render_lines(lines), 'proposal_image_url': reverse('proposal_snapshot', args=[proposal.pk])}


@login_required
@require_GET
def proposal_list(request):
    require_access(request)
    return render(request, 'production/proposals_list.html', {'proposals': Paginator(proposal_query(request), 25).get_page(request.GET.get('page'))})


@login_required
@transaction.atomic
def proposal_form(request, pk=None):
    require_access(request, True)
    instance = get_object_or_404(OrderProposal.objects.filter(business=request.business).select_for_update(), pk=pk) if pk else OrderProposal(business=request.business, created_by=request.user)
    form = ProposalForm(request.POST or None, instance=instance)
    formset = ProposalLines(request.POST or None, instance=instance, prefix='lines', form_kwargs={'business': request.business})
    if request.method == 'POST':
        valid = form.is_valid()
        valid = formset.is_valid() and valid
        if valid:
            proposal = form.save()
            formset.save()
            audit(request.business, request.user, 'update' if pk else 'create', proposal, 'Production proposal saved; no operational records changed')
            if request.POST.get('after_save') == 'share':
                return redirect(reverse('proposal_detail', args=[proposal.pk]) + '?share=1')
            return redirect('proposal_detail', pk=proposal.pk)
    return render(request, 'production/proposal_form.html', {'form': form, 'formset': formset, 'proposal': instance})


@login_required
@require_GET
def proposal_detail(request, pk):
    require_access(request)
    proposal = get_object_or_404(proposal_query(request), pk=pk)
    groups = grouped(proposal)
    return render(request, 'production/proposal_detail.html', {'proposal': proposal, 'groups': groups,
        **share_data(request, proposal, groups)})


@login_required
@require_POST
@transaction.atomic
def proposal_use(request, pk):
    require_access(request, True)
    proposal = get_object_or_404(proposal_query(request).select_for_update(of=('self',)), pk=pk, archived=False)
    groups = grouped(proposal)
    try:
        planned = date.fromisoformat(request.POST.get('planned_date', ''))
        products, destinations = OrderedDict(), []
        for group in groups:
            quantities = {}
            for line in group['lines']:
                quantity = Decimal(request.POST.get(f'quantity-{line.pk}', '')) * product_portion(line.finished_good)[1]
                if not quantity.is_finite() or quantity < 0 or quantity > Decimal('9999999999.99') or quantity != quantity.quantize(Decimal('.01')):
                    raise ValueError
                if quantity:
                    quantities[str(line.finished_good_id)] = str(Decimal(quantities.get(str(line.finished_good_id), '0')) + quantity)
                    products[line.finished_good_id] = products.get(line.finished_good_id, Decimal('0')) + quantity
            if quantities:
                customer = group['customer']
                destinations.append({'channel': group['channel'], 'customer': customer.pk if customer else None,
                    'customer_name': group['customer_name'], 'quantities': quantities})
        if len(destinations) > 20 or any(q > Decimal('9999999999.99') for q in products.values()):
            raise ValueError
        items = [{'finished_good': pk, 'piece_qty': str(quantity), 'batch_qty': '0',
                  'production_batch_qty': '0', 'production_piece_qty': '0'} for pk, quantity in products.items()]
        if not items:
            raise ValueError
    except (ValueError, InvalidOperation):
        messages.error(request, 'Choose a valid date and positive quantities (up to two decimal places), with at most 20 destinations.')
        groups = grouped(proposal, request.POST)
        return render(request, 'production/proposal_detail.html', {'proposal': proposal, 'groups': groups,
            'use_values': request.POST, **share_data(request, proposal, groups)}, status=400)
    token = secrets.token_urlsafe(24)
    seeds = request.session.get('production_proposals', {})
    seeds = {k: v for k, v in seeds.items() if time.time() - v['created'] < 7200}
    seeds = dict(list(seeds.items())[-9:])
    destination = destinations[0]
    customer = Customer.objects.filter(business=request.business, pk=destination['customer']).first() if destination['customer'] else None
    combined = len(destinations) > 1
    channel = 'physical_store' if combined else destination['channel']
    seeds[token] = {'business': request.business.pk, 'user': request.user.pk, 'created': time.time(),
        'proposal': proposal.pk, 'title': proposal.title, 'key': 'combined', 'items': items, 'combined': combined, 'destinations': destinations,
        'initial': {'date': planned.isoformat(), 'order_type': 'distribution' if channel == 'market_stock' else channel,
            'is_market_stock': channel == 'market_stock', 'customer': customer.pk if customer and not combined else None,
            'customer_name': '' if combined else (customer.name if customer else destination['customer_name']),
            'customer_region': customer.region if customer else '', 'customer_group': customer.customer_group if customer else '',
            'customer_payment_status': 'unpaid', 'notes': proposal.notes}}
    request.session['production_proposals'] = seeds
    return redirect(reverse('order_add') + '?proposal_seed=' + token)


def order_seed(request):
    token = request.POST.get('proposal_seed') or request.GET.get('proposal_seed')
    if not token:
        return None, None
    require_access(request, True)
    seed = request.session.get('production_proposals', {}).get(token)
    if not seed or seed['business'] != request.business.pk or seed['user'] != request.user.pk or time.time() - seed['created'] > 7200:
        raise Http404('This proposal review expired. Open the proposal and choose Use it again.')
    get_object_or_404(OrderProposal.objects.filter(business=request.business), pk=seed['proposal'], archived=False)
    return token, seed


@login_required
@require_GET
def proposal_snapshot(request, pk):
    require_access(request)
    proposal = get_object_or_404(proposal_query(request), pk=pk)
    groups = grouped(proposal)
    url = absolute_url(request, reverse('proposal_detail', args=[pk]))
    rows = [{'label': line.finished_good.name, 'value': format_product_quantity(line.quantity, line.finished_good),
             'detail': group['label']} for group in groups for line in group['lines']]
    image = render_report_snapshot(business=request.business, title='Production proposal',
        subtitle=f'{proposal.title} · {proposal.planned_date:%d %b %Y}', rows=rows,
        total_label='', total=None, footer='', scope='Planning only', url=url,
        link_heading='Open INPROFIC to review this proposal')
    return snapshot_response(image, f'production-proposal-{pk}')


@login_required
@require_POST
@transaction.atomic
def proposal_archive(request, pk):
    require_access(request, True)
    proposal = get_object_or_404(OrderProposal.objects.filter(business=request.business).select_for_update(), pk=pk)
    proposal.archived = not proposal.archived
    proposal.save(update_fields=['archived', 'updated_at'])
    audit(request.business, request.user, 'update', proposal,
        'Production proposal archived' if proposal.archived else 'Production proposal reopened')
    return redirect('proposal_detail', pk=pk)
