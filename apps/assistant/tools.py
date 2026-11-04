"""Permission-filtered, read-only tools for the staff assistant.

Boundaries enforced here (not by the model, not by the schema alone):

* The business and the user come from the authenticated request; no tool accepts
  a business or user identifier. Record ids are re-queried inside that business.
* A tool is offered only if the member can view its module, and the same check
  runs again at execution time.
* Arguments are validated against the tool's own strict schema; unknown keys are
  rejected.
* Tools only read, through the existing domain helpers (Daily brief, Finance,
  stock, production variance). The model never gets SQL, Python or free URLs.
* Text that originates in business records (customer names, notes) is returned
  as bounded data and labelled untrusted by the engine; it is never instructions.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable

from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from accounts.services import user_has_permission
from core import daily_brief, ledger
from . import help as help_docs

MAX_ROWS = 10
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class ToolError(ValueError):
    """A problem the member (or model) can fix by asking differently."""


@dataclass(frozen=True)
class Context:
    user: object
    business: object
    today: date = field(default_factory=timezone.localdate)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    module: str | None
    parameters: dict
    handler: Callable


@dataclass
class ToolResult:
    name: str
    data: dict
    text: str
    sources: list
    scope: str
    as_of: str


def _clean(value, limit=80):
    return _CONTROL.sub(" ", str(value or "")).strip()[:limit]


def _scope():
    return str(ledger.current_scope() or "live").capitalize()


def validate_arguments(schema, raw):
    """Strictly validate model-supplied arguments against ``schema``; return clean values."""
    if raw in (None, ""):
        raw = {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise ToolError("Tool arguments were not valid JSON.")
    if not isinstance(raw, dict):
        raise ToolError("Tool arguments must be an object.")
    props = schema.get("properties", {})
    extra = set(raw) - set(props)
    if extra:
        raise ToolError(f"Unknown argument: {sorted(extra)[0]}.")
    clean = {}
    for key, spec in props.items():
        if key not in raw or raw[key] in (None, ""):
            if key in schema.get("required", []):
                raise ToolError(f"Missing required argument: {key}.")
            continue
        value = raw[key]
        kind = spec.get("type")
        if kind == "integer":
            if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).lstrip("-").isdigit():
                raise ToolError(f"{key} must be a whole number.")
            value = int(value)
            if value < spec.get("minimum", -10**12) or value > spec.get("maximum", 10**12):
                raise ToolError(f"{key} is out of range.")
        elif kind == "string":
            if not isinstance(value, str):
                raise ToolError(f"{key} must be text.")
            value = value.strip()
            if len(value) > spec.get("maxLength", 200) or len(value) < spec.get("minLength", 0):
                raise ToolError(f"{key} has an invalid length.")
            if spec.get("format") == "date":
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise ToolError(f"{key} must be a date like 2026-10-31.")
            if "enum" in spec and value not in spec["enum"]:
                raise ToolError(f"{key} must be one of: {', '.join(spec['enum'])}.")
        else:  # pragma: no cover - schemas are defined in this file
            raise ToolError(f"Unsupported argument type for {key}.")
        clean[key] = value
    return clean


def _result(ctx, name, data, text, sources, as_of=None):
    return ToolResult(name=name, data=data, text=text, sources=sources, scope=_scope(),
                      as_of=as_of or timezone.localtime().strftime("%Y-%m-%d %H:%M"))


def _money(ctx, value):
    return daily_brief._money(ctx.business, value)


# --------------------------------------------------------------------------
# Handlers. Each takes (ctx, args) with args already validated.
# --------------------------------------------------------------------------

def _daily_brief(ctx, args):
    day, note = daily_brief.parse_day(args.get("date"), ctx.today)
    brief = daily_brief.build_brief(ctx.business, day)
    m = lambda v: _money(ctx, v)
    data = {
        "date": day.isoformat(), "note": note,
        "basis": "Sales and expenses are for the date. Cash, owed, owing and stock are current balances.",
        "sales_total": m(brief["sales_total"]), "sales_count": brief["sales_count"],
        "expenses_total": m(brief["expenses_total"]), "expenses_count": brief["expenses_count"],
        "cash_on_hand_now": m(brief["cash"]), "owed_to_you_now": m(brief["receivables"]),
        "you_owe_now": m(brief["payables"]),
        "top_debtors": [{"customer": _clean(n), "balance": m(a)} for n, a in brief["top_debtors"]],
        "items_low_on_stock_now": brief["low_stock"], "orders_awaiting_approval": brief["pending_orders"],
        "ingredients_off_recipe_this_month": brief["recipe_review"], "backlog_open": brief["backlog_open"],
    }
    url = reverse("daily_brief") + f"?date={day.isoformat()}"
    return _result(ctx, "get_daily_brief", data, daily_brief.render_text(brief, ctx.today).replace("*", ""),
                   [{"label": f"Daily brief for {day:%d %b %Y}", "url": url}])


def _weekly_summary(ctx, args):
    start, note = daily_brief.parse_week(args.get("date"), ctx.today)
    brief = daily_brief.build_weekly_brief(ctx.business, start, ctx.today)
    pl, m = brief["pl"], (lambda v: _money(ctx, v))
    data = {
        "week": daily_brief.week_label(brief["start"], brief["end"]), "note": note, "partial_week": brief["partial"],
        "basis": "Revenue counts fully paid sales (cash basis), like Finance > Profit & loss. Books recognise sales when recorded and can differ.",
        "revenue": m(pl["revenue"]), "cost_of_goods": m(pl["cogs"]), "gross_profit": m(pl["gross_profit"]),
        "expenses": m(pl["operating_expenses"]), "payroll": m(pl["payroll"]), "supplies_and_write_offs": m(brief["other_costs"]),
        "net_profit": m(pl["net_profit"]), "net_margin_pct": pl["net_margin"], "sales_recorded": brief["sales_count"],
        "revenue_vs_comparison": daily_brief.trend(ctx.business, brief["revenue_change"], True),
        "net_vs_comparison": daily_brief.trend(ctx.business, brief["net_change"], False),
        "unpaid_sales_not_in_revenue": m(pl["unpaid_sales"]), "unpaid_expenses_included": m(pl["unpaid_expenses"]),
    }
    url = reverse("daily_brief") + f"?view=weekly&date={brief['start'].isoformat()}"
    return _result(ctx, "get_weekly_summary", data, daily_brief.render_weekly_text(brief).replace("*", ""),
                   [{"label": f"Weekly summary, {data['week']}", "url": url},
                    {"label": "Finance › Profit & loss", "url": reverse("finance_profit_loss")}])


def _stock_health(ctx, args):
    from inventory.models import FinishedGood, RawMaterial
    limit = args.get("limit", MAX_ROWS)
    rows = []
    for m in RawMaterial.objects.filter(business=ctx.business, reorder_level__gt=0, stock__lte=F("reorder_level")).values(
            "name", "stock", "reorder_level", "usage_unit")[:200]:
        rows.append({"kind": "raw material", "name": _clean(m["name"]), "stock": m["stock"], "reorder_level": m["reorder_level"], "unit": _clean(m["usage_unit"], 20)})
    for g in FinishedGood.objects.filter(business=ctx.business, stock__isnull=False, reorder_level__gt=0, stock__lte=F("reorder_level")).values(
            "name", "stock", "reorder_level", "unit")[:200]:
        rows.append({"kind": "product", "name": _clean(g["name"]), "stock": g["stock"], "reorder_level": g["reorder_level"], "unit": _clean(g["unit"], 20)})
    rows.sort(key=lambda r: (r["stock"] / r["reorder_level"]) if r["reorder_level"] else 0)
    items = [{**r, "stock": f"{r['stock']:g}", "reorder_level": f"{r['reorder_level']:g}"} for r in rows[:limit]]
    data = {"items_at_or_below_reorder_level": len(rows), "shown": len(items), "items": items,
            "basis": "Current recorded stock against each item's reorder level. This is not a depletion forecast: no usage-rate method is applied."}
    lines = [f"{len(rows)} item{'s' if len(rows) != 1 else ''} at or below reorder level."] if rows else ["Nothing is at or below its reorder level."]
    lines += [f"• {i['name']} ({i['kind']}): {i['stock']} {i['unit']} left, reorder at {i['reorder_level']}" for i in items]
    return _result(ctx, "get_stock_health", data, "\n".join(lines), [{"label": "Inventory", "url": reverse("inventory")}])


def _open_items(ctx):
    from core.finance_views import _finance_open_items
    return _finance_open_items(ctx.business)


def _receivables(ctx, args):
    items = _open_items(ctx)
    owing = {}
    for row in items["outstanding_sales"]:
        name = _clean(row["sale"].customer or "Walk-in")
        owing[name] = owing.get(name, 0) + row["balance"]
    ranked = sorted(owing.items(), key=lambda kv: -kv[1])
    shown = ranked[:args.get("limit", MAX_ROWS)]
    data = {"total_owed_to_you": _money(ctx, items["receivables"]), "customers_owing": len(ranked),
            "shown": len(shown), "customers": [{"customer": n, "balance": _money(ctx, a)} for n, a in shown],
            "basis": "Unpaid balances on recorded sales as of now. Not revenue and not cash received."}
    lines = [f"Owed to you: {data['total_owed_to_you']} across {len(ranked)} customer{'s' if len(ranked) != 1 else ''}."]
    lines += [f"• {c['customer']}: {c['balance']}" for c in data["customers"]]
    return _result(ctx, "get_receivables", data, "\n".join(lines), [{"label": "Finance overview", "url": reverse("finance_dashboard")}])


def _payables(ctx, args):
    items = _open_items(ctx)
    pos = items["outstanding_pos"]
    data = {"total_you_owe": _money(ctx, items["payables"]), "supplier_purchases": _money(ctx, items["purchase_payables"]),
            "unpaid_expenses": _money(ctx, items["expense_payables"]), "unpaid_expense_count": items["expense_payable_count"],
            "purchase_orders_with_balance": len(list(pos)),
            "basis": "Unpaid supplier purchases and unpaid expenses as of now."}
    text = (f"You owe {data['total_you_owe']}: {data['supplier_purchases']} to suppliers "
            f"and {data['unpaid_expenses']} in unpaid expenses.")
    return _result(ctx, "get_payables", data, text, [{"label": "Finance overview", "url": reverse("finance_dashboard")}])


def _order_status(ctx, args):
    from production.models import Order
    if args.get("order_id"):
        order = Order.objects.filter(business=ctx.business, pk=args["order_id"]).first()
        if order is None:
            raise ToolError("No order with that number was found in this business.")
        data = {"order_id": order.pk, "date": order.date.isoformat(), "status": order.status,
                "type": _clean(order.order_type, 30), "customer": _clean(order.customer_name), "lines": order.items.count()}
        text = f"Order #{order.pk} ({data['date']}) is {order.status}, {data['lines']} line(s)."
        return _result(ctx, "get_order_status", data, text, [{"label": f"Order #{order.pk}", "url": reverse("order_detail", args=[order.pk])}])
    status = args.get("status", "pending")
    qs = Order.objects.filter(business=ctx.business, status=status).order_by("-date", "-pk")
    total = qs.count()
    rows = [{"order_id": o.pk, "date": o.date.isoformat(), "customer": _clean(o.customer_name)} for o in qs[:args.get("limit", MAX_ROWS)]]
    data = {"status": status, "orders_with_status": total, "shown": len(rows), "orders": rows}
    lines = [f"{total} order{'s' if total != 1 else ''} {status}."] + [f"• #{r['order_id']} · {r['date']} · {r['customer'] or 'no customer name'}" for r in rows]
    return _result(ctx, "get_order_status", data, "\n".join(lines), [{"label": "Production orders", "url": reverse("orders_list")}])


def _run_variance(ctx, args):
    from production.variance import variance_report
    end = date.fromisoformat(args["end"]) if args.get("end") else ctx.today
    start = date.fromisoformat(args["start"]) if args.get("start") else end.replace(day=1)
    if start > end:
        raise ToolError("The start date must not be after the end date.")
    if (end - start).days > 366:
        raise ToolError("Choose a range of one year or less.")
    report = variance_report(ctx.business, start, end)
    review = sorted((r for r in report["ingredients"] if r["status"] == "review"), key=lambda r: -abs(r["variance_value"]))[:5]
    t = report["totals"]
    data = {"from": start.isoformat(), "to": end.isoformat(), "ingredients_to_review": t["review_count"],
            "variance_value": _money(ctx, t["variance_value"]), "shrink_value": _money(ctx, t["shrink_value"]),
            "output_waste_value": _money(ctx, t["waste_value"]),
            "largest_variances": [{"ingredient": _clean(r["name"]), "unit": _clean(r["unit"], 20), "planned": f"{r['planned']:g}",
                                  "actual": f"{r['actual']:g}", "variance_pct": None if r["variance_pct"] is None else f"{r['variance_pct']:g}"} for r in review],
            "basis": "Recipe-planned vs actually released quantities on approved orders. Differences over 5% are flagged for review."}
    lines = [f"{start:%d %b} – {end:%d %b %Y}: {t['review_count']} ingredient(s) to review."]
    lines += [f"• {r['ingredient']}: planned {r['planned']} {r['unit']}, actual {r['actual']}" for r in data["largest_variances"]]
    return _result(ctx, "get_run_variance", data, "\n".join(lines), [{"label": "Recipe vs Actual", "url": "/orders/variance/"}])


def _search_help(ctx, args):
    hits = help_docs.search(args["query"])
    data = {"matches": hits, "basis": "Reviewed INPROFIC documentation. It explains how things work; it holds no business data."}
    text = "\n\n".join(f"{h['document']} › {h['section']}\n{h['excerpt']}" for h in hits) or "I could not find that in the help documentation."
    return _result(ctx, "search_help", data, text, [{"label": f"{h['document']} › {h['section']}", "url": ""} for h in hits])


PERIOD_LABELS = {"today": "Today", "yesterday": "Yesterday", "this_week": "This week", "last_week": "Last week",
                 "this_month": "This month", "last_month": "Last month"}


def period_range(period, today):
    """Inclusive (start, end) for a named period. Weeks run Monday to Sunday; months are calendar months."""
    if period == "today":
        return today, today
    if period == "yesterday":
        return today - timedelta(days=1), today - timedelta(days=1)
    monday = today - timedelta(days=today.weekday())
    if period == "this_week":
        return monday, today
    if period == "last_week":
        return monday - timedelta(days=7), monday - timedelta(days=1)
    first = today.replace(day=1)
    if period == "this_month":
        return first, today
    if period == "last_month":
        last_day = first - timedelta(days=1)
        return last_day.replace(day=1), last_day
    raise ToolError("Unknown period.")


def _pl(ctx, period):
    from core.accounting import daily_components, summarise
    start, end = period_range(period, ctx.today)
    return start, end, summarise(daily_components(ctx.business, start, end), start, end)


def _range_text(period, start, end):
    label = PERIOD_LABELS[period]
    return f"{label} ({start:%d %b})" if start == end else f"{label} ({start:%d %b} – {end:%d %b %Y})"


def _sales_summary(ctx, args):
    from sales.models import Sale
    period = args.get("period", "today")
    start, end, pl = _pl(ctx, period)
    m = lambda v: _money(ctx, v)
    recorded = pl["revenue"] + pl["unpaid_sales"]
    count = Sale.objects.filter(business=ctx.business, date__range=(start, end)).count()
    data = {"period": PERIOD_LABELS[period], "from": start.isoformat(), "to": end.isoformat(),
            "sales_recorded": m(recorded), "sales_count": count,
            "average_sale": m(recorded / count) if count else m(0),
            "paid_sales_revenue": m(pl["revenue"]), "unpaid_sales_not_yet_revenue": m(pl["unpaid_sales"]),
            "basis": "Recorded sales include unpaid ones. Revenue counts only fully paid sales, as in Finance > Profit & loss."}
    lines = [f"{_range_text(period, start, end)}: {count} sale{'s' if count != 1 else ''} recorded, worth {m(recorded)}."]
    if count:
        lines.append(f"• Paid: {m(pl['revenue'])}")
        if pl["unpaid_sales"]:
            lines.append(f"• Unpaid (not revenue yet): {m(pl['unpaid_sales'])}")
        lines.append(f"• Average sale: {data['average_sale']}")
    return _result(ctx, "get_sales_summary", data, "\n".join(lines),
                   [{"label": "Sales", "url": reverse("sales_list")}, {"label": "Daily brief", "url": reverse("daily_brief") + f"?date={end.isoformat()}"}])


def _top_products(ctx, args):
    from sales.models import SaleItem
    from core.accounting import _sale_expressions
    from django.db.models import Sum
    period, limit = args.get("period", "this_month"), args.get("limit", 5)
    start, end = period_range(period, ctx.today)
    _money_field, line_total, _cogs, units = _sale_expressions(prefix="")
    rows = list(SaleItem.objects.filter(sale__business=ctx.business, sale__date__range=(start, end))
                .values("finished_good__name").annotate(revenue=Sum(line_total), qty=Sum(units)).order_by("-revenue")[:limit])
    items = [{"product": _clean(r["finished_good__name"]), "sales_value": _money(ctx, r["revenue"] or 0), "units_sold": f"{(r['qty'] or 0):g}"} for r in rows]
    data = {"period": PERIOD_LABELS[period], "from": start.isoformat(), "to": end.isoformat(), "products": items,
            "basis": "Ranked by recorded sales value (paid and unpaid) in the period."}
    lines = [f"Best sellers, {_range_text(period, start, end)}:"] if items else [f"No sales recorded for {_range_text(period, start, end)}."]
    lines += [f"{i}. {x['product']}: {x['sales_value']} ({x['units_sold']} units)" for i, x in enumerate(items, 1)]
    return _result(ctx, "get_top_products", data, "\n".join(lines), [{"label": "Sales", "url": reverse("sales_list")}])


def _expenses_summary(ctx, args):
    period = args.get("period", "this_month")
    start, end, pl = _pl(ctx, period)
    m = lambda v: _money(ctx, v)
    cats = [{"category": _clean(line["label"]), "amount": m(line["amount"])} for line in pl["expense_lines"][:MAX_ROWS]]
    data = {"period": PERIOD_LABELS[period], "from": start.isoformat(), "to": end.isoformat(),
            "operating_expenses": m(pl["operating_expenses"]), "of_which_unpaid": m(pl["unpaid_expenses"]),
            "payroll_separate": m(pl["payroll"]), "categories": cats,
            "basis": "Expenses recorded in the period, paid or not. Payroll and stock supplies are reported separately."}
    lines = [f"{_range_text(period, start, end)}: expenses {m(pl['operating_expenses'])}"
             + (f", of which {m(pl['unpaid_expenses'])} still unpaid." if pl["unpaid_expenses"] else ".")]
    lines += [f"• {c['category']}: {c['amount']}" for c in cats[:5]]
    return _result(ctx, "get_expenses_summary", data, "\n".join(lines), [{"label": "Expenses", "url": reverse("expenses_list")}])


def _profit_summary(ctx, args):
    period = args.get("period", "this_month")
    start, end, pl = _pl(ctx, period)
    m = lambda v: _money(ctx, v)
    other = pl["supplies"] + pl["write_offs"]
    data = {"period": PERIOD_LABELS[period], "from": start.isoformat(), "to": end.isoformat(),
            "revenue": m(pl["revenue"]), "cost_of_goods": m(pl["cogs"]), "gross_profit": m(pl["gross_profit"]),
            "expenses": m(pl["operating_expenses"]), "payroll": m(pl["payroll"]), "supplies_and_write_offs": m(other),
            "net_profit": m(pl["net_profit"]), "net_margin_pct": pl["net_margin"],
            "unpaid_sales_not_in_revenue": m(pl["unpaid_sales"]),
            "basis": "Revenue counts fully paid sales (cash basis), like Finance > Profit & loss. Books recognise sales when recorded and can differ."}
    word = "loss" if pl["net_profit"] < 0 else "profit"
    lines = [f"{_range_text(period, start, end)}: net {word} {m(abs(pl['net_profit']))}."]
    lines += [f"• Revenue (paid sales): {m(pl['revenue'])}", f"• Cost of goods: {m(pl['cogs'])}", f"• Expenses: {m(pl['operating_expenses'])}"]
    if pl["payroll"]:
        lines.append(f"• Payroll: {m(pl['payroll'])}")
    if pl["unpaid_sales"]:
        lines.append(f"Not counted yet: {m(pl['unpaid_sales'])} of unpaid sales.")
    return _result(ctx, "get_profit_summary", data, "\n".join(lines), [{"label": "Finance > Profit & loss", "url": reverse("finance_profit_loss")}])


def _cash_position(ctx, args):
    from core.finance_views import _cash_account_balances
    accounts = list(_cash_account_balances(ctx.business, active_only=True))
    rows = [{"account": _clean(a.name), "balance": _money(ctx, a._calculated_balance)} for a in accounts[:MAX_ROWS]]
    total = sum((a._calculated_balance for a in accounts), 0)
    data = {"total_cash_now": _money(ctx, total), "accounts": rows, "basis": "Calculated balance of active cash accounts right now."}
    lines = [f"Cash on hand: {data['total_cash_now']} across {len(accounts)} account{'s' if len(accounts) != 1 else ''}."]
    lines += [f"• {r['account']}: {r['balance']}" for r in rows]
    return _result(ctx, "get_cash_position", data, "\n".join(lines), [{"label": "Finance overview", "url": reverse("finance_dashboard")}])


def _overdue_receivables(ctx, args):
    from core.financial_pack import _aging
    items = _open_items(ctx)
    buckets = _aging(items["outstanding_sales"], ctx.today, lambda r: r["sale"].date, lambda r: r["balance"])
    rows = [{"age": b["label"], "balance": _money(ctx, b["total"]), "sales": b["count"]} for b in buckets]
    data = {"total_owed_to_you": _money(ctx, items["receivables"]), "by_age": rows,
            "basis": "Unpaid sale balances grouped by how long ago the sale was recorded, as of today."}
    lines = [f"Owed to you: {data['total_owed_to_you']}, by age:"] + [f"• {r['age']}: {r['balance']} ({r['sales']})" for r in rows]
    return _result(ctx, "get_overdue_receivables", data, "\n".join(lines), [{"label": "Financial pack: ageing", "url": reverse("finance_pack")}])


def _pending_approvals(ctx, args):
    from procurement.models import PurchaseOrder
    from production.models import Order
    from procurement.services import request_count
    from django.db.models import Q
    pending = PurchaseOrder.objects.filter(business=ctx.business, status__in=['draft', 'ordered'])
    heads = list(pending.order_by('-date', '-pk').values('pk', 'request_id')[:MAX_ROWS])
    group_ids = {row['request_id'] for row in heads if row['request_id']}
    standalone = [row['pk'] for row in heads if not row['request_id']]
    requests = list(pending.filter(Q(request_id__in=group_ids) | Q(pk__in=standalone))
                    .prefetch_related('items').order_by('-date', '-pk'))
    waiting = request_count(pending)
    groups = {}
    for po in requests:
        groups.setdefault(('request', po.request_id) if po.request_id else ('po', po.pk), []).append(po)
    rows = [{'request_id': min(po.pk for po in vendors), 'date': vendors[0].date.isoformat(),
             'supplier': ' · '.join(_clean(po.supplier) or 'Supplier not named' for po in vendors),
             'total': _money(ctx, sum((po.total for po in vendors), Decimal('0')))} for vendors in groups.values()]
    production = Order.objects.filter(business=ctx.business, status="pending").count()
    data = {"purchase_requests_waiting": waiting, "shown": len(rows), "purchase_requests": rows, "production_orders_waiting": production}
    lines = [f"Waiting for approval: {waiting} purchase request{'s' if waiting != 1 else ''}, {production} production order{'s' if production != 1 else ''}."]
    lines += [f"• Request #{r['request_id']} · {r['supplier']} · {r['total']}" for r in rows]
    sources = [{"label": f"Request #{r['request_id']}", "url": reverse("po_approve", args=[r["request_id"]])} for r in rows[:3]]
    return _result(ctx, "get_pending_approvals", data, "\n".join(lines), sources + [{"label": "Production orders", "url": reverse("orders_list")}])


def _vat_position(ctx, args):
    from core.tax import vat_return
    period = args.get("period", "this_month")
    start, end = period_range(period, ctx.today)
    vat = vat_return(ctx.business, start, end)
    if not vat["registered"]:
        data = {"registered": False, "basis": "VAT is not switched on for this business in Tax & VAT settings."}
        return _result(ctx, "get_vat_position", data, "VAT is not switched on for this business, so there is no VAT position to report.",
                       [{"label": "Tax & VAT", "url": reverse("tax_home")}])
    m = lambda v: _money(ctx, v)
    data = {"period": PERIOD_LABELS[period], "from": start.isoformat(), "to": end.isoformat(), "rate_pct": f"{vat['rate']:g}",
            "output_vat": m(vat["output_vat"]), "input_vat_claimable": m(vat["input_vat"]), "net_vat": m(abs(vat["net_vat"])),
            "direction": "refundable" if vat["refundable"] else "payable", "due_date": vat["due_date"].isoformat(),
            "basis": "Prices are treated as VAT-inclusive and standard-rated sales only. An estimate from recorded sales; confirm with your tax authority."}
    text = (f"{_range_text(period, start, end)}: output VAT {m(vat['output_vat'])}, input VAT {m(vat['input_vat'])}, "
            f"net {m(abs(vat['net_vat']))} {data['direction']}, due {vat['due_date']:%d %b %Y}.")
    return _result(ctx, "get_vat_position", data, text, [{"label": "Tax & VAT", "url": reverse("tax_home")}])


_LIMIT = {"limit": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS}}
_DATE = {"type": "string", "format": "date", "maxLength": 10}


def _period(*allowed):
    return {"type": "object", "properties": {"period": {"type": "string", "enum": list(allowed)}}}


_ALL = ("today", "yesterday", "this_week", "last_week", "this_month", "last_month")
_RANGES = ("this_week", "last_week", "this_month", "last_month")

TOOLS = {t.name: t for t in (
    Tool("get_daily_brief", "Sales, expenses, cash, who owes you, low stock and orders awaiting approval for one date (default yesterday).",
         "dashboard", {"type": "object", "properties": {"date": {**_DATE, "description": "YYYY-MM-DD; omit for yesterday"}}}, _daily_brief),
    Tool("get_weekly_summary", "Profit and loss for a Monday-Sunday week with comparison to the previous week (default last completed week).",
         "finance", {"type": "object", "properties": {"date": {**_DATE, "description": "Any date in the week; omit for last week"}}}, _weekly_summary),
    Tool("get_sales_summary", "Sales recorded in a named period: count, total, paid vs unpaid, average sale.",
         "sales", _period(*_ALL), _sales_summary),
    Tool("get_top_products", "Best-selling products by sales value in a named period.",
         "sales", {"type": "object", "properties": {"period": {"type": "string", "enum": list(_RANGES)}, **_LIMIT}}, _top_products),
    Tool("get_expenses_summary", "Operating expenses by category in a named period, and how much is unpaid.",
         "expenses", _period(*_RANGES), _expenses_summary),
    Tool("get_profit_summary", "Profit and loss (revenue, costs, net profit) for a named week or month.",
         "finance", _period(*_RANGES), _profit_summary),
    Tool("get_cash_position", "Current balance of each active cash account and the total.",
         "finance", {"type": "object", "properties": {}}, _cash_position),
    Tool("get_stock_health", "Raw materials and products at or below their reorder level.",
         "inventory", {"type": "object", "properties": _LIMIT}, _stock_health),
    Tool("get_receivables", "Customers with unpaid balances and the total owed to the business.",
         "finance", {"type": "object", "properties": _LIMIT}, _receivables),
    Tool("get_overdue_receivables", "Unpaid customer balances grouped by age.",
         "finance", {"type": "object", "properties": {}}, _overdue_receivables),
    Tool("get_payables", "What the business owes suppliers and in unpaid expenses.",
         "finance", {"type": "object", "properties": {}}, _payables),
    Tool("get_pending_approvals", "Purchase requests and production orders waiting for approval.",
         "procurement", {"type": "object", "properties": {}}, _pending_approvals),
    Tool("get_order_status", "Production orders by status, or one order by its number.",
         "production", {"type": "object", "properties": {"order_id": {"type": "integer", "minimum": 1},
                        "status": {"type": "string", "enum": ["pending", "approved", "completed", "rejected", "reversed"]}, **_LIMIT}},
         _order_status),
    Tool("get_run_variance", "Recipe-planned vs actual ingredient use and waste for a date range (default this month).",
         "production", {"type": "object", "properties": {"start": _DATE, "end": _DATE}}, _run_variance),
    Tool("get_vat_position", "Estimated VAT payable or refundable for this or last month.",
         "finance", _period("this_month", "last_month"), _vat_position),
    Tool("search_help", "Look up how INPROFIC works in the reviewed help documentation.",
         None, {"type": "object", "properties": {"query": {"type": "string", "minLength": 2, "maxLength": 120}}, "required": ["query"]},
         _search_help),
)}


def available_tools(user, business):
    """Tools this member may use in this business right now."""
    return [t for t in TOOLS.values() if t.module is None or user_has_permission(user, business, t.module, "view")]


def provider_schema(tools):
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": {**t.parameters, "additionalProperties": False}}} for t in tools]


def run_tool(name, raw_args, user, business, today=None):
    """Execute one tool for this member. Re-checks permission; validates arguments."""
    tool = TOOLS.get(name)
    if tool is None or tool not in available_tools(user, business):
        raise ToolError("That information is not available to you.")
    args = validate_arguments(tool.parameters, raw_args)
    ctx = Context(user=user, business=business, today=today or timezone.localdate())
    return tool.handler(ctx, args), args
