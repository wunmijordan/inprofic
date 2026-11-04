"""Shared paper-style snapshots. Export colors stay stable across workspace themes."""
import io
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

_FONT_DIR = Path(__file__).resolve().parent / "static" / "core" / "fonts"
# Inter, not the UI's Sora: Sora has no naira sign and would draw an empty box.
FONT_REGULAR, FONT_BOLD = "Inter-Regular.ttf", "Inter-SemiBold.ttf"
WIDTH, PAD = 1080, 56
MAX_IMAGE_ROWS = 14

INK, MUTED, LINE = (38, 38, 36), (120, 113, 108), (231, 223, 204)
PAPER, ACCENT, WHITE = (255, 253, 248), (143, 23, 45), (255, 255, 255)
STOCK_COLORS = {"out": (185, 28, 28), "low": (185, 28, 28), "warning": (180, 83, 9), "ok": (22, 101, 52), "untracked": (120, 113, 108)}
CHIPS = {"pending": ((254, 243, 199), (146, 64, 14)), "approved": ((220, 252, 231), (22, 101, 52)),
         "received": ((224, 231, 255), (55, 48, 163))}


def _font(name, size):
    try:
        return ImageFont.truetype(str(_FONT_DIR / name), size)
    except OSError:  # never fail a share because a font file is missing
        return ImageFont.load_default(size)


def _fit(draw, text, font, width):
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _qr(url, size):
    """A scannable code for the sign-in link, or None if the QR library is unavailable."""
    try:
        import qrcode
        qr = qrcode.QRCode(border=1, box_size=6, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(url)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((size, size), Image.NEAREST)
    except Exception:  # never fail a share because a QR could not be drawn
        return None



def render_report_snapshot(*, business, title, subtitle, rows, total_label, total,
                           footer, url, link_heading, scope="", stamp="", max_rows=MAX_IMAGE_ROWS):
    """Use the procurement reference's typography, rows, total card and QR footer."""
    shown, hidden = rows[:max_rows], max(0, len(rows) - max_rows)
    row_h, header_h = 116, 250
    height = header_h + max(1, len(shown)) * row_h + (56 if hidden else 0) + 150 + 230 + 110
    image = Image.new("RGB", (WIDTH, height), PAPER)
    d = ImageDraw.Draw(image)
    name, body, small, tiny = _font(FONT_BOLD, 28), _font(FONT_REGULAR, 26), _font(FONT_REGULAR, 22), _font(FONT_REGULAR, 19)
    inner = WIDTH - 2 * PAD
    d.rectangle((0, 0, WIDTH, 14), fill=ACCENT)
    d.text((PAD, 48), _fit(d, business.name, small, inner - 280), font=small, fill=MUTED)
    if scope:
        chip_w = min(300, int(d.textlength(scope, font=small)) + 41)
        d.rounded_rectangle((WIDTH-PAD-chip_w,54,WIDTH-PAD,98), radius=22, fill=(240, 237, 230))
        d.text((WIDTH-PAD-chip_w+20,62), _fit(d,scope,small,chip_w-40), font=small, fill=INK)
    d.text((PAD,86), _fit(d,title,_font(FONT_BOLD,40),inner), font=_font(FONT_BOLD,40), fill=INK)
    d.text((PAD,150), _fit(d,subtitle,body,inner), font=body, fill=INK)
    d.text((PAD,192), "Item", font=small, fill=MUTED)
    d.text((WIDTH-PAD,192), "Amount / quantity", font=small, fill=MUTED, anchor="ra")
    d.line((PAD,232,WIDTH-PAD,232), fill=LINE, width=2)
    y = header_h
    for row in shown:
        amount = str(row.get("value", ""))
        amount_width = min(inner // 2, int(d.textlength(amount,font=name)))
        d.text((WIDTH-PAD,y+16), _fit(d,amount,name,inner//2), font=name, fill=INK, anchor="ra")
        d.text((PAD,y+16), _fit(d,row["label"],name,inner-amount_width-35), font=name, fill=INK)
        d.text((PAD,y+58), _fit(d,row.get("detail", ""),small,inner), font=small, fill=MUTED)
        d.line((PAD,y+row_h-2,WIDTH-PAD,y+row_h-2), fill=LINE)
        y += row_h
    if not shown:
        d.text((PAD,y+24), "No records in this scope.", font=body, fill=MUTED)
        y += row_h
    if hidden:
        d.text((PAD,y+12), f"+ {hidden} more rows (open INPROFIC for the full summary)", font=small, fill=MUTED)
        y += 56
    if stamp:
        d.text((WIDTH-PAD,y+4), stamp, font=tiny, fill=MUTED, anchor="ra")
    d.rounded_rectangle((PAD,y+30,WIDTH-PAD,y+180),radius=24,fill=WHITE,outline=LINE,width=2)
    d.text((PAD+32,y+60), total_label,font=body,fill=MUTED)
    d.text((WIDTH-PAD-32,y+54),_fit(d,str(total),_font(FONT_BOLD,48),inner-240),font=_font(FONT_BOLD,48),fill=ACCENT,anchor="ra")
    d.text((PAD+32,y+128),_fit(d,footer,small,inner-64),font=small,fill=MUTED)
    ly=y+214
    qr=_qr(url,170) if url else None
    text_w=inner-(198 if qr else 0)
    d.text((PAD,ly),_fit(d,link_heading,name,text_w),font=name,fill=ACCENT)
    d.text((PAD,ly+42),"Sign-in required. Only team members with access can open it.",font=tiny,fill=MUTED)
    d.text((PAD,ly+78),_fit(d,url.replace('https://','').replace('http://',''),tiny,text_w),font=tiny,fill=INK)
    d.text((PAD,ly+108),"Scan the code or open the link sent with this image." if qr else "Open the link sent with this image.",font=tiny,fill=MUTED)
    if qr: image.paste(qr,(WIDTH-PAD-170,ly-6))
    d.text((WIDTH//2,height-36),"Sent from INPROFIC",font=tiny,fill=MUTED,anchor="mm")
    output=io.BytesIO(); image.save(output,"PNG",optimize=True); return output.getvalue()


def snapshot_response(content, filename):
    from django.http import HttpResponse
    response = HttpResponse(content, content_type="image/png")
    response["Cache-Control"] = "private, no-store"
    response["Content-Disposition"] = f'inline; filename="{filename}.png"'
    return response


def render_distribution_snapshot(*, business, title, day, products, cards, url, scope=''):
    """Bounded product/customer table and financial cards on the shared paper."""
    shown = products[:8]
    customer_budget = 16
    table_rows = []
    hidden_customers = sum(len(product['customers']) for product in products[8:])
    for product in shown:
        customers = product['customers'][:customer_budget]
        hidden_customers += len(product['customers']) - len(customers)
        customer_budget -= len(customers)
        table_rows.append((product, customers))
    hidden_products = len(products) - len(shown)
    table_height = sum(72 + max(len(customers), 1) * 31 for _, customers in table_rows) or 90
    note_height = 60 if hidden_products or hidden_customers else 0
    cards_height = 282 if cards else 0
    height = 248 + table_height + note_height + cards_height + 270
    image = Image.new('RGB', (WIDTH, height), PAPER)
    draw = ImageDraw.Draw(image)
    title_font = _font(FONT_BOLD, 38)
    bold = _font(FONT_BOLD, 25)
    body = _font(FONT_REGULAR, 22)
    small = _font(FONT_REGULAR, 19)
    inner = WIDTH - PAD * 2
    draw.rectangle((0, 0, WIDTH, 14), fill=ACCENT)
    draw.text((PAD, 46), _fit(draw, business.name, body, inner-290), font=body, fill=MUTED)
    draw.text((WIDTH-PAD, 46), scope, font=small, fill=MUTED, anchor='ra')
    draw.text((PAD, 87), _fit(draw, title, title_font, inner), font=title_font, fill=INK)
    draw.text((PAD, 142), day.strftime('%d %b %Y') + ' · customer allocations', font=body, fill=INK)
    draw.text((PAD, 176), 'Made / taken: selected date. Remaining: stock now.', font=small, fill=MUTED)
    x_made, x_taken, x_left = 550, 790, WIDTH-PAD
    for label, x, anchor in [('Product / customer', PAD, 'la'),
                             ('Made' if business.uses_production else 'Received', x_made, 'ra'),
                             ('Taken', x_taken, 'ra'), ('Remaining now', x_left, 'ra')]:
        draw.text((x, 216), label, font=small, fill=MUTED, anchor=anchor)
    draw.line((PAD, 245, WIDTH-PAD, 245), fill=LINE, width=2)
    y = 258
    quantity = lambda value, unit: f'{value:,.2f}'.rstrip('0').rstrip('.') + ' ' + unit
    for product, customers in table_rows:
        draw.text((PAD, y), _fit(draw, product['product'], bold, 270), font=bold, fill=INK)
        for x, key in [(x_made, 'made'), (x_taken, 'distributed'), (x_left, 'available')]:
            amount = product[key] + product.get('physical', 0) if key == 'distributed' else product[key]
            value = quantity(amount, product['unit'])
            draw.text((x, y+3), _fit(draw, value, body, 215), font=body, fill=INK, anchor='ra')
        y += 44
        if customers:
            for customer in customers:
                draw.text((PAD+12, y), _fit(draw, customer['destination'], small, 480), font=small, fill=MUTED)
                draw.text((x_taken, y), _fit(draw, quantity(customer['quantity'], product['unit']), small, 210),
                          font=small, fill=MUTED, anchor='ra')
                y += 31
        else:
            draw.text((PAD+12, y), ('Allocations in the full text' if product['customers'] else 'No customer allocation on this date'), font=small, fill=MUTED)
            y += 31
        draw.line((PAD, y+12, WIDTH-PAD, y+12), fill=LINE)
        y += 28
    if not table_rows:
        draw.text((PAD, y+20), 'No product stock or allocations in this scope.', font=body, fill=MUTED)
        y += 90
    if note_height:
        draw.text((PAD, y+8), f'+ {hidden_products} products / {hidden_customers} destinations in the full text and dashboard',
                  font=small, fill=MUTED)
        y += note_height
    if cards:
        width = (inner - 24) // 3
        for index, card in enumerate(cards[:6]):
            cx, cy = PAD + (index % 3)*(width+12), y + (index//3)*129
            draw.rounded_rectangle((cx, cy, cx+width, cy+115), radius=17, fill=WHITE, outline=LINE, width=2)
            draw.text((cx+17, cy+14), _fit(draw, card['label'], small, width-34), font=small, fill=MUTED)
            value_font = _font(FONT_BOLD, 31)
            for size in range(31, 17, -1):
                value_font = _font(FONT_BOLD, size)
                if draw.textlength(card['value'], font=value_font) <= width-34:
                    break
            draw.text((cx+17, cy+43), card['value'], font=value_font, fill=ACCENT)
            draw.text((cx+17, cy+85), _fit(draw, card['detail'], _font(FONT_REGULAR,16), width-34), font=_font(FONT_REGULAR,16), fill=MUTED)
        y += cards_height
    qr = _qr(url, 152)
    text_width = inner-180
    draw.text((PAD, y+8), _fit(draw, 'Open INPROFIC for the full breakdown', bold, text_width), font=bold, fill=ACCENT)
    draw.text((PAD, y+48), 'Sign-in required. Full details accompany this image.', font=small, fill=MUTED)
    draw.text((PAD, y+78), 'Returns need credit/refund review; invoices are unchanged.', font=_font(FONT_REGULAR,17), fill=MUTED)
    draw.text((PAD, y+109), _fit(draw, url.replace('https://','').replace('http://',''), small, text_width), font=small, fill=INK)
    if qr:
        image.paste(qr, (WIDTH-PAD-152, y+3))
    draw.text((WIDTH//2,height-30), 'Sent from INPROFIC', font=small, fill=MUTED, anchor='mm')
    output = io.BytesIO()
    image.save(output, 'PNG', optimize=True)
    return output.getvalue()
