from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Prefetch, Subquery
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import VendorGroupFormSet, PurchaseOrderForm, PurchaseRequestItemFormSet, SupplierForm, PurchaseApprovalLineForm
from .models import PurchaseRequest, PurchaseOrder, PurchaseOrderItem, RawMaterialCostSnapshot, Supplier, SupplierPayment
from inventory.models import ProductionMaterial, RawMaterial, RecipeItem
from inventory.services import record_finished_good_movement, record_raw_material_movement
from inventory.models import FinishedGood, StockMovement
from core.invoice import purchase_order_pdf, purchase_request_pdf
from core.services import record_cash, audit
from core import ledger
from core.models import FinancialTransaction
from accounts.services import user_has_permission, is_business_admin
from .services import describe_request, request_editable, request_orders, resolve_supplier, recorded_vendor_payments, retain_deleted_request_payments, link_direct_vendor_payments, order_audit_state
from .share import render_snapshot, share_text, summarize
from .stockview import stock_payload, stock_status


def today():
    return timezone.localdate()


def _ensure_po_payment_recorded(po, user):
    """Record only the remaining amount when a PO is marked Paid.

    A partially paid PO may already have SupplierPayment rows. If the PO is
    later marked Paid before it reaches Finance, only the outstanding balance
    should be recorded here; never charge the supplier twice.
    """
    if po.payment_status != "paid" or not po.account:
        return
    if FinancialTransaction.objects.filter(
        business=po.business, reference=f"PO-{po.pk}", category="Procurement"
    ).exists():
        return

    paid = sum((p.amount for p in po.payments.all()), Decimal("0"))
    outstanding = max(Decimal("0"), po.total - paid)
    if not outstanding:
        return

    record_cash(
        po.business, user, date=po.date, amount=outstanding,
        transaction_type=FinancialTransaction.OUTFLOW, category="Procurement",
        description=f"Payment for purchase order #{po.pk}", payment_method=po.payment_method,
        reference=f"PO-{po.pk}", account=po.account,
    )


@login_required
def procurement_list(request):
    from .spending import workspace_records
    context = workspace_records(request)
    raw_materials = list(RawMaterial.objects.filter(business=request.business)) if context['spending_access']['procurement'] else []
    grouped_materials = {value: [] for value, _ in RawMaterial.CATEGORY_CHOICES}
    for material in raw_materials:
        grouped_materials.setdefault(material.category, []).append(material)
    context.update(
        raw_material_categories=[{'value': value, 'label': label, 'items': grouped_materials.get(value, [])}
                                 for value, label in RawMaterial.CATEGORY_CHOICES],
        suppliers=Supplier.objects.filter(business=request.business).order_by('name') if context['spending_access']['procurement'] else [],
    )
    if context['period_valid']:
        from .spending_views import share_context
        context.update(share_context(request, context))
    return render(request, "procurement/procurement_list.html", context)


@login_required
def supplier_add(request):
    if not user_has_permission(request.user, request.business, "procurement", "edit"):
        return render(request, "403.html", status=403)
    form = SupplierForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        supplier = form.save(commit=False)
        supplier.business = request.business
        supplier.created_by = request.user
        try:
            supplier.save()
        except IntegrityError:
            form.add_error("name", "A supplier with that name already exists in this business.")
        else:
            messages.success(request, f"Supplier {supplier.name} saved.")
            return redirect("procurement_list")
    return render(request, "procurement/supplier_form.html", {"form": form})


@login_required
def supplier_edit(request, pk):
    if not user_has_permission(request.user, request.business, "procurement", "edit"):
        return render(request, "403.html", status=403)
    supplier = get_object_or_404(Supplier, pk=pk, business=request.business)
    form = SupplierForm(request.POST or None, instance=supplier)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            form.add_error("name", "A supplier with that name already exists in this business.")
        else:
            messages.success(request, f"Supplier {supplier.name} updated.")
            return redirect("procurement_list")
    return render(request, "procurement/supplier_form.html", {"form": form, "supplier": supplier})


@login_required
@transaction.atomic
def po_form(request, pk=None):
    obj = get_object_or_404(PurchaseOrder.objects.select_for_update(), pk=pk, business=request.business) if pk else None
    share_only = bool(obj and request.method == "GET" and request.GET.get("share") == "1")
    if not user_has_permission(request.user, request.business, "procurement", "view" if share_only else "edit"):
        return render(request, "403.html", status=403)
    if obj and obj.request_id:
        obj = request_orders(obj, lock=True).first()
    if share_only:
        summary = summarize(_shareable_order(request, obj.pk), request.business,
                            request.build_absolute_uri(reverse("po_approve", args=[obj.pk])), user=request.user)
        return render(request, "procurement/po_preview.html", {
            "obj": obj, "summary": summary, "share_text": share_text(summary),
            "share_image_url": reverse("po_share_image", args=[obj.pk]),
        })
    vendor_orders = list(request_orders(obj, lock=True).prefetch_related('payments')) if obj else []
    if obj and not request_editable(vendor_orders):
        messages.error(request, "Approved or received requests cannot be edited.")
        return redirect("procurement_list")

    # Build inventory choices once for the request-wide model formset.
    # Build tenant-scoped inventory choices once per request so each form does not
    # repeat the same category/product queries while rendering or validating.
    raw_materials = list(RawMaterial.objects.filter(business=request.business).order_by("name"))
    latest_purchase_cost = (
        StockMovement.objects.filter(
            business=request.business,
            finished_good_id=OuterRef("pk"),
            movement_type=StockMovement.FG_PURCHASE,
            quantity__gt=0,
        )
        .order_by("-occurred_at", "-id")
        .values("unit_value")[:1]
    )
    products_qs = (
        FinishedGood.objects.filter(business=request.business, stock__isnull=False)
        .select_related("business")
        .annotate(prefetched_latest_purchase_unit_value=Subquery(latest_purchase_cost))
        .prefetch_related(
            Prefetch("recipe_items", queryset=RecipeItem.objects.select_related("raw_material")),
            Prefetch("production_materials", queryset=ProductionMaterial.objects.select_related("raw_material")),
        )
    )
    if request.business.uses_production:
        products_qs = products_qs.filter(source_type=FinishedGood.SOURCE_PURCHASED_FOR_RESALE)
    products = list(products_qs.distinct().order_by("name"))
    inventory_catalog = {
        "raw": {item.pk: item for item in raw_materials},
        "finished": {item.pk: item for item in products},
    }
    if obj and obj.status in {"approved", "received"}:
        messages.error(request, "Approved or received requests cannot be edited.")
        return redirect("procurement_list")
    item_queryset = PurchaseOrderItem.objects.filter(purchase_order__in=vendor_orders).select_related(
        'purchase_order', 'raw_material', 'finished_good')
    before_states = {po.pk: order_audit_state(po) for po in vendor_orders} if request.method == 'POST' else {}
    recorded_paid, _direct_cash = recorded_vendor_payments(vendor_orders)
    order_lookup = {po.pk: po for po in vendor_orders}
    group_initial = [dict(order_id=po.pk, supplier=po.supplier, payment_status=po.payment_status,
                          payment_method=po.payment_method, account=po.account_id) for po in vendor_orders] or [
                              {'payment_status': 'unpaid', 'payment_method': 'Transfer'}]
    grouped = request.method != 'POST' or 'vendors-TOTAL_FORMS' in request.POST
    vendor_formset = VendorGroupFormSet(request.POST if request.method == 'POST' and grouped else None,
        prefix='vendors', initial=group_initial, form_kwargs={'business': request.business})
    for vendor_form in vendor_formset.forms:
        identity = vendor_form.data.get(vendor_form.add_prefix('order_id')) if vendor_form.is_bound else vendor_form.initial.get('order_id')
        original = order_lookup.get(int(identity)) if str(identity or '').isdigit() else None
        vendor_form.recorded_paid = recorded_paid.get(original.pk, Decimal('0')) if original else Decimal('0')
        if vendor_form.recorded_paid:
            vendor_form.recorded_order = original
            vendor_form.fields['supplier'].disabled = True
            vendor_form.initial['supplier'] = original.supplier
            for field in ['payment_status', 'payment_method', 'account', 'amount_paid']:
                vendor_form.fields.pop(field)
    if request.method == "POST":
        form = PurchaseOrderForm(request.POST, instance=obj, grouped=grouped)
        formset = PurchaseRequestItemFormSet(
            request.POST, prefix='items', queryset=item_queryset,
            form_kwargs={"business": request.business, "inventory_catalog": inventory_catalog},
        )
        validations = [form.is_valid(), formset.is_valid()]
        if grouped:
            validations.append(vendor_formset.is_valid())
        if all(validations):
            active_items = [
                f.cleaned_data for f in formset.forms
                if f.cleaned_data and not f.cleaned_data.get("DELETE")
            ]
            if not active_items:
                form.add_error(None, "Add at least one item.")
            else:
                groups = {}
                if grouped:
                    for index, vendor_form in enumerate(vendor_formset.forms):
                        data = vendor_form.cleaned_data
                        if data and not data.get('DELETE'):
                            groups[str(index)] = {'name': data.get('supplier', '').strip(), 'forms': [],
                                'total': Decimal('0'), 'payment': dict(data), 'form': vendor_form,
                                'original': order_lookup.get(data.get('order_id'))}
                            if data.get('order_id') and data['order_id'] not in order_lookup:
                                vendor_form.add_error('order_id', 'Reload this request; the purchase group is invalid.')
                default = (form.cleaned_data.get('supplier') or '').strip()
                for item_form in formset.forms:
                    data = item_form.cleaned_data
                    if not data or data.get('DELETE'):
                        continue
                    if grouped:
                        group = groups.get(data.get('vendor_group', ''))
                        if group is None:
                            item_form.add_error(None, 'Choose an active vendor group for this item.')
                            form.add_error(None, 'Some items have an invalid purchase group. Reload the request and try again.')
                            continue
                    else:
                        name = (data.get('supplier') or default).strip()
                        group = groups.setdefault(name.casefold(), {'name': name, 'forms': [], 'total': Decimal('0'),
                            'payment': dict(form.cleaned_data), 'form': form})
                    group['forms'].append(item_form)
                    group['total'] += data['qty'] * data['unit_cost']
                if not grouped and any(recorded_paid.values()):
                    form.add_error(None, 'Reload this request to edit its recorded payment groups safely.')
                if not grouped:
                    paid_vendor = (form.cleaned_data.get('payment_supplier') or default).strip().casefold()
                    if len(groups) == 1:
                        paid_vendor = next(iter(groups))
                    if form.cleaned_data['payment_status'] == 'partial' and (paid_vendor not in groups or (len(groups) > 1 and not paid_vendor)):
                        form.add_error('payment_supplier', 'Select a vendor on this request for the advance.')
                    amount = form.cleaned_data.get('amount_paid') or Decimal('0')
                    if form.cleaned_data['payment_status'] == 'paid' and amount and amount != sum((g['total'] for g in groups.values()), Decimal('0')):
                        form.add_error('amount_paid', 'For a Paid request, the amount must equal the entire request total.')
                    for key, group in groups.items():
                        if group['payment']['payment_status'] == 'partial' and key != paid_vendor:
                            group['payment'].update(payment_status='unpaid', amount_paid=Decimal('0'))
                if grouped:
                    identities = [g['original'].pk for g in groups.values() if g['original']]
                    if len(identities) != len(set(identities)):
                        form.add_error(None, 'A purchase group was submitted more than once.')
                    for previous in vendor_orders:
                        if recorded_paid.get(previous.pk) and previous.pk not in identities:
                            form.add_error(None, 'Keep groups with recorded payments. To remove the whole request, use Delete; payments remain in Finance.')
                for group in groups.values():
                    original = group.get('original')
                    paid_before = recorded_paid.get(original.pk, Decimal('0')) if original else Decimal('0')
                    group['recorded_paid'] = paid_before
                    if paid_before:
                        group['payment'].update(payment_status='paid' if paid_before >= group['total'] else 'partial',
                            payment_method=original.payment_method, account=original.account, amount_paid=Decimal('0'))
                        if group['total'] < paid_before:
                            group['form'].add_error(None, 'Group total cannot be lower than payments already recorded. Handle refunds separately in Finance.')
                    payment = group['payment']
                    amount = payment.get('amount_paid') or Decimal('0')
                    if not group['forms']:
                        group['form'].add_error(None, 'Add at least one item to this group, or remove it.')
                    if payment['payment_status'] == 'partial' and amount > group['total']:
                        group['form'].add_error('amount_paid', 'The advance cannot exceed this vendor’s total.')
                    if grouped and payment['payment_status'] == 'paid' and amount and amount != group['total']:
                        group['form'].add_error('amount_paid', 'For a Paid group, the amount must equal its total.')
                if not form.errors and not any(f.errors for f in formset.forms if not f.cleaned_data.get('DELETE')) and not any(g['form'].errors for g in groups.values()):
                    if obj:
                        link_direct_vendor_payments(vendor_orders, request.user)
                    envelope = obj.request if obj and obj.request_id else None
                    if len(groups) > 1 and envelope is None:
                        envelope = PurchaseRequest.objects.create(business=request.business, created_by=request.user)
                    existing = {po.supplier.strip().casefold(): po for po in vendor_orders}
                    saved, used = [], set()
                    for index, (key, group) in enumerate(groups.items()):
                        # The first vendor order anchors existing shared request URLs.
                        # Keep it even when its original vendor is removed from a draft.
                        po = group.get('original') if grouped else obj if index == 0 and obj is not None else existing.get(key)
                        if grouped and index == 0 and obj is not None and not recorded_paid.get(obj.pk) and not group.get('recorded_paid') and obj.pk not in [g['original'].pk for g in groups.values() if g.get('original')]:
                            po = obj
                        if po is None or po.pk in used:
                            po = PurchaseOrder(business=request.business, created_by=request.user)
                        po.request = envelope
                        po.date = form.cleaned_data['date']
                        po.supplier = group['name']
                        po.supplier_master = resolve_supplier(request.business, po.supplier, created_by=request.user)
                        payment = group['payment']
                        po.account = payment.get('account')
                        po.payment_method = payment['payment_method']
                        advance = (payment.get('amount_paid') or Decimal('0')) if payment['payment_status'] == 'partial' else Decimal('0')
                        po.payment_status = (payment['payment_status'] if group.get('recorded_paid') else
                            'paid' if payment['payment_status'] == 'paid' or (advance and advance >= group['total'])
                            else 'partial' if advance else 'unpaid')
                        po.save()
                        saved.append(po)
                        group['saved_order'] = po
                        used.add(po.pk)
                        for item_form in group['forms']:
                            item = item_form.save(commit=False)
                            item.purchase_order = po
                            item.requested_qty = item.qty
                            item.save()
                    for item_form in formset.forms:
                        if item_form.cleaned_data.get('DELETE') and item_form.instance.pk:
                            item_form.instance.delete()
                    for previous in vendor_orders:
                        if previous.pk not in used:
                            audit(request.business, request.user, 'delete', previous, 'Vendor group removed from unapproved request',
                                  {'before': before_states.get(previous.pk), 'after': None, 'recorded_paid': '0'})
                            previous.delete()  # Paid groups are retained by the validation above.
                    for group in groups.values():
                        po = group['saved_order']
                        payment_data = group['payment']
                        if group.get('recorded_paid'):
                            continue  # Editing items must never repost a historical payment.
                        advance = (payment_data.get('amount_paid') or Decimal('0')) if payment_data['payment_status'] == 'partial' else Decimal('0')
                        if po.payment_status == 'paid':
                            _ensure_po_payment_recorded(po, request.user)
                        elif advance:
                            payment = SupplierPayment.objects.create(
                                business=po.business, created_by=request.user, date=po.date,
                                supplier=po.supplier or 'Unnamed supplier', supplier_master=po.supplier_master,
                                amount=advance, payment_method=po.payment_method, reference=f'PO-{po.pk}-INITIAL',
                                notes=f'Initial payment for purchase order #{po.pk}', purchase_order=po, account=po.account)
                            record_cash(po.business, request.user, date=payment.date, amount=advance,
                                        transaction_type=FinancialTransaction.OUTFLOW, category='Supplier payment',
                                        description=f'Advance to {po.supplier} — purchase order #{po.pk}',
                                        payment_method=payment.payment_method, reference=payment.reference, account=payment.account)
                    for po in saved:
                        audit(request.business, request.user, 'update' if obj else 'create', po,
                              'Purchase request saved', {'request_id': envelope.pk if envelope else None,
                                  'before': before_states.get(po.pk), 'after': order_audit_state(po),
                                  'recorded_paid': str(recorded_paid.get(po.pk, Decimal('0')))})
                    anchor = min(saved, key=lambda po: po.pk)
                    messages.success(request, 'Purchase request submitted for approval.')
                    if request.POST.get('after_save') == 'share':
                        return redirect(reverse('po_edit', args=[anchor.pk]) + '?share=1')
                    return redirect('procurement_list')
    else:
        form = PurchaseOrderForm(instance=obj, initial=None if obj else {'date': ledger.default_entry_date()}, grouped=True)
        formset = PurchaseRequestItemFormSet(prefix='items', queryset=item_queryset,
            form_kwargs={"business": request.business, "inventory_catalog": inventory_catalog})
    # Keep flat item management/prefixes while presenting each vendor as a group.
    group_blocks = []
    order_indexes = {po.pk: str(index) for index, po in enumerate(vendor_orders)}
    for index, vendor_form in enumerate(vendor_formset.forms):
        rows = []
        for item_form in formset.forms:
            key = item_form.data.get(item_form.add_prefix('vendor_group')) if item_form.is_bound and grouped else order_indexes.get(item_form.instance.purchase_order_id, '0')
            if key == str(index):
                item_form.initial['vendor_group'] = str(index)
                rows.append(item_form)
        group_blocks.append({'form': vendor_form, 'rows': rows, 'index': index})
    current_costs = {
        **{f"raw:{m.pk}": f"{m.cost_per_purchase_unit:.2f}" for m in raw_materials},
        **{f"finished:{g.pk}": f"{g.est_cost:.2f}" for g in products},
    }
    stock_levels = {
        **{f"raw:{m.pk}": stock_payload(stock_status(m)) for m in raw_materials},
        **{f"finished:{g.pk}": stock_payload(stock_status(g)) for g in products},
    }
    return render(request, "procurement/po_form.html", {
        "form": form, "formset": formset, "legacy_post": not grouped, "has_recorded_payments": any(recorded_paid.values()), "vendor_formset": vendor_formset, "group_blocks": group_blocks, "obj": obj, "current_costs": current_costs, "stock_levels": stock_levels,
        "share_text": share_text(summarize(_shareable_order(request, obj.pk), request.business, request.build_absolute_uri(reverse("po_approve", args=[obj.pk])), user=request.user)) if obj else "",
        "share_image_url": reverse("po_share_image", args=[obj.pk]) if obj else "",
        "stock_time": timezone.localtime().strftime("%d %b %H:%M"),
        "suppliers": Supplier.objects.filter(business=request.business, active=True).order_by("name"),
    })


@login_required
def po_receive(request, pk):
    if not user_has_permission(request.user, request.business, "procurement", "edit"):
        return render(request, "403.html", status=403)
    po = get_object_or_404(PurchaseOrder, pk=pk, business=request.business)
    if request.method == "POST":
        # Stock arrives on the day it really arrives: today by default, or an
        # explicit "event_date" so a late-entered receipt keeps its true date.
        receipt_date = ledger.resolve_event_date(request, po.date)
        with transaction.atomic():
            # Prevent two workers from receiving the same PO and duplicating
            # its stock/finance effects.
            po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
            if po.status == "received":
                return redirect("procurement_list")
            if po.status != "approved":
                messages.error(request, "Approve this purchase request before receiving stock.")
                return redirect("procurement_list")
            items = list(po.items.filter(qty__gt=0).select_related("raw_material", "finished_good"))
            if not items:
                messages.error(request, "There are no approved items to receive.")
                return redirect("procurement_list")
            invalid_item = next((
                item for item in items
                if (
                    not item.stock_item
                    or item.stock_item.business_id != po.business_id
                    or item.qty <= 0
                    or item.unit_cost < 0
                    or (item.finished_good_id and item.finished_good.stock is None)
                    or (
                        item.finished_good_id
                        and po.business.uses_production
                        and item.finished_good.source_type != FinishedGood.SOURCE_PURCHASED_FOR_RESALE
                    )
                )
            ), None)
            if invalid_item:
                messages.error(request, "This purchase order contains an invalid or non-purchasable inventory item.")
                return redirect("procurement_list")
            for item in items:
                if item.raw_material_id:
                    mat = item.raw_material
                    factor = mat.total_conversion_factor or 1
                    # unit (e.g. bags); stock and cost_per_unit are always in
                    # its usage unit (e.g. kg) — convert on the way in.
                    usage_cost = (item.unit_cost / factor).quantize(Decimal("0.000001"))
                    record_raw_material_movement(
                        mat,
                        item.qty * factor,
                        StockMovement.RAW_PURCHASE,
                        note="Purchase order received", reference=f"PO-{po.pk}", unit_value=usage_cost,
                        on_date=receipt_date,
                    )
                    RawMaterialCostSnapshot.objects.create(
                        business=po.business,
                        raw_material=mat,
                        purchase_order_item=item,
                        effective_date=receipt_date,
                        purchase_unit_cost=item.unit_cost,
                        usage_unit_cost=usage_cost,
                        supplier=po.supplier or "",
                        supplier_master=po.supplier_master,
                    )
                    # This remains the material's current/latest procurement cost.
                    # A Backlog receipt is history, and a late-entered older receipt
                    # must not replace a newer dated cost, so backdating stays safe.
                    newer_cost = RawMaterialCostSnapshot.raw_objects.filter(
                        raw_material=mat, effective_date__gt=receipt_date
                    ).exists()
                    if ledger.segment_for(po.business_id, receipt_date) == ledger.SEGMENT_LIVE and not newer_cost:
                        mat.cost_per_unit = usage_cost
                        mat.save(update_fields=["cost_per_unit"])
                else:
                    good = item.finished_good
                    if good.business_id != po.business_id or good.stock is None:
                        raise ValueError("A purchased stock product must belong to this business and have stock enabled.")
                    record_finished_good_movement(
                        good,
                        item.qty,
                        StockMovement.FG_PURCHASE,
                        note=f"Stock arrival from {po.supplier or 'supplier'}",
                        reference=f"PO-{po.pk}",
                        unit_value=item.unit_cost,
                        on_date=receipt_date,
                    )
            po.status = "received"
            po.received_date = receipt_date
            po.save()
            if po.payment_status == "paid":
                _ensure_po_payment_recorded(po, request.user)
            audit(po.business, request.user, "receive", po, f"Purchase order #{po.pk} received", {"payment_status": po.payment_status})
        messages.success(request, "Stock received into inventory.")
    return redirect("procurement_list")


@login_required
@transaction.atomic
def po_delete(request, pk):
    if not user_has_permission(request.user, request.business, 'procurement', 'edit'):
        return render(request, '403.html', status=403)
    obj = get_object_or_404(PurchaseOrder.objects.select_for_update(), pk=pk, business=request.business)
    if request.method == "POST":
        vendors = list(request_orders(obj, lock=True).prefetch_related('payments'))
        if not request_editable(vendors):
            messages.error(request, 'Only unapproved requests can be deleted. Approved and received requests must be preserved.')
        else:
            envelope = obj.request if obj.request_id else None
            deleted_states = {vendor.pk: order_audit_state(vendor) for vendor in vendors}
            paid_before, _cash = recorded_vendor_payments(vendors)
            retain_deleted_request_payments(vendors, request.user)
            for vendor in vendors:
                audit(request.business, request.user, 'delete', vendor, f'Unapproved purchase order #{vendor.pk} removed',
                      {'before': deleted_states[vendor.pk], 'retained_payment_amount': str(paid_before[vendor.pk])})
                vendor.delete()
            if envelope:
                envelope.delete()
            messages.success(request, 'Request removed. Recorded payments remain in Finance; no refund was recorded.' if any(paid_before.values()) else 'Request removed.')
    return redirect("procurement_list")

@login_required
def po_invoice(request, pk):
    if not user_has_permission(request.user, request.business, 'procurement', 'view'):
        return render(request, '403.html', status=403)
    if request.GET.get('request') == '1':
        po = _shareable_order(request, pk)
        return purchase_request_pdf(summarize(po, request.business, '', user=request.user))
    po = get_object_or_404(
        PurchaseOrder.objects.prefetch_related("items__raw_material", "items__finished_good"),
        pk=pk, business=request.business,
    )
    return purchase_order_pdf(po)



def _shareable_order(request, pk):
    return get_object_or_404(
        PurchaseOrder.objects.select_related("approved_by").prefetch_related("items__raw_material", "items__finished_good"),
        pk=pk, business=request.business)


@login_required
def po_share_image(request, pk):
    """PNG snapshot of the request for forwarding on WhatsApp. Members with Procurement access only."""
    if not user_has_permission(request.user, request.business, "procurement", "view"):
        return render(request, "403.html", status=403)
    po = _shareable_order(request, pk)
    url = request.build_absolute_uri(reverse("po_approve", args=[po.pk]))
    response = HttpResponse(render_snapshot(summarize(po, request.business, url, user=request.user)), content_type="image/png")
    response["Cache-Control"] = "private, no-store"
    # Inline opens the full-size image in the browser's viewer; ?download=1 saves it to the device.
    disposition = "attachment" if request.GET.get("download") == "1" else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="purchase-request-{po.pk}.png"'
    return response


@login_required
@transaction.atomic
def po_approve(request, pk):
    if not user_has_permission(request.user, request.business, "procurement", "view"):
        return render(request, "403.html", status=403)
    lock = request.method == 'POST'
    orders = PurchaseOrder.objects.select_for_update() if lock else PurchaseOrder.objects.all()
    po = get_object_or_404(orders, pk=pk, business=request.business)
    vendors = list(request_orders(po, lock=lock).prefetch_related('payments'))
    items_query = PurchaseOrderItem.objects.filter(purchase_order__in=vendors)
    if lock:
        # Material/product joins are nullable: PostgreSQL can lock only the item rows.
        items_query = items_query.select_for_update(of=('self',))
    items = list(items_query.select_related('raw_material', 'finished_good', 'purchase_order')
                 .order_by('purchase_order_id', 'pk'))
    can_approve = is_business_admin(request.user, request.business)
    pending = all(v.status in {"draft", "ordered"} for v in vendors)
    if request.method == "POST" and not can_approve:
        return render(request, "403.html", status=403)
    if request.method == "POST" and not pending:
        messages.info(request, "This purchase request has already been approved or received.")
        return redirect("procurement_list")
    line_forms = [PurchaseApprovalLineForm(
        request.POST if request.method == "POST" else None,
        item=item, prefix=f"item-{item.pk}",
        initial={"qty": item.qty, "reason": item.approval_reason},
    ) for item in items]
    error = ""
    if request.method == "POST":
        valid = all([form.is_valid() for form in line_forms])
        if valid:
            unsafe_payment = False
            paid_totals, _cash = recorded_vendor_payments(vendors)
            for vendor in vendors:
                vendor_total = sum((f.cleaned_data['qty'] * f.item.unit_cost for f in line_forms
                                    if f.item.purchase_order_id == vendor.pk), Decimal('0'))
                unsafe_payment = unsafe_payment or vendor_total < paid_totals[vendor.pk]
            if not any(form.cleaned_data["qty"] > 0 for form in line_forms):
                error = "Keep at least one approved item. Leave an entirely declined request unapproved."
            elif unsafe_payment:
                error = "The approved total cannot be lower than payments already recorded. Resolve the supplier advance before reducing this request."
            else:
                changes = []
                for form in line_forms:
                    item = form.item
                    item.requested_qty = form.requested
                    item.qty = form.cleaned_data["qty"]
                    item.approval_reason = form.cleaned_data["reason"].strip()
                    item.save(update_fields=["requested_qty", "qty", "approval_reason"])
                    changes.append({"item_id": item.pk, "requested": str(item.requested_qty), "approved": str(item.qty), "reason": item.approval_reason})
                for vendor in vendors:
                    vendor.status = 'approved'
                    vendor.approved_by = request.user
                    vendor.approved_at = timezone.now()
                    vendor.save(update_fields=['status', 'approved_by', 'approved_at'])
                    audit(request.business, request.user, 'approve', vendor,
                          f'Purchase request #{po.pk} approved', {'items': [c for c in changes if any(i.pk == c['item_id'] and i.purchase_order_id == vendor.pk for i in items)]})
                messages.success(request, "Request approved. Receive the approved items when they arrive.")
                return redirect("procurement_list")
    approval_url = request.build_absolute_uri(reverse("po_approve", args=[po.pk]))
    summary = summarize(_shareable_order(request, pk), request.business, approval_url, user=request.user)
    for line in line_forms:
        line.stock = stock_status(line.item.stock_item)
    return render(request, "procurement/po_approve.html", {
        "po": po, "line_forms": line_forms, "can_approve": can_approve and pending, "approval_error": error,
        "summary": summary,
    })
