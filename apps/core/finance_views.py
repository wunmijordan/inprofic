import csv
from datetime import date
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.db.models.functions import Coalesce
from django.db.models import Count, DecimalField, ExpressionWrapper, F, OuterRef, Q, Subquery, Sum, Value, Window
from openpyxl import Workbook
from . import ledger
from .models import CashAccount, FinancialTransaction, AuditLog
from accounts.platform_integrations import redact_disabled_integrations
from accounts.services import user_has_permission
from .finance_forms import CashAccountForm, SupplierPaymentForm, CustomerPaymentForm, StockAdjustmentForm
from .services import record_cash, audit
from procurement.models import PurchaseOrder, PurchaseOrderItem, SupplierPayment
from sales.models import CustomerPayment, Sale, SaleItem
from inventory.models import StockAdjustment, StockMovement
from expenses.models import Expense, ExpensePayment
from inventory.services import record_raw_material_movement, record_finished_good_movement
from .finance_cache import FINANCE_ALERT_CACHE_TIMEOUT, finance_alert_cache_key
from .performance import performance_section
from .accounting import (
    _sale_expressions, compare_periods, daily_components, fiscal_period, margin_trend, period_buckets, period_options,
    product_profitability, shift_period, summarise,
)

def today(): return timezone.localdate()


def _finance_open_items(business, *, detail_limit=30):
    """Current settlement totals; None includes every detail row for full reports."""
    # Shared with Books, tax, reports and the Daily Brief so a receivable never
    # drifts from the sale line's frozen batch yield or commercial snapshot.
    money_field, sale_line_value, _cogs, _units = _sale_expressions(prefix="")
    sale_total_sq = (
        SaleItem.objects.filter(sale_id=OuterRef("pk"))
        .values("sale_id")
        .annotate(total=Sum(sale_line_value))
        .values("total")[:1]
    )
    sale_paid_sq = (
        CustomerPayment.raw_objects.filter(sale_id=OuterRef("pk"))
        .values("sale_id")
        .annotate(total=Sum("amount"))
        .values("total")[:1]
    )
    open_sales = list(
        Sale.raw_objects.filter(
            business=business,
            source__in=("distribution_order", "online_order", "walkin"),
            transaction_type__in=("unpaid", "partial"),
        ).only(
            "id", "source", "customer", "customer_master_id", "date", "transaction_type"
        ).annotate(
            _invoice_total=Coalesce(Subquery(sale_total_sq, output_field=money_field), Value(Decimal("0"), output_field=money_field)),
            _paid_total=Coalesce(Subquery(sale_paid_sq, output_field=money_field), Value(Decimal("0"), output_field=money_field)),
        )
    )
    outstanding_sales = []
    unpaid_invoice_sales = []
    receivables = Decimal("0")
    settlement_sale_ids = []
    outstanding_sales_count = 0
    unpaid_invoice_count = 0
    settlement_sale_count = 0
    for sale in open_sales:
        balance = max(Decimal("0"), Decimal(sale._invoice_total or 0) - Decimal(sale._paid_total or 0))
        is_customer_order = sale.source in {"distribution_order", "online_order"}
        is_walkin = sale.source == "walkin"
        if balance:
            if is_customer_order:
                receivables += balance
                outstanding_sales_count += 1
                if detail_limit is None or len(outstanding_sales) < detail_limit:
                    outstanding_sales.append({"sale": sale, "balance": balance})
            elif is_walkin:
                unpaid_invoice_count += 1
                if detail_limit is None or len(unpaid_invoice_sales) < min(10, detail_limit):
                    unpaid_invoice_sales.append({"sale": sale, "balance": balance})
        elif is_customer_order or is_walkin:
            settlement_sale_count += 1
            if len(settlement_sale_ids) < 8:
                settlement_sale_ids.append(sale.pk)

    po_money_field = DecimalField(max_digits=24, decimal_places=6)
    po_total_sq = (
        PurchaseOrderItem.objects.filter(purchase_order_id=OuterRef("pk"))
        .values("purchase_order_id")
        .annotate(
            total=Sum(
                ExpressionWrapper(F("qty") * F("unit_cost"), output_field=po_money_field)
            )
        )
        .values("total")[:1]
    )
    po_paid_sq = (
        SupplierPayment.raw_objects.filter(purchase_order_id=OuterRef("pk"))
        .values("purchase_order_id")
        .annotate(total=Sum("amount"))
        .values("total")[:1]
    )
    open_pos = list(
        PurchaseOrder.raw_objects.filter(
            business=business, payment_status__in=("unpaid", "partial"), status="received"
        ).only(
            "id", "supplier", "supplier_master__name", "date", "received_date", "payment_status", "status"
        ).select_related('supplier_master').annotate(
            _invoice_total=Coalesce(Subquery(po_total_sq, output_field=po_money_field), Value(Decimal("0"), output_field=po_money_field)),
            _paid_total=Coalesce(Subquery(po_paid_sq, output_field=po_money_field), Value(Decimal("0"), output_field=po_money_field)),
        )
    )
    outstanding_pos = []
    purchase_payables = Decimal("0")
    settlement_po_ids = []
    outstanding_pos_count = 0
    settlement_po_count = 0
    for po in open_pos:
        balance = max(Decimal("0"), Decimal(po._invoice_total or 0) - Decimal(po._paid_total or 0))
        if balance:
            purchase_payables += balance
            outstanding_pos_count += 1
            if detail_limit is None or len(outstanding_pos) < detail_limit:
                outstanding_pos.append({"po": po, "balance": balance})
        else:
            settlement_po_count += 1
            if len(settlement_po_ids) < 8:
                settlement_po_ids.append(po.pk)

    from expenses.services import with_outstanding
    unpaid_expenses = list(
        with_outstanding(Expense.raw_objects.filter(business=business, payment_status="unpaid"))
        .filter(_remaining_balance__gt=0)
        .only("id", "date", "description", "amount", "vendor")
        .annotate(
            _all_unpaid_total=Window(expression=Sum("_remaining_balance")),
            _all_unpaid_count=Window(expression=Count("pk")),
        )
        .order_by("-date", "-id")[:detail_limit]
    )
    expense_payables = (unpaid_expenses[0]._all_unpaid_total or Decimal("0")) if unpaid_expenses else Decimal("0")
    expense_payable_count = int(unpaid_expenses[0]._all_unpaid_count or 0) if unpaid_expenses else 0
    outstanding_expenses = [
        {"expense": expense, "balance": expense._remaining_balance}
        for expense in unpaid_expenses
    ]
    return {
        "outstanding_sales": outstanding_sales,
        "receivables": receivables,
        "unpaid_invoice_sales": unpaid_invoice_sales,
        "outstanding_pos": outstanding_pos,
        "purchase_payables": purchase_payables,
        "outstanding_expenses": outstanding_expenses,
        "expense_payables": expense_payables,
        "expense_payable_count": expense_payable_count,
        "payables": purchase_payables + expense_payables,
        "settlement_sale_ids": settlement_sale_ids,
        "settlement_po_ids": settlement_po_ids,
        "outstanding_sales_count": outstanding_sales_count,
        "unpaid_invoice_count": unpaid_invoice_count,
        "outstanding_pos_count": outstanding_pos_count,
        "settlement_sale_count": settlement_sale_count,
        "settlement_po_count": settlement_po_count,
    }


def _cash_account_balances(business, *, active_only=False):
    account_qs = CashAccount.objects.filter(business=business)
    if active_only:
        account_qs = account_qs.filter(active=True)
    return ledger.apply_cash_totals(account_qs)


def _finance_transactions(request):
    return FinancialTransaction.objects.filter(business=request.business).select_related("account", "created_by")


def _finance_audit_logs(request):
    return AuditLog.objects.filter(business=request.business).select_related("created_by")


def _csv_export(filename, header, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    # BOM keeps UTF-8 text (including the Naira sign) friendly in Excel.
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


def _xlsx_export(filename, sheet_name, header, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]
    ws.append(header)
    for row in rows:
        ws.append(list(row))
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for column in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in column)
        ws.column_dimensions[column[0].column_letter].width = min(max(max_len + 2, 10), 42)
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


def _money_movement_rows(request):
    rows = []
    for item in _finance_transactions(request)[:100]:
        rows.append([
            item.date.isoformat(),
            "Money in" if item.transaction_type == FinancialTransaction.INCOME else "Money out",
            redact_disabled_integrations(item.description),
            item.payment_method or "",
            item.amount,
            item.account.name if item.account else "",
        ])
    return rows


def _audit_trail_rows(request):
    rows = []
    for item in _finance_audit_logs(request)[:80]:
        created_at = timezone.localtime(item.created_at) if timezone.is_aware(item.created_at) else item.created_at
        rows.append([
            created_at.strftime("%Y-%m-%d %H:%M"),
            item.action,
            f"{item.model_name} #{item.object_id}",
            redact_disabled_integrations(item.description),
            item.created_by.username if item.created_by else "",
        ])
    return rows

@login_required
def finance_dashboard(request):
    with performance_section(request, "finance.accounts"):
        accounts = _cash_account_balances(request.business)
    with performance_section(request, "finance.transactions"):
        tx = list(_finance_transactions(request)[:100])
    with performance_section(request, "finance.open_items"):
        open_items = _finance_open_items(request.business)
    with performance_section(request, "finance.audit"):
        audit_logs = list(_finance_audit_logs(request)[:80])
    context = {
        "accounts": accounts,
        "transactions": tx,
        "audit_logs": audit_logs,
        "receivables": open_items["receivables"],
        "payables": open_items["payables"],
        "outstanding_sales": open_items["outstanding_sales"][:30],
        "outstanding_pos": open_items["outstanding_pos"][:30],
        "outstanding_expenses": open_items["outstanding_expenses"],
    }
    with performance_section(request, "finance.render"):
        return render(request, "core/finance.html", context)


@login_required
@require_GET
def finance_alert_feed(request):
    if not user_has_permission(request.user, request.business, "finance", "view"):
        return JsonResponse({"detail": "Finance alert access is unavailable."}, status=403)

    return JsonResponse(finance_alert_payload(request.business))


def finance_alert_payload(business):
    """Shared, tenant-scoped Finance alert data for the tray and device push."""
    cache_key = finance_alert_cache_key(business.pk)
    payload = cache.get(cache_key)
    if payload is not None:
        return payload

    items = _finance_open_items(business)
    currency = business.currency_symbol
    alerts = []
    target_url = reverse("finance_dashboard")

    def money(value):
        return f"{currency}{value:,.2f}"

    for row in items["unpaid_invoice_sales"][:10]:
        sale = row["sale"]
        alerts.append({
            "id": f"invoice-{sale.pk}",
            "type": "invoice",
            "title": f"Unpaid invoice / sale #{sale.pk}",
            "message": f"{sale.customer or 'Customer'} · {money(row['balance'])} still unpaid · {sale.date:%d %b %Y}",
            "target_url": target_url,
            "sort_date": sale.date.isoformat(),
        })
    for row in items["outstanding_sales"][:12]:
        sale = row["sale"]
        alerts.append({
            "id": f"sale-{sale.pk}",
            "type": "receivable",
            "title": f"Invoice / sale #{sale.pk} has an outstanding balance",
            "message": f"{sale.customer or 'Customer'} · {money(row['balance'])} receivable · {sale.date:%d %b %Y}",
            "target_url": target_url,
            "sort_date": sale.date.isoformat(),
        })
    for row in items["outstanding_pos"][:12]:
        po = row["po"]
        alerts.append({
            "id": f"po-{po.pk}",
            "type": "payable",
            "title": f"Supplier payable on PO #{po.pk}",
            "message": f"{po.supplier or 'Unnamed supplier'} · {money(row['balance'])} pending",
            "target_url": target_url,
            "sort_date": (po.received_date or po.date).isoformat(),
        })
    for row in items["outstanding_expenses"][:10]:
        expense = row["expense"]
        alerts.append({
            "id": f"expense-{expense.pk}",
            "type": "payable",
            "title": "Unpaid expense",
            "message": f"{expense.description} · {money(row['balance'])} · {expense.date:%d %b %Y}",
            "target_url": target_url,
            "sort_date": expense.date.isoformat(),
        })
    for sale_id in items["settlement_sale_ids"][:8]:
        alerts.append({
            "id": f"balance-sale-{sale_id}",
            "type": "balancing",
            "title": f"Sale #{sale_id} settlement status needs balancing",
            "message": "Payments cover the recorded total, but the sale is still marked unpaid or partially paid.",
            "target_url": target_url,
            "sort_date": today().isoformat(),
        })
    for po_id in items["settlement_po_ids"][:8]:
        alerts.append({
            "id": f"balance-po-{po_id}",
            "type": "balancing",
            "title": f"PO #{po_id} settlement status needs balancing",
            "message": "Recorded supplier payments cover the purchase total, but the PO is still marked unpaid or partially paid.",
            "target_url": target_url,
            "sort_date": today().isoformat(),
        })
    account_balances = _cash_account_balances(business, active_only=True)
    for account in account_balances:
        if account.balance < 0:
            alerts.append({
                "id": f"account-{account.pk}",
                "type": "balancing",
                "title": f"{account.name} is below zero",
                "message": f"Current ledger balance is {money(account.balance)}. Review entries and opening balance.",
                "target_url": target_url,
                "sort_date": today().isoformat(),
            })

    alerts.sort(key=lambda item: item["sort_date"], reverse=True)
    balancing_count = (
        items["settlement_sale_count"]
        + items["settlement_po_count"]
        + sum(1 for account in account_balances if account.balance < 0)
    )
    finance_count = (
        items["unpaid_invoice_count"]
        + items["outstanding_sales_count"]
        + items["outstanding_pos_count"]
        + items["expense_payable_count"]
        + balancing_count
    )
    payload = {
        "enabled": True,
        "count": finance_count,
        "invoice_count": items["unpaid_invoice_count"],
        "receivable_count": items["outstanding_sales_count"],
        "payable_count": items["outstanding_pos_count"] + items["expense_payable_count"],
        "balancing_count": balancing_count,
        "receivables_total": str(items["receivables"]),
        "payables_total": str(items["payables"]),
        "alerts": alerts[:30],
        "poll_seconds": 45,
    }
    cache.set(cache_key, payload, timeout=FINANCE_ALERT_CACHE_TIMEOUT)
    return payload


@login_required
def cash_account_form(request,pk=None):
    obj=get_object_or_404(CashAccount,pk=pk) if pk else None
    form=CashAccountForm(request.POST or None,instance=obj)
    if request.method=="POST" and form.is_valid():
        x=form.save(commit=False); x.business=request.business
        if not obj: x.created_by=request.user
        x.save(); audit(request.business,request.user,"create" if not obj else "update",x,"Cash account saved"); messages.success(request,"Cash account saved."); return redirect("finance_dashboard")
    return render(request,"core/cash_account_form.html",{"form":form,"obj":obj})
@login_required
def cash_account_delete(request, pk):
    account = get_object_or_404(CashAccount, pk=pk)
    if request.method != "POST":
        return redirect("finance_dashboard")
    try:
        name = account.name
        account_id = account.pk
        account.delete()
        audit(request.business, request.user, "delete", None, f"Cash account deleted: {name}", {"account_id": account_id, "account_name": name})
        messages.success(request, f"{name} deleted.")
    except ProtectedError:
        messages.error(request, "This account has linked financial records and cannot be deleted. Edit it and make it unavailable for new transactions instead.")
    return redirect("finance_dashboard")


@login_required
def supplier_payment_form(request):
    form = SupplierPaymentForm(request.POST or None, business=request.business, initial={'date': ledger.default_entry_date()})
    if request.method == 'GET' and request.GET.get('purchase_order', '').isdigit():
        selected = form.fields['purchase_order'].queryset.filter(pk=request.GET['purchase_order']).first()
        if selected:
            form.initial.update(purchase_order=selected.pk, supplier=selected.supplier)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            payment = form.save(commit=False)
            payment.business, payment.created_by = request.business, request.user
            po = payment.purchase_order
            if po:
                po = PurchaseOrder.raw_objects.select_for_update().get(pk=po.pk, business=request.business)
                paid = SupplierPayment.raw_objects.filter(business=request.business, purchase_order=po).aggregate(v=Sum('amount'))['v'] or Decimal('0')
                outstanding = Decimal('0') if po.payment_status == 'paid' else max(po.total - paid, Decimal('0'))
                if payment.amount > outstanding:
                    form.add_error('amount', 'Payment exceeds the current vendor balance. Reload to review recent payments.')
            if not form.errors:
                supplier = po.supplier if po else payment.supplier
                from procurement.services import resolve_supplier
                payment.supplier = supplier
                payment.supplier_master = po.supplier_master if po else resolve_supplier(request.business, supplier, created_by=request.user)
                payment.save()
                if po:
                    po.payment_status = 'paid' if paid + payment.amount >= po.total else 'partial'
                    po.save(update_fields=['payment_status','updated_at'])
                record_cash(request.business, request.user, date=payment.date, amount=payment.amount,
                            transaction_type=FinancialTransaction.OUTFLOW, category='Supplier payment',
                            description=f'Payment to {supplier}', payment_method=payment.payment_method,
                            reference=payment.reference or f'SUPPAY-{payment.pk}', account=payment.account)
                audit(request.business, request.user, 'create', payment, 'Supplier payment recorded',
                      {'amount': str(payment.amount), 'purchase_order': po.pk if po else None})
                messages.success(request, 'Vendor payment recorded and balance updated.')
                return redirect('finance_dashboard')
    return render(request, 'core/payment_form.html', {'form':form, 'supplier_payload':form.supplier_payload,
        'title':'Supplier Payment', 'help_text':'Select one vendor purchase order. Pay an advance or settle received goods; this records money only and does not receive stock.'})
@login_required
def customer_payment_form(request):
    from .finance_forms import CustomerPaymentPartFormSet
    split_mode = request.method != 'POST' or 'payments-TOTAL_FORMS' in request.POST
    form = CustomerPaymentForm(request.POST or None, business=request.business, split_mode=split_mode,
                               initial={'date': ledger.default_entry_date()})
    parts = CustomerPaymentPartFormSet(
        request.POST if request.method == 'POST' and split_mode else None, prefix='payments',
        initial=[{'payment_method': method, 'amount': '0'} for method, _ in Sale.PAYMENT_CHOICES],
        form_kwargs={'business': request.business}) if split_mode else None
    if request.method == 'GET' and request.GET.get('sale', '').isdigit():
        selected = form.fields['sale'].queryset.filter(pk=request.GET['sale']).first()
        if selected:
            form.initial.update(sale=selected.pk, customer=selected.customer)
    if request.method == 'POST':
        valid = form.is_valid()
        if parts is not None:
            valid = parts.is_valid() and valid
        if valid:
            entries = ([p.cleaned_data for p in parts.forms if p.cleaned_data.get('amount')]
                       if parts is not None else [form.cleaned_data])
            amount = sum((p['amount'] for p in entries), Decimal('0'))
            with transaction.atomic():
                sale = Sale.raw_objects.select_for_update().get(pk=form.cleaned_data['sale'].pk, business=request.business)
                paid_before = CustomerPayment.raw_objects.filter(business=request.business, sale=sale).aggregate(v=Sum('amount'))['v'] or Decimal('0')
                outstanding = Decimal('0') if sale.transaction_type == 'paid' else max(sale.net_total - paid_before, Decimal('0'))
                if amount > outstanding:
                    form.add_error(None, 'The combined payment exceeds the outstanding sale balance. Reload to review recent payments.')
                else:
                    for entry in entries:
                        payment = CustomerPayment.objects.create(
                            business=request.business, created_by=request.user, date=form.cleaned_data['date'],
                            customer=sale.customer, customer_master=sale.customer_master, sale=sale,
                            amount=entry['amount'], payment_method=entry['payment_method'], account=entry['account'],
                            reference=entry.get('reference') or form.cleaned_data.get('reference', ''),
                            notes=form.cleaned_data.get('notes', ''))
                        record_cash(request.business, request.user, date=payment.date, amount=payment.amount,
                                    transaction_type=FinancialTransaction.INCOME, category='Customer payment',
                                    description=f'Payment from {sale.customer} — Sale #{sale.pk}',
                                    payment_method=payment.payment_method, reference=payment.reference or f'CUSTPAY-{payment.pk}', account=payment.account)
                        audit(request.business, request.user, 'create', payment, 'Customer payment recorded',
                              {'amount': str(payment.amount), 'sale': sale.pk, 'payment_method': payment.payment_method})
                    new_paid = paid_before + amount
                    sale.transaction_type = 'paid' if new_paid >= sale.net_total else 'partial'
                    sale.save(update_fields=['transaction_type', 'updated_at'])
                    if sale.linked_order and new_paid >= sale.net_total:
                        linked = sale.linked_order
                        linked.customer_payment_status = 'paid'
                        linked.save(update_fields=['customer_payment_status', 'updated_at'])
                    messages.success(request, 'Customer payment recorded across the selected methods and accounts.')
                    return redirect('finance_dashboard')
    return render(request, 'core/payment_form.html', {
        'form': form, 'payment_parts': parts, 'sales_payload': form.sales_payload, 'title': 'Customer Payment',
        'help_text': 'Choose a customer and invoice. Enter what arrived by Cash, Card and Transfer; each amount posts to its own receiving account.'})
@login_required
def stock_adjustment_form(request):
    form=StockAdjustmentForm(request.POST or None,initial={"date":ledger.default_entry_date()})
    if request.method=="POST" and form.is_valid():
        with transaction.atomic():
            x=form.save(commit=False); x.business=request.business; x.created_by=request.user
            if x.raw_material:
                from inventory.models import RawMaterial
                x.raw_material = RawMaterial.objects.select_for_update().get(pk=x.raw_material_id, business=request.business)
                x.unit_value=x.raw_material.cost_per_unit
                if x.quantity + x.raw_material.stock < 0:
                    form.add_error("quantity","Adjustment would make stock negative."); return render(request,"core/adjustment_form.html",{"form":form})
            else:
                from inventory.models import FinishedGood
                x.finished_good = FinishedGood.objects.select_for_update().get(pk=x.finished_good_id, business=request.business)
                if x.finished_good.stock is None:
                    form.add_error("finished_good","This product is not in physical-store stock."); return render(request,"core/adjustment_form.html",{"form":form})
                if x.quantity + x.finished_good.stock < 0:
                    form.add_error("quantity","Adjustment would make stock negative."); return render(request,"core/adjustment_form.html",{"form":form})
                latest=x.finished_good.adjustments.order_by("-date","-id").first()
                x.unit_value=latest.unit_value if latest else x.finished_good.est_cost
            x.save()
            ref=f"ADJ-{x.pk}"
            if x.raw_material:
                record_raw_material_movement(x.raw_material,x.quantity,StockMovement.ADJUSTMENT,note=x.description,reference=ref,unit_value=x.unit_value,location=x.location,on_date=x.date)
            else:
                record_finished_good_movement(x.finished_good,x.quantity,StockMovement.ADJUSTMENT,note=x.description,reference=ref,unit_value=x.unit_value,location=x.location,on_date=x.date)
            audit(request.business,request.user,"create",x,"Stock adjustment recorded",{"quantity":str(x.quantity),"reason":x.reason})
        messages.success(request,"Stock adjustment recorded."); return redirect("finance_dashboard")
    return render(request,"core/adjustment_form.html",{"form":form})

@login_required
def export_money_movements_csv(request):
    return _csv_export(
        "finance-money-movements.csv",
        ["Date", "Type", "Description", "Method", "Amount", "Account"],
        _money_movement_rows(request),
    )


@login_required
def export_money_movements_xlsx(request):
    return _xlsx_export(
        "finance-money-movements.xlsx",
        "Money Movements",
        ["Date", "Type", "Description", "Method", "Amount", "Account"],
        _money_movement_rows(request),
    )


@login_required
def export_audit_trail_csv(request):
    return _csv_export(
        "finance-audit-trail.csv",
        ["When", "Action", "Record", "Description", "User"],
        _audit_trail_rows(request),
    )


@login_required
def export_audit_trail_xlsx(request):
    return _xlsx_export(
        "finance-audit-trail.xlsx",
        "Audit Trail",
        ["When", "Action", "Record", "Description", "User"],
        _audit_trail_rows(request),
    )



def _profit_loss_context(request):
    """Resolve the requested reporting period and build its statement.

    ``?start=YYYY-MM-DD`` selects any date inside the period to view; the
    period length and year start come from the business settings.
    """
    business = request.business
    try:
        anchor = timezone.datetime.strptime(request.GET.get("start", ""), "%Y-%m-%d").date()
    except ValueError:
        anchor = today()
    period = fiscal_period(business, anchor)
    previous = shift_period(business, period, -1)
    following = shift_period(business, period, 1)
    components = daily_components(business, previous["start"], period["end"])
    statement = summarise(components, period["start"], period["end"])
    prior = summarise(components, previous["start"], previous["end"])
    # Never chart days that have not happened yet.
    chart_end = min(period["end"], today()) if period["start"] <= today() else period["start"]
    buckets = period_buckets(components, period["start"], max(chart_end, period["start"]), weekly=period["months"] <= 3)
    biggest = max([abs(row["profit"]) for row in buckets] or [0]) or 1
    for row in buckets:
        row["bar"] = round(abs(row["profit"]) / biggest * 100, 1)
        row["abs_profit"] = abs(row["profit"])
    revenue = statement["revenue"]

    def share(amount):
        return float(round(amount / revenue * 100, 1)) if revenue else None

    lines = [
        {"label": "Revenue (paid sales)", "amount": statement["revenue"], "kind": "total"},
        {"label": "Cost of goods sold", "amount": -statement["cogs"], "kind": "cost"},
        {"label": "Gross profit", "amount": statement["gross_profit"], "kind": "subtotal", "margin": statement["gross_margin"]},
    ]
    lines += [{"label": line["label"], "amount": -line["amount"], "kind": "cost", "indent": True} for line in statement["expense_lines"]]
    lines += [
        {"label": "Payroll (employer cost)", "amount": -statement["payroll"], "kind": "cost"},
        {"label": "Operating supplies used", "amount": -statement["supplies"], "kind": "cost"},
        {"label": "Other stock write-offs & losses" if statement["distribution_damage"] else "Stock write-offs & losses",
         "amount": -(statement["write_offs"] - statement["distribution_damage"]), "kind": "cost"},
        *([{"label": "Damaged distribution goods cost", "amount": -statement["distribution_damage"], "kind": "cost"}]
          if statement["distribution_damage"] else []),
        {"label": "Net profit", "amount": statement["net_profit"], "kind": "net", "margin": statement["net_margin"]},
    ]
    for line in lines:
        line["abs"] = abs(line["amount"])
        line["share"] = share(line["abs"])
    change = statement["net_profit"] - prior["net_profit"]
    return {
        "period": period, "previous_period": previous, "next_period": following,
        "is_current": period["start"] <= today() <= period["end"],
        "can_go_next": following["start"] <= today(),
        "statement": statement, "prior": prior, "lines": lines, "buckets": buckets,
        "net_change": change,
        "business_year_start": date(2000, business.fiscal_year_start_month, 1).strftime("%B"),
    }


def _require_finance_view(request):
    from django.core.exceptions import PermissionDenied
    if not user_has_permission(request.user, request.business, "finance", "view"):
        raise PermissionDenied


@login_required
def finance_profit_loss(request):
    _require_finance_view(request)
    with performance_section(request, "finance.profit_loss"):
        context = _profit_loss_context(request)
    return render(request, "core/profit_loss.html", context)


@login_required
def export_profit_loss_csv(request):
    _require_finance_view(request)
    context = _profit_loss_context(request)
    period, statement = context["period"], context["statement"]
    rows = [[line["label"], f"{line['amount']:.2f}", "" if line["share"] is None else f"{line['share']}%"] for line in context["lines"]]
    rows += [[], ["Unpaid sales (receivables, not in revenue)", f"{statement['unpaid_sales']:.2f}", ""],
             ["Unpaid expenses (already in costs)", f"{statement['unpaid_expenses']:.2f}", ""]]
    return _csv_export(
        f"profit-and-loss-{period['start']:%Y-%m-%d}-to-{period['end']:%Y-%m-%d}.csv",
        [f"Profit and loss · {period['label']}", "Amount", "% of revenue"], rows,
    )


COMPARE_KINDS = [("month", "Month"), ("quarter", "Quarter"), ("year", "Year")]


def _parse_day(value, default):
    try:
        return timezone.datetime.strptime(value or "", "%Y-%m-%d").date()
    except ValueError:
        return default


@login_required
def finance_profit_compare(request):
    """Set any two periods (month, quarter or year) side by side."""
    _require_finance_view(request)
    business = request.business
    kind = request.GET.get("kind") if request.GET.get("kind") in dict(COMPARE_KINDS) else "month"
    current = fiscal_period(business, today(), kind=kind)
    previous = shift_period(business, current, -1)
    a_day = _parse_day(request.GET.get("a"), current["start"])
    b_day = _parse_day(request.GET.get("b"), previous["start"])
    with performance_section(request, "finance.profit_compare"):
        result = compare_periods(business, kind, a_day, b_day)
        trend = margin_trend(business, kind, today(), count={"month": 12, "quarter": 8, "year": 5}[kind])
    options = period_options(business, kind, today(), count={"month": 24, "quarter": 12, "year": 6}[kind])
    # A period picked from the URL that is older than the list still has to appear.
    for chosen in (result["a"], result["b"]):
        if all(option["start"] != chosen["start"] for option in options):
            options.append(chosen)
    best = max((row["net_margin"] for row in trend if row["net_margin"] is not None), default=None)
    worst = min((row["net_margin"] for row in trend if row["net_margin"] is not None), default=None)
    scale = max([abs(row["net_margin"]) for row in trend if row["net_margin"] is not None] or [1]) or 1
    for row in trend:
        row["bar"] = round(abs(row["net_margin"]) / scale * 100, 1) if row["net_margin"] is not None else 0
    return render(request, "core/profit_compare.html", {
        "kind": kind, "kinds": COMPARE_KINDS, "options": options, "result": result, "trend": trend,
        "best_margin": best, "worst_margin": worst,
        "swap_query": f"kind={kind}&a={result['b']['start']:%Y-%m-%d}&b={result['a']['start']:%Y-%m-%d}",
    })


PRODUCT_FILTERS = [
    ("all", "All products"), ("best", "Best sellers"), ("earner", "Top earners"), ("low", "Low margin"),
    ("loss", "Loss-making"), ("slow", "Slow movers"), ("none", "No sales"),
]
_FILTER_BADGE = {"best": "Best seller", "earner": "Top earner", "low": "Low margin", "loss": "Loss-making", "slow": "Slow mover", "none": "No sales"}


def _product_profit_context(request):
    business = request.business
    kind = request.GET.get("kind") if request.GET.get("kind") in dict(COMPARE_KINDS) else "month"
    period = fiscal_period(business, _parse_day(request.GET.get("start"), today()), kind=kind)
    result = product_profitability(business, period["start"], min(period["end"], max(today(), period["start"])))
    show = request.GET.get("show") if request.GET.get("show") in dict(PRODUCT_FILTERS) else "all"
    rows = result["rows"]
    if show != "all":
        rows = [row for row in rows if _FILTER_BADGE[show] in row["badges"]]
    sort = request.GET.get("sort", "profit")
    keys = {
        "profit": (lambda r: -r["profit"]), "units": (lambda r: -r["units"]), "revenue": (lambda r: -r["revenue"]),
        "margin": (lambda r: -(r["margin"] if r["margin"] is not None else -10**6)), "name": (lambda r: r["name"].lower()),
    }
    rows = sorted(rows, key=keys.get(sort, keys["profit"]))
    counts = {key: sum(1 for row in result["rows"] if label in row["badges"]) for key, label in _FILTER_BADGE.items()}
    return {
        "kind": kind, "kinds": COMPARE_KINDS, "period": period, "previous_period": shift_period(business, period, -1),
        "next_period": shift_period(business, period, 1), "can_go_next": shift_period(business, period, 1)["start"] <= today(),
        "result": result, "rows": rows, "show": show, "sort": sort, "filters": PRODUCT_FILTERS, "counts": counts,
    }


@login_required
def finance_product_profit(request):
    _require_finance_view(request)
    with performance_section(request, "finance.product_profit"):
        context = _product_profit_context(request)
    return render(request, "core/product_profit.html", context)


@login_required
def export_product_profit_csv(request):
    _require_finance_view(request)
    context = _product_profit_context(request)
    header = ["Product", "Units sold", "Revenue", "Cost of goods", "Gross profit", "Margin %", "Avg price", "Avg unit cost",
              "Raw material cost/unit (current)", "Production cost/unit (current)", "Purchase cost/unit (current)", "Flags"]
    rows = [[
        r["name"], r["units"], r["revenue"], r["cogs"], r["profit"], "" if r["margin"] is None else r["margin"],
        "" if r["avg_price"] is None else r["avg_price"], "" if r["avg_cost"] is None else r["avg_cost"],
        r["build"]["raw_material"], r["build"]["production"], r["build"]["purchase"], "; ".join(r["badges"]),
    ] for r in context["rows"]]
    period = context["period"]
    return _csv_export(f"product-profitability-{period['start']:%Y-%m-%d}-to-{period['end']:%Y-%m-%d}.csv", header, rows)
