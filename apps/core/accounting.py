"""Profit and loss accounting for a business's reporting period.

Basis (kept consistent with the rest of INPROFIC):

* Revenue is recognised on **paid** sales, as on the dashboard. Unpaid and
  partly-paid sales are reported only as a memo (receivables), never as revenue.
* Cost of goods sold is the frozen unit cost of the items on those paid sales.
* Operating expenses are recognised when **incurred** (every Expense row, paid
  or unpaid), so a bill you owe still reduces profit.
* Payroll is the employer cost of issued payslips (gross pay plus employer
  contributions) on the payroll run's pay date.
* Operating supplies used and stock write-offs/losses are real costs that never
  pass through an Expense row, so they are included as separate lines.

Buying stock is *not* an expense: it is an asset until it is sold (COGS) or
used/written off. Everything here is business-scoped explicitly.
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Case, DecimalField, ExpressionWrapper, F, Sum, Value, When
from django.db.models.functions import Coalesce

ZERO = Decimal("0")
CENT = Decimal("0.01")

PERIOD_MONTHS = {"year": 12, "half": 6, "quarter": 3, "month": 1}
PERIOD_LABELS = {"year": "Business year", "half": "Half year", "quarter": "Quarter", "month": "Month"}
# Stock adjustment reasons that are genuine losses/gains to the business. Returns
# to or from a supplier/customer are handled by their own documents.
WRITE_OFF_REASONS = ("count", "wastage", "damage", "internal", "charity", "staff", "other")


def _money(value):
    return (value or ZERO).quantize(CENT)


def add_months(day, months):
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def fiscal_period(business, anchor=None, kind=None):
    """The reporting period containing ``anchor`` (default today).

    Periods tile the business year starting in ``fiscal_year_start_month`` and
    run for a year, half year or quarter as set on the business. ``kind`` lets a
    screen ask for another length (a month, quarter or year) for comparisons.
    """
    anchor = anchor or date.today()
    kind = kind if kind in PERIOD_MONTHS else (business.profit_period if business.profit_period in PERIOD_MONTHS else "year")
    length = PERIOD_MONTHS[kind]
    start_month = min(max(int(business.fiscal_year_start_month or 1), 1), 12)
    year_start = date(anchor.year if anchor.month >= start_month else anchor.year - 1, start_month, 1)
    offset = (anchor.year - year_start.year) * 12 + anchor.month - year_start.month
    index = offset // length
    start = add_months(year_start, index * length)
    end = add_months(start, length) - timedelta(days=1)
    number = index + 1
    if kind == "month":
        short, label = f"{start:%b}", f"{start:%B %Y}"
    else:
        short = {"year": "Full year", "half": f"H{number}", "quarter": f"Q{number}"}[kind]
        span = f"{start:%b %Y} – {end:%b %Y}" if start.year != end.year or kind == "year" else f"{start:%b} – {end:%b %Y}"
        label = f"{short} · {span}"
    return {
        "kind": kind, "kind_label": PERIOD_LABELS[kind], "start": start, "end": end, "index": number,
        "short": short, "label": label, "months": length,
    }


def shift_period(business, period, steps):
    """The period ``steps`` periods before (negative) or after (positive)."""
    return fiscal_period(business, add_months(period["start"], steps * period["months"]), kind=period["kind"])


def _sale_expressions(prefix="items__"):
    """Revenue, cost and unit expressions. ``prefix`` is the path to the sale
    item: ``items__`` from a Sale queryset, ``""`` from a SaleItem queryset.
    Mirrors SaleItem.line_total and SaleItem.total_units so database aggregates
    agree with the figures shown on sales screens."""
    money_field = DecimalField(max_digits=24, decimal_places=6)
    units_field = DecimalField(max_digits=20, decimal_places=4)

    def f(name):
        return F(f"{prefix}{name}")

    units_per_batch = Case(
        When(**{f"{prefix}finished_good__units_per_batch__gt": 0}, then=f("finished_good__units_per_batch")),
        default=Value(Decimal("1")), output_field=units_field,
    )
    total_units = ExpressionWrapper(f("batch_qty") * units_per_batch + f("piece_qty"), output_field=units_field)
    standard = ExpressionWrapper(total_units * (f("price") - f("discount")), output_field=money_field)
    commercial = ExpressionWrapper(f("commercial_quantity") * f("commercial_unit_price"), output_field=money_field)
    line_total = Case(
        When(**{f"{prefix}commercial_quantity__isnull": False, f"{prefix}commercial_unit_price__isnull": False}, then=commercial),
        default=standard, output_field=money_field,
    )
    cogs = ExpressionWrapper(
        Coalesce(f("unit_cost"), Value(ZERO), output_field=money_field) * total_units, output_field=money_field,
    )
    return money_field, line_total, cogs, total_units


def daily_components(business, start, end):
    """Every profit-and-loss component grouped by date for ``start``..``end``.

    Returned once so a period total and any number of chart buckets are all
    built from the same query results.
    """
    from accounts.models import Payslip
    from expenses.models import Expense
    from inventory.models import OperationalSupplyDispense, StockAdjustment
    from sales.models import Sale

    money_field, line_total, cogs, _units = _sale_expressions()
    revenue, cost_of_goods = defaultdict(Decimal), defaultdict(Decimal)
    for row in (
        Sale.raw_objects.filter(business=business, date__range=(start, end), transaction_type="paid")
        .values("date").annotate(sales=Sum(line_total, output_field=money_field), cogs=Sum(cogs, output_field=money_field))
    ):
        revenue[row["date"]] = row["sales"] or ZERO
        cost_of_goods[row["date"]] = row["cogs"] or ZERO

    expenses = defaultdict(lambda: defaultdict(Decimal))
    unpaid_expenses = defaultdict(Decimal)
    for row in Expense.raw_objects.filter(business=business, date__range=(start, end)).values("date", "category", "payment_status").annotate(total=Sum("amount")):
        expenses[row["category"]][row["date"]] += row["total"] or ZERO
        if row["payment_status"] == "unpaid":
            unpaid_expenses[row["date"]] += row["total"] or ZERO

    payroll = defaultdict(Decimal)
    for row in (
        Payslip.raw_objects.filter(business=business)
        .annotate(cost_date=Coalesce("payroll_run__pay_date", "period_end"))
        .filter(cost_date__range=(start, end)).values("cost_date").annotate(total=Sum("employer_cost"))
    ):
        payroll[row["cost_date"]] = row["total"] or ZERO

    supplies = defaultdict(Decimal)
    for row in (
        OperationalSupplyDispense.raw_objects.filter(business=business, date__range=(start, end))
        .values("date").annotate(total=Sum(ExpressionWrapper(F("quantity") * F("raw_material__cost_per_unit"), output_field=money_field)))
    ):
        supplies[row["date"]] = row["total"] or ZERO

    # A negative adjustment (loss) costs the business; a positive one (found
    # stock) offsets earlier losses, so the cost is the negated value.
    write_offs = defaultdict(Decimal)
    for row in (
        StockAdjustment.raw_objects.filter(business=business, date__range=(start, end), reversed=False, reason__in=WRITE_OFF_REASONS)
        .values("date").annotate(total=Sum(ExpressionWrapper(F("quantity") * F("unit_value"), output_field=money_field)))
    ):
        write_offs[row["date"]] = -(row["total"] or ZERO)

    unpaid_sales = defaultdict(Decimal)
    for sale in Sale.raw_objects.filter(business=business, date__range=(start, end)).exclude(transaction_type="paid").prefetch_related("items__finished_good"):
        unpaid_sales[sale.date] += sale.total

    # Plain dicts (not defaultdicts with lambdas) so the result can be cached.
    return {
        "revenue": dict(revenue), "cogs": dict(cost_of_goods),
        "expenses": {category: dict(by_date) for category, by_date in expenses.items()},
        "unpaid_expenses": dict(unpaid_expenses), "payroll": dict(payroll), "supplies": dict(supplies),
        "write_offs": dict(write_offs), "unpaid_sales": dict(unpaid_sales),
    }


def _total(values, start, end):
    return sum((amount for day, amount in values.items() if start <= day <= end), ZERO)


def _expense_labels():
    from expenses.models import Expense
    return dict(Expense.CATEGORY_CHOICES)


def summarise(components, start, end):
    """Collapse the daily components into one profit-and-loss statement."""
    labels = _expense_labels()
    revenue = _total(components["revenue"], start, end)
    cogs = _total(components["cogs"], start, end)
    gross = revenue - cogs
    expense_lines = []
    for key, by_date in components["expenses"].items():
        amount = _total(by_date, start, end)
        if amount:
            expense_lines.append({"key": key, "label": labels.get(key, key.title()), "amount": _money(amount)})
    expense_lines.sort(key=lambda line: -line["amount"])
    operating = sum((line["amount"] for line in expense_lines), ZERO)
    payroll = _total(components["payroll"], start, end)
    supplies = _total(components["supplies"], start, end)
    write_offs = _total(components["write_offs"], start, end)
    total_costs = cogs + operating + payroll + supplies + write_offs
    net = revenue - total_costs

    def margin(part):
        return float(round(part / revenue * 100, 1)) if revenue else None

    return {
        "start": start, "end": end,
        "revenue": _money(revenue), "cogs": _money(cogs), "gross_profit": _money(gross), "gross_margin": margin(gross),
        "expense_lines": expense_lines, "operating_expenses": _money(operating),
        "payroll": _money(payroll), "supplies": _money(supplies), "write_offs": _money(write_offs),
        "total_costs": _money(total_costs), "net_profit": _money(net), "net_margin": margin(net),
        "unpaid_sales": _money(_total(components["unpaid_sales"], start, end)),
        "unpaid_expenses": _money(_total(components["unpaid_expenses"], start, end)),
    }


def profit_and_loss(business, start, end, components=None):
    return summarise(components or daily_components(business, start, end), start, end)


def bucket_row(components, start, end, label):
    """One chart bucket carrying every component so the UI can total any view."""
    labels_total = summarise(components, start, end)
    return {
        "label": label,
        "sales": float(labels_total["revenue"]), "cogs": float(labels_total["cogs"]),
        "expenses": float(labels_total["operating_expenses"]), "payroll": float(labels_total["payroll"]),
        "supplies": float(labels_total["supplies"]), "write_offs": float(labels_total["write_offs"]),
        "costs": float(labels_total["total_costs"]), "profit": float(labels_total["net_profit"]),
    }


def period_buckets(components, start, end, *, weekly=False):
    """Month (or week) buckets across ``start``..``end`` for the profit chart."""
    rows, cursor = [], start
    while cursor <= end:
        if weekly:
            stop = min(cursor + timedelta(days=6), end)
            label = f"{cursor:%d %b}"
        else:
            stop = min(add_months(date(cursor.year, cursor.month, 1), 1) - timedelta(days=1), end)
            label = f"{cursor:%b %y}" if cursor.month == 1 or not rows else f"{cursor:%b}"
        rows.append(bucket_row(components, cursor, stop, label))
        cursor = stop + timedelta(days=1)
    return rows


# ---------------------------------------------------------------------------
# Comparing periods
# ---------------------------------------------------------------------------

def period_options(business, kind, anchor=None, count=24):
    """Recent periods of ``kind`` (newest first), including the current one."""
    anchor = anchor or date.today()
    current = fiscal_period(business, anchor, kind=kind)
    return [shift_period(business, current, -step) for step in range(count)]


def _share(amount, revenue):
    return float(round(amount / revenue * 100, 1)) if revenue else None


def compare_periods(business, kind, a_anchor, b_anchor):
    """Line-by-line profit and loss for two periods of the same length.

    Each row carries both amounts, the change, and each line's share of revenue
    so the reader sees where money goes differently, not only the net result.
    """
    a = fiscal_period(business, a_anchor, kind=kind)
    b = fiscal_period(business, b_anchor, kind=kind)
    start, end = min(a["start"], b["start"]), max(a["end"], b["end"])
    components = daily_components(business, start, end)
    sa, sb = summarise(components, a["start"], a["end"]), summarise(components, b["start"], b["end"])

    def row(label, key_a, key_b, *, kind_="cost", indent=False):
        change = key_a - key_b
        revenue_a, revenue_b = sa["revenue"], sb["revenue"]
        share_a, share_b = _share(key_a, revenue_a), _share(key_b, revenue_b)
        return {
            "label": label, "kind": kind_, "indent": indent, "a": key_a, "b": key_b, "change": change,
            "change_pct": float(round(change / abs(key_b) * 100, 1)) if key_b else None,
            "share_a": share_a, "share_b": share_b,
            "share_delta": round(share_a - share_b, 1) if share_a is not None and share_b is not None else None,
        }

    categories = {}
    for statement in (sa, sb):
        for line in statement["expense_lines"]:
            categories[line["key"]] = line["label"]
    by_key = lambda statement: {line["key"]: line["amount"] for line in statement["expense_lines"]}
    amounts_a, amounts_b = by_key(sa), by_key(sb)
    rows = [
        row("Revenue (paid sales)", sa["revenue"], sb["revenue"], kind_="income"),
        row("Cost of goods sold", sa["cogs"], sb["cogs"]),
        row("Gross profit", sa["gross_profit"], sb["gross_profit"], kind_="subtotal"),
    ]
    rows += [row(label, amounts_a.get(key, ZERO), amounts_b.get(key, ZERO), indent=True) for key, label in categories.items()]
    rows += [
        row("Payroll (employer cost)", sa["payroll"], sb["payroll"]),
        row("Operating supplies used", sa["supplies"], sb["supplies"]),
        row("Stock write-offs & losses", sa["write_offs"], sb["write_offs"]),
        row("Total costs", sa["total_costs"], sb["total_costs"], kind_="subtotal"),
        row("Net profit", sa["net_profit"], sb["net_profit"], kind_="net"),
    ]
    # Cost lines that took a bigger (or smaller) bite of revenue, largest first.
    movers = sorted(
        (r for r in rows if r["kind"] == "cost" and r["share_delta"] is not None and r["share_delta"] != 0),
        key=lambda r: -abs(r["share_delta"]),
    )[:4]
    return {
        "a": a, "b": b, "statement_a": sa, "statement_b": sb, "rows": rows, "movers": movers,
        "net_margin_delta": (round(sa["net_margin"] - sb["net_margin"], 1) if sa["net_margin"] is not None and sb["net_margin"] is not None else None),
        "gross_margin_delta": (round(sa["gross_margin"] - sb["gross_margin"], 1) if sa["gross_margin"] is not None and sb["gross_margin"] is not None else None),
    }


def margin_trend(business, kind, anchor=None, count=12):
    """Net and gross margin for the last ``count`` periods, oldest first."""
    periods = list(reversed(period_options(business, kind, anchor, count)))
    components = daily_components(business, periods[0]["start"], periods[-1]["end"])
    rows = []
    for period in periods:
        statement = summarise(components, period["start"], period["end"])
        rows.append({
            "start": period["start"], "label": period["short"] if kind != "month" else f"{period['start']:%b %y}",
            "full_label": period["label"], "revenue": statement["revenue"], "costs": statement["total_costs"],
            "net_profit": statement["net_profit"], "net_margin": statement["net_margin"], "gross_margin": statement["gross_margin"],
        })
    return rows


# ---------------------------------------------------------------------------
# Product profitability
# ---------------------------------------------------------------------------

LOW_MARGIN_PCT = 20.0


def _cost_build_up(good):
    """Current per-unit cost split into ingredients and production materials."""
    upb = good.units_per_batch or Decimal("1")
    recipe = list(good.recipe_items.all())
    production = list(good.production_materials.all())
    if good.business.uses_production and not good.is_purchased_for_resale and (recipe or production):
        raw = sum((ri.raw_material.cost_per_unit * ri.qty_per_batch for ri in recipe), ZERO) / upb
        prod = sum((pm.raw_material.cost_per_unit * pm.qty_per_batch for pm in production), ZERO) / upb
        return {"raw_material": _money(raw), "production": _money(prod), "purchase": ZERO, "total": _money(raw + prod), "basis": "recipe"}
    purchase = good.est_cost or ZERO
    return {"raw_material": ZERO, "production": ZERO, "purchase": _money(purchase), "total": _money(purchase), "basis": "purchase"}


def _goods_with_recipes(business):
    from inventory.models import FinishedGood
    return list(
        FinishedGood.raw_objects.filter(business=business)
        .select_related("business", "variant_of")
        .prefetch_related("recipe_items__raw_material", "production_materials__raw_material")
    )


def product_profitability(business, start, end, goods=None):
    """What each product earns between ``start`` and ``end`` (paid sales).

    Revenue and cost come from the sale lines (cost is the unit cost frozen when
    the sale was made), so history is never repriced with today's material
    costs. The cost build-up shows today's recipe split for context. Products
    that did not sell are included so under-performers are visible.
    """
    from django.db.models import Count
    from inventory.models import FinishedGood
    from sales.models import SaleItem

    money_field, line_total, cogs, units = _sale_expressions(prefix="")
    uncosted = Sum(
        Case(When(unit_cost__isnull=True, then=units), default=Value(ZERO), output_field=DecimalField(max_digits=20, decimal_places=4)),
        output_field=DecimalField(max_digits=20, decimal_places=4),
    )
    sold = {
        row["finished_good_id"]: row
        for row in SaleItem.objects.filter(
            sale__business=business, sale__date__range=(start, end), sale__transaction_type="paid", finished_good__isnull=False,
        ).values("finished_good_id").annotate(
            units=Sum(units), revenue=Sum(line_total, output_field=money_field), cogs=Sum(cogs, output_field=money_field),
            uncosted=uncosted, sales=Count("sale_id", distinct=True),
        )
    }
    if goods is None:
        goods = _goods_with_recipes(business)
    total_revenue = sum((row["revenue"] or ZERO for row in sold.values()), ZERO)
    total_profit = sum(((row["revenue"] or ZERO) - (row["cogs"] or ZERO) for row in sold.values()), ZERO)
    rows = []
    for good in goods:
        data = sold.get(good.pk)
        units_sold = data["units"] or ZERO if data else ZERO
        revenue = data["revenue"] or ZERO if data else ZERO
        cost = data["cogs"] or ZERO if data else ZERO
        profit = revenue - cost
        rows.append({
            "id": good.pk, "name": good.name, "unit": good.unit, "is_resale": good.is_purchased_for_resale,
            "units": units_sold.quantize(CENT), "sales": data["sales"] if data else 0,
            "revenue": _money(revenue), "cogs": _money(cost), "profit": _money(profit),
            "margin": _share(profit, revenue),
            "avg_price": _money(revenue / units_sold) if units_sold else None,
            "avg_cost": _money(cost / units_sold) if units_sold else None,
            "revenue_share": _share(revenue, total_revenue), "profit_share": _share(profit, total_profit) if total_profit > 0 else None,
            "uncosted_units": (data["uncosted"] or ZERO).quantize(CENT) if data else ZERO,
            "build": _cost_build_up(good), "price": _money(good.selling_price),
            "badges": [], "rank_units": None, "rank_profit": None,
        })

    sellers = [row for row in rows if row["units"] > 0]
    by_units = sorted(sellers, key=lambda r: (-r["units"], r["name"].lower()))
    by_profit = sorted(sellers, key=lambda r: (-r["profit"], r["name"].lower()))
    for position, row in enumerate(by_units, 1):
        row["rank_units"] = position
    for position, row in enumerate(by_profit, 1):
        row["rank_profit"] = position
    top_n = max(1, min(3, -(-len(sellers) // 4))) if sellers else 0
    slow_cutoff = by_units[int(len(by_units) * 0.75)]["units"] if len(by_units) >= 4 else None
    for row in rows:
        badges = row["badges"]
        if row["units"] <= 0:
            badges.append("No sales")
            continue
        if row["rank_units"] <= top_n:
            badges.append("Best seller")
        if row["rank_profit"] <= top_n and row["profit"] > 0:
            badges.append("Top earner")
        if row["profit"] < 0:
            badges.append("Loss-making")
        elif row["margin"] is not None and row["margin"] < LOW_MARGIN_PCT:
            badges.append("Low margin")
        if slow_cutoff is not None and row["units"] <= slow_cutoff and "Best seller" not in badges:
            badges.append("Slow mover")
        if row["uncosted_units"] > 0:
            badges.append("Cost not recorded")
    rows.sort(key=lambda r: (r["units"] <= 0, -r["profit"], r["name"].lower()))
    return {
        "rows": rows, "total_revenue": _money(total_revenue), "total_profit": _money(total_profit),
        "total_margin": _share(total_profit, total_revenue), "sellers": len(sellers), "products": len(rows),
        "best_seller": by_units[0] if by_units else None, "top_earner": by_profit[0] if by_profit else None,
        "weakest": next((r for r in reversed(by_profit) if r["profit"] <= 0 or (r["margin"] is not None and r["margin"] < LOW_MARGIN_PCT)), None),
    }


def product_monthly_trend(business, good, months=6, anchor=None):
    """One product's units, revenue, profit and margin by month (oldest first)."""
    from sales.models import SaleItem

    anchor = anchor or date.today()
    first = add_months(date(anchor.year, anchor.month, 1), -(months - 1))
    money_field, line_total, cogs, units = _sale_expressions(prefix="")
    by_month = {}
    for row in SaleItem.objects.filter(
        sale__business=business, sale__transaction_type="paid", finished_good=good, sale__date__gte=first, sale__date__lte=anchor,
    ).values("sale__date").annotate(units=Sum(units), revenue=Sum(line_total, output_field=money_field), cogs=Sum(cogs, output_field=money_field)):
        key = (row["sale__date"].year, row["sale__date"].month)
        bucket = by_month.setdefault(key, [ZERO, ZERO, ZERO])
        bucket[0] += row["units"] or ZERO
        bucket[1] += row["revenue"] or ZERO
        bucket[2] += row["cogs"] or ZERO
    trend = []
    for step in range(months):
        month = add_months(first, step)
        units_sold, revenue, cost = by_month.get((month.year, month.month), [ZERO, ZERO, ZERO])
        trend.append({
            "label": f"{month:%b}", "units": float(units_sold), "revenue": float(_money(revenue)),
            "profit": float(_money(revenue - cost)), "margin": _share(revenue - cost, revenue),
        })
    return trend


def _verdict(row, result, label):
    if row is None or row["units"] <= 0:
        return f"No paid sales {label.lower()}."
    parts = [f"#{row['rank_units']} of {result['sellers']} by units sold", f"#{row['rank_profit']} by profit"]
    lead = "Best seller" if "Best seller" in row["badges"] else "Top earner" if "Top earner" in row["badges"] else ""
    flags = [b for b in row["badges"] if b in ("Loss-making", "Low margin", "Slow mover", "Cost not recorded")]
    sentence = ", ".join(parts) + "."
    if lead:
        sentence = f"{lead}: " + sentence
    if flags:
        sentence += " Watch: " + ", ".join(flag.lower() for flag in flags) + "."
    return sentence


def product_profit_windows(business, good, today=None):
    """One product's results for this month, last month, this quarter and year,
    ranked against the business's other products, plus its cost build-up."""
    today = today or date.today()
    month = fiscal_period(business, today, kind="month")
    windows = [
        ("This month", month), ("Last month", shift_period(business, month, -1)),
        ("This quarter", fiscal_period(business, today, kind="quarter")), ("This year", fiscal_period(business, today, kind="year")),
    ]
    goods = _goods_with_recipes(business)
    out = []
    for label, period in windows:
        result = product_profitability(business, period["start"], min(period["end"], today), goods=goods)
        row = next((r for r in result["rows"] if r["id"] == good.pk), None)
        out.append({
            "label": label, "span": f"{period['start']:%d %b} – {min(period['end'], today):%d %b %Y}",
            "units": float(row["units"]) if row else 0.0, "revenue": float(row["revenue"]) if row else 0.0,
            "cogs": float(row["cogs"]) if row else 0.0, "profit": float(row["profit"]) if row else 0.0,
            "margin": row["margin"] if row else None, "revenue_share": row["revenue_share"] if row else None,
            "avg_price": float(row["avg_price"]) if row and row["avg_price"] is not None else None,
            "avg_cost": float(row["avg_cost"]) if row and row["avg_cost"] is not None else None,
            "rank_units": row["rank_units"] if row else None, "rank_profit": row["rank_profit"] if row else None,
            "sellers": result["sellers"], "badges": row["badges"] if row else ["No sales"],
            "verdict": _verdict(row, result, label),
        })
    mine = next((g for g in goods if g.pk == good.pk), good)
    build = _cost_build_up(mine)
    price = mine.selling_price or ZERO
    return {
        "windows": out,
        "build": {key: float(value) if isinstance(value, Decimal) else value for key, value in build.items()},
        "price": float(price), "margin_per_unit": float(_money(price - build["total"])),
        "margin_pct": _share(price - build["total"], price),
        "trend": product_monthly_trend(business, good, months=6, anchor=today),
    }