import csv
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
from django.db.models import Case, Count, DecimalField, ExpressionWrapper, F, OuterRef, Q, Subquery, Sum, Value, When, Window
from openpyxl import Workbook
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

def today(): return timezone.localdate()


def _finance_open_items(business):
    money_field = DecimalField(max_digits=24, decimal_places=6)
    sale_line_value = Case(
        When(
            commercial_quantity__isnull=False,
            commercial_unit_price__isnull=False,
            then=ExpressionWrapper(
                F("commercial_quantity") * F("commercial_unit_price"),
                output_field=money_field,
            ),
        ),
        default=ExpressionWrapper(
            (F("batch_qty") * F("finished_good__units_per_batch") + F("piece_qty"))
            * (F("price") - F("discount")),
            output_field=money_field,
        ),
        output_field=money_field,
    )
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
            "id", "source", "customer", "date", "transaction_type"
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
                if len(outstanding_sales) < 30:
                    outstanding_sales.append({"sale": sale, "balance": balance})
            elif is_walkin:
                unpaid_invoice_count += 1
                if len(unpaid_invoice_sales) < 10:
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
            "id", "supplier", "date", "received_date", "payment_status", "status"
        ).annotate(
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
            if len(outstanding_pos) < 30:
                outstanding_pos.append({"po": po, "balance": balance})
        else:
            settlement_po_count += 1
            if len(settlement_po_ids) < 8:
                settlement_po_ids.append(po.pk)

    unpaid_expenses = list(
        Expense.raw_objects.filter(business=business, payment_status="unpaid")
        .only("id", "date", "description", "amount")
        .annotate(
            _all_unpaid_total=Window(expression=Sum("amount")),
            _all_unpaid_count=Window(expression=Count("pk")),
        )
        .order_by("-date", "-id")[:30]
    )
    expense_payables = (unpaid_expenses[0]._all_unpaid_total or Decimal("0")) if unpaid_expenses else Decimal("0")
    expense_payable_count = int(unpaid_expenses[0]._all_unpaid_count or 0) if unpaid_expenses else 0
    outstanding_expenses = [
        {"expense": expense, "balance": expense.amount}
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
    account_qs = CashAccount.raw_objects.filter(business=business)
    if active_only:
        account_qs = account_qs.filter(active=True)
    accounts = list(
        account_qs.annotate(
            _money_in=Sum(
                "transactions__amount",
                filter=Q(transactions__transaction_type=FinancialTransaction.INCOME),
            ),
            _money_out=Sum(
                "transactions__amount",
                filter=Q(transactions__transaction_type=FinancialTransaction.OUTFLOW),
            ),
        )
    )
    for account in accounts:
        account._calculated_balance = (
            account.opening_balance
            + (account._money_in or Decimal("0"))
            - (account._money_out or Decimal("0"))
        )
    return accounts


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
    accounts = _cash_account_balances(request.business)
    tx = _finance_transactions(request)[:100]
    open_items = _finance_open_items(request.business)
    return render(request, "core/finance.html", {
        "accounts": accounts,
        "transactions": tx,
        "audit_logs": _finance_audit_logs(request)[:80],
        "receivables": open_items["receivables"],
        "payables": open_items["payables"],
        "outstanding_sales": open_items["outstanding_sales"][:30],
        "outstanding_pos": open_items["outstanding_pos"][:30],
        "outstanding_expenses": open_items["outstanding_expenses"],
    })


@login_required
@require_GET
def finance_alert_feed(request):
    if not user_has_permission(request.user, request.business, "finance", "view"):
        return JsonResponse({"detail": "Finance alert access is unavailable."}, status=403)

    cache_key = finance_alert_cache_key(request.business.pk)
    payload = cache.get(cache_key)
    if payload is not None:
        return JsonResponse(payload)

    items = _finance_open_items(request.business)
    currency = request.business.currency_symbol
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
    account_balances = _cash_account_balances(request.business, active_only=True)
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
    return JsonResponse(payload)


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
    form=SupplierPaymentForm(request.POST or None,initial={"date":today()})
    if request.method=="POST" and form.is_valid():
        with transaction.atomic():
            x=form.save(commit=False); x.business=request.business; x.created_by=request.user; x.save()
            if x.purchase_order:
                po=x.purchase_order
                paid_before=sum((p.amount for p in po.payments.exclude(pk=x.pk)), Decimal("0"))
                outstanding=max(Decimal("0"), po.total-paid_before)
                new_paid=paid_before+x.amount
                po.payment_status="paid" if new_paid >= po.total else "partial"
                if new_paid >= po.total: po.payment_status="paid"
                po.save(update_fields=["payment_status","updated_at"])
                supplier=po.supplier or x.supplier
            else:
                supplier=x.supplier
            record_cash(request.business,request.user,date=x.date,amount=x.amount,transaction_type=FinancialTransaction.OUTFLOW,category="Supplier payment",description=f"Payment to {supplier}",payment_method=x.payment_method,reference=x.reference or f"SUPPAY-{x.pk}",account=x.account)
            audit(request.business,request.user,"create",x,"Supplier payment recorded",{"amount":str(x.amount),"purchase_order":getattr(x.purchase_order,"pk",None)})
        messages.success(request,"Supplier payment recorded and payable updated."); return redirect("finance_dashboard")
    return render(request,"core/payment_form.html",{"form":form,"supplier_payload":form.supplier_payload,"title":"Supplier Payment","help_text":"Pay an outstanding received purchase order. Stock was already received when the PO was received; this payment only settles the payable."})
@login_required
def customer_payment_form(request):
    form=CustomerPaymentForm(request.POST or None,initial={"date":today()})
    if request.method=="POST" and form.is_valid():
        with transaction.atomic():
            x=form.save(commit=False); x.business=request.business; x.created_by=request.user; x.save()
            sale=x.sale
            paid_before=sum((p.amount for p in sale.payments.exclude(pk=x.pk)), Decimal("0"))
            new_paid=paid_before+x.amount
            sale.transaction_type="paid" if new_paid >= sale.total else "partial"
            sale.save(update_fields=["transaction_type","updated_at"])
            if sale.linked_order and sale.source in ("distribution_order", "online_order"):
                # Customer orders have their own payment-status field. Keep the
                # physical-store transaction_type untouched; only a fully settled
                # receivable becomes Received on the originating order.
                linked_order = sale.linked_order
                if new_paid >= sale.total:
                    linked_order.customer_payment_status = "paid"
                    linked_order.save(update_fields=["customer_payment_status","updated_at"])
            record_cash(request.business,request.user,date=x.date,amount=x.amount,transaction_type=FinancialTransaction.INCOME,category="Customer payment",description=f"Payment from {sale.customer} — Sale #{sale.pk}",payment_method=x.payment_method,reference=x.reference or f"CUSTPAY-{x.pk}",account=x.account)
            audit(request.business,request.user,"create",x,"Customer payment recorded",{"amount":str(x.amount),"sale":sale.pk,"customer":sale.customer})
        messages.success(request,"Customer payment recorded and receivable updated."); return redirect("finance_dashboard")
    return render(request,"core/payment_form.html",{"form":form,"sales_payload":form.sales_payload,"title":"Customer Payment","help_text":"Only Distribution and Online customer sales appear here. Select the customer and then the specific sale being settled."})
@login_required
def stock_adjustment_form(request):
    form=StockAdjustmentForm(request.POST or None,initial={"date":today()})
    if request.method=="POST" and form.is_valid():
        with transaction.atomic():
            x=form.save(commit=False); x.business=request.business; x.created_by=request.user
            if x.raw_material:
                x.unit_value=x.raw_material.cost_per_unit
                if x.quantity + x.raw_material.stock < 0:
                    form.add_error("quantity","Adjustment would make stock negative."); return render(request,"core/adjustment_form.html",{"form":form})
            else:
                if x.finished_good.stock is None:
                    form.add_error("finished_good","This product is not configured for physical-store stock."); return render(request,"core/adjustment_form.html",{"form":form})
                if x.quantity + x.finished_good.stock < 0:
                    form.add_error("quantity","Adjustment would make stock negative."); return render(request,"core/adjustment_form.html",{"form":form})
                latest=x.finished_good.adjustments.order_by("-date","-id").first()
                x.unit_value=latest.unit_value if latest else x.finished_good.est_cost
            x.save()
            ref=f"ADJ-{x.pk}"
            if x.raw_material:
                record_raw_material_movement(x.raw_material,x.quantity,StockMovement.ADJUSTMENT,note=x.description,reference=ref,unit_value=x.unit_value,location=x.location)
            else:
                record_finished_good_movement(x.finished_good,x.quantity,StockMovement.ADJUSTMENT,note=x.description,reference=ref,unit_value=x.unit_value,location=x.location)
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

