import csv
import json
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.core import serializers
from django.db.models import Prefetch, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from openpyxl import Workbook

from .models import Business, FinancialTransaction
from .forms import BusinessForm
from .services import audit
from .verticals import vertical_config
from accounts.models import BusinessModuleAccess, UserBusiness
from accounts.services import is_business_admin, seed_business_modules
from inventory.models import (
    DistributionReturn,
    FinishedGood,
    MarketStockLot,
    MarketStockMovement,
    OperationalSupplyDispense,
    ProductionMaterial,
    RawMaterial,
    RecipeItem,
    StockAdjustment,
    StockMovement,
)
from procurement.models import PurchaseOrder, PurchaseOrderItem, RawMaterialCostSnapshot, SupplierPayment
from production.models import Order, OrderItem, ProductionBatch
from sales.models import Sale, SaleItem
from expenses.models import Expense


def marketing_home(request):
    """Public product overview; remembered authenticated sessions continue to the app."""
    if request.user.is_authenticated and request.GET.get("view") != "marketing":
        return redirect("dashboard")
    from accounts.models import MarketingPromoCampaign, SubscriptionPlan, SubscriptionPolicySettings
    from accounts.subscription_services import attach_active_promotions, build_plan_feature_matrix
    plans = attach_active_promotions(
        SubscriptionPlan.objects.filter(active=True)
        .prefetch_related("module_entitlements")
        .order_by("monthly_price", "id")
    )
    moment = timezone.now()
    marketing_campaigns = list(
        MarketingPromoCampaign.objects.filter(
            active=True,
            promotion__active=True,
            promotion__starts_at__lte=moment,
            promotion__ends_at__gt=moment,
        )
        .select_related("promotion__plan")
        .order_by("-priority", "id")
    )
    starter_plan = next((plan for plan in plans if plan.code == SubscriptionPlan.CODE_STARTER), None)
    trial_policy = SubscriptionPolicySettings.load()
    return render(request, "marketing/home.html", {
        "plans": plans, "starter_plan": starter_plan, "marketing_campaigns": marketing_campaigns,
        "general_trial_days": max(1, int(trial_policy.general_trial_days or 30)),
        "plan_feature_matrix": build_plan_feature_matrix(plans),
    })


def today():
    return timezone.localdate()


def _production_units(queryset):
    total = Decimal("0")
    for o in queryset.prefetch_related("items__finished_good"):
        total += o.total_units
    return total


def _sales_revenue(queryset):
    """Recognised revenue: only paid sales count as revenue/cash receipts.
    Unpaid sales remain visible as receivables/non-cash activity."""
    total = Decimal("0")
    for sale in queryset.filter(transaction_type="paid").prefetch_related("items__finished_good"):
        total += sale.total
    return total


def _sales_cogs(queryset):
    total = Decimal("0")
    for sale in queryset.prefetch_related("items"):
        if sale.transaction_type != "paid":
            continue
        for item in sale.items.all():
            total += (item.unit_cost or Decimal("0")) * item.total_units
    return total


def _unpaid_product_value(start, end):
    """Retail-value and cost-value of non-cash product issues.
    This includes unpaid sales and unpaid physical-store production issues."""
    retail = Decimal("0"); cost = Decimal("0")
    for sale in Sale.objects.filter(source="walkin", transaction_type="unpaid", date__range=(start, end)).prefetch_related("items"):
        retail += sale.total
        for item in sale.items.all(): cost += (item.unit_cost or Decimal("0")) * item.total_units
    for order in Order.objects.filter(order_type="physical_store", transaction_type="unpaid", status="completed", completed_date__range=(start, end)).prefetch_related("cost_snapshots"):
        for snap in order.cost_snapshots.all(): cost += snap.total_cost
        retail += order.total
    return retail, cost


def _procurement_spend(start, end):
    """Cash/spend ledger for received purchase orders only.

    A PO becomes business spend when it is received into inventory. Draft and
    ordered-but-not-received POs remain commitments, not realised spend.
    """
    total = Decimal("0")
    qs = PurchaseOrder.objects.filter(status="received").filter(
        Q(received_date__range=(start, end)) |
        Q(received_date__isnull=True, date__range=(start, end))
    )
    for po in qs.prefetch_related("items"):
        total += po.total
    return total


def _expense_spend(start, end):
    return Expense.objects.filter(date__range=(start, end), payment_status="paid").aggregate(total=Sum("amount"))["total"] or Decimal("0")


def _cash_procurement(start, end):
    """Actual procurement cash leaving accounts, including later supplier payments."""
    return FinancialTransaction.objects.filter(
        date__range=(start, end), transaction_type=FinancialTransaction.OUTFLOW,
        category__in=("Procurement", "Supplier payment"),
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")


def _procurement_scope_key(line):
    if line.finished_good_id:
        return "products_for_resale"
    category = line.raw_material.category
    if category == RawMaterial.CATEGORY_INGREDIENT:
        return "raw_materials"
    if category in (RawMaterial.CATEGORY_PACKAGING, RawMaterial.CATEGORY_PRODUCTION_SUPPLY):
        return "production_materials"
    if category == RawMaterial.CATEGORY_OPERATIONAL_SUPPLY:
        return "operational_materials"
    return "other_procurement"


def _cash_outflow_breakdown(start, end):
    """Cash outflow categories without counting unpaid inventory as cash."""
    buckets = {"raw_materials": Decimal("0"), "production_materials": Decimal("0"), "operational_materials": Decimal("0"), "products_for_resale": Decimal("0"), "other_procurement": Decimal("0")}

    # Load all referenced purchase orders once. The previous per-transaction
    # lookup made dashboard query count grow with procurement volume.
    procurement_txs = list(FinancialTransaction.objects.filter(
        date__range=(start, end),
        transaction_type=FinancialTransaction.OUTFLOW,
        category="Procurement",
    ))
    payments = list(SupplierPayment.objects.filter(date__range=(start, end)))
    transaction_po_ids = {}
    po_ids = {payment.purchase_order_id for payment in payments if payment.purchase_order_id}
    for tx in procurement_txs:
        try:
            po_id = int((tx.reference or "").split("-")[-1])
        except (TypeError, ValueError):
            po_id = None
        transaction_po_ids[tx.pk] = po_id
        if po_id:
            po_ids.add(po_id)

    po_cache = {
        po.pk: po
        for po in PurchaseOrder.objects.filter(pk__in=po_ids).prefetch_related(
            Prefetch(
                "items",
                queryset=PurchaseOrderItem.objects.select_related("raw_material", "finished_good"),
            )
        )
    }

    # Immediate payments made when a received PO was paid.
    for tx in procurement_txs:
        po_id = transaction_po_ids.get(tx.pk)
        if not po_id:
            buckets["other_procurement"] += tx.amount
            continue
        po = po_cache.get(po_id)
        if not po or not po.total:
            buckets["other_procurement"] += tx.amount
            continue
        for line in po.items.all():
            buckets[_procurement_scope_key(line)] += tx.amount * ((line.line_total or Decimal("0")) / po.total)

    # Later payments are tied directly to their PO.
    for payment in payments:
        po = po_cache.get(payment.purchase_order_id)
        if not po or not po.total:
            buckets["other_procurement"] += payment.amount
            continue
        for line in po.items.all():
            buckets[_procurement_scope_key(line)] += payment.amount * ((line.line_total or Decimal("0")) / po.total)
    return buckets

def _spend(start, end):
    return _procurement_spend(start, end) + _expense_spend(start, end)


def _quarter_start(d):
    q_month = (d.month - 1) // 3 * 3 + 1
    return d.replace(month=q_month, day=1)


def _financial_periods():
    t = today()
    quarter = _quarter_start(t)
    return [
        ("Weekly", t - timedelta(days=t.weekday()), t),
        ("Monthly", t.replace(day=1), t),
        ("Quarterly", quarter, t),
        ("Yearly", t.replace(month=1, day=1), t),
    ]


def _financial_breakdown(start, end):
    """Return a cash-flow and revenue breakdown for one reporting window.

    Procurement is split by the raw-material classification already used by
    inventory. Sales are split by actual sale channel. Production itself is
    an internal conversion, not a second cash outflow, so it is shown only as
    an informational production figure rather than being subtracted twice.
    """
    procurement_qs = PurchaseOrder.objects.filter(status="received").filter(
        Q(received_date__range=(start, end)) |
        Q(received_date__isnull=True, date__range=(start, end))
    ).prefetch_related("items__raw_material", "items__finished_good")

    procurement = {
        "raw_materials": Decimal("0"),
        "production_materials": Decimal("0"),
        "operational_materials": Decimal("0"),
        "products_for_resale": Decimal("0"),
        "other_procurement": Decimal("0"),
    }
    for po in procurement_qs:
        for line in po.items.all():
            key = _procurement_scope_key(line)
            procurement[key] += line.line_total or Decimal("0")

    expense_qs = Expense.objects.filter(date__range=(start, end), payment_status="paid")
    misc_by_category = {}
    for expense in expense_qs:
        label = expense.get_category_display()
        misc_by_category[label] = misc_by_category.get(label, Decimal("0")) + (expense.amount or Decimal("0"))

    channel_map = {
        "walkin": "Physical Store",
        "distribution_order": "Distribution",
        "online_order": "Online",
    }
    sales_by_channel = {label: Decimal("0") for label in channel_map.values()}
    for sale in Sale.objects.filter(date__range=(start, end), transaction_type="paid").prefetch_related("items__finished_good"):
        label = channel_map.get(sale.source, sale.source.replace("_", " ").title())
        sales_by_channel[label] = sales_by_channel.get(label, Decimal("0")) + sale.total

    production_orders = Order.objects.filter(
        status="completed", completed_date__range=(start, end)
    ).prefetch_related("items__finished_good")
    produced_units = _production_units(production_orders)
    operational_dispensed_cost = Decimal("0")
    for movement in StockMovement.objects.filter(movement_type=StockMovement.OPERATIONAL_DISPENSE, occurred_at__date__range=(start,end)):
        operational_dispensed_cost += abs(movement.quantity or Decimal("0")) * (movement.unit_value or Decimal("0"))

    procurement_total = sum(procurement.values(), Decimal("0"))
    misc_total = sum(misc_by_category.values(), Decimal("0"))
    sales_total = sum(sales_by_channel.values(), Decimal("0"))
    paid_sales_qs = Sale.objects.filter(date__range=(start, end), transaction_type="paid")
    cogs = _sales_cogs(paid_sales_qs)
    unpaid_retail, unpaid_cost = _unpaid_product_value(start, end)
    cash_procurement = _cash_procurement(start, end)
    total_cash_out = cash_procurement + misc_total

    cash_procurement_breakdown = _cash_outflow_breakdown(start, end)
    outflows = [
        ("Raw materials", cash_procurement_breakdown["raw_materials"]),
        ("Production materials", cash_procurement_breakdown["production_materials"]),
        ("Operational materials", cash_procurement_breakdown["operational_materials"]),
        ("Products for resale", cash_procurement_breakdown["products_for_resale"]),
    ]
    outflows.extend((label, amount) for label, amount in sorted(misc_by_category.items()) if amount)
    if cash_procurement_breakdown["other_procurement"]:
        outflows.append(("Other procurement / supplier payments", cash_procurement_breakdown["other_procurement"]))

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sales_total": sales_total,
        "sales_by_channel": [{"label": k, "value": float(v)} for k, v in sales_by_channel.items() if v],
        "outflows": [{"label": k, "value": float(v)} for k, v in outflows if v],
        "procurement": {k: float(v) for k, v in procurement.items()},
        "misc_total": float(misc_total),
        "cash_procurement": float(cash_procurement),
        "cash_procurement_breakdown": {k: float(v) for k,v in cash_procurement_breakdown.items()},
        "total_cash_out": float(total_cash_out),
        "net_cash_flow": float(sales_total - total_cash_out),
        "cogs": float(cogs),
        "gross_profit": float(sales_total - cogs),
        "unpaid_product_retail_value": float(unpaid_retail),
        "unpaid_product_cost_value": float(unpaid_cost),
        "produced_units": float(produced_units),
        "operational_dispensed_cost": float(operational_dispensed_cost),
    }


def _financial_daily_ledgers(start, end):
    """Load the dashboard's year-to-date finance ledgers once per request.

    Both the financial cards and the chart consume the same sales, cash-out and
    expense rows. Keeping that shared data in one in-memory snapshot avoids
    repeating the same PostgreSQL round trips during a dashboard render.
    """
    sales_by_date = defaultdict(Decimal)
    cogs_by_date = defaultdict(Decimal)
    for sale in (
        Sale.objects.filter(date__range=(start, end), transaction_type="paid")
        .prefetch_related("items__finished_good")
    ):
        sales_by_date[sale.date] += sale.total
        for item in sale.items.all():
            cogs_by_date[sale.date] += (item.unit_cost or Decimal("0")) * item.total_units

    procurement_by_date = defaultdict(Decimal)
    purchase_orders = PurchaseOrder.objects.filter(status="received").filter(
        Q(received_date__range=(start, end))
        | Q(received_date__isnull=True, date__range=(start, end))
    ).prefetch_related("items")
    for purchase_order in purchase_orders:
        procurement_date = purchase_order.received_date or purchase_order.date
        procurement_by_date[procurement_date] += purchase_order.total

    cash_procurement_by_date = {
        row["date"]: row["total"] or Decimal("0")
        for row in FinancialTransaction.objects.filter(
            date__range=(start, end),
            transaction_type=FinancialTransaction.OUTFLOW,
            category__in=("Procurement", "Supplier payment"),
        ).values("date").annotate(total=Sum("amount"))
    }
    expenses_by_date = {
        row["date"]: row["total"] or Decimal("0")
        for row in Expense.objects.filter(
            date__range=(start, end), payment_status="paid"
        ).values("date").annotate(total=Sum("amount"))
    }
    return {
        "start": start,
        "end": end,
        "sales_by_date": sales_by_date,
        "cogs_by_date": cogs_by_date,
        "procurement_by_date": procurement_by_date,
        "cash_procurement_by_date": cash_procurement_by_date,
        "expenses_by_date": expenses_by_date,
    }


def _financial_snapshot(ledger=None):
    periods = _financial_periods()
    year_start = min(start for _label, start, _end in periods)
    period_end = max(end for _label, _start, end in periods)
    ledger = ledger or _financial_daily_ledgers(year_start, period_end)

    sales_by_date = ledger["sales_by_date"]
    cogs_by_date = ledger["cogs_by_date"]
    procurement_by_date = ledger["procurement_by_date"]
    cash_procurement_by_date = ledger["cash_procurement_by_date"]
    expenses_by_date = ledger["expenses_by_date"]

    def period_total(values, start, end):
        return sum(
            (amount for date, amount in values.items() if start <= date <= end),
            Decimal("0"),
        )

    rows = []
    for label, start, end in periods:
        sales = period_total(sales_by_date, start, end)
        procurement = period_total(procurement_by_date, start, end)
        cash_procurement = period_total(cash_procurement_by_date, start, end)
        misc = period_total(expenses_by_date, start, end)
        spend = cash_procurement + misc
        cogs = period_total(cogs_by_date, start, end)
        rows.append({
            "label": label,
            "sales": sales,
            "procurement": procurement,
            "cash_procurement": cash_procurement,
            "misc": misc,
            "spend": spend,
            "gross_profit": sales - cogs,
            "cogs": cogs,
            "net_cash_flow": sales - spend,
            "revenue": sales - spend,  # backward-compatible export/dashboard key
        })
    return rows


def _financial_chart_series(ledger=None):
    """Return dashboard chart data at useful resolutions for each period tab.

    Week/month use daily points; quarter uses weekly points; year uses monthly
    points. Spend is realised procurement plus miscellaneous expense, matching
    the financial cards and revenue calculation already shown on the dashboard.
    """
    t = today()
    week_start = t - timedelta(days=6)
    month_start = t.replace(day=1)
    quarter_start = _quarter_start(t)
    year_start = t.replace(month=1, day=1)
    data_start = min(year_start, week_start)

    # The dashboard passes the same year-to-date ledger used by the financial
    # cards. Standalone callers still load it here for backward compatibility.
    ledger = ledger or _financial_daily_ledgers(data_start, t)
    sales_by_date = ledger["sales_by_date"]
    procurement_by_date = ledger["cash_procurement_by_date"]
    expenses_by_date = ledger["expenses_by_date"]

    def period_total(values, start, end):
        return sum(
            (amount for date, amount in values.items() if start <= date <= end),
            Decimal("0"),
        )

    def bucket(start, end, label):
        sales = period_total(sales_by_date, start, end)
        procurement = period_total(procurement_by_date, start, end)
        misc = period_total(expenses_by_date, start, end)

        spend = procurement + misc
        revenue = sales - spend

        return {
            "label": str(label),
            "sales": float(sales or 0),
            "spend": float(spend or 0),
            "revenue": float(revenue or 0),
        }

    daily = []
    d = week_start
    while d <= t:
        daily.append(bucket(d, d, d.strftime("%a %d")))
        d += timedelta(days=1)

    month_daily = []
    d = month_start
    while d <= t:
        month_daily.append(bucket(d, d, d.strftime("%d %b")))
        d += timedelta(days=1)

    quarterly = []
    d = quarter_start
    while d <= t:
        end = min(d + timedelta(days=6), t)
        quarterly.append(bucket(d, end, f"{d.strftime('%d %b')}–{end.strftime('%d %b')}"))
        d = end + timedelta(days=1)

    yearly = []
    for month in range(1, t.month + 1):
        start = year_start.replace(month=month, day=1)
        if month == 12:
            next_start = year_start.replace(year=t.year + 1, month=1, day=1)
        else:
            next_start = year_start.replace(month=month + 1, day=1)
        end = min(next_start - timedelta(days=1), t)
        yearly.append(bucket(start, end, start.strftime("%b")))

    return {"week": daily, "month": month_daily, "quarter": quarterly, "year": yearly}

def _financial_chart_json():
    return _financial_chart_series()


def _sales_by_channel(start=None, end=None, sales=None):
    if sales is None:
        qs = Sale.objects.all()
        if start and end:
            qs = qs.filter(date__range=(start, end))
        sales = qs.prefetch_related("items__finished_good")
    result = {"walkin": Decimal("0"), "distribution_order": Decimal("0"), "online_order": Decimal("0")}
    for sale in sales:
        if start and end and not (start <= sale.date <= end):
            continue
        result[sale.source] = result.get(sale.source, Decimal("0")) + sale.total
    return result

def _channel_breakdown(channel, start, end, sales=None):
    """Sales breakdown for a customer channel by region and customer group."""
    source = {"distribution": "distribution_order", "online": "online_order"}.get(channel)
    if not source:
        return []
    if sales is None:
        sales = (
            Sale.objects.filter(
                source=source, date__range=(start, end), transaction_type="paid"
            )
            .select_related("linked_order")
            .prefetch_related("items__finished_good")
        )
    buckets = {}
    for sale in sales:
        if sale.source != source or sale.transaction_type != "paid" or not (start <= sale.date <= end):
            continue
        order = sale.linked_order
        region = (order.customer_region if order and order.customer_region else "Unassigned region").strip()
        group = (order.customer_group if order and order.customer_group else "Unassigned group").strip()
        key = (region, group)
        buckets[key] = buckets.get(key, Decimal("0")) + sale.total
    return [{"region": k[0], "group": k[1], "sales": v} for k, v in sorted(buckets.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))]


def _stock_periods(item):
    """Build stock, procurement spend and purchase-price movement overview.

    For raw materials, each period's price comparison is:
        latest procurement occurring inside that period
        vs.
        the immediately preceding procurement overall.

    The preceding procurement may therefore be before the period.
    """
    t = today()
    periods = [
        ("Today", t),
        ("This Week", t - timedelta(days=t.weekday())),
        ("This Month", t.replace(day=1)),
        ("This Quarter", _quarter_start(t)),
        ("This Year", t.replace(month=1, day=1)),
    ]

    movements = item.stock_movements.all()
    is_finished_good = isinstance(item, FinishedGood)

    closing = item.stock
    rows = []

    if not is_finished_good:
        purchase_lines = list(
            PurchaseOrderItem.objects.filter(
                raw_material=item,
                purchase_order__status="received",
            ).select_related("purchase_order")
        )

        def procurement_date(line):
            po = line.purchase_order
            return po.received_date or po.date

        # Chronological procurement ledger.
        # pk is used as a deterministic tie-breaker when two lines have
        # the same procurement date.
        purchase_lines.sort(
            key=lambda line: (
                procurement_date(line),
                line.pk,
            )
        )

    else:
        purchase_lines = []

    for label, start in periods:
        period_movements = movements.filter(
            occurred_at__date__gte=start,
            occurred_at__date__lte=t,
        )

        stock_movements = period_movements.filter(affects_stock=True)

        net = (
            stock_movements.aggregate(total=Sum("quantity"))["total"]
            or Decimal("0")
        )

        opening = closing - net

        production = Decimal("0")

        if is_finished_good:
            production = (
                period_movements.filter(
                    movement_type=StockMovement.FG_PRODUCTION,
                ).aggregate(total=Sum("quantity"))["total"]
                or Decimal("0")
            )

        net_spend = Decimal("0")
        price_difference = None
        price_difference_pct = None
        latest_purchase_cost = None
        previous_purchase_cost = None
        latest_procurement = None
        previous_procurement = None

        if not is_finished_good:
            # All procurement lines occurring inside this period.
            period_lines = [
                line
                for line in purchase_lines
                if start <= procurement_date(line) <= t
            ]

            if period_lines:
                # Because purchase_lines is already chronological, the
                # last line is the latest procurement in this period.
                latest_line = period_lines[-1]
                latest_index = purchase_lines.index(latest_line)

                latest_procurement = latest_line
                latest_purchase_cost = (
                    latest_line.unit_cost or Decimal("0")
                )

                # The immediately preceding procurement overall.
                if latest_index > 0:
                    previous_line = purchase_lines[latest_index - 1]
                    previous_procurement = previous_line
                    previous_purchase_cost = (
                        previous_line.unit_cost or Decimal("0")
                    )

                    price_difference = (
                        latest_purchase_cost - previous_purchase_cost
                    )

                    if previous_purchase_cost != 0:
                        price_difference_pct = (
                            price_difference
                            / previous_purchase_cost
                            * Decimal("100")
                        )

            # Spend remains the total spend occurring inside this period.
            for line in period_lines:
                net_spend += line.line_total or Decimal("0")

        rows.append({
            "label": label,
            "opening": opening,
            "closing": closing,
            "net": net,
            "production": production,
            "net_spend": net_spend,

            # Latest procurement vs immediately previous procurement.
            "price_difference": price_difference,
            "price_difference_pct": price_difference_pct,

            # Useful if you later want to show the actual two costs.
            "latest_purchase_cost": latest_purchase_cost,
            "previous_purchase_cost": previous_purchase_cost,
        })

    return rows


# ---------------------------------------------------------------------------
# Dashboard global search
# ---------------------------------------------------------------------------

def _search_periods():
    """Named reporting windows used by dashboard search detail modals."""
    t = today()
    return [
        ("Today", t),
        ("This week", t - timedelta(days=t.weekday())),
        ("This month", t.replace(day=1)),
        ("This quarter", _quarter_start(t)),
        ("This year", t.replace(month=1, day=1)),
    ]


def _money(value):
    return float(value or 0)


def _decimal(value):
    return float(value or 0)


def _period_row(
    label,
    start,
    *,
    spend=0,
    revenue=0,
    units=0,
    events=0,
    price_difference=None,
    price_difference_pct=None,
    latest_unit_cost=None,
    previous_unit_cost=None,
):
    return {
        "label": label,
        "spend": _money(spend),
        "revenue": _money(revenue),
        "units": _decimal(units),
        "events": int(events or 0),
        "price_difference": _money(price_difference) if price_difference is not None else None,
        "price_difference_pct": _money(price_difference_pct) if price_difference_pct is not None else None,
        "latest_unit_cost": _money(latest_unit_cost) if latest_unit_cost is not None else None,
        "previous_unit_cost": _money(previous_unit_cost) if previous_unit_cost is not None else None,
    }


def _search_results(q):
    """Return a deliberately small, labelled result set for the dashboard.

    Results are identifiers, not full objects. The detail endpoint is only
    called after the user chooses a result, which keeps the dashboard fast.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return []

    results = []
    needle = q.lower()

    raw_qs = RawMaterial.objects.filter(
        Q(name__icontains=q) |
        Q(category__icontains=q) |
        Q(purchase_unit__icontains=q) |
        Q(package_unit__icontains=q) |
        Q(usage_unit__icontains=q)
    ).order_by("name")[:12]
    for m in raw_qs:
        results.append({
            "key": f"raw:{m.pk}",
            "type": "raw",
            "label": m.name,
            "identifier": m.get_category_display(),
            "meta": f"{m.usage_unit or '—'} • {m.purchase_unit or 'purchase unit not set'}",
        })

    fg_qs = FinishedGood.objects.filter(
        Q(name__icontains=q) | Q(unit__icontains=q)
    ).order_by("name")[:12]
    for g in fg_qs:
        results.append({
            "key": f"fg:{g.pk}",
            "type": "fg",
            "label": g.name,
            "identifier": "Finished good",
            "meta": f"{g.unit} • selling {g.selling_price}",
        })

    suppliers = PurchaseOrder.objects.filter(
        supplier__icontains=q
    ).values_list("supplier", flat=True).distinct()[:12]
    for supplier in suppliers:
        if supplier:
            results.append({
                "key": f"supplier:{supplier}",
                "type": "supplier",
                "label": supplier,
                "identifier": "Procurement vendor",
                "meta": "Purchase history and spend",
            })

    # Keep customer identities separate by channel. This is intentional: the
    # same name can legitimately exist in Distribution and Online records.
    customer_rows = Order.objects.filter(
        Q(customer_name__icontains=q) |
        Q(customer_region__icontains=q) |
        Q(customer_group__icontains=q)
    ).exclude(customer_name="").values_list(
        "customer_name", "order_type"
    ).distinct()[:30]
    seen = set()
    for name, order_type in customer_rows:
        channel = {
            "distribution": "Distribution customer",
            "online": "Online customer",
        }.get(order_type)
        if not channel:
            continue
        key = (name.strip(), order_type)
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "key": f"customer:{order_type}:{name}",
            "type": "customer",
            "channel": order_type,
            "label": name.strip(),
            "identifier": channel,
            "meta": "Orders, sales, region and group",
        })

    # Regions and groups are useful search objects in their own right.
    regions = Order.objects.filter(customer_region__icontains=q).exclude(
        customer_region=""
    ).values_list("customer_region", flat=True).distinct()[:8]
    for region in regions:
        results.append({
            "key": f"region:{region}",
            "type": "region",
            "label": region,
            "identifier": "Customer region",
            "meta": "Distribution and online activity",
        })

    groups = Order.objects.filter(customer_group__icontains=q).exclude(
        customer_group=""
    ).values_list("customer_group", flat=True).distinct()[:8]
    for group in groups:
        results.append({
            "key": f"group:{group}",
            "type": "group",
            "label": group,
            "identifier": "Customer group",
            "meta": "Distribution and online activity",
        })

    # Exact-name matches first, then alphabetical. This makes a search for
    # "Flour" open the material/product candidates before broader metadata.
    results.sort(key=lambda r: (0 if r["label"].lower() == needle else 1, r["label"].lower(), r["identifier"]))
    return results[:30]


def _raw_search_detail(material):
    vocabulary = vertical_config(material.business)
    periods = _search_periods()
    purchase_lines = list(
        PurchaseOrderItem.objects.filter(
            raw_material=material,
            purchase_order__status="received",
        ).select_related("purchase_order")
    )

    def po_date(line):
        return line.purchase_order.received_date or line.purchase_order.date

    purchase_lines.sort(key=lambda line: (po_date(line), line.pk))
    purchases = []
    for line in purchase_lines:
        purchases.append({
            "date": po_date(line).isoformat(),
            "supplier": line.purchase_order.supplier or "Unnamed supplier",
            "qty": _decimal(line.qty),
            "unit": material.purchase_unit,
            "unit_cost": _money(line.unit_cost),
            "total": _money(line.line_total),
        })

    rows = []
    for label, start in periods:
        lines = [line for line in purchase_lines if start <= po_date(line) <= today()]
        movements = material.stock_movements.filter(
            occurred_at__date__range=(start, today())
        )
        consumed = movements.filter(
            movement_type=StockMovement.RAW_CONSUMPTION
        ).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
        operational_dispensed = movements.filter(
            movement_type=StockMovement.OPERATIONAL_DISPENSE
        ).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
        spend = sum((line.line_total or Decimal("0") for line in lines), Decimal("0"))
        qty = sum((line.qty or Decimal("0") for line in lines), Decimal("0"))

        # Procurement price Δ is a price change, not a spend change:
        # latest purchase price in this window minus the immediately
        # preceding purchase price for THIS SAME raw material. No averaging.
        latest = lines[-1] if lines else None
        latest_unit_cost = latest.unit_cost if latest else None
        previous_unit_cost = None
        price_difference = None
        price_difference_pct = None
        if latest:
            latest_index = purchase_lines.index(latest)
            if latest_index > 0:
                previous = purchase_lines[latest_index - 1]
                previous_unit_cost = previous.unit_cost
                price_difference = latest_unit_cost - previous_unit_cost
                if previous_unit_cost:
                    price_difference_pct = (
                        price_difference / previous_unit_cost * Decimal("100")
                    )

        rows.append({
            "label": label,
            "purchased_qty": _decimal(qty),
            "purchase_unit": material.purchase_unit,
            "spend": _money(spend),
            "consumed_qty": _decimal(abs(consumed)),
            "operational_dispensed_qty": _decimal(abs(operational_dispensed)),
            "usage_unit": material.usage_unit,
            "purchase_count": len(lines),
            "latest_unit_cost": _money(latest_unit_cost) if latest_unit_cost is not None else None,
            "previous_unit_cost": _money(previous_unit_cost) if previous_unit_cost is not None else None,
            "price_difference": _money(price_difference) if price_difference is not None else None,
            "price_difference_pct": _money(price_difference_pct) if price_difference_pct is not None else None,
        })

    operational_dispenses = OperationalSupplyDispense.objects.filter(raw_material=material).select_related("location","created_by").order_by("-date","-id")[:50]

    price_history = [
        {
            "date": po_date(line).isoformat(),
            "supplier": line.purchase_order.supplier or "Unnamed supplier",
            "unit_cost": _money(line.unit_cost),
            "purchase_unit": material.purchase_unit,
        }
        for line in purchase_lines
    ]

    linked_goods = []
    goods = FinishedGood.objects.filter(
        Q(recipe_items__raw_material=material) |
        Q(production_materials__raw_material=material)
    ).distinct().order_by("name")
    for good in goods:
        recipe_qty = sum(
            (x.qty_per_batch for x in good.recipe_items.filter(raw_material=material)),
            Decimal("0"),
        )
        production_qty = sum(
            (x.qty_per_batch for x in good.production_materials.filter(raw_material=material)),
            Decimal("0"),
        )
        linked_goods.append({
            "name": good.name,
            "qty_per_batch": _decimal(recipe_qty + production_qty),
            "usage_unit": material.usage_unit,
        })

    return {
        "title": material.name,
        "type": "Raw material",
        "identifier": material.get_category_display(),
        "uses_production": vocabulary["uses_production"],
        "recipe_label": vocabulary["recipe_label"],
        "recipe_item_label": vocabulary["recipe_item_label"],
        "production_inputs_label": vocabulary["production_inputs_label"],
        "summary": {
            "category": material.get_category_display(),
            "stock": _decimal(material.stock),
            "stock_unit": material.usage_unit,
            "purchase_unit": material.purchase_unit,
            "cost_per_purchase_unit": _money(material.cost_per_purchase_unit),
            "reorder_level": _decimal(material.reorder_level_purchase_units),
            "reorder_unit": material.purchase_unit,
            "last_purchase": purchases[-1]["date"] if purchases else None,
            "supplier_count": len({p["supplier"] for p in purchases}),
            "operational_dispensed": _decimal(sum((d.quantity for d in OperationalSupplyDispense.objects.filter(raw_material=material)), Decimal("0"))),
        },
        "periods": rows,
        "price_history": price_history,
        "purchases": purchases[-50:][::-1],
        "linked_goods": linked_goods,
    }


def _finished_good_search_detail(good):
    vocabulary = vertical_config(good.business)
    periods = _search_periods()
    recipe_lines = list(good.recipe_items.select_related("raw_material"))
    production_lines = list(good.production_materials.select_related("raw_material"))
    # Current recipe cost is retained for the summary card only. Historical
    # production/sale calculations must use frozen production-cost snapshots,
    # whose material lines were priced from the latest received procurement
    # snapshot available on the production date. Never weighted-average
    # historical procurement prices.
    recipe_cost_per_batch = sum(
        (line.raw_material.cost_per_unit * line.qty_per_batch for line in recipe_lines),
        Decimal("0"),
    ) + sum(
        (line.raw_material.cost_per_unit * line.qty_per_batch for line in production_lines),
        Decimal("0"),
    )
    units_per_batch = good.units_per_batch or Decimal("1")
    estimated_unit_cost = (
        recipe_cost_per_batch / units_per_batch
        if vocabulary["uses_production"]
        else good.est_cost
    )

    completed_orders = Order.objects.filter(
        status="completed", items__finished_good=good
    ).prefetch_related("items")
    sales = Sale.objects.filter(
        items__finished_good=good
    ).prefetch_related("items")

    channel_names = {
        "physical_store": "Physical store",
        "distribution": "Distribution",
        "online": "Online",
    }
    channel_rows = []
    for channel in ("physical_store", "distribution", "online"):
        batches_qs = ProductionBatch.objects.filter(
            finished_good=good,
            order__order_type=channel,
            order__status="completed",
            is_reversed=False,
        ).prefetch_related("reconciliation_in", "reconciliation_out")
        units = sum((b.saleable_units for b in batches_qs), Decimal("0"))
        shortages = sum((b.shortage_units for b in batches_qs), Decimal("0"))
        reconciled = sum((b.reconciled_units for b in batches_qs), Decimal("0"))
        planned_offcut_units = sum((b.planned_surplus_stock_units + b.planned_surplus_customer_units for b in batches_qs), Decimal("0"))
        additional_excess_units = sum((b.excess_units for b in batches_qs), Decimal("0"))
        excess_units = sum((b.total_surplus_units for b in batches_qs), Decimal("0"))
        excess_stock_units = sum((b.planned_surplus_stock_units + b.excess_stock_units for b in batches_qs), Decimal("0"))
        excess_market_stock_units = sum((b.excess_market_stock_units for b in batches_qs), Decimal("0"))
        excess_non_stock_units = sum((b.excess_non_stock_units for b in batches_qs), Decimal("0"))
        channel_rows.append({
            "channel": channel_names[channel],
            "production_events": batches_qs.count(),
            "units": _decimal(units),
            "shortage_units": _decimal(shortages),
            "reconciled_units": _decimal(reconciled),
            "outstanding_shortage_units": _decimal(max(Decimal("0"), shortages - reconciled)),
            "planned_offcut_units": _decimal(planned_offcut_units),
            "additional_excess_units": _decimal(additional_excess_units),
            "excess_units": _decimal(excess_units),
            "excess_stock_units": _decimal(excess_stock_units),
            "excess_market_stock_units": _decimal(excess_market_stock_units),
            "excess_non_stock_units": _decimal(excess_non_stock_units),
        })

    period_rows = []
    for label, start in periods:
        order_qs = completed_orders.filter(completed_date__range=(start, today()))
        sale_qs = sales.filter(date__range=(start, today()))
        batch_qs = ProductionBatch.objects.filter(
            finished_good=good,
            production_date__range=(start, today()),
            order__status="completed",
            is_reversed=False,
        ).prefetch_related("reconciliation_in", "reconciliation_out")
        produced = sum((b.saleable_units for b in batch_qs), Decimal("0"))
        production_events = batch_qs.count()
        shortage_units = sum((b.shortage_units for b in batch_qs), Decimal("0"))
        reconciled_units = sum((b.reconciled_units for b in batch_qs), Decimal("0"))
        planned_offcut_units = sum((b.planned_surplus_stock_units + b.planned_surplus_customer_units for b in batch_qs), Decimal("0"))
        additional_excess_units = sum((b.excess_units for b in batch_qs), Decimal("0"))
        excess_units = sum((b.total_surplus_units for b in batch_qs), Decimal("0"))
        excess_stock_units = sum((b.planned_surplus_stock_units + b.excess_stock_units for b in batch_qs), Decimal("0"))
        excess_market_stock_units = sum((b.excess_market_stock_units for b in batch_qs), Decimal("0"))
        excess_non_stock_units = sum((b.excess_non_stock_units for b in batch_qs), Decimal("0"))
        sold_units = Decimal("0")
        revenue = Decimal("0")
        unpaid_value = Decimal("0")
        sale_events = 0
        for sale in sale_qs:
            matched = [item for item in sale.items.all() if item.finished_good_id == good.id]
            if matched:
                sale_events += 1
            for item in matched:
                sold_units += item.total_units
                if sale.transaction_type == "paid":
                    revenue += item.line_total
                else:
                    unpaid_value += item.line_total
        # SaleItem.unit_cost is already frozen at sale time. For linked
        # customer orders it comes directly from that order's
        # ProductionCostSnapshot; walk-in sales use the latest production
        # snapshot available on the sale date. This keeps period margin
        # historical rather than recalculating it from today's material cost.
        historical_cost = Decimal("0")
        for sale in sale_qs:
            for sale_item in sale.items.all():
                if sale.transaction_type == "paid" and sale_item.finished_good_id == good.id and sale_item.unit_cost is not None:
                    historical_cost += sale_item.total_units * sale_item.unit_cost

        period_rows.append({
            "label": label,
            "produced_units": _decimal(produced),
            "production_events": production_events,
            "shortage_units": _decimal(shortage_units),
            "reconciled_units": _decimal(reconciled_units),
            "outstanding_shortage_units": _decimal(max(Decimal("0"), shortage_units - reconciled_units)),
            "planned_offcut_units": _decimal(planned_offcut_units),
            "additional_excess_units": _decimal(additional_excess_units),
            "excess_units": _decimal(excess_units),
            "excess_stock_units": _decimal(excess_stock_units),
            "excess_market_stock_units": _decimal(excess_market_stock_units),
            "excess_non_stock_units": _decimal(excess_non_stock_units),
            "sold_units": _decimal(sold_units),
            "sale_events": sale_events,
            "revenue": _money(revenue),
            "unpaid_product_value": _money(unpaid_value),
            "estimated_recipe_cost": _money(historical_cost),
            "gross_profit": _money(revenue - historical_cost),
            "gross_margin_pct": _money((revenue - historical_cost) / revenue * Decimal("100")) if revenue else 0,
        })

    sales_by_channel = []
    for source, label in (
        ("walkin", "Physical store sales"),
        ("distribution_order", "Distribution sales"),
        ("online_order", "Online sales"),
    ):
        qs = sales.filter(source=source)
        units = Decimal("0")
        revenue = Decimal("0")
        for sale in qs:
            for item in sale.items.all():
                if item.finished_good_id == good.id:
                    units += item.total_units
                    if sale.transaction_type == "paid":
                        revenue += item.line_total
        sales_by_channel.append({
            "channel": label,
            "units": _decimal(units),
            "revenue": _money(revenue),
        })

    return {
        "title": good.name,
        "type": "Finished good",
        "identifier": vocabulary["product_label"],
        "uses_production": vocabulary["uses_production"],
        "recipe_label": vocabulary["recipe_label"],
        "recipe_item_label": vocabulary["recipe_item_label"],
        "production_inputs_label": vocabulary["production_inputs_label"],
        "summary": {
            "unit": good.unit,
            "stock": _decimal(good.stock),
            "market_stock": _decimal(good.market_stock),
            "expired_market_stock": _decimal(good.expired_market_stock),
            "market_transfer_shelf_allowance": _decimal(good.transferred_market_stock),
            "total_produced": _decimal(good.total_produced),
            "delivered_to_customers": _decimal(good.total_delivered_to_customers),
            "selling_price": _money(good.selling_price),
            "estimated_recipe_cost_per_unit": _money(estimated_unit_cost),
            "estimated_margin_per_unit": _money(good.selling_price - estimated_unit_cost),
            "margin_pct": _money(((good.selling_price - estimated_unit_cost) / good.selling_price * 100) if good.selling_price else 0),
            "units_per_batch": _decimal(good.units_per_batch),
        },
        "periods": period_rows,
        "production_channels": channel_rows,
        "sales_channels": sales_by_channel,
        "recipe": [
            {"name": line.raw_material.name, "qty_per_batch": _decimal(line.qty_per_batch), "unit": line.raw_material.usage_unit, "current_cost": _money(line.raw_material.cost_per_unit)}
            for line in recipe_lines + production_lines
        ],
        "note": (
            f"Current {vocabulary['recipe_label'].lower()} cost uses current material costs. "
            "Historical production and sale margin use frozen production-cost snapshots based on "
            "the latest received procurement price available on each production date; procurement "
            "prices are never weighted-averaged."
        ),
    }


def _supplier_search_detail(supplier):
    purchase_orders = PurchaseOrder.objects.filter(
        supplier__iexact=supplier, status="received"
    ).prefetch_related("items__raw_material", "items__finished_good")
    periods = _search_periods()
    vendor_lines = []
    for po in purchase_orders:
        for line in po.items.all():
            vendor_lines.append(line)
    vendor_lines.sort(
        key=lambda line: (
            line.purchase_order.received_date or line.purchase_order.date,
            line.pk,
        )
    )

    rows = []
    for label, start in periods:
        qs = purchase_orders.filter(
            Q(received_date__range=(start, today())) |
            Q(received_date__isnull=True, date__range=(start, today()))
        )
        period_lines = [
            line for line in vendor_lines
            if start <= (line.purchase_order.received_date or line.purchase_order.date) <= today()
        ]
        spend = sum(
            (line.line_total or Decimal("0") for line in period_lines),
            Decimal("0"),
        )

        # A vendor can buy many different materials, so a single vendor-wide
        # unit-price delta would be mathematically misleading. Compare prices
        # only when the latest and preceding purchase are for the same item.
        latest = period_lines[-1] if period_lines else None
        price_difference = None
        price_difference_pct = None
        latest_unit_cost = None
        previous_unit_cost = None
        if latest:
            latest_unit_cost = latest.unit_cost
            # Include same-date earlier lines deterministically.
            latest_date = latest.purchase_order.received_date or latest.purchase_order.date
            prior_same_material = [
                line for line in vendor_lines
                if line.item_identity == latest.item_identity
                and (
                    (line.purchase_order.received_date or line.purchase_order.date) < latest_date
                    or (
                        (line.purchase_order.received_date or line.purchase_order.date) == latest_date
                        and line.pk < latest.pk
                    )
                )
            ]
            if prior_same_material:
                previous_unit_cost = prior_same_material[-1].unit_cost
                price_difference = latest_unit_cost - previous_unit_cost
                if previous_unit_cost:
                    price_difference_pct = (
                        price_difference / previous_unit_cost * Decimal("100")
                    )

        rows.append(_period_row(
            label,
            start,
            spend=spend,
            events=qs.count(),
            price_difference=price_difference,
            price_difference_pct=price_difference_pct,
            latest_unit_cost=latest_unit_cost,
            previous_unit_cost=previous_unit_cost,
        ))

    all_spend = sum((po.total for po in purchase_orders), Decimal("0"))
    last = purchase_orders.order_by("-received_date", "-date", "-id").first()
    material_totals = {}
    for po in purchase_orders:
        for line in po.items.all():
            key = line.item_name
            material_totals[key] = material_totals.get(key, Decimal("0")) + (line.line_total or Decimal("0"))

    return {
        "title": supplier,
        "type": "Procurement vendor",
        "identifier": "Supplier / vendor",
        "summary": {
            "total_purchase": _money(all_spend),
            "purchase_orders": purchase_orders.count(),
            "last_purchase": (last.received_date or last.date).isoformat() if last else None,
            "materials_bought": len(material_totals),
            "average_purchase": _money(all_spend / purchase_orders.count()) if purchase_orders.count() else 0,
        },
        "periods": rows,
        "materials": [
            {"name": name, "spend": _money(value)}
            for name, value in sorted(material_totals.items(), key=lambda x: -x[1])
        ],
        "purchases": [
            {
                "date": (po.received_date or po.date).isoformat(),
                "status": po.status,
                "total": _money(po.total),
                "items": [{"name": i.item_name, "qty": _decimal(i.qty), "unit": i.stock_unit, "unit_cost": _money(i.unit_cost), "total": _money(i.line_total)} for i in po.items.all()],
            }
            for po in purchase_orders[:50]
        ],
    }


def _customer_search_detail(name, channel):
    order_qs = Order.objects.filter(
        customer_name__iexact=name,
        order_type=channel,
    ).prefetch_related("items__finished_good")
    source = "distribution_order" if channel == "distribution" else "online_order"
    sale_qs = Sale.objects.filter(
        customer__iexact=name,
        source=source,
    ).prefetch_related("items__finished_good", "linked_order")
    periods = _search_periods()
    rows = []
    for label, start in periods:
        orders = order_qs.filter(date__range=(start, today()))
        sales = sale_qs.filter(date__range=(start, today()))
        units = Decimal("0")
        revenue = Decimal("0")
        for sale in sales:
            if sale.transaction_type == "paid":
                revenue += sale.total
            for item in sale.items.all():
                units += item.total_units
        rows.append(_period_row(label, start, revenue=revenue, units=units, events=orders.count()))

    total_revenue = Decimal("0")
    units = Decimal("0")
    for sale in sale_qs:
        if sale.transaction_type == "paid":
            total_revenue += sale.total
        for item in sale.items.all():
            units += item.total_units
    last_order = order_qs.order_by("-date", "-id").first()
    last_sale = sale_qs.order_by("-date", "-id").first()
    regions = sorted({o.customer_region.strip() for o in order_qs if o.customer_region.strip()})
    groups = sorted({o.customer_group.strip() for o in order_qs if o.customer_group.strip()})

    return {
        "title": name,
        "type": "Customer",
        "identifier": "Distribution customer" if channel == "distribution" else "Online customer",
        "summary": {
            "channel": "Distribution" if channel == "distribution" else "Online",
            "orders": order_qs.count(),
            "completed_orders": order_qs.filter(status="completed").count(),
            "sales": sale_qs.count(),
            "revenue": _money(total_revenue),
            "units": _decimal(units),
            "last_order": last_order.date.isoformat() if last_order else None,
            "last_sale": last_sale.date.isoformat() if last_sale else None,
            "regions": regions,
            "groups": groups,
        },
        "periods": rows,
        "orders": [
            {
                "date": o.date.isoformat(),
                "status": o.status,
                "region": o.customer_region or "—",
                "group": o.customer_group or "—",
                "total": _money(o.total),
                "units": _decimal(o.total_units),
            }
            for o in order_qs[:50]
        ],
        "sales": [
            {"date": s.date.isoformat(), "total": _money(s.total), "payment": (s.payment_method if s.transaction_type == "paid" else s.get_transaction_type_display())}
            for s in sale_qs[:50]
        ],
    }


def _segment_search_detail(kind, value):
    field = "customer_region" if kind == "region" else "customer_group"
    orders = Order.objects.filter(**{f"{field}__iexact": value}).exclude(
        customer_name=""
    ).prefetch_related("items")
    sales = Sale.objects.filter(
        linked_order__in=orders,
        source__in=("distribution_order", "online_order"),
    ).prefetch_related("items")
    periods = []
    for label, start in _search_periods():
        os = orders.filter(date__range=(start, today()))
        ss = sales.filter(date__range=(start, today()))
        revenue = sum((s.total for s in ss if s.transaction_type == "paid"), Decimal("0"))
        periods.append(_period_row(label, start, revenue=revenue, events=os.count()))
    customers = sorted({o.customer_name for o in orders if o.customer_name})
    return {
        "title": value,
        "type": "Customer region" if kind == "region" else "Customer group",
        "identifier": "Distribution / Online analytics",
        "summary": {
            "orders": orders.count(),
            "sales": sales.count(),
            "revenue": _money(sum((s.total for s in sales), Decimal("0"))),
            "customers": len(customers),
            "customers_sample": customers[:30],
        },
        "periods": periods,
        "customers": customers[:100],
    }


@login_required
def dashboard_search(request):
    q = request.GET.get("q", "")
    return JsonResponse({"results": _search_results(q)})


@login_required
def dashboard_search_detail(request):
    kind = request.GET.get("type", "")
    pk = request.GET.get("id", "")
    channel = request.GET.get("channel", "")
    value = request.GET.get("value", "")

    detail = None
    if kind == "raw" and pk.isdigit():
        material = RawMaterial.objects.filter(pk=int(pk)).first()
        if material:
            detail = _raw_search_detail(material)
    elif kind == "fg" and pk.isdigit():
        good = FinishedGood.objects.filter(pk=int(pk)).first()
        if good:
            detail = _finished_good_search_detail(good)
    elif kind == "supplier" and value:
        detail = _supplier_search_detail(value)
    elif kind == "customer" and value and channel in ("distribution", "online"):
        detail = _customer_search_detail(value, channel)
    elif kind in ("region", "group") and value:
        detail = _segment_search_detail(kind, value)

    if not detail:
        return JsonResponse({"error": "Search object not found."}, status=404)
    return JsonResponse(detail)



def reports_full_required(view_func):
    from functools import wraps
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        from accounts.subscription_services import business_has_feature
        if not business_has_feature(getattr(request, "business", None), "reports_full"):
            return render(request, "403.html", {"feature_message": "Your current plan includes Basic Reports. Upgrade for this export/backup feature."}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapped


@login_required
def dashboard(request):
    dashboard_date = today()
    month_start = dashboard_date.replace(day=1)
    year_start = dashboard_date.replace(month=1, day=1)
    raw_materials = list(RawMaterial.objects.all())
    finished_goods = list(FinishedGood.objects.select_related("business"))
    warning_raw = [m for m in raw_materials if m.is_warning]
    warning_goods = [g for g in finished_goods if g.is_warning]
    low_raw = [m for m in raw_materials if m.is_low]
    low_goods = [g for g in finished_goods if g.is_low]
    total_low_count = len(low_raw) + len(low_goods)
    total_warning_count = len(warning_raw) + len(warning_goods)
    if request.business.uses_production:
        pending_orders_count = Order.objects.filter(status="pending").count()
        open_purchase_orders_count = 0
    else:
        pending_orders_count = 0
        open_purchase_orders_count = PurchaseOrder.objects.exclude(status="received").count()

    # The dashboard uses current-month sales for today's KPIs and both channel
    # summaries. Load that relation graph once instead of repeating it for each
    # card/modal.
    month_sales = list(
        Sale.objects.filter(date__range=(month_start, dashboard_date))
        .select_related("linked_order")
        .prefetch_related("items__finished_good")
    )
    today_sales = [sale for sale in month_sales if sale.date == dashboard_date]
    today_revenue = sum(
        (sale.total for sale in today_sales if sale.transaction_type == "paid"),
        Decimal("0"),
    )

    received_products = StockMovement.objects.filter(
        movement_type=StockMovement.FG_PURCHASE,
        quantity__gt=0,
        affects_stock=True,
    )
    received_totals = received_products.aggregate(
        daily=Sum("quantity", filter=Q(occurred_at__date=dashboard_date)),
        monthly=Sum("quantity", filter=Q(occurred_at__date__gte=month_start)),
        yearly=Sum("quantity", filter=Q(occurred_at__date__gte=year_start)),
    )
    daily_units_received = received_totals["daily"] or Decimal("0")

    if request.business.uses_production:
        completed_orders = list(
            Order.objects.filter(status="completed", completed_date__gte=year_start)
            .prefetch_related("items__finished_good")
        )
        daily_units_made = sum(
            (order.total_units for order in completed_orders if order.completed_date == dashboard_date),
            Decimal("0"),
        )
        monthly_units = sum(
            (order.total_units for order in completed_orders if order.completed_date >= month_start),
            Decimal("0"),
        )
        yearly_units = sum((order.total_units for order in completed_orders), Decimal("0"))
    else:
        daily_units_made = _production_units(
            Order.objects.filter(status="completed", completed_date=dashboard_date)
        )
        monthly_units = received_totals["monthly"] or Decimal("0")
        yearly_units = received_totals["yearly"] or Decimal("0")
    financial_ledger_start = min(
        year_start, dashboard_date - timedelta(days=dashboard_date.weekday())
    )
    financial_ledger = _financial_daily_ledgers(financial_ledger_start, dashboard_date)
    financial = _financial_snapshot(financial_ledger)
    financial_json = _financial_chart_series(financial_ledger)
    channel = _sales_by_channel(month_start, dashboard_date, sales=month_sales)
    channel_breakdowns = {
        "distribution": _channel_breakdown(
            "distribution",
            month_start,
            dashboard_date,
            sales=month_sales,
        ),
        "online": _channel_breakdown(
            "online",
            month_start,
            dashboard_date,
            sales=month_sales,
        ),
    }

    raw_material_categories = []
    for value, label in RawMaterial.CATEGORY_CHOICES:
        items = [material for material in raw_materials if material.category == value]
        raw_material_categories.append({"value": value, "label": label, "items": items})

    selected_key = request.GET.get("stock_item", "")
    selected_kind, _, selected_pk = selected_key.partition(":")
    selected_item = None
    stock_periods = None
    stock_unit = ""
    if selected_kind == "raw":
        selected_item = next((item for item in raw_materials if str(item.pk) == selected_pk), None)
        if selected_item:
            stock_periods = _stock_periods(selected_item)
            stock_unit = selected_item.usage_unit
    elif selected_kind == "fg":
        selected_item = next((item for item in finished_goods if str(item.pk) == selected_pk), None)
        if selected_item:
            stock_periods = _stock_periods(selected_item)
            stock_unit = selected_item.unit

    return render(request, "core/dashboard.html", {
        "raw_count": len(raw_materials),
        "goods_count": len(finished_goods),
        "warning_raw": warning_raw,
        "warning_goods": warning_goods,
        "low_raw": low_raw,
        "low_goods": low_goods,
        "total_low_count": total_low_count,
        "total_warning_count": total_warning_count,
        "pending_orders_count": pending_orders_count,
        "open_purchase_orders_count": open_purchase_orders_count,
        "today_sales_count": len(today_sales),
        "today_revenue": today_revenue,
        "daily_units_made": daily_units_made,
        "daily_units_received": daily_units_received,
        "monthly_units": monthly_units,
        "yearly_units": yearly_units,
        "financial": financial,
        "financial_json": financial_json,
        "channel_sales": channel,
        "channel_breakdowns": channel_breakdowns,
        "all_raw_materials": raw_materials,
        "raw_material_categories": raw_material_categories,
        "all_finished_goods": finished_goods,
        "selected_kind": selected_kind,
        "selected_pk": selected_pk,
        "selected_item": selected_item,
        "stock_periods": stock_periods,
        "stock_unit": stock_unit,
    })




@login_required
@require_POST
def onboarding_tour_complete(request):
    """Persist completion/dismissal for the current tenant membership.

    The endpoint is intentionally tiny: the tour itself is static UI guidance,
    so persisting one version integer must not add work to normal navigation.
    """
    from .onboarding import CURRENT_TOUR_VERSION

    updated = UserBusiness.objects.filter(
        user=request.user, business=request.business, active=True
    ).update(onboarding_tour_version=CURRENT_TOUR_VERSION)
    return JsonResponse({
        "ok": True,
        "updated": bool(updated),
        "version": CURRENT_TOUR_VERSION,
    })


@login_required
def dashboard_financial_breakdown(request):
    """Load the dashboard's detailed finance modal only when it is opened.

    The month/year breakdown touches several ledgers. Keeping it out of the
    initial dashboard render materially reduces warm-navigation database work,
    while preserving exactly the same figures when a user requests the modal.
    """
    scope = request.GET.get("scope", "month")
    dashboard_date = today()
    if scope == "month":
        start = dashboard_date.replace(day=1)
    elif scope == "year":
        start = dashboard_date.replace(month=1, day=1)
    else:
        return JsonResponse({"error": "Invalid financial scope."}, status=400)

    return JsonResponse(_financial_breakdown(start, dashboard_date))


@login_required
def reports(request):
    return render(request, "core/reports.html")


@login_required
def business_settings(request):
    if not is_business_admin(request.user, request.business):
        return render(request, "403.html", status=403)
    seed_business_modules(request.business)
    if request.method == "POST":
        previous_slug = request.business.slug
        form = BusinessForm(request.POST, request.FILES, instance=request.business)
        if form.is_valid():
            business = form.save()
            audit(
                business, request.user, "update", business,
                "Business preferences updated",
                {
                    "vertical": business.vertical,
                    "background_color": business.background_color,
                    "accent_color": business.accent_color,
                    "previous_slug": previous_slug,
                    "slug": business.slug,
                    "storefront_logo_configured": bool(business.storefront_logo),
                },
            )
            messages.success(request, "Business preferences updated.")
            return redirect("business_settings")
    else:
        form = BusinessForm(instance=request.business)
    modules = request.business.module_access.order_by("module")
    return render(request, "core/business_settings.html", {"form": form, "business_modules": modules})


@login_required
@require_POST
def switch_business(request):
    try:
        business_id = int(request.POST.get("business_id", ""))
    except (TypeError, ValueError):
        return render(request, "403.html", status=403)
    if request.user.is_superuser:
        allowed = Business.objects.filter(pk=business_id).exists()
    else:
        allowed = UserBusiness.objects.filter(
            user=request.user, business_id=business_id, active=True
        ).exists()
    if not allowed:
        return render(request, "403.html", status=403)
    request.session["active_business_id"] = business_id
    next_url = request.POST.get("next") or "dashboard"
    if next_url != "dashboard" and not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = "dashboard"
    return redirect(next_url)


def _csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return response


def _xlsx_response(filename, sheet_name, header, rows):
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
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


def _stock_rows():
    rows = []
    for m in RawMaterial.objects.all():
        rows.append(["Raw material", m.name, m.get_category_display(), m.usage_unit, m.stock, "", "", "", m.reorder_level, m.cost_per_unit, m.purchase_unit, m.total_conversion_factor])
    for g in FinishedGood.objects.prefetch_related("market_stock_lots"):
        rows.append(["Finished good", g.name, "Finished good", g.unit, g.stock, g.market_stock, g.expired_market_stock, g.transferred_market_stock, g.reorder_level, g.selling_price, "", ""])
    return rows


def _market_stock_rows():
    rows = []
    for movement in MarketStockMovement.objects.select_related(
        "lot__finished_good", "lot__production_batch", "customer", "sale"
    ):
        rows.append([
            movement.date,
            movement.lot.finished_good.name,
            movement.lot.production_batch.batch_number if movement.lot.production_batch else "",
            movement.get_movement_type_display(),
            movement.quantity,
            movement.balance_after,
            movement.customer.name if movement.customer else "",
            movement.sale_id or "",
            movement.unit_value,
            movement.value,
            movement.note,
        ])
    for returned in DistributionReturn.objects.filter(
        condition=DistributionReturn.DAMAGED
    ).select_related("sale_item__sale", "sale_item__finished_good", "sale_item__production_batch"):
        rows.append([
            returned.date,
            returned.sale_item.finished_good.name,
            returned.sale_item.production_batch.batch_number if returned.sale_item.production_batch else "",
            "Damaged Distribution return",
            -returned.quantity,
            "",
            returned.sale_item.sale.customer,
            returned.sale_item.sale_id,
            returned.unit_value,
            -returned.writeoff_value,
            returned.reason,
        ])
    return sorted(rows, key=lambda row: (row[0], str(row[1])), reverse=True)


def _procurement_rows():
    rows = []
    for p in PurchaseOrder.objects.prefetch_related("items__raw_material", "items__finished_good"):
        for i in p.items.all():
            rows.append([p.date, p.received_date, p.supplier, p.status, p.payment_status, p.payment_method, i.item_name, i.item_category, i.qty, i.stock_unit, i.unit_cost, i.line_total])
    return rows


def _production_rows():
    rows = []
    for o in Order.objects.filter(status="completed").prefetch_related("items__finished_good"):
        for item in o.items.all():
            rows.append([o.completed_date, o.display_order_type, o.transaction_type, o.unpaid_description, o.customer_name, item.finished_good.name, item.total_units, item.line_total])
    return rows


def _sales_rows(qs=None):
    qs = qs or Sale.objects.all()
    rows = []
    for s in qs.prefetch_related("items__finished_good"):
        items = ", ".join(f"{i.finished_good.name} x{i.total_units}" for i in s.items.all())
        rows.append([s.date, s.customer, items, s.total, s.transaction_type, s.unpaid_description, s.payment_method, s.display_source])
    return rows



def _adjustment_rows():
    return [[a.date, a.raw_material.name if a.raw_material else a.finished_good.name, "Raw material" if a.raw_material else "Finished good", a.get_reason_display(), a.quantity, a.unit_value, a.value, a.description, a.location.name if a.location else ""] for a in StockAdjustment.objects.select_related("raw_material","finished_good","location")]

def _operational_dispense_rows():
    return [[d.date, d.raw_material.name, d.raw_material.usage_unit, d.quantity, d.get_reason_display(), d.description, d.location.name if d.location else "", d.created_by.username if d.created_by else ""] for d in OperationalSupplyDispense.objects.select_related("raw_material","location","created_by")]


def _expense_rows():
    rows = []
    for e in Expense.objects.all():
        rows.append([e.date, e.get_category_display(), e.description, e.vendor, e.amount, e.payment_status, e.payment_method, e.notes])
    return rows


@login_required
def export_stock_csv(request):
    return _csv_response("stock-report.csv", ["Type", "Name", "Category", "Stock Unit", "Physical/Material Stock", "Distribution Market Stock", "Expired Market Stock", "Market-origin Shelf Allowance", "Reorder level", "Cost/Price per unit", "Purchase Unit", "Usage Units per Purchase Unit"], _stock_rows())


@login_required
@reports_full_required
def export_stock_xlsx(request):
    return _xlsx_response("stock-report.xlsx", "Stock", ["Type", "Name", "Category", "Stock Unit", "Physical/Material Stock", "Distribution Market Stock", "Expired Market Stock", "Market-origin Shelf Allowance", "Reorder level", "Cost/Price per unit", "Purchase Unit", "Usage Units per Purchase Unit"], _stock_rows())


@login_required
@reports_full_required
def export_market_stock_csv(request):
    return _csv_response(
        "distribution-market-stock.csv",
        ["Date", "Product", "Batch", "Event", "Signed Quantity", "Lot Balance", "Customer", "Sale", "Unit Cost", "Signed Value", "Note"],
        _market_stock_rows(),
    )


@login_required
@reports_full_required
def export_market_stock_xlsx(request):
    return _xlsx_response(
        "distribution-market-stock.xlsx",
        "Distribution Market Stock",
        ["Date", "Product", "Batch", "Event", "Signed Quantity", "Lot Balance", "Customer", "Sale", "Unit Cost", "Signed Value", "Note"],
        _market_stock_rows(),
    )


@login_required
@reports_full_required
def export_procurement_csv(request):
    return _csv_response("procurement-report.csv", ["Date", "Received Date", "Supplier", "Status", "Payment Status", "Payment Method", "Item", "Category", "Qty", "Stock / Purchase Unit", "Unit Cost", "Line Total"], _procurement_rows())


@login_required
@reports_full_required
def export_procurement_xlsx(request):
    return _xlsx_response("procurement-report.xlsx", "Procurement", ["Date", "Received Date", "Supplier", "Status", "Payment Status", "Payment Method", "Item", "Category", "Qty", "Stock / Purchase Unit", "Unit Cost", "Line Total"], _procurement_rows())


@login_required
@reports_full_required
def export_production_csv(request):
    return _csv_response("production-report.csv", ["Date", "Type", "Transaction", "Unpaid Reason", "Customer", "Product", "Qty produced", "Order Value"], _production_rows())


@login_required
@reports_full_required
def export_production_xlsx(request):
    return _xlsx_response("production-report.xlsx", "Production", ["Date", "Type", "Transaction", "Unpaid Reason", "Customer", "Product", "Qty produced", "Order Value"], _production_rows())


@login_required
def export_sales_csv(request):
    qs = Sale.objects.all()
    date_from = request.GET.get("from")
    date_to = request.GET.get("to")
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    return _csv_response("sales-report.csv", ["Date", "Customer", "Items", "Total", "Transaction", "Unpaid Reason", "Payment", "Source"], _sales_rows(qs))


@login_required
@reports_full_required
def export_sales_xlsx(request):
    qs = Sale.objects.all()
    date_from = request.GET.get("from")
    date_to = request.GET.get("to")
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    return _xlsx_response("sales-report.xlsx", "Sales", ["Date", "Customer", "Items", "Total", "Transaction", "Unpaid Reason", "Payment", "Source"], _sales_rows(qs))


@login_required
@reports_full_required
def export_expenses_csv(request):
    return _csv_response("expenses-report.csv", ["Date", "Category", "Description", "Vendor", "Amount", "Payment Status", "Payment Method", "Notes"], _expense_rows())


@login_required
@reports_full_required
def export_expenses_xlsx(request):
    return _xlsx_response("expenses-report.xlsx", "Expenses", ["Date", "Category", "Description", "Vendor", "Amount", "Payment Status", "Payment Method", "Notes"], _expense_rows())


@login_required
@reports_full_required
def export_adjustments_csv(request):
    return _csv_response("stock-adjustments.csv", ["Date", "Item", "Type", "Reason", "Quantity", "Unit Value", "Value", "Description", "Location"], _adjustment_rows())

@login_required
@reports_full_required
def export_adjustments_xlsx(request):
    return _xlsx_response("stock-adjustments.xlsx", "Adjustments", ["Date", "Item", "Type", "Reason", "Quantity", "Unit Value", "Value", "Description", "Location"], _adjustment_rows())

@login_required
@reports_full_required
def export_operational_dispenses_csv(request):
    return _csv_response("operational-supply-dispenses.csv", ["Date","Operational supply","Usage unit","Quantity","Reason","Description","Location","Logged by"], _operational_dispense_rows())


@login_required
@reports_full_required
def export_operational_dispenses_xlsx(request):
    return _xlsx_response("operational-supply-dispenses.xlsx", "Operational Dispenses", ["Date","Operational supply","Usage unit","Quantity","Reason","Description","Location","Logged by"], _operational_dispense_rows())


@login_required
@reports_full_required
def export_financial_csv(request):
    rows = [[r["label"], r["sales"], r["cogs"], r["gross_profit"], r["procurement"], r["cash_procurement"], r["misc"], r["spend"], r["net_cash_flow"]] for r in _financial_snapshot()]
    return _csv_response("financial-summary.csv", ["Period", "Paid Sales", "COGS", "Gross Profit", "Procurement Received", "Procurement Cash Paid", "Expenses Paid", "Cash Outflow", "Net Cash Flow"], rows)


@login_required
@reports_full_required
def export_financial_xlsx(request):
    rows = [[r["label"], r["sales"], r["cogs"], r["gross_profit"], r["procurement"], r["cash_procurement"], r["misc"], r["spend"], r["net_cash_flow"]] for r in _financial_snapshot()]
    return _xlsx_response("financial-summary.xlsx", "Financial Summary", ["Period", "Paid Sales", "COGS", "Gross Profit", "Procurement Received", "Procurement Cash Paid", "Expenses Paid", "Cash Outflow", "Net Cash Flow"], rows)


@login_required
@reports_full_required
def backup_json(request):
    # Backups are an explicit tenant boundary. Never rely on the ambient scoped
    # manager here: Business itself is unscoped and several child tables (recipe
    # items, order items, sale items, etc.) do not carry a business_id column.
    from .services import tenant_backup_objects

    business = getattr(request, "business", None)
    if business is None:
        return JsonResponse({"error": "No active business is selected."}, status=400)
    data = serializers.serialize("json", tenant_backup_objects(business), indent=2)
    response = HttpResponse(data, content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="inprofic-{business.slug}-backup-{today()}.json"'
    response["X-Content-Type-Options"] = "nosniff"
    return response
