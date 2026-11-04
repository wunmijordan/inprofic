"""WhatsApp-ready views of a purchase request: a text message and a PNG snapshot.

Both are built from one ``summarize`` result so the text and the picture can never
disagree. Nothing here is public: the snapshot is served only to signed-in members
who can view Procurement, and the text is composed on the page for that member to
send themselves. No WhatsApp template or API is involved.
"""
import io
from decimal import Decimal

from django.utils import timezone
from PIL import Image, ImageDraw

from .stockview import stock_line, stock_status

from core.snapshot_images import (
    _FONT_DIR, FONT_REGULAR, FONT_BOLD, WIDTH, PAD, MAX_IMAGE_ROWS, INK, MUTED, LINE,
    PAPER, ACCENT, WHITE, STOCK_COLORS, CHIPS, _font, _fit, _qr,
)


def _money(value, symbol):
    return f"{symbol}{Decimal(value):,.2f}"


def _qty(value):
    value = Decimal(value)
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def status_key(po):
    return "pending" if po.status in {"draft", "ordered"} else po.status


def status_label(po):
    key = status_key(po)
    if key == "pending":
        return "Awaiting approval"
    if key == "approved":
        return "Approved"
    return po.get_status_display()


def summarize(po, business, url=""):
    """Plain data for one request: lines with requested and approved amounts, current stock, and totals."""
    from .services import describe_request, request_orders
    vendors = list(request_orders(po).select_related('approved_by').prefetch_related('items__raw_material', 'items__finished_good', 'payments'))
    display = describe_request(vendors)
    po = display
    symbol = business.currency_symbol
    lines, requested_total, approved_total = [], Decimal("0"), Decimal("0")
    for item in display.request_items:
        vendor = next(v for v in vendors if v.pk == item.purchase_order_id)
        requested = item.requested_qty if item.requested_qty is not None else item.qty
        removed = item.qty == 0
        lines.append({
            "vendor": vendor.supplier or "Supplier not named", "name": item.item_name, "unit": item.stock_unit, "requested": requested, "approved": item.qty,
            "unit_cost": item.unit_cost, "total": item.qty * item.unit_cost, "removed": removed,
            "reduced": item.qty < requested, "reason": (item.approval_reason or "").strip(),
            "stock": stock_status(item.stock_item),
        })
        requested_total += requested * item.unit_cost
        approved_total += item.qty * item.unit_cost
    return {
        "po": po, "business": business, "symbol": symbol, "lines": lines, "number": po.pk,
        "supplier": display.request_supplier or "Supplier not named", "date": po.date,
        "status": 'pending' if display.request_status in {'draft', 'ordered'} else display.request_status,
        "status_label": 'Awaiting approval' if display.request_status in {'draft', 'ordered'} else display.request_status_label,
        "approved_by": str(po.approved_by) if po.approved_at and po.approved_by_id else "",
        "requested_total": requested_total, "total": approved_total,
        "changed": approved_total != requested_total,
        "payment": dict(po.PAYMENT_STATUS_CHOICES).get(display.request_payment, ""),
        "url": url, "stock_time": timezone.localtime().strftime("%d %b %H:%M"),
    }


def link_prompt(summary):
    verb = "approve this purchase request" if summary["status"] == "pending" else "view this purchase request"
    return f"Open INPROFIC to {verb} (sign-in required)"


def share_text(summary, url=None):
    """WhatsApp-formatted message (``*bold*``) describing the request."""
    s, symbol = summary, summary["symbol"]
    url = s.get("url", "") if url is None else url
    out = [f"*Purchase request #{s['number']}*", f"{s['business'].name} · {s['date']:%d %b %Y}",
           f"Supplier: {s['supplier']}", f"Status: {s['status_label']}", ""]
    for line in s["lines"]:
        if line["removed"]:
            out.append(f"• {line['name']}: removed (was {_qty(line['requested'])} {line['unit']})")
        else:
            note = f" (requested {_qty(line['requested'])})" if line["reduced"] else ""
            out.append(f"• {line['name']}: {_qty(line['approved'])} {line['unit']} × {_money(line['unit_cost'], symbol)} "
                       f"= {_money(line['total'], symbol)}{note}")
        out.append(f"   Vendor: {line['vendor']}")
        out.append(f"   Stock now: {stock_line(line['stock'])}")
    out.append(f"_Stock as of {s['stock_time']}_")
    out.append("")
    if s["changed"]:
        out.append(f"Requested total: {_money(s['requested_total'], symbol)}")
    out.append(f"*Total: {_money(s['total'], symbol)}*")
    if s["payment"]:
        out.append(f"Payment: {s['payment']}")
    if s["approved_by"]:
        out.append(f"Approved by {s['approved_by']}")
    if url:
        out += ["", f"{link_prompt(s)}:", url]
    return "\n".join(out)


# --------------------------------------------------------------------------
# PNG snapshot (mirrors the compact in-app request card)
# --------------------------------------------------------------------------


def render_snapshot(summary):
    """Return PNG bytes of the request, sized for a phone chat and for full-screen viewing."""
    s, symbol = summary, summary["symbol"]
    regular, bold = FONT_REGULAR, FONT_BOLD
    f_title, f_name, f_body, f_small, f_total = _font(bold, 40), _font(bold, 28), _font(regular, 26), _font(regular, 22), _font(bold, 48)
    f_tiny = _font(regular, 19)
    shown = s["lines"][:MAX_IMAGE_ROWS]
    hidden = len(s["lines"]) - len(shown)
    row_h, header_h = 132, 250
    total_h = 150 + (36 if s["changed"] else 0)
    link_h = 230 if s["url"] else 70
    height = header_h + max(len(shown), 1) * row_h + (56 if hidden else 0) + total_h + link_h + 80
    image = Image.new("RGB", (WIDTH, height), PAPER)
    d = ImageDraw.Draw(image)
    inner = WIDTH - 2 * PAD
    d.rectangle([0, 0, WIDTH, 14], fill=ACCENT)
    d.text((PAD, 48), _fit(d, s["business"].name, f_small, inner - 300), font=f_small, fill=MUTED)
    d.text((PAD, 86), f"Purchase request #{s['number']}", font=f_title, fill=INK)
    chip_bg, chip_fg = CHIPS.get(s["status"], CHIPS["pending"])
    chip_w = int(d.textlength(s["status_label"], font=f_small)) + 40
    d.rounded_rectangle([WIDTH - PAD - chip_w, 54, WIDTH - PAD, 98], radius=22, fill=chip_bg)
    d.text((WIDTH - PAD - chip_w + 20, 62), s["status_label"], font=f_small, fill=chip_fg)
    d.text((PAD, 150), _fit(d, f"{s['supplier']}  ·  {s['date']:%d %b %Y}", f_body, inner), font=f_body, fill=INK)
    d.text((PAD, 192), "Item", font=f_small, fill=MUTED)
    d.text((WIDTH - PAD, 192), "Amount", font=f_small, fill=MUTED, anchor="ra")
    d.line([PAD, 232, WIDTH - PAD, 232], fill=LINE, width=2)
    y = header_h
    for line in shown:
        muted = line["removed"]
        d.text((WIDTH - PAD, y + 16), "Removed" if muted else _money(line["total"], symbol), font=f_name, fill=MUTED if muted else INK, anchor="ra")
        d.text((PAD, y + 16), _fit(d, line["name"], f_name, inner - 280), font=f_name, fill=MUTED if muted else INK)
        if muted:
            detail = f"Removed · was {_qty(line['requested'])} {line['unit']}"
        else:
            detail = f"{_qty(line['approved'])} {line['unit']} × {_money(line['unit_cost'], symbol)}"
            if line["reduced"]:
                detail += f"  ·  requested {_qty(line['requested'])}"
        detail += f" · {line['vendor']}"
        d.text((PAD, y + 56), _fit(d, detail, f_small, inner), font=f_small, fill=MUTED)
        stock = line["stock"]
        colour = STOCK_COLORS.get(stock["status"], MUTED)
        d.ellipse([PAD, y + 98, PAD + 14, y + 112], fill=colour)
        stock_text = f"Stock now: {stock_line(stock)}"
        d.text((PAD + 26, y + 94), _fit(d, stock_text, f_small, inner - 26), font=f_small, fill=colour)
        d.line([PAD, y + row_h - 2, WIDTH - PAD, y + row_h - 2], fill=LINE, width=1)
        y += row_h
    if not shown:
        d.text((PAD, y + 24), "No items on this request.", font=f_body, fill=MUTED)
        y += row_h
    if hidden:
        d.text((PAD, y + 12), f"+ {hidden} more item{'s' if hidden != 1 else ''} (see INPROFIC)", font=f_small, fill=MUTED)
        y += 56
    d.text((WIDTH - PAD, y + 4), f"Stock as of {s['stock_time']}", font=f_tiny, fill=MUTED, anchor="ra")
    d.rounded_rectangle([PAD, y + 30, WIDTH - PAD, y + 30 + total_h], radius=24, fill=WHITE, outline=LINE, width=2)
    ty = y + 54
    if s["changed"]:
        d.text((PAD + 32, ty), f"Requested total {_money(s['requested_total'], symbol)}", font=f_small, fill=MUTED)
        ty += 36
    d.text((PAD + 32, ty + 6), "Total", font=f_body, fill=MUTED)
    d.text((WIDTH - PAD - 32, ty - 2), _money(s["total"], symbol), font=f_total, fill=ACCENT, anchor="ra")
    foot = s["status_label"] + (f" by {s['approved_by']}" if s["approved_by"] else "")
    if s["payment"]:
        foot += f"  ·  Payment: {s['payment']}"
    d.text((PAD + 32, ty + 70), _fit(d, foot, f_small, inner - 64), font=f_small, fill=MUTED)
    ly = y + 30 + total_h + 34
    if s["url"]:
        qr_size = 170
        qr = _qr(s["url"], qr_size)
        text_w = inner - (qr_size + 28 if qr else 0)
        verb = "approve this request" if s["status"] == "pending" else "view this request"
        d.text((PAD, ly), _fit(d, f"Open INPROFIC to {verb}", f_name, text_w), font=f_name, fill=ACCENT)
        d.text((PAD, ly + 42), "Sign-in required. Only team members with access can open it.", font=f_tiny, fill=MUTED)
        link = s["url"].replace("https://", "").replace("http://", "")
        d.text((PAD, ly + 78), _fit(d, link, f_tiny, text_w), font=f_tiny, fill=INK)
        d.text((PAD, ly + 108), "Scan the code or open the link sent with this image." if qr else "Open the link sent with this image.", font=f_tiny, fill=MUTED)
        if qr:
            image.paste(qr, (WIDTH - PAD - qr_size, ly - 6))
    d.text((WIDTH // 2, height - 36), "Sent from INPROFIC", font=f_tiny, fill=MUTED, anchor="mm")
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
