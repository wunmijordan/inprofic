"""Daily-balance data for the dashboard Raw materials / Products cards.

Opening balance = current balance minus the net of today's stock-affecting
movements, so the figure the card shows starts the day at its opening balance
and moves with every update made that day. Finished goods carry two balances:
Physical Store stock and Distribution Market stock.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from inventory.models import FinishedGood, MarketStockMovement, RawMaterial, StockMovement

MAX_ITEMS = 60
ZERO = Decimal("0")


def _num(value):
    return float(Decimal(value or 0).quantize(Decimal("0.01")))


def _balance(current, net_today):
    current = Decimal(current or 0)
    opening = max(ZERO, current - Decimal(net_today or 0))
    return {"balance": _num(current), "opening": _num(opening), "change": _num(current - opening)}


def build_stock_ticker(day):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    end = start + timedelta(days=1)

    todays = StockMovement.objects.filter(occurred_at__gte=start, occurred_at__lt=end, affects_stock=True)
    # One grouped query covers both raw materials and finished goods.
    raw_net, good_net = {}, {}
    for row in todays.values("raw_material_id", "finished_good_id").annotate(net=Sum("quantity")):
        if row["raw_material_id"]:
            raw_net[row["raw_material_id"]] = row["net"]
        elif row["finished_good_id"]:
            good_net[row["finished_good_id"]] = row["net"]
    market_net = {
        row["lot__finished_good_id"]: row["net"]
        for row in MarketStockMovement.objects.filter(date=day).values("lot__finished_good_id").annotate(net=Sum("quantity"))
    }

    raw = []
    for material in RawMaterial.objects.all().order_by("name"):
        net = raw_net.get(material.pk)
        stock = Decimal(material.stock or 0)
        if stock <= 0 and not net:
            continue
        raw.append({"id": material.pk, "name": material.name, "unit": material.usage_unit or "", **_balance(stock, net)})

    finished = []
    # Bulk-load what the stock properties read per product (its business and
    # market lots); without this every product costs extra queries per refresh.
    for good in FinishedGood.objects.select_related("business").prefetch_related("market_stock_lots").order_by("name"):
        store_now = good.physical_saleable_stock
        market_now = good.market_stock
        store_net, mkt_net = good_net.get(good.pk), market_net.get(good.pk)
        if store_now <= 0 and market_now <= 0 and not store_net and not mkt_net:
            continue
        row = {"id": good.pk, "name": good.name, "unit": good.unit or "", "store": _balance(store_now, store_net), "market": None}
        if market_now > 0 or mkt_net:
            row["market"] = _balance(market_now, mkt_net)
        finished.append(row)

    return {"raw": raw[:MAX_ITEMS], "finished": finished[:MAX_ITEMS], "date": day.isoformat()}
