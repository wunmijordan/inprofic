from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import PurchaseOrderForm, PurchaseOrderItemFormSet
from .models import PurchaseOrder, PurchaseOrderItem, RawMaterialCostSnapshot, SupplierPayment
from inventory.models import RawMaterial
from inventory.services import record_finished_good_movement, record_raw_material_movement
from inventory.models import FinishedGood, StockMovement
from core.invoice import purchase_order_pdf
from core.services import record_cash, audit
from core.models import FinancialTransaction


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
    # Material categories are a small fixed set. Load this tenant's materials
    # once and group them in Python instead of issuing one query per category.
    raw_materials = list(RawMaterial.objects.all())
    grouped_materials = {value: [] for value, _ in RawMaterial.CATEGORY_CHOICES}
    for material in raw_materials:
        grouped_materials.setdefault(material.category, []).append(material)
    raw_material_categories = [
        {"value": value, "label": label, "items": grouped_materials.get(value, [])}
        for value, label in RawMaterial.CATEGORY_CHOICES
    ]

    # The list renders creator plus each PO line's material/product repeatedly.
    # Join the creator and fold line-item foreign keys into the item prefetch so
    # query count stays constant as the number of purchase orders grows.
    orders = PurchaseOrder.objects.select_related("created_by").prefetch_related(
        Prefetch(
            "items",
            queryset=PurchaseOrderItem.objects.select_related("raw_material", "finished_good"),
        )
    )
    return render(request, "procurement/procurement_list.html", {
        "orders": orders,
        "raw_material_categories": raw_material_categories,
    })


@login_required
def po_form(request, pk=None):
    obj = get_object_or_404(PurchaseOrder, pk=pk) if pk else None
    if obj and obj.status == "received":
        messages.error(request, "This purchase order has already been received and can't be edited.")
        return redirect("procurement_list")
    if request.method == "POST":
        form = PurchaseOrderForm(request.POST, instance=obj)
        formset = PurchaseOrderItemFormSet(
            request.POST,
            instance=obj if obj else PurchaseOrder(),
            form_kwargs={"business": request.business},
        )
        if form.is_valid() and formset.is_valid():
            active_items = [
                f.cleaned_data for f in formset.forms
                if f.cleaned_data and not f.cleaned_data.get("DELETE")
            ]
            if not active_items:
                form.add_error(None, "Add at least one item.")
            else:
                po_total = sum(
                    (data["qty"] * data["unit_cost"] for data in active_items),
                    Decimal("0"),
                )
                amount_paid = form.cleaned_data.get("amount_paid") or Decimal("0")
                payment_status = form.cleaned_data.get("payment_status")

                if payment_status == "partial" and amount_paid >= po_total:
                    # A payment covering the whole PO is no longer partial.
                    form.cleaned_data["payment_status"] = "paid"
                    payment_status = "paid"
                if amount_paid > po_total:
                    form.add_error("amount_paid", "The amount paid cannot exceed the purchase order total.")
                elif payment_status == "paid" and amount_paid and amount_paid != po_total:
                    form.add_error("amount_paid", "For a Paid order, the amount paid must equal the full purchase order total.")
                else:
                    with transaction.atomic():
                        po = form.save(commit=False)
                        po.business = request.business
                        if obj is None:
                            po.created_by = request.user
                        po.save()
                        formset.instance = po
                        formset.save()

                        # Paid orders keep the existing immediate-payment behavior.
                        # Partially paid orders create the initial SupplierPayment here,
                        # so Finance can show only the remaining payable balance and
                        # later payments can settle it normally.
                        if payment_status == "paid":
                            _ensure_po_payment_recorded(po, request.user)
                        elif payment_status == "partial" and obj is None:
                            payment = SupplierPayment.objects.create(
                                business=po.business,
                                created_by=request.user,
                                date=po.date,
                                supplier=po.supplier or "Unnamed supplier",
                                amount=amount_paid,
                                payment_method=po.payment_method,
                                reference=f"PO-{po.pk}-INITIAL",
                                notes=f"Initial payment for purchase order #{po.pk}",
                                purchase_order=po,
                                account=po.account,
                            )
                            record_cash(
                                po.business,
                                request.user,
                                date=payment.date,
                                amount=payment.amount,
                                transaction_type=FinancialTransaction.OUTFLOW,
                                category="Supplier payment",
                                description=f"Initial payment for purchase order #{po.pk}",
                                payment_method=payment.payment_method,
                                reference=payment.reference,
                                account=payment.account,
                            )

                        messages.success(request, "Purchase order saved.")
                        return redirect("procurement_list")
    else:
        form = PurchaseOrderForm(instance=obj, initial=None if obj else {"date": today()})
        formset = PurchaseOrderItemFormSet(instance=obj, form_kwargs={"business": request.business})
    current_costs = {
        **{f"raw:{m.pk}": f"{m.cost_per_purchase_unit:.2f}" for m in RawMaterial.objects.all()},
        **{f"finished:{g.pk}": f"{g.est_cost:.2f}" for g in FinishedGood.objects.filter(
            stock__isnull=False,
        ).distinct()},
    }
    return render(request, "procurement/po_form.html", {"form": form, "formset": formset, "obj": obj, "current_costs": current_costs})


@login_required
def po_receive(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method == "POST":
        with transaction.atomic():
            # Prevent two workers from receiving the same PO and duplicating
            # its stock/finance effects.
            po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
            if po.status == "received":
                return redirect("procurement_list")
            items = list(po.items.select_related("raw_material", "finished_good"))
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
                    # item.qty / item.unit_cost are in the material's purchase
                    # unit (e.g. bags); stock and cost_per_unit are always in
                    # its usage unit (e.g. kg) — convert on the way in.
                    usage_cost = (item.unit_cost / factor).quantize(Decimal("0.000001"))
                    record_raw_material_movement(
                        mat,
                        item.qty * factor,
                        StockMovement.RAW_PURCHASE,
                        note="Purchase order received", reference=f"PO-{po.pk}", unit_value=usage_cost,
                    )
                    RawMaterialCostSnapshot.objects.create(
                        business=po.business,
                        raw_material=mat,
                        purchase_order_item=item,
                        effective_date=po.received_date or today(),
                        purchase_unit_cost=item.unit_cost,
                        usage_unit_cost=usage_cost,
                        supplier=po.supplier or "",
                    )
                    # This remains the material's current/latest procurement cost.
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
                    )
            po.status = "received"
            po.received_date = today()
            po.save()
            if po.payment_status == "paid":
                _ensure_po_payment_recorded(po, request.user)
            audit(po.business, request.user, "receive", po, f"Purchase order #{po.pk} received", {"payment_status": po.payment_status})
        messages.success(request, "Stock received into inventory.")
    return redirect("procurement_list")


@login_required
def po_delete(request, pk):
    obj = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method == "POST":
        obj.delete()
        messages.success(request, "Removed.")
    return redirect("procurement_list")

@login_required
def po_invoice(request, pk):
    po = get_object_or_404(
        PurchaseOrder.objects.prefetch_related("items__raw_material", "items__finished_good"),
        pk=pk,
    )
    return purchase_order_pdf(po)
