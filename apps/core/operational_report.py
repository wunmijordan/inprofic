"""Monthly, tenant-scoped operational analysis presented as a PDF."""
from datetime import date
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from commerce.models import CommerceIntake
from inventory.models import FinishedGood, MarketStockLot, MarketStockMovement, RawMaterial, StockMovement
from procurement.models import PurchaseOrder
from production.models import ProductionBatch
from sales.models import Sale
from .pdf_fonts import PDF_BODY_BOLD_FONT, PDF_BODY_FONT, PDF_DISPLAY_FONT


ZERO = Decimal("0")
NAVY = colors.HexColor("#050733")
ORANGE = colors.HexColor("#d14900")
BURGUNDY = colors.HexColor("#8f172d")
PAPER = colors.HexColor("#f5efe0")
MUTED = colors.HexColor("#66606b")
SOURCE_KEYS = ("manual", "hosted", "pos", "external")
SOURCE_LABELS = ("Manual entry", "Hosted storefront", "In-premise POS", "External API / connector")
SOURCE_COLORS = (NAVY, ORANGE, BURGUNDY, colors.HexColor("#a16b24"))


def _month_rows(start, end):
    cursor = start.replace(day=1)
    rows = []
    while cursor <= end:
        rows.append({
            "month": cursor,
            "revenue": ZERO, "cogs": ZERO, "procurement": ZERO,
            "raw_procurement": ZERO, "production_supplies": ZERO,
            "operational_supplies": ZERO, "resale_procurement": ZERO,
            "production_cost": ZERO, "wastage_cost": ZERO, "batches": 0,
            "stock_in": ZERO, "stock_out": ZERO, "market_in": ZERO, "market_out": ZERO,
            **{key: ZERO for key in SOURCE_KEYS},
        })
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return rows


def _source_bucket(source):
    if source in (CommerceIntake.SOURCE_API, CommerceIntake.SOURCE_CONNECTOR):
        return "external"
    if source == CommerceIntake.SOURCE_STAFF_POS:
        return "pos"
    return "hosted"


def collect_operational_report(business, start, end):
    """Use recorded transactions once each; never treat internal conversions as cash."""
    rows = _month_rows(start, end)
    by_month = {(row["month"].year, row["month"].month): row for row in rows}

    sales = list(
        Sale.objects.filter(business=business, date__range=(start, end), transaction_type="paid")
        .exclude(linked_order__status="reversed")
        .prefetch_related("items__finished_good")
    )
    sale_ids = [sale.pk for sale in sales]
    order_ids = [sale.linked_order_id for sale in sales if sale.linked_order_id]
    by_sale = {}
    by_order = {}
    if sale_ids or order_ids:
        intakes = CommerceIntake.objects.filter(business=business).filter(
            Q(accepted_sale_id__in=sale_ids)
            | Q(accepted_order_id__in=order_ids)
            | Q(split_order_id__in=order_ids)
        ).order_by("id")
        for intake in intakes:
            bucket = _source_bucket(intake.source)
            if intake.accepted_sale_id:
                by_sale.setdefault(intake.accepted_sale_id, bucket)
            for order_id in (intake.accepted_order_id, intake.split_order_id):
                if order_id:
                    by_order.setdefault(order_id, bucket)

    for sale in sales:
        row = by_month[(sale.date.year, sale.date.month)]
        amount = sale.total
        row["revenue"] += amount
        bucket = by_sale.get(sale.pk) or by_order.get(sale.linked_order_id) or "manual"
        row[bucket] += amount
        for item in sale.items.all():
            row["cogs"] += (item.unit_cost or ZERO) * item.total_units

    purchases = PurchaseOrder.objects.filter(business=business, status="received").filter(
        Q(received_date__range=(start, end))
        | Q(received_date__isnull=True, date__range=(start, end))
    ).prefetch_related("items__raw_material")
    for purchase in purchases:
        received = purchase.received_date or purchase.date
        row = by_month[(received.year, received.month)]
        for item in purchase.items.all():
            amount = item.line_total
            row["procurement"] += amount
            if item.finished_good_id:
                row["resale_procurement"] += amount
            elif item.raw_material.category == RawMaterial.CATEGORY_INGREDIENT:
                row["raw_procurement"] += amount
            elif item.raw_material.category == RawMaterial.CATEGORY_OPERATIONAL_SUPPLY:
                row["operational_supplies"] += amount
            else:
                row["production_supplies"] += amount

    for batch in ProductionBatch.objects.filter(
        business=business, production_date__range=(start, end),
        is_reversed=False, order__status="completed",
    ).only("production_date", "total_cost", "wastage_units", "unit_cost"):
        row = by_month[(batch.production_date.year, batch.production_date.month)]
        row["batches"] += 1
        row["production_cost"] += batch.total_cost or ZERO
        row["wastage_cost"] += (batch.wastage_units or ZERO) * (batch.unit_cost or ZERO)

    for movement in StockMovement.objects.filter(
        business=business, occurred_at__date__range=(start, end), affects_stock=True,
    ).only("occurred_at", "quantity", "unit_value"):
        local_date = timezone.localtime(movement.occurred_at).date()
        if not start <= local_date <= end:
            continue
        row = by_month[(local_date.year, local_date.month)]
        value = abs(movement.quantity or ZERO) * abs(movement.unit_value or ZERO)
        row["stock_in" if movement.quantity > 0 else "stock_out"] += value

    for movement in MarketStockMovement.objects.filter(
        business=business, date__range=(start, end),
    ).only("date", "quantity", "unit_value"):
        row = by_month[(movement.date.year, movement.date.month)]
        value = abs(movement.quantity or ZERO) * abs(movement.unit_value or ZERO)
        row["market_in" if movement.quantity > 0 else "market_out"] += value

    totals = {key: sum((row[key] for row in rows), ZERO) for key in (
        "revenue", "cogs", "procurement", "raw_procurement", "production_supplies",
        "operational_supplies", "resale_procurement", "production_cost", "wastage_cost",
        "stock_in", "stock_out", "market_in", "market_out", *SOURCE_KEYS,
    )}
    totals["batches"] = sum(row["batches"] for row in rows)
    raw_materials = list(RawMaterial.objects.filter(business=business).only("stock", "cost_per_unit", "reorder_level"))
    finished_goods = list(FinishedGood.objects.filter(business=business).only("stock", "reorder_level"))
    market_lots = list(MarketStockLot.objects.filter(business=business).only("quantity_available", "unit_cost"))
    snapshot = {
        "raw_value": sum(((item.stock or ZERO) * (item.cost_per_unit or ZERO) for item in raw_materials), ZERO),
        "market_value": sum(((lot.quantity_available or ZERO) * (lot.unit_cost or ZERO) for lot in market_lots), ZERO),
        "low_raw": sum(item.stock is not None and item.reorder_level is not None and item.stock <= item.reorder_level for item in raw_materials),
        "low_finished": sum(item.stock is not None and item.reorder_level is not None and item.stock <= item.reorder_level for item in finished_goods),
        "as_of": timezone.localdate(),
    }
    return {"rows": rows, "totals": totals, "snapshot": snapshot, "start": start, "end": end}


def _money(value, symbol):
    return f"{symbol}{value:,.2f}"


def _ratio(value, revenue):
    return f"{value / revenue * Decimal('100'):,.1f}%" if revenue else "—"


def _style_table(table, header=True):
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 0), (-1, -1), PDF_BODY_FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), .3, colors.HexColor("#dfd9ca")),
    ]
    if header:
        commands += [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), PDF_BODY_BOLD_FONT),
        ]
    table.setStyle(TableStyle(commands))
    return table


def _chart(rows, keys, palette, title, width, labels, stacked=False):
    height = 73 * mm
    drawing = Drawing(width, height)
    drawing.add(String(0, height - 12, title, fontName=PDF_BODY_BOLD_FONT, fontSize=10, fillColor=NAVY))
    top = height - 45
    baseline = 31
    plot_height = top - baseline
    max_value = max((sum((row[key] for key in keys), ZERO) if stacked else max(row[key] for key in keys) for row in rows), default=ZERO)
    scale = plot_height / float(max_value) if max_value > 0 else 0
    for index, (label, color) in enumerate(zip(labels, palette)):
        x = index * (width / len(labels))
        drawing.add(Rect(x, height - 32, 7, 7, fillColor=color, strokeColor=None))
        drawing.add(String(x + 11, height - 31, label, fontName=PDF_BODY_FONT, fontSize=7, fillColor=MUTED))
    drawing.add(Line(0, baseline, width, baseline, strokeColor=colors.HexColor("#d9cfb4"), strokeWidth=.7))
    slot = width / max(len(rows), 1)
    for index, row in enumerate(rows):
        center = (index + .5) * slot
        if stacked:
            y = baseline
            bar_width = min(25, slot * .55)
            for key, color in zip(keys, palette):
                bar_height = max(0, float(row[key]) * scale)
                if bar_height:
                    drawing.add(Rect(center - bar_width / 2, y, bar_width, bar_height, fillColor=color, strokeColor=None))
                    y += bar_height
        else:
            bar_width = min(15, slot * .25)
            for offset, (key, color) in enumerate(zip(keys, palette)):
                bar_height = max(0, float(row[key]) * scale)
                if bar_height:
                    drawing.add(Rect(center + (offset - (len(keys) - 1) / 2) * (bar_width + 3) - bar_width / 2, baseline, bar_width, bar_height, fillColor=color, strokeColor=None))
        drawing.add(String(center, 17, row["month"].strftime("%b %y"), fontName=PDF_BODY_FONT, fontSize=6.6, textAnchor="middle", fillColor=MUTED))
    return drawing


def _page(canvas, doc):
    canvas.setStrokeColor(colors.HexColor("#d9cfb4"))
    canvas.line(doc.leftMargin, 13 * mm, landscape(A4)[0] - doc.rightMargin, 13 * mm)
    canvas.setFont(PDF_BODY_FONT, 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(doc.leftMargin, 9 * mm, "INPROFIC · Operational performance")
    canvas.drawRightString(landscape(A4)[0] - doc.rightMargin, 9 * mm, str(doc.page))


def render_operational_pdf(business, report):
    rows, totals, snapshot = report["rows"], report["totals"], report["snapshot"]
    symbol = business.currency_symbol or "₦"
    width = 260 * mm
    styles = getSampleStyleSheet()
    title = ParagraphStyle("ops_title", parent=styles["Title"], fontName=PDF_DISPLAY_FONT, fontSize=22, leading=27, textColor=NAVY)
    heading = ParagraphStyle("ops_heading", parent=styles["Heading2"], fontName=PDF_DISPLAY_FONT, fontSize=13, leading=17, textColor=BURGUNDY, spaceBefore=10, spaceAfter=7)
    body = ParagraphStyle("ops_body", parent=styles["Normal"], fontName=PDF_BODY_FONT, fontSize=8, leading=12, textColor=NAVY)
    note = ParagraphStyle("ops_note", parent=body, fontSize=7.3, leading=10, textColor=MUTED)
    story = [
        Paragraph("Operational performance", title),
        Paragraph(escape(business.name), heading),
        Paragraph(f"{report['start']:%d %b %Y} – {report['end']:%d %b %Y} · Monthly analysis", body),
        Spacer(1, 6 * mm),
    ]

    kpis = [
        ["Paid sales revenue", "Gross profit", "Received procurement", "Production cost"],
        [_money(totals["revenue"], symbol), _money(totals["revenue"] - totals["cogs"], symbol),
         _money(totals["procurement"], symbol), _money(totals["production_cost"], symbol)],
    ]
    card = Table(kpis, colWidths=[width / 4] * 4)
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PAPER), ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#d9cfb4")),
        ("FONTNAME", (0, 0), (-1, 0), PDF_BODY_FONT), ("FONTNAME", (0, 1), (-1, 1), PDF_BODY_BOLD_FONT),
        ("FONTSIZE", (0, 0), (-1, 0), 7), ("FONTSIZE", (0, 1), (-1, 1), 12),
        ("TEXTCOLOR", (0, 1), (-1, 1), NAVY), ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9), ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story += [card, Spacer(1, 7 * mm)]
    story.append(_chart(rows, ("revenue", "procurement"), (ORANGE, NAVY), "Monthly paid revenue and received procurement", width, ("Paid revenue", "Received purchases")))
    story.append(Paragraph("Revenue and purchase value follow their recorded dates; procurement is inventory received, not cash paid.", note))
    story.append(Paragraph("Monthly performance", heading))
    performance = [["Month", "Paid revenue", "COGS", "Gross profit", "Received purchases", "Purchases / revenue", "Production cost", "Cost / revenue"]]
    for row in rows:
        performance.append([
            row["month"].strftime("%b %Y"), _money(row["revenue"], symbol), _money(row["cogs"], symbol),
            _money(row["revenue"] - row["cogs"], symbol), _money(row["procurement"], symbol),
            _ratio(row["procurement"], row["revenue"]), _money(row["production_cost"], symbol),
            _ratio(row["production_cost"], row["revenue"]),
        ])
    story.append(_style_table(LongTable(performance, colWidths=[26*mm, 34*mm, 31*mm, 34*mm, 37*mm, 31*mm, 34*mm, 33*mm], repeatRows=1)))
    story += [PageBreak(), Paragraph("Sales channel analysis", heading)]
    story.append(_chart(rows, SOURCE_KEYS, SOURCE_COLORS, "Paid revenue by origin", width, SOURCE_LABELS, stacked=True))
    source_table = [["Month", *SOURCE_LABELS, "Total paid revenue"]]
    for row in rows:
        source_table.append([row["month"].strftime("%b %Y"), *(_money(row[key], symbol) for key in SOURCE_KEYS), _money(row["revenue"], symbol)])
    source_table.append(["TOTAL", *(_money(totals[key], symbol) for key in SOURCE_KEYS), _money(totals["revenue"], symbol)])
    story += [_style_table(LongTable(source_table, colWidths=[28*mm, 43*mm, 45*mm, 43*mm, 51*mm, 50*mm], repeatRows=1)), Spacer(1, 3*mm)]
    story.append(Paragraph("Manual entry means no linked Commerce intake. Hosted and POS are INPROFIC-managed channels; External covers API and connector intakes. Each paid sale appears once.", note))
    story += [PageBreak(), Paragraph("Procurement, production and inventory", heading)]
    story.append(_chart(
        rows, ("revenue", "production_cost", "stock_out"), (ORANGE, BURGUNDY, NAVY),
        "Paid revenue beside production cost and stock issued value", width,
        ("Paid revenue", "Production cost", "Stock out value"),
    ))
    story.append(Paragraph("Production and stock values show recorded operating activity; they are not additional cash deductions from revenue.", note))
    operations = [["Month", "Raw inputs", "Prod. supplies", "Operational supplies", "Resale stock", "Batches", "Production cost", "Wastage cost", "Stock in", "Stock out"]]
    for row in rows:
        operations.append([
            row["month"].strftime("%b %Y"), _money(row["raw_procurement"], symbol),
            _money(row["production_supplies"], symbol), _money(row["operational_supplies"], symbol),
            _money(row["resale_procurement"], symbol), str(row["batches"]),
            _money(row["production_cost"], symbol), _money(row["wastage_cost"], symbol),
            _money(row["stock_in"], symbol), _money(row["stock_out"], symbol),
        ])
    story.append(_style_table(LongTable(operations, colWidths=[23*mm, 27*mm, 27*mm, 31*mm, 27*mm, 15*mm, 29*mm, 27*mm, 27*mm, 27*mm], repeatRows=1)))
    story.append(Paragraph("Distribution market stock activity", heading))
    market_rows = [["Month", "Received / returned at lot value", "Released / transferred / written off at lot value"]]
    for row in rows:
        market_rows.append([row["month"].strftime("%b %Y"), _money(row["market_in"], symbol), _money(row["market_out"], symbol)])
    story.append(_style_table(LongTable(market_rows, colWidths=[40*mm, 110*mm, 110*mm], repeatRows=1)))
    story.append(Paragraph("Current inventory snapshot", heading))
    story.append(Paragraph(
        f"As of {snapshot['as_of']:%d %b %Y}: raw material value at current unit cost {_money(snapshot['raw_value'], symbol)}; "
        f"distribution market stock at lot cost {_money(snapshot['market_value'], symbol)}; "
        f"low-stock items: {snapshot['low_raw']} raw materials and {snapshot['low_finished']} finished goods.", body,
    ))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        "Method: revenue includes fully paid sales dated in the selected range and excludes sales linked to reversed orders. "
        "COGS uses frozen sale-item costs. Procurement uses received purchase lines, including unpaid receipts. "
        "Production cost and wastage use completed, unreversed batch snapshots; they are informational and are not subtracted again from gross profit. "
        "Stock in/out uses signed shelf/raw-material movements at recorded unit values; market stock uses its separate lot movement ledger. "
        "Transfers can appear in both ledgers. These are operational activity values, not revenue or cash flow. "
        "Current inventory values are a present-day snapshot, not reconstructed month-end balances. "
        "A dash in a ratio means there was no paid revenue in that month.", note,
    ))
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=landscape(A4), leftMargin=18*mm, rightMargin=18*mm,
        topMargin=17*mm, bottomMargin=19*mm, title="INPROFIC Operational Performance",
        author="INPROFIC",
    )
    document.build(story, onFirstPage=_page, onLaterPages=_page)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="inprofic-{business.slug}-operations-{report["start"]}-{report["end"]}.pdf"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response
