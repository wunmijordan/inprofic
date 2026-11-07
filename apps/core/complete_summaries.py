"""Executive summary sections for the Complete dashboard.

Each builder takes a ``Ctx`` and returns ``{"kpis": [...], "tables": [...],
"charts": [...], "message": str?}`` using only business-scoped, ledger-scoped
managers (``Model.objects``), so Live / Backlog / Combined behaves exactly as it
does on every other screen. Money and quantities are returned as display text.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Case, Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from .accounting import _sale_expressions, daily_components, period_buckets, profit_and_loss

ZERO = Decimal("0")
MONEY = DecimalField(max_digits=24, decimal_places=6)
QTY = DecimalField(max_digits=24, decimal_places=4)


@dataclass
class Ctx:
    request: object
    business: object
    start: date
    end: date
    today: date

    @property
    def cur(self):
        return self.business.currency_symbol or ""

    @property
    def label(self):
        return f"{self.start:%d %b %Y} – {self.end:%d %b %Y}"


# ----- formatting ----------------------------------------------------------

def money(ctx, value):
    value = Decimal(value or 0)
    sign = "-" if value < 0 else ""
    return f"{sign}{ctx.cur}{abs(value):,.2f}"


def num(value, places=2):
    value = Decimal(value or 0)
    text = f"{value:,.{places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def pct(value):
    return "—" if value is None else f"{Decimal(value):.1f}%"


def kpi(label, value, hint=""):
    return {"label": label, "value": num(value) if isinstance(value, Decimal) else value, "hint": hint}


def table(title, columns, rows):
    return {"title": title, "columns": columns, "rows": [[("" if c is None else num(c) if isinstance(c, Decimal) else str(c)) for c in row] for row in rows]}


def chart(title, labels, series, unit=""):
    return {"title": title, "type": "columns", "unit": unit, "labels": labels,
            "series": [{"label": name, "values": [float(v) for v in values]} for name, values in series]}


def pretty(value):
    return str(value or "Unspecified").replace("_", " ").title()


def _share(part, whole):
    whole = Decimal(whole or 0)
    return None if whole == 0 else Decimal(part or 0) / whole * 100


def _range_buckets(ctx, qs, date_field, value_expr, *, extra_filter=None):
    """Sum ``value_expr`` per day (or per month for long ranges) -> (labels, values)."""
    qs = qs.filter(**{f"{date_field}__range": (ctx.start, ctx.end)})
    if extra_filter:
        qs = qs.filter(extra_filter)
    if (ctx.end - ctx.start).days > 62:
        rows = qs.annotate(_b=TruncMonth(date_field)).values("_b").annotate(total=Sum(value_expr)).order_by("_b")
        return [r["_b"].strftime("%b %Y") for r in rows], [r["total"] or ZERO for r in rows]
    rows = qs.values(date_field).annotate(total=Sum(value_expr)).order_by(date_field)
    return [r[date_field].strftime("%d %b") for r in rows], [r["total"] or ZERO for r in rows]


# ----- shared lookups ------------------------------------------------------

def _sales_qs(ctx):
    from sales.models import Sale
    return Sale.objects.filter(business=ctx.business, date__range=(ctx.start, ctx.end))


def _items_qs(ctx):
    """Sale lines for the period, limited by the ledger-scoped Sale manager."""
    from sales.models import SaleItem
    return SaleItem.objects.filter(sale__in=_sales_qs(ctx))


def _open_items(ctx):
    from .finance_views import _finance_open_items
    return _finance_open_items(ctx.business)


def _cash_accounts(ctx):
    from .finance_views import _cash_account_balances
    return _cash_account_balances(ctx.business, active_only=True)


def _stock_values(ctx):
    from inventory.models import FinishedGood, RawMaterial
    raw = RawMaterial.objects.filter(business=ctx.business).aggregate(
        value=Sum(ExpressionWrapper(F("stock") * F("cost_per_unit"), output_field=MONEY)), count=Count("pk"))
    fg = FinishedGood.objects.filter(business=ctx.business, stock__isnull=False).aggregate(
        value=Sum(ExpressionWrapper(F("stock") * F("selling_price"), output_field=MONEY)), count=Count("pk"))
    return raw, fg


# ===========================================================================
# Business Pulse
# ===========================================================================

def pulse_indicators(ctx):
    from inventory.models import FinishedGood, RawMaterial
    from production.models import Order
    components = daily_components(ctx.business, ctx.start, ctx.end)
    pnl = profit_and_loss(ctx.business, ctx.start, ctx.end, components)
    open_items = _open_items(ctx)
    accounts = _cash_accounts(ctx)
    cash = sum((a._calculated_balance for a in accounts), ZERO)
    raw_stock, fg_stock = _stock_values(ctx)
    low_raw = RawMaterial.objects.filter(business=ctx.business, reorder_level__gt=0, stock__lte=F("reorder_level")).count()
    low_fg = FinishedGood.objects.filter(business=ctx.business, stock__isnull=False, reorder_level__gt=0,
                                         stock__lte=F("reorder_level")).count()
    open_orders = Order.objects.filter(business=ctx.business, status__in=("pending", "approved")).count()
    receivables, payables = open_items["receivables"], open_items["payables"]
    kpis = [
        kpi("Revenue", money(ctx, pnl["revenue"]), f"Paid sales · {ctx.label}"),
        kpi("Gross profit", money(ctx, pnl["gross_profit"]), pct(pnl.get("gross_margin")) if pnl.get("gross_margin") is not None else ""),
        kpi("Net profit", money(ctx, pnl["net_profit"]), f"Net margin {pct(pnl['net_margin'])}"),
        kpi("Cash on hand", money(ctx, cash), f"{len(accounts)} active account{'s' if len(accounts) != 1 else ''}"),
        kpi("Receivables", money(ctx, receivables), "Customers owe you"),
        kpi("Payables", money(ctx, payables), "You owe suppliers and vendors"),
        kpi("Net working position", money(ctx, cash + receivables - payables), "Cash + receivables − payables"),
        kpi("Stock value", money(ctx, (raw_stock["value"] or ZERO) + (fg_stock["value"] or ZERO)),
            "Raw materials at cost + products at selling price"),
        kpi("Items to reorder", low_raw + low_fg, f"{low_raw} raw material(s), {low_fg} product(s)"),
        kpi("Open orders", open_orders, "Pending approval or in production"),
    ]
    buckets = period_buckets(components, ctx.start, ctx.end)
    from accounts.services import user_has_permission
    permits = lambda module: user_has_permission(ctx.request.user, ctx.business, module, 'view')
    finance_labels = {'Revenue', 'Gross profit', 'Net profit', 'Cash on hand', 'Receivables', 'Payables', 'Net working position'}
    kpis = [item for item in kpis if
            (item['label'] not in finance_labels or permits('finance'))
            and (item['label'] not in {'Stock value', 'Items to reorder'} or permits('inventory'))
            and (item['label'] != 'Open orders' or (ctx.business.uses_production and permits('production')))]
    return {
        "kpis": kpis,
        "charts": [chart("Revenue, costs and net profit", [b["label"] for b in buckets], [
            ("Revenue", [round(b["sales"], 2) for b in buckets]),
            ("Costs", [round(b["costs"], 2) for b in buckets]),
            ("Net profit", [round(b["profit"], 2) for b in buckets]),
        ], ctx.cur)] if permits("finance") else [],
        "tables": [],
        "message": "Revenue counts paid sales only; expenses count when incurred. Cash, receivables, payables and stock are as of now.",
    }


def pulse_attention(ctx):
    from inventory.models import FinishedGood, MarketStockLot, RawMaterial
    from production.models import Order
    from .models import AuditQuery
    soon = ctx.today + timedelta(days=7)
    open_items = _open_items(ctx)
    low_raw = list(RawMaterial.objects.filter(business=ctx.business, reorder_level__gt=0, stock__lte=F("reorder_level")).order_by("stock")[:8])
    pending = list(Order.objects.filter(business=ctx.business, status="pending").order_by("date")[:8])
    expiring = list(MarketStockLot.objects.filter(business=ctx.business, active=True, expiry_date__isnull=False,
                                                  expiry_date__lte=soon).select_related("finished_good").order_by("expiry_date")[:8])
    queries = AuditQuery.objects.filter(business=ctx.business).exclude(status="closed").count()
    debtors = open_items["outstanding_sales"][:8]
    result = {
        "kpis": [
            kpi("Raw materials low", len(low_raw)), kpi("Awaiting approval", len(pending)),
            kpi("Lots expiring in 7 days", len(expiring)), kpi("Open audit queries", queries),
        ],
        "tables": [
            table("Low raw materials", ["Material", "In stock", "Reorder at"],
                  [[m.name, f"{num(m.stock, 2)} {m.usage_unit}", num(m.reorder_level, 2)] for m in low_raw]),
            table("Orders awaiting approval", ["Order", "Date", "Type"],
                  [[f"#{o.display_number}", o.date.isoformat(), o.display_order_type] for o in pending]),
            table("Market lots expiring soon", ["Product", "Expires", "Available"],
                  [[l.finished_good.name, l.expiry_date.isoformat(), num(l.quantity_available)] for l in expiring]),
            table("Largest unpaid sales", ["Customer", "Date", "Balance"],
                  [[row["sale"].customer, row["sale"].date.isoformat(), money(ctx, row["balance"])] for row in debtors]),
        ],
        "charts": [],
    }
    from accounts.services import user_has_permission
    modules = ['inventory', 'production', 'inventory', 'audit']
    result['kpis'] = [item for item, module in zip(result['kpis'], modules)
                      if user_has_permission(ctx.request.user, ctx.business, module, 'view')
                      and (module != 'production' or ctx.business.uses_production)]
    modules = ['inventory', 'production', 'inventory', 'finance']
    result['tables'] = [item for item, module in zip(result['tables'], modules)
                        if user_has_permission(ctx.request.user, ctx.business, module, 'view')
                        and (module != 'production' or ctx.business.uses_production)]
    return result



# ===========================================================================
# Sales
# ===========================================================================

def sales_summary(ctx):
    from sales.models import Sale
    _m, line_total, cogs, units = _sale_expressions(prefix="")
    items = _items_qs(ctx)
    totals = items.aggregate(revenue=Sum(line_total, output_field=MONEY), cost=Sum(cogs, output_field=MONEY),
                             units=Sum(units, output_field=QTY))
    paid = items.filter(sale__transaction_type="paid").aggregate(revenue=Sum(line_total, output_field=MONEY))
    count = _sales_qs(ctx).count()
    gross = totals["revenue"] or ZERO
    profit = gross - (totals["cost"] or ZERO)
    source_labels = dict(Sale._meta.get_field("source").choices) if Sale._meta.get_field("source").choices else {}
    by_source = (items.values("sale__source").annotate(total=Sum(line_total, output_field=MONEY),
                 n=Count("sale", distinct=True)).order_by("-total"))
    by_type = (items.values("sale__transaction_type").annotate(total=Sum(line_total, output_field=MONEY),
               n=Count("sale", distinct=True)).order_by("-total"))
    top_products = (items.values("finished_good__name").annotate(
        revenue=Sum(line_total, output_field=MONEY), units=Sum(units, output_field=QTY),
        cost=Sum(cogs, output_field=MONEY)).order_by("-revenue")[:10])
    top_customers = (items.values("sale__customer").annotate(total=Sum(line_total, output_field=MONEY),
                     n=Count("sale", distinct=True)).order_by("-total")[:10])
    labels, values = _range_buckets(ctx, items, "sale__date", line_total)
    return {
        "kpis": [
            kpi("Total sales", money(ctx, gross), ctx.label), kpi("Paid revenue", money(ctx, paid["revenue"])),
            kpi("Unpaid / part-paid", money(ctx, gross - (paid["revenue"] or ZERO))),
            kpi("Gross profit on sales", money(ctx, profit), f"Margin {pct(_share(profit, gross))}"),
            kpi("Number of sales", count), kpi("Average sale", money(ctx, gross / count if count else 0)),
            kpi("Units sold", num(totals["units"])),
        ],
        "charts": [chart("Sales over the period", labels, [("Sales", values)], ctx.cur)],
        "tables": [
            table("By channel", ["Channel", "Sales", "Value", "Share"],
                  [[source_labels.get(r["sale__source"], pretty(r["sale__source"])), r["n"], money(ctx, r["total"]),
                    pct(_share(r["total"], gross))] for r in by_source]),
            table("By payment status", ["Status", "Sales", "Value"],
                  [[pretty(r["sale__transaction_type"]), r["n"], money(ctx, r["total"])] for r in by_type]),
            table("Top products", ["Product", "Units", "Revenue", "Margin"],
                  [[r["finished_good__name"] or "—", num(r["units"]), money(ctx, r["revenue"]),
                    pct(_share((r["revenue"] or ZERO) - (r["cost"] or ZERO), r["revenue"]))] for r in top_products]),
            table("Top customers", ["Customer", "Sales", "Value"],
                  [[r["sale__customer"] or "Walk-in", r["n"], money(ctx, r["total"])] for r in top_customers]),
        ],
    }


# ===========================================================================
# Production
# ===========================================================================

def production_summary(ctx):
    from production.models import Order, ProductionBatch
    orders = Order.objects.filter(business=ctx.business, date__range=(ctx.start, ctx.end))
    by_status = orders.values("status").annotate(n=Count("pk")).order_by("-n")
    batches = ProductionBatch.objects.filter(business=ctx.business, production_date__range=(ctx.start, ctx.end), is_reversed=False)
    agg = batches.aggregate(produced=Sum("produced_units"), wastage=Sum("wastage_units"), planned=Sum("planned_units"),
                            cost=Sum("total_cost"), n=Count("pk"))
    produced, wastage = agg["produced"] or ZERO, agg["wastage"] or ZERO
    by_product = (batches.values("finished_good__name").annotate(
        produced=Sum("produced_units"), wastage=Sum("wastage_units"), cost=Sum("total_cost"), n=Count("pk")).order_by("-produced")[:10])
    labels, values = _range_buckets(ctx, batches, "production_date", F("produced_units"))
    pending = Order.objects.filter(business=ctx.business, status="pending").count()
    return {
        "kpis": [
            kpi("Orders in period", orders.count(), ctx.label), kpi("Awaiting approval", pending),
            kpi("Batches produced", agg["n"]), kpi("Units produced", num(produced)),
            kpi("Wastage", num(wastage), f"{pct(_share(wastage, produced))} of output"),
            kpi("Production cost", money(ctx, agg["cost"]),
                f"{money(ctx, (agg['cost'] or ZERO) / max(ZERO, produced-wastage))} per saleable unit" if produced > wastage else ""),
        ],
        "charts": [chart("Units produced", labels, [("Units", values)])],
        "tables": [
            table("Orders by status", ["Status", "Orders"], [[pretty(r["status"]), r["n"]] for r in by_status]),
            table("Output by product", ["Product", "Batches", "Produced", "Wastage", "Cost"],
                  [[r["finished_good__name"] or "—", r["n"], num(r["produced"]), num(r["wastage"]), money(ctx, r["cost"])]
                   for r in by_product]),
        ],
    }


def recipe_variance_section(ctx):
    from production.variance import REVIEW_THRESHOLD, variance_report
    report = variance_report(ctx.business, ctx.start, ctx.end)
    top = report["ingredients"][:8]
    totals = report["totals"]
    return {
        "kpis": [
            kpi("Ingredients to review", totals["review_count"], f"Over {REVIEW_THRESHOLD}% from recipe"),
            kpi("Over / under recipe", money(ctx, totals["variance_value"]), ctx.label),
            kpi("Stock written off", money(ctx, totals["shrink_value"]), "Removed by adjustments"),
            kpi("Wasted output", money(ctx, totals["waste_value"]), "Valued at production cost"),
        ],
        "charts": [chart("Planned vs actual ingredient use", [r["name"] for r in top],
                         [("Planned", [r["planned"] for r in top]), ("Actual", [r["actual"] for r in top])])] if top else [],
        "tables": [
            table("Ingredients", ["Ingredient", "Planned", "Actual", "Variance", "Value", "Written off", "Status"],
                  [[r["name"], f"{num(r['planned'], 2)} {r['unit']}", num(r["actual"], 2),
                    f"{num(r['variance'], 2)} ({pct(r['variance_pct'])})", money(ctx, r["variance_value"]),
                    num(r["shrink_qty"], 2), pretty(r["status"])] for r in report["ingredients"][:25]]),
            table("Output", ["Product", "Batches", "Planned", "Produced", "Wasted", "Yield", "Waste value"],
                  [[r["name"], r["batches"], num(r["planned"]), num(r["produced"]), num(r["wasted"]),
                    pct(r["yield_pct"]), money(ctx, r["waste_value"])] for r in report["outputs"][:25]]),
        ],
        "message": "Planned is what the recipe says approved orders should use; actual is what was released. Full report: Orders > Recipe vs Actual.",
    }


# ===========================================================================
# Procurement
# ===========================================================================

def procurement_summary(ctx):
    from procurement.models import PurchaseOrder, PurchaseOrderItem, RawMaterialCostSnapshot
    line_value = ExpressionWrapper(F("qty") * F("unit_cost"), output_field=MONEY)
    received = PurchaseOrder.objects.filter(business=ctx.business, status="received", received_date__range=(ctx.start, ctx.end))
    lines = PurchaseOrderItem.objects.filter(purchase_order__in=received)
    spend = lines.aggregate(total=Sum(line_value))["total"] or ZERO
    ordered = PurchaseOrder.objects.filter(business=ctx.business, status__in=("draft", "ordered", "approved"))
    ordered_value = PurchaseOrderItem.objects.filter(purchase_order__in=ordered).aggregate(total=Sum(line_value))["total"] or ZERO
    open_items = _open_items(ctx)
    supplier = Coalesce("purchase_order__supplier_master__name", "purchase_order__supplier")
    by_supplier = lines.annotate(_s=supplier).values("_s").annotate(total=Sum(line_value), n=Count("purchase_order", distinct=True)).order_by("-total")[:10]
    item_name = Coalesce("raw_material__name", "finished_good__name")
    by_item = lines.annotate(_i=item_name).values("_i").annotate(total=Sum(line_value), qty=Sum("qty")).order_by("-total")[:10]
    snapshots = (RawMaterialCostSnapshot.objects.filter(business=ctx.business, effective_date__range=(ctx.start, ctx.end))
                 .select_related("raw_material").order_by("-effective_date", "-id")[:15])
    return {
        "kpis": [
            kpi("Spend (received)", money(ctx, spend), ctx.label), kpi("Orders received", received.count()),
            kpi("On order", ordered.count(), money(ctx, ordered_value)), kpi("Owed to suppliers", money(ctx, open_items["purchase_payables"]),
                                                                              f"{open_items['outstanding_pos_count']} invoice(s)" if "outstanding_pos_count" in open_items else ""),
        ],
        "charts": [chart("Spend by supplier", [(r["_s"] or "Unnamed") for r in by_supplier], [("Spend", [r["total"] or ZERO for r in by_supplier])], ctx.cur)],
        "tables": [
            table("Suppliers", ["Supplier", "Orders", "Spend"], [[r["_s"] or "Unnamed supplier", r["n"], money(ctx, r["total"])] for r in by_supplier]),
            table("Most purchased items", ["Item", "Quantity", "Spend"], [[r["_i"] or "—", num(r["qty"]), money(ctx, r["total"])] for r in by_item]),
            table("Price movement (cost per usage unit)", ["Date", "Material", "Cost", "Supplier"],
                  [[s.effective_date.isoformat(), s.raw_material.name, money(ctx, s.usage_unit_cost), s.supplier or "—"] for s in snapshots]),
        ],
    }


# ===========================================================================
# Inventory, Recipes, Market stock
# ===========================================================================

def inventory_summary(ctx):
    from inventory.models import FinishedGood, RawMaterial, StockAdjustment, StockMovement
    raw_stock, fg_stock = _stock_values(ctx)
    raws = RawMaterial.objects.filter(business=ctx.business)
    goods = FinishedGood.objects.filter(business=ctx.business, stock__isnull=False)
    raw_out = raws.filter(stock__lte=0).count()
    raw_low = raws.filter(stock__gt=0, reorder_level__gt=0, stock__lte=F("reorder_level")).count()
    fg_out = goods.filter(stock__lte=0).count()
    adjustments = StockAdjustment.objects.filter(business=ctx.business, date__range=(ctx.start, ctx.end), reversed=False)
    adj_value = adjustments.aggregate(v=Sum(ExpressionWrapper(F("quantity") * F("unit_value"), output_field=MONEY)))["v"] or ZERO
    movements = (StockMovement.objects.filter(business=ctx.business, business_date__range=(ctx.start, ctx.end))
                 .values("movement_type").annotate(n=Count("pk"), qty=Sum("quantity")).order_by("-n"))
    top_value = raws.annotate(_v=ExpressionWrapper(F("stock") * F("cost_per_unit"), output_field=MONEY)).filter(stock__gt=0).order_by("-_v")[:10]
    low_goods = goods.filter(reorder_level__gt=0, stock__lte=F("reorder_level")).order_by("stock")[:10]
    return {
        "kpis": [
            kpi("Raw material value", money(ctx, raw_stock["value"]), f"{raw_stock['count']} item(s) at cost"),
            kpi("Product stock value", money(ctx, fg_stock["value"]), f"{fg_stock['count']} item(s) at selling price"),
            kpi("Raw materials out of stock", raw_out), kpi("Raw materials low", raw_low),
            kpi("Products out of stock", fg_out), kpi("Adjustments (net value)", money(ctx, adj_value), f"{adjustments.count()} in {ctx.label}"),
        ],
        "charts": [chart("Raw materials by stock health", ["Out of stock", "Low", "Healthy"], [(
            "Items", [raw_out, raw_low, max(raw_stock["count"] - raw_out - raw_low, 0)])])],
        "tables": [
            table("Highest-value raw materials", ["Material", "In stock", "Value"],
                  [[m.name, f"{num(m.stock, 2)} {m.usage_unit}", money(ctx, m._v)] for m in top_value]),
            table("Products at or below reorder level", ["Product", "In stock", "Reorder at"],
                  [[g.name, f"{num(g.stock)} {g.unit}", num(g.reorder_level)] for g in low_goods]),
            table("Stock movements in period", ["Type", "Movements", "Net quantity"],
                  [[pretty(r["movement_type"]), r["n"], num(r["qty"], 2)] for r in movements]),
        ],
    }


def recipe_ratios_section(ctx):
    from inventory.recipe_ratios import recipe_overview
    rows = recipe_overview(ctx.business)
    products = len({r["product"] for r in rows})
    return {
        "kpis": [kpi("Products with a base material", products), kpi("Ingredient lines shown", len(rows))],
        "charts": [],
        "tables": [table("Ingredient quantities relative to each product's base material",
                         ["Product", "Base material", "Ingredient", "Per batch", "Relation to base", "Share"],
                         [[r["product"], r["base"], r["name"], r["qty_text"], r["relation"], r["percent_text"]] for r in rows])],
        "message": "Quantities are per batch. A line such as “1 kg per 20 kg Flour” means that for every 20 kg of the base material the recipe uses 1 kg of the ingredient.",
    }


def market_summary(ctx):
    from inventory.models import DistributionReturn, MarketStockLot
    lots = MarketStockLot.objects.filter(business=ctx.business, active=True)
    agg = lots.aggregate(qty=Sum("quantity_available"), value=Sum(ExpressionWrapper(F("quantity_available") * F("unit_cost"), output_field=MONEY)))
    soon = lots.filter(expiry_date__isnull=False, expiry_date__lte=ctx.today + timedelta(days=7), expiry_date__gte=ctx.today).count()
    expired = lots.filter(expiry_date__lt=ctx.today).count()
    returns = DistributionReturn.objects.filter(business=ctx.business, date__range=(ctx.start, ctx.end))
    by_product = lots.values("finished_good__name").annotate(qty=Sum("quantity_available"), n=Count("pk"),
        value=Sum(ExpressionWrapper(F("quantity_available") * F("unit_cost"), output_field=MONEY))).order_by("-value")[:10]
    by_condition = returns.values("condition").annotate(n=Count("pk"), qty=Sum("quantity")).order_by("-n")
    return {
        "kpis": [
            kpi("Active lots", lots.count()), kpi("Units available", num(agg["qty"])),
            kpi("Value at cost", money(ctx, agg["value"])), kpi("Expiring in 7 days", soon), kpi("Expired", expired),
            kpi("Returns in period", returns.count(), ctx.label),
        ],
        "charts": [],
        "tables": [
            table("Available by product", ["Product", "Lots", "Units", "Value"],
                  [[r["finished_good__name"] or "—", r["n"], num(r["qty"]), money(ctx, r["value"])] for r in by_product]),
            table("Returns by condition", ["Condition", "Returns", "Quantity"], [[pretty(r["condition"]), r["n"], num(r["qty"])] for r in by_condition]),
        ],
    }


# ===========================================================================
# Finance, Expenses
# ===========================================================================

def finance_position(ctx):
    open_items = _open_items(ctx)
    accounts = _cash_accounts(ctx)
    cash = sum((a._calculated_balance for a in accounts), ZERO)
    return {
        "kpis": [
            kpi("Cash on hand", money(ctx, cash)), kpi("Receivables", money(ctx, open_items["receivables"])),
            kpi("Payables", money(ctx, open_items["payables"]),
                f"Suppliers {money(ctx, open_items['purchase_payables'])} · Expenses {money(ctx, open_items['expense_payables'])}"),
            kpi("Net position", money(ctx, cash + open_items["receivables"] - open_items["payables"])),
        ],
        "charts": [chart("Cash by account", [a.name for a in accounts], [("Balance", [a._calculated_balance for a in accounts])], ctx.cur)],
        "tables": [
            table("Cash accounts", ["Account", "Type", "Balance"], [[a.name, pretty(a.account_type), money(ctx, a._calculated_balance)] for a in accounts]),
            table("Outstanding customer invoices", ["Customer", "Date", "Balance"],
                  [[r["sale"].customer, r["sale"].date.isoformat(), money(ctx, r["balance"])] for r in open_items["outstanding_sales"][:15]]),
            table("Outstanding supplier invoices", ["Supplier", "Ordered", "Balance"],
                  [[r["po"].supplier or "Unnamed", r["po"].date.isoformat(), money(ctx, r["balance"])] for r in open_items["outstanding_pos"][:15]]),
            table("Unpaid expenses", ["Description", "Date", "Amount"],
                  [[r["expense"].description, r["expense"].date.isoformat(), money(ctx, r["balance"])] for r in open_items["outstanding_expenses"][:15]]),
        ],
        "message": "Receivables and payables stay matched invoice by invoice across the Live and Backlog periods.",
    }


def money_flow(ctx):
    from .models import FinancialTransaction
    tx = FinancialTransaction.objects.filter(business=ctx.business, date__range=(ctx.start, ctx.end), reversed=False)
    flows = tx.values("transaction_type").annotate(total=Sum("amount"), n=Count("pk"))
    totals = {r["transaction_type"]: (r["total"] or ZERO, r["n"]) for r in flows}
    income, outflow = totals.get(FinancialTransaction.INCOME, (ZERO, 0)), totals.get(FinancialTransaction.OUTFLOW, (ZERO, 0))
    by_category = tx.values("transaction_type", "category").annotate(total=Sum("amount"), n=Count("pk")).order_by("-total")[:15]
    by_method = tx.values("payment_method").annotate(total=Sum("amount"), n=Count("pk")).order_by("-total")
    in_labels, in_values = _range_buckets(ctx, tx, "date", F("amount"), extra_filter=Q(transaction_type=FinancialTransaction.INCOME))
    out_labels, out_values = _range_buckets(ctx, tx, "date", F("amount"), extra_filter=Q(transaction_type=FinancialTransaction.OUTFLOW))
    in_by, out_by = dict(zip(in_labels, in_values)), dict(zip(out_labels, out_values))
    labels = list(dict.fromkeys([*in_labels, *out_labels]))
    ins, outs = [in_by.get(label, ZERO) for label in labels], [out_by.get(label, ZERO) for label in labels]
    return {
        "kpis": [kpi("Money in", money(ctx, income[0]), f"{income[1]} movement(s)"), kpi("Money out", money(ctx, outflow[0]), f"{outflow[1]} movement(s)"),
                 kpi("Net cash flow", money(ctx, income[0] - outflow[0]), ctx.label)],
        "charts": [chart("Money in vs out", labels, [("In", ins), ("Out", outs)], ctx.cur)] if labels else [],
        "tables": [
            table("By category", ["Direction", "Category", "Movements", "Amount"],
                  [["In" if r["transaction_type"] == FinancialTransaction.INCOME else "Out", pretty(r["category"]), r["n"], money(ctx, r["total"])] for r in by_category]),
            table("By payment method", ["Method", "Movements", "Amount"], [[pretty(r["payment_method"]), r["n"], money(ctx, r["total"])] for r in by_method]),
        ],
    }


def expense_summary(ctx):
    from expenses.models import Expense
    qs = Expense.objects.filter(business=ctx.business, date__range=(ctx.start, ctx.end))
    agg = qs.aggregate(total=Sum("amount"), n=Count("pk"))
    unpaid = qs.filter(payment_status="unpaid").aggregate(total=Sum("amount"), n=Count("pk"))
    by_category = qs.values("category").annotate(total=Sum("amount"), n=Count("pk")).order_by("-total")
    by_vendor = qs.exclude(vendor="").values("vendor").annotate(total=Sum("amount"), n=Count("pk")).order_by("-total")[:10]
    labels, values = _range_buckets(ctx, qs, "date", F("amount"))
    total = agg["total"] or ZERO
    return {
        "kpis": [kpi("Expenses", money(ctx, total), ctx.label), kpi("Records", agg["n"]),
                 kpi("Unpaid", money(ctx, unpaid["total"]), f"{unpaid['n']} bill(s)"), kpi("Average expense", money(ctx, total / agg["n"] if agg["n"] else 0))],
        "charts": [chart("Expenses by category", [pretty(r["category"]) for r in by_category], [("Amount", [r["total"] or ZERO for r in by_category])], ctx.cur),
                   chart("Expenses over time", labels, [("Amount", values)], ctx.cur)],
        "tables": [
            table("By category", ["Category", "Records", "Amount", "Share"], [[pretty(r["category"]), r["n"], money(ctx, r["total"]), pct(_share(r["total"], total))] for r in by_category]),
            table("Top vendors", ["Vendor", "Records", "Amount"], [[r["vendor"], r["n"], money(ctx, r["total"])] for r in by_vendor]),
        ],
    }


# ===========================================================================
# Customers, Commerce, People, Controls, Ledger
# ===========================================================================

def customer_summary(ctx):
    from sales.models import Customer
    customers = Customer.objects.filter(business=ctx.business)
    open_items = _open_items(ctx)
    new = customers.filter(created_at__date__range=(ctx.start, ctx.end)).count()
    by_region = customers.filter(active=True).values("region").annotate(n=Count("pk")).order_by("-n")[:10]
    by_group = customers.filter(active=True).values("customer_group").annotate(n=Count("pk")).order_by("-n")[:10]
    _m, line_total, _c, _u = _sale_expressions(prefix="")
    top = (_items_qs(ctx).values("sale__customer").annotate(total=Sum(line_total, output_field=MONEY), n=Count("sale", distinct=True)).order_by("-total")[:10])
    owing = defaultdict(Decimal)
    for row in open_items["outstanding_sales"]:
        owing[row["sale"].customer] += row["balance"]
    debtors = sorted(owing.items(), key=lambda kv: -kv[1])[:10]
    return {
        "kpis": [kpi("Customers", customers.count(), f"{customers.filter(active=True).count()} active"), kpi("New in period", new, ctx.label),
                 kpi("With a credit limit", customers.filter(credit_limit__gt=0).count()), kpi("Total receivables", money(ctx, open_items["receivables"]))],
        "charts": [],
        "tables": [
            table("Top customers by sales", ["Customer", "Sales", "Value"], [[r["sale__customer"] or "Walk-in", r["n"], money(ctx, r["total"])] for r in top]),
            table("Largest balances owed", ["Customer", "Owes"], [[name, money(ctx, value)] for name, value in debtors]),
            table("By region", ["Region", "Customers"], [[r["region"] or "Unspecified", r["n"]] for r in by_region]),
            table("By customer area", ["Area", "Customers"], [[pretty(r["customer_group"]), r["n"]] for r in by_group]),
        ],
    }


def commerce_summary(ctx):
    from commerce.models import CommerceIntake, DeliveryAssignment
    intakes = CommerceIntake.objects.filter(business=ctx.business, created_at__date__range=(ctx.start, ctx.end))
    agg = intakes.aggregate(n=Count("pk"), fees=Sum("delivery_fee"))
    by_status = intakes.values("status").annotate(n=Count("pk")).order_by("-n")
    by_channel = intakes.values("sales_channel").annotate(n=Count("pk")).order_by("-n")
    by_payment = intakes.values("payment_state").annotate(n=Count("pk")).order_by("-n")
    deliveries = DeliveryAssignment.objects.filter(business=ctx.business, created_at__date__range=(ctx.start, ctx.end))
    by_delivery = deliveries.values("status").annotate(n=Count("pk")).order_by("-n")
    return {
        "kpis": [kpi("Online / commerce orders", agg["n"], ctx.label), kpi("Delivery fees", money(ctx, agg["fees"])), kpi("Deliveries", deliveries.count())],
        "charts": [chart("Orders by status", [pretty(r["status"]) for r in by_status], [("Orders", [r["n"] for r in by_status])])] if by_status else [],
        "tables": [
            table("Orders by channel", ["Channel", "Orders"], [[pretty(r["sales_channel"]), r["n"]] for r in by_channel]),
            table("Payment state", ["State", "Orders"], [[pretty(r["payment_state"]), r["n"]] for r in by_payment]),
            table("Deliveries by status", ["Status", "Deliveries"], [[pretty(r["status"]), r["n"]] for r in by_delivery]),
        ],
    }


def people_summary(ctx):
    from accounts.models import PayrollRun, UserBusiness
    runs = PayrollRun.objects.filter(business=ctx.business, period_start__lte=ctx.end, period_end__gte=ctx.start)
    agg = runs.aggregate(gross=Sum("total_gross_pay"), net=Sum("total_net_pay"), cost=Sum("total_employer_cost"), n=Count("pk"))
    members = UserBusiness.objects.filter(business=ctx.business, active=True)
    by_role = members.values("role__key").annotate(n=Count("pk")).order_by("-n")
    recent = runs.order_by("-pay_date", "-pk")[:10]
    return {
        "kpis": [kpi("Active team members", members.count()), kpi("Payroll runs", agg["n"], ctx.label), kpi("Gross pay", money(ctx, agg["gross"])),
                 kpi("Net pay", money(ctx, agg["net"])), kpi("Employer cost", money(ctx, agg["cost"]))],
        "charts": [],
        "tables": [
            table("Team by role", ["Role", "Members"], [[pretty(r["role__key"]), r["n"]] for r in by_role]),
            table("Payroll runs", ["Period", "Pay date", "Staff", "Gross", "Employer cost"],
                  [[f"{r.period_start} – {r.period_end}", r.pay_date or "—", r.staff_count, money(ctx, r.total_gross_pay), money(ctx, r.total_employer_cost)] for r in recent]),
        ],
    }


def audit_summary(ctx):
    from .models import AuditLog, AuditQuery
    queries = AuditQuery.objects.filter(business=ctx.business)
    by_status = queries.values("status").annotate(n=Count("pk")).order_by("-n")
    by_severity = queries.exclude(status="closed").values("severity").annotate(n=Count("pk")).order_by("-n")
    by_module = queries.exclude(status="closed").values("module").annotate(n=Count("pk")).order_by("-n")
    logs = AuditLog.objects.filter(business=ctx.business, created_at__date__range=(ctx.start, ctx.end))
    by_action = logs.values("action").annotate(n=Count("pk")).order_by("-n")[:12]
    recent = logs.order_by("-created_at")[:12]
    return {
        "kpis": [kpi("Open queries", queries.exclude(status="closed").count()), kpi("All queries", queries.count()),
                 kpi("Audit entries", logs.count(), ctx.label)],
        "charts": [chart("Activity by action", [pretty(r["action"]) for r in by_action], [("Entries", [r["n"] for r in by_action])])] if by_action else [],
        "tables": [
            table("Queries by status", ["Status", "Queries"], [[pretty(r["status"]), r["n"]] for r in by_status]),
            table("Open queries by severity", ["Severity", "Queries"], [[pretty(r["severity"]), r["n"]] for r in by_severity]),
            table("Open queries by module", ["Module", "Queries"], [[pretty(r["module"]), r["n"]] for r in by_module]),
            table("Recent activity", ["When", "Action", "Record", "Description"],
                  [[timezone.localtime(r.created_at).strftime("%d %b %H:%M"), pretty(r.action), r.model_name, r.description] for r in recent]),
        ],
    }


def ledger_status(ctx):
    from . import ledger
    cutover = ledger.latest_cutover(ctx.business)
    if not cutover:
        return {"kpis": [kpi("Ledger split", "Not configured")], "tables": [], "charts": [],
                "message": "The business keeps a single ledger. A business admin can split Live from a Backlog on the Ledger setup page."}
    health = ledger.backlog_negative_dates(ctx.business)
    drift = ledger.integrity_report(ctx.business)
    positions = ledger.positions_map(ctx.business, "live")
    provisional = sum(1 for p in positions.values() if p.source == "derived")
    names = {}
    if health or drift:
        from inventory.models import FinishedGood, RawMaterial
        names = {("raw", pk): n for pk, n in RawMaterial.raw_objects.filter(business=ctx.business).values_list("pk", "name")}
        names.update({("fg", pk): n for pk, n in FinishedGood.raw_objects.filter(business=ctx.business).values_list("pk", "name")})
    return {
        "kpis": [kpi("Status", cutover.get_status_display()), kpi("Live starts", cutover.cutover_date.strftime("%d %b %Y")),
                 kpi("Backlog items dipping below zero", len(health), "Usually missing entries"), kpi("Stock figures off ledger", len(drift)),
                 kpi("Provisional openings", provisional, "Not yet confirmed by a count")],
        "charts": [],
        "tables": [
            table("Backlog items going negative", ["Item", "First negative", "Days negative"],
                  [[names.get(key, key[1]), days[0].isoformat(), len(days)] for key, days in sorted(health.items(), key=lambda kv: kv[1][0])[:20]]),
            table("Stock differing from opening + movements", ["Item", "Stored", "Expected", "Drift"],
                  [[names.get((r["kind"], r["id"]), r["id"]), num(r["stored"], 2), num(r["expected"], 2), num(r["drift"], 2)] for r in drift[:20]]),
        ],
    }
