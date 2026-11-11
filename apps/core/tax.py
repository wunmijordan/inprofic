"""VAT and e-invoicing.

VAT here is **extracted from VAT-inclusive prices**: Inprofic records the price a
customer pays, so output VAT on a standard-rated line is ``gross x rate / (1 + rate)``.
Zero-rated and exempt lines carry no VAT but are reported separately, because only
zero-rated sales let you recover input VAT. Rates, thresholds and due dates are
settings, never hard-coded law: check the current figures with the tax authority.

Everything reads ``raw_objects`` so tax periods span both ledger periods.

E-invoicing here means *structured invoice data, numbering and clearance tracking*.
Actually clearing an invoice with the tax authority goes through an accredited
access-point provider; this module can post the invoice to such a provider over
HTTPS and records the IRN, stamp and QR text it returns, or you can record those
by hand after submitting in a provider's portal. The payload is a generic,
UBL-style structure: map it to your provider's exact schema before going live.
"""
import ipaddress
import json
import socket
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.db import transaction
from django.db.models import DecimalField, Sum
from django.utils import timezone

from .accounting import _sale_expressions
from .models import EInvoice, InputVatEntry, TaxSettings

ZERO = Decimal("0")
CENT = Decimal("0.01")
MONEY = DecimalField(max_digits=24, decimal_places=6)
PROVIDER_TIMEOUT = 20
CURRENCY_CODES = {"₦": "NGN", "$": "USD", "£": "GBP", "€": "EUR", "KSh": "KES", "GH₵": "GHS", "R": "ZAR"}


def q(value):
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def get_settings(business):
    settings, _ = TaxSettings.raw_objects.get_or_create(business=business)
    return settings


def vat_from_gross(gross, rate_percent):
    rate = Decimal(rate_percent) / 100
    return q(Decimal(gross) * rate / (1 + rate))


def currency_code(business):
    return CURRENCY_CODES.get(business.currency_symbol or "", business.currency_symbol or "NGN")


def applies_on(settings, day):
    """VAT applies to a sale only if the business is registered and the sale is on/after registration."""
    return bool(settings.vat_registered and (settings.vat_registration_date is None or day >= settings.vat_registration_date))


def _sales(business, start, end):
    from sales.models import Sale
    return Sale.raw_objects.filter(business=business, date__range=(start, end))


# --------------------------------------------------------------------------
# VAT return
# --------------------------------------------------------------------------

def vat_return(business, start, end):
    """Output VAT, input VAT and the net position for a tax period."""
    from sales.models import SaleItem
    settings = get_settings(business)
    _m, line_total, _c, _u = _sale_expressions(prefix="")
    sales = _sales(business, start, end)
    if settings.vat_registration_date:
        sales = sales.filter(date__gte=settings.vat_registration_date)
    groups = {"standard": ZERO, "zero": ZERO, "exempt": ZERO}
    for row in (SaleItem.objects.filter(sale__in=sales).values("finished_good__vat_treatment")
                .annotate(gross=Sum(line_total, output_field=MONEY))):
        groups[row["finished_good__vat_treatment"] or "standard"] += q(row["gross"])
    registered = settings.vat_registered
    output_vat = vat_from_gross(groups["standard"], settings.vat_rate) if registered else ZERO
    inputs = InputVatEntry.raw_objects.filter(business=business, date__range=(start, end))
    claimable = inputs.filter(claimable=True).aggregate(v=Sum("vat_amount"))["v"] or ZERO
    blocked = inputs.filter(claimable=False).aggregate(v=Sum("vat_amount"))["v"] or ZERO
    net = output_vat - claimable
    month_end = date(end.year + (end.month == 12), (end.month % 12) + 1, 1)
    due = month_end.replace(day=min(settings.filing_due_day, 28))
    return {
        "start": start, "end": end, "registered": registered, "rate": settings.vat_rate,
        "standard_gross": groups["standard"], "standard_net": groups["standard"] - output_vat,
        "zero_rated": groups["zero"], "exempt": groups["exempt"],
        "output_vat": output_vat, "input_vat": q(claimable), "input_vat_blocked": q(blocked),
        "net_vat": q(net), "refundable": net < 0, "due_date": due,
    }


def vat_register_rows(business, start, end):
    """Every sale line in the period with its VAT, for the accountant's register."""
    from sales.models import SaleItem
    settings = get_settings(business)
    items = (SaleItem.objects.filter(sale__in=_sales(business, start, end))
             .select_related("sale", "finished_good").order_by("sale__date", "sale_id", "id"))
    for item in items:
        gross = q(item.line_total)
        treatment = item.finished_good.vat_treatment or "standard"
        vat = vat_from_gross(gross, settings.vat_rate) if applies_on(settings, item.sale.date) and treatment == "standard" else ZERO
        yield {"date": item.sale.date, "sale_id": item.sale_id, "customer": item.sale.customer,
               "product": item.finished_good.name, "treatment": treatment if applies_on(settings, item.sale.date) else "not registered",
               "gross": gross, "vat": vat, "net": gross - vat}


def registration_status(business, today=None):
    """Trailing 12-month turnover against the registration threshold."""
    from sales.models import SaleItem
    settings = get_settings(business)
    today = today or timezone.localdate()
    _m, line_total, _c, _u = _sale_expressions(prefix="")
    sales = _sales(business, today - timedelta(days=365), today)
    turnover = q(SaleItem.objects.filter(sale__in=sales).aggregate(t=Sum(line_total, output_field=MONEY))["t"])
    threshold = settings.registration_threshold or ZERO
    if settings.vat_registered:
        state = "registered"
    elif threshold and turnover >= threshold:
        state = "exceeded"
    elif threshold and turnover >= threshold * Decimal("0.8"):
        state = "approaching"
    else:
        state = "below"
    return {"state": state, "turnover": turnover, "threshold": threshold,
            "share": (turnover / threshold * 100) if threshold else None}


# --------------------------------------------------------------------------
# E-invoices
# --------------------------------------------------------------------------

class EInvoiceError(ValueError):
    pass


def build_invoice_payload(sale, settings, buyer_tin=""):
    business = sale.business
    vat_on = applies_on(settings, sale.date)
    lines, tax = [], {}
    for item in sale.items.select_related("finished_good"):
        gross = q(item.line_total)
        treatment = item.finished_good.vat_treatment or "standard"
        category = treatment if vat_on else "out_of_scope"
        vat = vat_from_gross(gross, settings.vat_rate) if vat_on and treatment == "standard" else ZERO
        units = item.commercial_quantity if item.commercial_quantity is not None else Decimal(item.total_units or 0) / item.price_unit_quantity
        label = item.finished_good.name + (f" ({item.product_option_value})" if item.product_option_value else "")
        lines.append({
            "description": label, "quantity": str(units), "unit_price_gross": str(q(gross / units) if units else q(gross)),
            "vat_category": category, "vat_rate": str(settings.vat_rate if category == "standard" else ZERO),
            "net_amount": str(q(gross - vat)), "vat_amount": str(q(vat)), "gross_amount": str(q(gross)),
        })
        bucket = tax.setdefault(category, {"category": category, "rate": str(settings.vat_rate if category == "standard" else ZERO),
                                           "taxable_amount": ZERO, "vat_amount": ZERO})
        bucket["taxable_amount"] += gross - vat
        bucket["vat_amount"] += vat
    net = q(sum((Decimal(l["net_amount"]) for l in lines), ZERO))
    vat_total = q(sum((Decimal(l["vat_amount"]) for l in lines), ZERO))
    return {
        "document_type": "invoice", "issue_date": sale.date.isoformat(), "currency": currency_code(business),
        "supplier": {"name": business.name, "tin": settings.tin, "vat_registered": bool(settings.vat_registered)},
        "buyer": {"name": sale.customer or "Walk-in customer", "tin": buyer_tin},
        "lines": lines,
        "tax_breakdown": [{**b, "taxable_amount": str(q(b["taxable_amount"])), "vat_amount": str(q(b["vat_amount"]))} for b in tax.values()],
        "totals": {"net": str(net), "vat": str(vat_total), "gross": str(q(net + vat_total))},
        "payment_status": "paid" if sale.transaction_type == "paid" else "unpaid",
    }


@transaction.atomic
def prepare_einvoice(business, user, sale, buyer_tin=""):
    """Create (or refresh, if not yet submitted) the e-invoice for a sale."""
    settings = TaxSettings.raw_objects.select_for_update().get_or_create(business=business)[0]
    if not settings.einvoice_enabled:
        raise EInvoiceError("E-invoicing is switched off. Turn it on in Tax settings first.")
    if not settings.tin:
        raise EInvoiceError("Add your tax identification number (TIN) in Tax settings before creating invoices.")
    if sale.business_id != business.pk:
        raise EInvoiceError("That sale does not belong to this business.")
    if not sale.items.exists():
        raise EInvoiceError("This sale has no lines to invoice.")
    existing = EInvoice.raw_objects.filter(business=business, sale=sale).first()
    if existing:
        if existing.status in (EInvoice.SUBMITTED, EInvoice.CLEARED):
            raise EInvoiceError("This invoice has already been submitted and can no longer be changed.")
        existing.buyer_tin = buyer_tin or existing.buyer_tin
        existing.payload = build_invoice_payload(sale, settings, existing.buyer_tin)
        existing.status, existing.error = EInvoice.READY, ""
        existing.save(update_fields=["buyer_tin", "payload", "status", "error", "updated_at"])
        return existing
    number = f"{settings.invoice_prefix}-{settings.next_invoice_number:06d}"
    settings.next_invoice_number += 1
    settings.save(update_fields=["next_invoice_number", "updated_at"])
    return EInvoice.raw_objects.create(
        business=business, created_by=user, sale=sale, invoice_number=number, status=EInvoice.READY,
        buyer_tin=buyer_tin, payload=build_invoice_payload(sale, settings, buyer_tin))


def _check_endpoint(url):
    """HTTPS only, and never an internal address (a configured URL must not reach our own network)."""
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or not parsed.hostname:
        raise EInvoiceError("The provider address must be a secure https:// URL.")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except socket.gaierror:
        raise EInvoiceError("The provider address could not be resolved.")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise EInvoiceError("The provider address points to an internal network, which is not allowed.")


def _post_json(url, body, api_key):
    request = Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        **({"Authorization": f"Bearer {api_key}"} if api_key else {})})
    with urlopen(request, timeout=PROVIDER_TIMEOUT) as response:  # noqa: S310 (https enforced above)
        return json.loads(response.read().decode() or "{}")


def submit_einvoice(einvoice):
    """Post the invoice to the configured provider and record what comes back."""
    from whatsapp_storefront.security import decrypt_access_token
    settings = get_settings(einvoice.business)
    if einvoice.status in (EInvoice.SUBMITTED, EInvoice.CLEARED):
        raise EInvoiceError("This invoice has already been submitted.")
    if settings.einvoice_provider != TaxSettings.PROVIDER_WEBHOOK:
        raise EInvoiceError("No provider is connected. Submit in your provider's portal, then record the IRN here.")
    _check_endpoint(settings.einvoice_endpoint)
    try:
        api_key = decrypt_access_token(settings.einvoice_api_key_encrypted) if settings.einvoice_api_key_encrypted else ""
    except ValueError:
        raise EInvoiceError("The saved provider key cannot be read. Enter it again in Tax settings.")
    body = {"invoice_number": einvoice.invoice_number, **einvoice.payload}
    einvoice.submitted_at = timezone.now()
    try:
        result = _post_json(settings.einvoice_endpoint, body, api_key)
    except HTTPError as exc:
        return _fail(einvoice, f"The provider answered with an error ({exc.code}).")
    except (URLError, TimeoutError, OSError, ValueError):
        return _fail(einvoice, "The provider could not be reached. Try again shortly.")
    status = str(result.get("status", "")).lower()
    irn, csid = str(result.get("irn", ""))[:120], str(result.get("csid") or result.get("stamp") or "")[:255]
    einvoice.response = result if isinstance(result, dict) else {}
    einvoice.qr_text = str(result.get("qr") or result.get("qr_code") or "")
    if status in {"rejected", "failed", "error"}:
        einvoice.status, einvoice.error = EInvoice.REJECTED, str(result.get("message", "Rejected by the provider."))[:255]
    elif irn:
        einvoice.irn, einvoice.csid = irn, csid
        einvoice.status, einvoice.cleared_at, einvoice.error = EInvoice.CLEARED, timezone.now(), ""
    else:
        einvoice.status, einvoice.error = EInvoice.SUBMITTED, ""
    einvoice.save()
    return einvoice


def _fail(einvoice, message):
    einvoice.status, einvoice.error = EInvoice.FAILED, message
    einvoice.save(update_fields=["status", "error", "submitted_at", "updated_at"])
    return einvoice


def record_clearance(einvoice, irn, csid="", qr_text=""):
    """Record the IRN and stamp from a portal submission made outside Inprofic."""
    irn = (irn or "").strip()
    if not irn:
        raise EInvoiceError("Enter the invoice reference number (IRN) returned by the tax system.")
    if einvoice.status == EInvoice.CLEARED:
        raise EInvoiceError("This invoice is already cleared.")
    einvoice.irn, einvoice.csid, einvoice.qr_text = irn[:120], (csid or "")[:255], qr_text or ""
    einvoice.status, einvoice.cleared_at, einvoice.error = EInvoice.CLEARED, timezone.now(), ""
    einvoice.submitted_at = einvoice.submitted_at or timezone.now()
    einvoice.save()
    return einvoice
