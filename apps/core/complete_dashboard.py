"""Permission-filtered registry for the lazy Complete dashboard."""
from datetime import date

from django.apps import apps
from django.core.cache import cache
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from accounts.services import user_has_permission
from .accounting import daily_components, fiscal_period, period_buckets, profit_and_loss
from .verticals import vertical_config


GROUPS = [
    ("pulse", "Business Pulse", [("overview", "Recent Sales Activity", "dashboard", "sales.Sale", ("date", "total", "transaction_type"))]),
    ("sales", "Sales", [("sales", "Sales", "sales", "sales.Sale", ("date", "customer", "total", "transaction_type")), ("pos_activity", "Point of Sale", "pos", "sales.Sale", ("date", "customer", "total", "transaction_type"))]),
    ("production", "Production", [("orders", "Orders", "production", "production.Order", ("order_number", "date", "status", "order_type")), ("runs", "Production Runs", "production", "production.ProductionRun", ("run_number", "date", "status", "production_basis")), ("batches", "Batches", "production", "production.ProductionBatch", ("batch_number", "production_date", "produced_units", "is_reversed")), ("kitchen", "Kitchen Production", "kitchen", "production.Order", ("order_number", "date", "status", "order_type"))]),
    ("procurement", "Procurement", [("purchases", "Purchase Orders", "procurement", "procurement.PurchaseOrder", ("id", "date", "status", "total")), ("suppliers", "Suppliers", "procurement", "procurement.Supplier", ("name", "phone", "active")), ("supplier_payments", "Supplier Payments", "procurement", "procurement.SupplierPayment", ("date", "supplier", "amount", "payment_method"))]),
    ("inventory", "Inventory", [("raw", "Raw Materials", "inventory", "inventory.RawMaterial", ("name", "stock", "usage_unit", "reorder_level")), ("finished", "Finished Goods", "inventory", "inventory.FinishedGood", ("name", "stock", "unit", "reorder_level")), ("movements", "Stock Movements", "inventory", "inventory.StockMovement", ("business_date", "movement_type", "quantity", "reference")), ("adjustments", "Stock Adjustments", "inventory", "inventory.StockAdjustment", ("date", "reason", "description", "quantity")), ("dispenses", "Operational Supplies Used", "inventory", "inventory.OperationalSupplyDispense", ("date", "raw_material__name", "quantity", "reason"))]),
    ("market", "Market Stock", [("lots", "Market Lots", "inventory", "inventory.MarketStockLot", ("finished_good__name", "received_date", "quantity_received", "quantity_available")), ("market_movements", "Movements", "inventory", "inventory.MarketStockMovement", ("date", "movement_type", "quantity", "value"))]),
    ("finance", "Finance", [("profit_loss", "Profit & Loss", "finance", "", ()), ("money", "Money Movements", "finance", "core.FinancialTransaction", ("date", "transaction_type", "amount", "category")), ("accounts", "Cash Accounts", "finance", "core.CashAccount", ("name", "account_type", "opening_balance", "active"))]),
    ("expenses", "Expenses", [("expenses", "Expenses", "expenses", "expenses.Expense", ("date", "category", "description", "amount")), ("expense_payments", "Payments", "expenses", "expenses.ExpensePayment", ("date", "expense__description", "amount", "payment_method"))]),
    ("customers", "Customers", [("directory", "Customer Directory", "sales", "sales.Customer", ("name", "phone", "email", "active")), ("receipts", "Customer Payments", "sales", "sales.CustomerPayment", ("date", "customer", "amount", "payment_method"))]),
    ("commerce", "Commerce & Delivery", [("intakes", "Commerce Orders", "commerce", "commerce.CommerceIntake", ("created_at", "customer_name", "status", "total")), ("commerce_payments", "Commerce Payments", "commerce", "commerce.CommercePayment", ("created_at", "status", "amount", "method")), ("delivery", "Deliveries", "delivery", "commerce.DeliveryAssignment", ("status", "delivery_method", "eta_at", "created_at")), ("rider_delivery", "Rider Deliveries", "delivery_rider", "commerce.DeliveryAssignment", ("status", "delivery_method", "eta_at", "created_at"))]),
    ("reports", "Reports", [("reporting", "Sales Report Data", "reports", "sales.Sale", ("date", "customer", "total", "transaction_type", "source"))]),
    ("people", "People", [("payroll", "Payroll Runs", "users", "accounts.PayrollRun", ("period_start", "period_end", "status"))]),
    ("controls", "Audit & Controls", [("audit", "Audit Queries", "audit", "core.AuditQuery", ("subject", "module", "severity", "status"))]),
    ("ledger", "Ledger & Data Integrity", [("backlog", "Backlog Health", "ledger", "inventory.StockMovement", ("business_date", "movement_type", "quantity", "reference")), ("openings", "Opening Positions", "ledger", "core.OpeningPosition", ("segment", "kind", "object_id", "source"))]),
]


def dashboard_manifest(user, business):
    vertical_ui = vertical_config(business)
    result = []
    for group_key, group_label, raw_sections in GROUPS:
        sections = [
            {"key": key, "label": _vertical_section_label(key, label, vertical_ui)}
            for key, label, module, _model, _fields in raw_sections
            if user_has_permission(user, business, module, "view")
        ]
        if sections:
            label = group_label
            if group_key == "production":
                label = vertical_ui.get("kitchen_label", "Production") if vertical_ui["uses_production"] else "Stock Operations"
            result.append({"key": group_key, "label": label, "sections": sections})
    return result


def _section(section_key):
    return next((section for _gk, _gl, sections in GROUPS for section in sections if section[0] == section_key), None)


@login_required
@require_GET
def complete_dashboard(request):
    manifest = dashboard_manifest(request.user, request.business)
    return render(request, "core/complete_dashboard.html", {"dashboard_manifest": manifest})


@login_required
@require_GET
def complete_dashboard_section(request, group, section):
    selected = _section(section)
    if not selected:
        return JsonResponse({"error": "Unknown section."}, status=404)
    _key, label, module, model_label, fields = selected
    label = _vertical_section_label(section, label, vertical_config(request.business))
    if not user_has_permission(request.user, request.business, module, "view"):
        return JsonResponse({"error": "This section is not available to your role."}, status=403)
    group_entry = next((item for item in GROUPS if item[0] == group), None)
    if not group_entry or selected not in group_entry[2]:
        return JsonResponse({"error": "Section does not belong to this group."}, status=404)
    if section == "profit_loss":
        today = timezone.localdate()
        period = fiscal_period(request.business, today)
        start_default = period["start"]
        end_default = min(period["end"], today)
        try:
            start = _parse_date(request.GET.get("start")) or start_default
            end = _parse_date(request.GET.get("end")) or end_default
        except ValueError as error:
            return JsonResponse({"error": str(error)}, status=400)
        if start > end:
            return JsonResponse({"error": "Start date must be on or before end date."}, status=400)
        if (end - start).days > 366 * 5:
            return JsonResponse({"error": "Choose a date range of five years or less."}, status=400)
    else:
        start = end = None
    cache_key = f"complete-dashboard:{request.business.pk}:{request.user.pk}:{request.ledger_scope}:{group}:{section}:{start}:{end}"
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)
    if section == "profit_loss":
        components = daily_components(request.business, start, end)
        statement = profit_and_loss(request.business, start, end, components)
        monthly = period_buckets(components, start, end)
        lines = [
            ("Revenue · paid sales", statement["revenue"]),
            ("Cost of goods sold", statement["cogs"]),
            ("Gross profit", statement["gross_profit"]),
            *[(f"Operating expense · {row['label']}", row["amount"]) for row in statement["expense_lines"]],
            ("Payroll · employer cost", statement["payroll"]),
            ("Operating supplies used", statement["supplies"]),
            ("Stock write-offs and losses", statement["write_offs"]),
            ("Total costs", statement["total_costs"]),
            ("Net profit", statement["net_profit"]),
            ("Memo · unpaid sales", statement["unpaid_sales"]),
            ("Memo · unpaid expenses", statement["unpaid_expenses"]),
        ]
        payload = {
            "group": group, "section": section, "title": label,
            "kpis": [
                {"label": "Revenue", "value": _money_text(statement["revenue"], request.business.currency_symbol)},
                {"label": "Gross profit", "value": _money_text(statement["gross_profit"], request.business.currency_symbol)},
                {"label": "Net profit", "value": _money_text(statement["net_profit"], request.business.currency_symbol)},
                {"label": "Net margin", "value": _percent_text(statement["net_margin"])},
            ],
            "tables": [{"title": f"Statement · {start.isoformat()} to {end.isoformat()}", "columns": ["Line", "Amount"], "rows": [[name, _money_text(value, request.business.currency_symbol)] for name, value in lines]}],
            "charts": [{"title": "Monthly performance", "type": "columns", "unit": request.business.currency_symbol, "labels": [row["label"] for row in monthly], "series": [
                {"label": "Revenue", "values": [round(row["sales"], 2) for row in monthly]},
                {"label": "Costs", "values": [round(row["costs"], 2) for row in monthly]},
                {"label": "Net profit", "values": [round(row["profit"], 2) for row in monthly]},
            ]}],
            "date_range": {"start": start.isoformat(), "end": end.isoformat()},
            "currency": request.business.currency_symbol,
            "scope": getattr(request, "ledger_scope", "all"),
        }
        cache.set(cache_key, payload, 30)
        return JsonResponse(payload)
    model = apps.get_model(model_label)
    queryset = model.objects.all().order_by("-pk")
    related_paths = tuple(dict.fromkeys(field.rsplit("__", 1)[0] for field in fields if "__" in field))
    if related_paths:
        queryset = queryset.select_related(*related_paths)
    if model_label == "sales.Sale":
        queryset = queryset.prefetch_related("items")
    elif model_label == "procurement.PurchaseOrder":
        queryset = queryset.prefetch_related("items")
    if section == "pos_activity":
        queryset = queryset.filter(source="walkin")
    if model_label == "commerce.CommerceIntake":
        queryset = queryset.order_by("-created_at")
    if model_label == "commerce.DeliveryAssignment":
        queryset = queryset.order_by("-created_at")
    if model_label == "sales.Sale":
        queryset = queryset.order_by("-date", "-pk")
    objects = list(queryset[:20])
    rows = []
    for obj in objects:
        row = []
        for field_name in fields:
            value = obj
            for part in field_name.split("__"):
                value = getattr(value, part, "") if value is not None else ""
            if hasattr(value, "strftime"):
                value = value.isoformat()
            elif value is None:
                value = ""
            else:
                value = str(value)
            row.append(value)
        rows.append(row)
    charts = []
    if section in {"raw", "finished"}:
        inventory_counts = queryset.aggregate(
            out=Count("pk", filter=Q(stock__lte=0)),
            reorder=Count("pk", filter=Q(stock__gt=0, stock__lte=F("reorder_level"))),
            healthy=Count("pk", filter=Q(stock__gt=F("reorder_level"))),
        )
        charts.append({"title": "Stock health · items", "type": "columns", "labels": ["Out of stock", "At/below reorder", "Above reorder"], "series": [{"label": "Items", "values": [inventory_counts["out"], inventory_counts["reorder"], inventory_counts["healthy"]]}]})
    chart_fields = {
        "orders": "status", "batches": "is_reversed", "purchases": "status",
        "runs": "status", "expenses": "category", "dispenses": "reason", "adjustments": "reason",
        "supplier_payments": "payment_method", "receipts": "payment_method",
        "sales": "transaction_type", "pos_activity": "transaction_type",
        "intakes": "status", "commerce_payments": "status", "delivery": "status", "rider_delivery": "status",
    }
    chart_field = chart_fields.get(section)
    if chart_field:
        chart_rows = list(queryset.order_by().values(chart_field).annotate(total=Count("pk")).order_by("-total")[:8])
        charts.append({"title": f"Records by {chart_field.replace('_', ' ')}", "type": "columns", "labels": [str(row[chart_field] or "Unspecified").replace("_", " ").title() for row in chart_rows], "series": [{"label": "Records", "values": [row["total"] for row in chart_rows]}]})
    payload = {
        "group": group, "section": section, "title": label,
        "kpis": [{"label": "Records shown", "value": len(objects)}, {"label": "Total records", "value": queryset.count()}],
        "tables": [{"title": label, "columns": [field.replace("__", " · ").replace("_", " ").title() for field in fields], "rows": rows}],
        "scope": getattr(request, "ledger_scope", "all"),
        "charts": charts,
    }
    if section == "overview":
        payload["message"] = "A quick view of recent recorded sales and their payment type. Use it to check sales activity and payment mix; it is not a profit-and-loss or full business-health measure."
    cache.set(cache_key, payload, 30)
    return JsonResponse(payload)


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("Enter dates in YYYY-MM-DD format.")


def _money_text(value, currency_symbol=""):
    if value < 0:
        return f"-{currency_symbol}{abs(value):,.2f}"
    return f"{currency_symbol}{value:,.2f}"


def _percent_text(value):
    return f"{value:.1f}%" if value is not None else "—"


def _vertical_section_label(key, label, vertical_ui):
    labels = {
        "orders": vertical_ui["orders"],
        "runs": vertical_ui["runs"],
        "batches": vertical_ui["batches"],
        "kitchen": vertical_ui.get("kitchen_label", vertical_ui["orders"]),
        "raw": "Raw Materials" if vertical_ui["uses_production"] else "Supporting Supplies",
        "finished": vertical_ui["product_label"],
        "customers": vertical_ui["customers"],
        "directory": vertical_ui["customers"],
        "sales": vertical_ui["sales"],
        "pos_activity": vertical_ui["pos"]["title"],
        "dispenses": "Operational Supplies Used",
    }
    return labels.get(key, label)
