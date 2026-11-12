from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Max, Min, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .customer_activity import ActivityDates
from .models import Customer, Sale
from .repricing import apply_price_review, price_review, receipt_review, require_repricing

SALT = 'customer-distribution-repricing-v1'


@login_required
@require_http_methods(['GET', 'POST'])
def customer_reprice(request, pk):
    require_repricing(request.user, request.business)
    customer = get_object_or_404(Customer.objects.filter(business=request.business), pk=pk)
    bounds = Sale.raw_objects.filter(business=request.business, source='distribution_order').filter(
        Q(customer_master=customer) | Q(customer_master__isnull=True, customer=customer.name)
    ).aggregate(first=Min('date'), last=Max('date'))
    today = timezone.localdate()
    first = bounds['first'] or today
    all_end = max(today, bounds['last'] or today)
    values = request.POST if request.method == 'POST' else {
        'start': request.GET.get('start', first.isoformat()), 'end': request.GET.get('end', all_end.isoformat())}
    form = ActivityDates(values)
    context = {'customer': customer, 'date_form': form, 'first_date': first, 'all_end': all_end}
    correct_receipts = request.POST.get('receipt_mode') == 'correct' if request.method == 'POST' else request.GET.get('receipts') == 'correct'
    context['correct_receipts'] = correct_receipts
    amounts = None
    previous_fingerprint = None
    if form.is_valid():
        start, end = form.cleaned_data['start'], form.cleaned_data['end']
        if request.method == 'POST' and request.POST.get('action') == 'preview_receipts':
            try:
                prior = load_review(request, customer, start, end)
                if not correct_receipts or prior.get('receipt_corrections') is None:
                    raise signing.BadSignature()
                amounts = prior['receipt_corrections'].copy()
                previous_fingerprint = prior['fingerprint']
                for key, value in request.POST.items():
                    if key.startswith('receipt_') and key != 'receipt_mode':
                        receipt_key = key.removeprefix('receipt_')
                        if receipt_key not in amounts:
                            raise signing.BadSignature()
                        amounts[receipt_key] = value
            except (signing.BadSignature, KeyError, AttributeError):
                form.add_error(None, 'The receipt review expired or is invalid. Choose Review period again.')
        if request.method == 'POST' and request.POST.get('action') == 'apply':
            try:
                payload = load_review(request, customer, start, end)
                if request.POST.get('confirm') != 'yes':
                    raise ValidationError('Confirm that you approve the reviewed invoice and receipt changes.')
                amounts = payload.get('receipt_corrections')
                if correct_receipts != (amounts is not None):
                    raise signing.BadSignature()
                result = apply_price_review(business=request.business, customer=customer, start=start, end=end,
                    user=request.user, fingerprint=payload['fingerprint'], receipt_corrections=amounts)
            except (signing.BadSignature, KeyError):
                form.add_error(None, 'The review expired or is invalid. Review the figures again.')
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, f"Corrected {result['count']} distribution invoices. Invoice and settlement corrections have been audited.")
                return redirect('customer_activity', pk=customer.pk)
        review = price_review(business=request.business, customer=customer, start=start, end=end, user=request.user, include_receipts=correct_receipts)
        if correct_receipts:
            receipts = receipt_review(request.business, review, amounts, user=request.user)
            amounts = receipts['amounts']
            review['errors'].extend(receipts['errors'])
        if previous_fingerprint and previous_fingerprint != review['fingerprint']:
            form.add_error(None, 'Prices or receipts changed. Choose Review period again before confirming.')
        context['review'] = review
        page = Paginator(review['rows'], 50).get_page(request.POST.get('page') or request.GET.get('page'))
        context['review_page'] = page
        context['review_rows'] = page.object_list
        context['review_token'] = signing.dumps({'business': request.business.pk, 'customer': customer.pk,
            'user': request.user.pk, 'start': str(start), 'end': str(end), 'fingerprint': review['fingerprint'], 'receipt_corrections': amounts if correct_receipts else None}, salt=SALT, compress=True)
    return render(request, 'sales/customer_reprice.html', context)


def load_review(request, customer, start, end):
    payload = signing.loads(request.POST.get('review_token', ''), salt=SALT, max_age=1800)
    if (payload.get('business') != request.business.pk or payload.get('customer') != customer.pk
            or payload.get('user') != request.user.pk or payload.get('start') != str(start)
            or payload.get('end') != str(end)):
        raise signing.BadSignature()
    return payload
