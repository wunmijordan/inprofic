"""Current stock position of a requested item, shared by every procurement surface.

One function, so the request form, the approval page, the WhatsApp text and the
snapshot image all report the same numbers. Stock is read through the model, so it
already respects an active ledger cut-over (the live segment balance).
"""
from decimal import Decimal
from core.quantities import format_quantity

from inventory.models import RawMaterial

STATUS_LABELS = {"out": "Out of stock", "low": "Low", "warning": "Getting low", "ok": "In stock", "untracked": "Stock not tracked"}


def _fmt(value):
    value = Decimal(value)
    return f"{value:,.2f}".rstrip("0").rstrip(".") if value % 1 else f"{value:,.0f}"


def stock_status(item):
    """Plain-data stock position for a RawMaterial or FinishedGood."""
    is_raw = isinstance(item, RawMaterial)
    unit = ((item.usage_unit or item.purchase_unit or "") if is_raw else (item.unit or "")).strip()
    request_unit = ((item.purchase_unit or unit) if is_raw else unit).strip()
    stock = item.stock
    if stock is None:
        return {"tracked": False, "status": "untracked", "label": STATUS_LABELS["untracked"], "text": "Not tracked", "unit": unit,
                "request_unit": request_unit, "stock": None, "factor": "1", "reorder": "0", "reorder_text": "", "packs": ""}
    stock, reorder = Decimal(stock), Decimal(item.reorder_level or 0)
    factor = Decimal(item.total_conversion_factor) if is_raw else Decimal("1")
    if stock <= 0:
        status = "out"
    elif reorder > 0 and stock <= reorder:
        status = "low"
    elif reorder > 0 and stock <= reorder * Decimal("1.5"):
        status = "warning"
    else:
        status = "ok"
    packs = ""
    if is_raw and item.has_unit_conversion and factor > 0:
        whole, remainder = item.stock_breakdown
        packs = " + ".join(part for part in (format_quantity(whole,item.purchase_unit) if whole else "",
                                             format_quantity(remainder,unit) if remainder else "") if part) or format_quantity(0,item.purchase_unit)
    return {
        "tracked": True, "status": status, "label": STATUS_LABELS[status], "stock": stock, "unit": unit, "request_unit": request_unit,
        "text": format_quantity(stock,unit), "packs": packs, "factor": str(factor), "reorder": str(reorder),
        "reorder_text": f"reorder at {format_quantity(reorder,unit)}" if reorder > 0 else "",
    }


def stock_line(status):
    """One readable line: ``12 kg (Low, reorder at 20 kg)``."""
    if not status["tracked"]:
        return status["label"]
    extra = ", ".join(part for part in (status["label"], status["reorder_text"]) if part)
    packs = f" = {status['packs']}" if status["packs"] else ""
    return f"{status['text']}{packs} ({extra})"


def stock_payload(status):
    """JSON-safe version for the browser."""
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in status.items()}
