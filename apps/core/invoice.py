from decimal import Decimal
from io import BytesIO

from django.http import HttpResponse
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .pdf_fonts import PDF_BODY_FONT, PDF_DISPLAY_FONT


def _money(value, symbol):
    value = Decimal(value or 0)
    return f"{symbol}{value:,.2f}"


def _date(value):
    return value.strftime("%d %b %Y") if value else "—"


def _styles():
    styles = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("invoice_title", parent=styles["Title"], fontName=PDF_DISPLAY_FONT, fontSize=18, leading=22, spaceAfter=4),
        "subtitle": ParagraphStyle("invoice_subtitle", parent=styles["Normal"], fontName=PDF_DISPLAY_FONT, fontSize=14, textColor=colors.HexColor("#8f172d"), leading=17),
        "body": ParagraphStyle("invoice_body", parent=styles["Normal"], fontName=PDF_BODY_FONT, fontSize=9, leading=12),
        "right": ParagraphStyle("invoice_right", parent=styles["Normal"], fontName=PDF_BODY_FONT, fontSize=9, leading=12, alignment=TA_RIGHT),
        "small": ParagraphStyle("invoice_small", parent=styles["Normal"], fontName=PDF_BODY_FONT, fontSize=7.5, textColor=colors.HexColor("#666666"), leading=10),
    }


def _header(story, business, document_title, number, date, counterparty_label, counterparty):
    styles = _styles()
    story.append(Paragraph(document_title, styles["title"]))
    story.append(Paragraph(str(business.name), styles["subtitle"]))
    story.append(Spacer(1, 4 * mm))
    meta = [
        [Paragraph(f"<b>Document:</b> {number}", styles["body"]), Paragraph(f"<b>Date:</b> {_date(date)}", styles["right"])],
        [Paragraph(f"<b>{counterparty_label}:</b> {counterparty or '—'}", styles["body"]), ""],
    ]
    table = Table(meta, colWidths=[110 * mm, 70 * mm])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(table)
    story.append(Spacer(1, 5 * mm))


def _table(story, headers, rows, widths, total=None, symbol="₦"):
    styles = _styles()
    data = [[Paragraph(f"<b>{h}</b>", styles["body"]) for h in headers]]
    for row in rows:
        data.append([Paragraph(str(v), styles["body"]) for v in row])
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0EAD9")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#444444")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D9CFB4")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    if total is not None:
        story.append(Spacer(1, 3 * mm))
        total_table = Table([[Paragraph("<b>Total</b>", styles["body"]), Paragraph(f"<b>{_money(total, symbol)}</b>", styles["right"])]], colWidths=[110 * mm, 70 * mm])
        total_table.setStyle(TableStyle([
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(total_table)


def _response(buffer, filename):
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _build(story, filename):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm, title=filename)
    doc.build(story)
    return _response(buffer, filename)


def purchase_order_pdf(order):
    symbol = order.business.currency_symbol
    story = []
    _header(story, order.business, "PURCHASE ORDER", f"PO #{order.pk}", order.date, "Supplier", order.supplier)
    rows = []
    for item in order.items.select_related("raw_material", "finished_good"):
        rows.append([
            item.item_name,
            item.item_category,
            f"{item.qty:,.2f} {item.stock_unit}",
            _money(item.unit_cost, symbol),
            _money(item.line_total, symbol),
        ])
    _table(story, ["Inventory item", "Category", "Quantity", "Unit cost", "Line total"], rows, [50*mm, 30*mm, 32*mm, 30*mm, 38*mm], order.total, symbol)
    styles = _styles()
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(f"Status: {order.get_status_display()}", styles["body"]))
    story.append(Paragraph(f"Payment status: <b>{order.get_payment_status_display()}</b> · Method: {order.payment_method}", styles["body"]))
    if order.account:
        story.append(Paragraph(f"Payment account: {order.account.name}", styles["body"]))
    if order.received_date:
        story.append(Paragraph(f"Received: {_date(order.received_date)}", styles["body"]))
    return _build(story, f"purchase-order-{order.pk}.pdf")


def production_order_pdf(order):
    from .verticals import vertical_config

    symbol = order.business.currency_symbol
    story = []
    is_customer_order = order.is_customer_order
    is_market_stock = order.is_market_stock_order
    is_multi_channel = order.is_multi_channel
    channel_label = dict(order.TYPE_CHOICES).get(order.order_type, order.order_type).upper()
    stock_location = vertical_config(order.business)["stock_location"]
    counterparty = (
        "Several channels" if is_multi_channel
        else order.customer_name if is_customer_order
        else ("Market stock / future customers" if is_market_stock else stock_location)
    )
    _header(story, order.business, "PRODUCTION ORDER", f"Production Order #{order.display_number}", order.date, "Customer" if is_customer_order else "For", counterparty)
    rows = []
    for item in order.items.select_related("finished_good"):
        rows.append([
            item.finished_good.name,
            f"{item.batch_qty:,.2f}",
            f"{item.piece_qty:,.2f}",
            f"{item.total_units:,.2f} {item.finished_good.unit}",
            _money(item.price, symbol) if is_customer_order else "—",
            _money(item.line_total, symbol) if is_customer_order else "—",
        ])
    _table(story, ["Finished good", "Batches", "Pieces", "Total units", "Price", "Line total"], rows, [42*mm, 22*mm, 22*mm, 34*mm, 28*mm, 32*mm], order.total if is_customer_order else None, symbol)
    styles = _styles()
    story.append(Spacer(1, 5 * mm))
    if is_multi_channel:
        story.append(Paragraph("Production destination: <b>SEVERAL CHANNELS</b>", styles["body"]))
        for number, allocation in enumerate(order.allocations.prefetch_related("lines__order_item__finished_good"), start=1):
            story.append(Paragraph(f"<b>{number}. {allocation.label}</b>", styles["body"]))
            for line in allocation.lines.all():
                story.append(Paragraph(
                    f"&nbsp;&nbsp;&nbsp;{line.order_item.finished_good.name}: {line.total_units:,.2f} units"
                    + (f" (delivered {line.delivered_units:,.2f})" if order.status == "completed" else ""),
                    styles["small"],
                ))
    elif is_customer_order:
        story.append(Paragraph(f"Sales channel: <b>{channel_label}</b>", styles["body"]))
        story.append(Paragraph(f"Customer order type: {order.display_order_type}", styles["body"]))
    elif is_market_stock:
        story.append(Paragraph(f"Production channel: <b>{channel_label}</b>", styles["body"]))
        story.append(Paragraph("Production destination: <b>DISTRIBUTION MARKET STOCK</b>", styles["body"]))
        story.append(Paragraph("No customer sale or payment is created until these goods are sold.", styles["body"]))
    else:
        story.append(Paragraph(f"Sales channel: <b>{stock_location.upper()}</b>", styles["body"]))
        destination = order.get_production_destination_display() if hasattr(order, "get_production_destination_display") else "Store replenishment"
        story.append(Paragraph(f"Production destination: <b>{destination}</b>", styles["body"]))
    story.append(Paragraph(f"Status: {order.get_status_display()}", styles["body"]))
    if order.customer_region:
        story.append(Paragraph(f"Region: {order.customer_region}", styles["body"]))
    if order.customer_group:
        story.append(Paragraph(f"Customer group: {order.customer_group}", styles["body"]))
    if is_customer_order:
        payment_label = "Received" if order.customer_payment_status == "paid" else "Receivable"
        story.append(Paragraph(f"Payment status: <b>{payment_label}</b>", styles["body"]))
        if order.customer_payment_status == "paid":
            story.append(Paragraph(f"Payment method: <b>{order.customer_payment_method}</b>", styles["body"]))
    if order.notes:
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(f"Notes: {order.notes}", styles["small"]))
    if order.status == "completed":
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(f"Completed: {_date(order.completed_date)}", styles["body"]))
    return _build(story, f"production-order-{order.pk}.pdf")


def expense_invoice_pdf(expense):
    symbol = expense.business.currency_symbol
    story = []
    vendor = getattr(expense, "vendor", None) or "—"
    _header(story, expense.business, "EXPENSE RECORD", f"Expense #{expense.pk}", expense.date, "Vendor", vendor)

    styles = _styles()
    rows = [
        ["Category", expense.get_category_display()],
        ["Description", expense.description or "—"],
        ["Notes", expense.notes or "—"],
        ["Payment status", expense.get_payment_status_display()],
        ["Payment method", expense.payment_method or "—"],
    ]
    data = [[Paragraph(f"<b>Field</b>", styles["body"]), Paragraph(f"<b>Details</b>", styles["body"])]]
    for label, value in rows:
        data.append([Paragraph(str(label), styles["body"]), Paragraph(str(value), styles["body"])])
    data.append([Paragraph("<b>Amount</b>", styles["body"]), Paragraph(f"<b>{_money(expense.amount, symbol)}</b>", styles["right"])])
    table = Table(data, colWidths=[45 * mm, 135 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0EAD9")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D9CFB4")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    story.append(Spacer(1, 5 * mm))
    recorder = getattr(getattr(expense, "created_by", None), "username", None) or "—"
    story.append(Paragraph(f"Recorded by: {recorder}", styles["small"]))
    return _build(story, f"expense-{expense.pk}.pdf")


def sale_invoice_pdf(sale):
    symbol = sale.business.currency_symbol
    story = []
    _header(story, sale.business, "SALES INVOICE", f"Sale #{sale.pk}", sale.date, "Customer", sale.customer or "Walk-in")
    rows = []
    for item in sale.items.select_related("finished_good"):
        effective_price = (item.price or Decimal("0")) - (item.discount or Decimal("0"))
        rows.append([
            item.finished_good.name,
            f"{item.total_units:,.2f} {item.finished_good.unit}",
            _money(effective_price, symbol),
            _money(item.line_total, symbol),
        ])
    _table(story, ["Finished good", "Quantity", "Unit price", "Line total"], rows, [70*mm, 35*mm, 35*mm, 40*mm], sale.total, symbol)
    styles = _styles()
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(f"Source: {sale.display_source}", styles["body"]))
    if sale.service_mode:
        story.append(Paragraph(f"Service: {sale.get_service_mode_display()}", styles["body"]))
    if sale.table_reference:
        story.append(Paragraph(f"Table / reference: {sale.table_reference}", styles["body"]))
    story.append(Paragraph(f"Transaction: <b>{sale.get_transaction_type_display()}</b>", styles["body"]))
    if sale.transaction_type == "unpaid" and sale.unpaid_description:
        story.append(Paragraph(f"Unpaid reason: {sale.unpaid_description}", styles["small"]))
    story.append(Paragraph(f"Payment method: {sale.payment_method}", styles["body"]))
    if sale.linked_order_id:
        story.append(Paragraph(f"Linked production order: #{sale.linked_order.display_number}", styles["body"]))
    story.append(Spacer(1, 7 * mm))
    story.append(Paragraph("Thank you for your business.", styles["small"]))
    return _build(story, f"sales-invoice-{sale.pk}.pdf")