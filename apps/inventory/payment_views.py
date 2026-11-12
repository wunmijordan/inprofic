from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from sales.models import Sale
from .forms import MarketReceiptConfirmationForm, MarketReceiptCorrectionFormSet, MarketReceiptReasonForm
from .release_payments import market_payment_review, apply_market_payment_review

SALT = 'market-stock-receipt-correction-v1'


@login_required
@require_http_methods(['GET', 'POST'])
def market_stock_payments(request, pk):
    sale = get_object_or_404(Sale.objects.filter(business=request.business,
        source__in=['distribution_order', 'online_order'], linked_order__isnull=True,
        market_stock_movements__movement_type='release').distinct(), pk=pk)
    action = request.POST.get('action') if request.method == 'POST' else None
    confirm = MarketReceiptConfirmationForm(request.POST if action == 'apply' else None)
    error = None
    payload = {}
    if action == 'apply':
        try:
            payload = signing.loads(request.POST.get('review_token', ''), salt=SALT, max_age=1800)
            if (payload['business'] != request.business.pk or payload['user'] != request.user.pk or payload['sale'] != sale.pk):
                raise signing.BadSignature()
            if not confirm.is_valid():
                raise ValidationError('Confirm the reviewed actual payment amounts before saving.')
            apply_market_payment_review(business=request.business, sale_id=sale.pk, user=request.user,
                amounts=payload['amounts'], fingerprint=payload['fingerprint'], reason=payload['reason'])
        except (signing.BadSignature, KeyError, TypeError):
            error = 'The payment review expired or is invalid. Review the receipts again.'
        except ValidationError as exc:
            error = exc
        else:
            messages.success(request, f'Recorded receipts for sale #{sale.pk} corrected with an audit trail.')
            return redirect(reverse('market_stock') + '?date=' + sale.date.isoformat() + '#distribution-summary')
    review = market_payment_review(business=request.business, sale_id=sale.pk, user=request.user)
    form = MarketReceiptReasonForm(request.POST if action == 'preview' else {'reason': payload.get('reason', '')} if action == 'apply' else None)
    initial = [{'key': r['key'], 'amount': r['old']} for r in review['row']['receipts']]
    formset = MarketReceiptCorrectionFormSet(request.POST if action == 'preview' else None,
        initial=initial, prefix='receipts')
    if error:
        form.add_error(None, error)
    token = None
    if action == 'preview' and form.is_valid() and formset.is_valid():
        amounts = {row.cleaned_data['key']: str(row.cleaned_data['amount']) for row in formset}
        try:
            review = market_payment_review(business=request.business, sale_id=sale.pk, user=request.user, amounts=amounts)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            if not review['errors']:
                token = signing.dumps({'business': request.business.pk, 'user': request.user.pk, 'sale': sale.pk,
                    'amounts': amounts, 'fingerprint': review['fingerprint'], 'reason': form.cleaned_data['reason']}, salt=SALT, compress=True)
    for row, receipt in zip(formset.forms, review['row']['receipts']):
        row.receipt = receipt
    return render(request, 'inventory/market_stock_payments.html', {
        'sale': sale, 'form': form, 'formset': formset, 'review': review,
        'review_token': token, 'confirmation_form': confirm,
    })
