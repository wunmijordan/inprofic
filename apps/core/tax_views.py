"""Tax screens: settings, VAT return and register, input VAT, product VAT, e-invoices."""
import csv
import json
import re
from datetime import date as date_cls, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import ledger, tax
from .models import EInvoice, InputVatEntry, TaxSettings
from .services import audit

LIST_LIMIT = 200


def _date(raw, default):
    try:
        return date_cls.fromisoformat((raw or "").strip()) if raw else default
    except ValueError:
        return default


def _money(raw, label):
    try:
        value = Decimal((raw or "").replace(",", "").strip())
    except InvalidOperation:
        raise ValueError(f"{label} must be a number.")
    if value < 0:
        raise ValueError(f"{label} cannot be negative.")
    return value


def _admin(request):
    return ledger.can_manage(request.user, request.business)


@login_required
@require_http_methods(["GET", "POST"])
def tax_home(request):
    business = request.business
    settings = tax.get_settings(business)
    if request.method == "POST":
        if not _admin(request):
            return render(request, "403.html", status=403)
        try:
            rate = _money(request.POST.get("vat_rate"), "VAT rate")
            threshold = _money(request.POST.get("registration_threshold") or "0", "Registration threshold")
            due_day = int(request.POST.get("filing_due_day") or 21)
            prefix = (request.POST.get("invoice_prefix") or "INV").strip().upper()
            endpoint = (request.POST.get("einvoice_endpoint") or "").strip()
            provider = request.POST.get("einvoice_provider") or TaxSettings.PROVIDER_NONE
            if rate > 100 or not 1 <= due_day <= 28:
                raise ValueError("The VAT rate must be between 0 and 100, and the due day between 1 and 28.")
            if not re.fullmatch(r"[A-Z0-9-]{1,12}", prefix):
                raise ValueError("The invoice prefix may use letters, numbers and dashes (up to 12 characters).")
            if provider == TaxSettings.PROVIDER_WEBHOOK and endpoint and not endpoint.startswith("https://"):
                raise ValueError("The provider address must start with https://.")
            settings.vat_registered = request.POST.get("vat_registered") == "1"
            settings.vat_registration_date = _date(request.POST.get("vat_registration_date"), None)
            settings.tin = (request.POST.get("tin") or "").strip()[:30]
            settings.vat_rate, settings.registration_threshold, settings.filing_due_day = rate, threshold, due_day
            settings.einvoice_enabled = request.POST.get("einvoice_enabled") == "1"
            settings.einvoice_provider, settings.einvoice_endpoint, settings.invoice_prefix = provider, endpoint, prefix
            key = (request.POST.get("einvoice_api_key") or "").strip()
            if key:
                from whatsapp_storefront.security import WhatsAppTokenError, encrypt_access_token
                try:
                    settings.einvoice_api_key_encrypted = encrypt_access_token(key)
                except WhatsAppTokenError:
                    raise ValueError("API keys can only be stored once the server's WHATSAPP_TOKEN_ENCRYPTION_KEY secret is set (32+ characters). Nothing was saved.")
            settings.save()
            audit(business, request.user, "tax_settings", settings, "Tax settings updated",
                  {"vat_registered": settings.vat_registered, "einvoice_enabled": settings.einvoice_enabled})
            messages.success(request, "Tax settings saved.")
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("tax_home")
    return render(request, "core/tax_home.html", {
        "s": settings, "status": tax.registration_status(business), "can_edit": _admin(request),
        "has_key": bool(settings.einvoice_api_key_encrypted),
    })


def _last_month(today):
    first_this = today.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    return last_prev.replace(day=1), last_prev


@login_required
@require_GET
def vat_return_page(request):
    business = request.business
    default_start, default_end = _last_month(timezone.localdate())
    start, end = _date(request.GET.get("start"), default_start), _date(request.GET.get("end"), default_end)
    if start > end:
        start, end = end, start
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="vat-register-{start}-{end}.csv"'
        writer = csv.writer(response)
        writer.writerow(["Date", "Sale", "Customer", "Product", "VAT treatment", "Gross", "VAT", "Net"])
        for r in tax.vat_register_rows(business, start, end):
            writer.writerow([r["date"], r["sale_id"], r["customer"], r["product"], r["treatment"], r["gross"], r["vat"], r["net"]])
        return response
    return render(request, "core/vat_return.html", {"r": tax.vat_return(business, start, end), "start": start, "end": end})


@login_required
@require_http_methods(["GET", "POST"])
def input_vat(request):
    business = request.business
    if request.method == "POST":
        try:
            if request.POST.get("action") == "delete":
                get_object_or_404(InputVatEntry.raw_objects, pk=request.POST.get("entry"), business=business).delete()
                messages.success(request, "Entry removed.")
            else:
                entry = InputVatEntry.raw_objects.create(
                    business=business, created_by=request.user, date=_date(request.POST.get("date"), timezone.localdate()),
                    supplier=(request.POST.get("supplier") or "")[:160], reference=(request.POST.get("reference") or "")[:80],
                    kind=request.POST.get("kind") if request.POST.get("kind") in {"purchase", "expense"} else "purchase",
                    net_amount=_money(request.POST.get("net_amount"), "Net amount"),
                    vat_amount=_money(request.POST.get("vat_amount"), "VAT amount"),
                    claimable=request.POST.get("claimable") == "1", note=(request.POST.get("note") or "")[:255])
                messages.success(request, "Input VAT recorded. Rebuild the books to include it.")
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("input_vat")
    start, end = _date(request.GET.get("start"), None), _date(request.GET.get("end"), None)
    qs = InputVatEntry.raw_objects.filter(business=business)
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    return render(request, "core/input_vat.html", {"entries": qs[:LIST_LIMIT], "today": timezone.localdate(), "start": start, "end": end})


@login_required
@require_http_methods(["GET", "POST"])
def product_vat(request):
    from inventory.models import FinishedGood
    business = request.business
    goods = FinishedGood.raw_objects.filter(business=business).order_by("name")
    if request.method == "POST":
        valid = {c for c, _l in FinishedGood.VAT_CHOICES}
        changed = 0
        for good in goods:
            value = request.POST.get(f"vat_{good.pk}")
            if value in valid and value != good.vat_treatment:
                FinishedGood.raw_objects.filter(pk=good.pk, business=business).update(vat_treatment=value)
                changed += 1
        if changed:
            audit(business, request.user, "product_vat", None, f"VAT treatment changed on {changed} product(s)", {"changed": changed})
        messages.success(request, f"{changed} product(s) updated." if changed else "Nothing changed.")
        return redirect("product_vat")
    return render(request, "core/product_vat.html", {"goods": goods, "choices": FinishedGood.VAT_CHOICES})


@login_required
@require_http_methods(["GET", "POST"])
def einvoices(request):
    from sales.models import Sale
    business = request.business
    settings = tax.get_settings(business)
    if request.method == "POST":
        try:
            action = request.POST.get("action")
            if action == "prepare":
                sale = get_object_or_404(Sale.raw_objects, pk=request.POST.get("sale"), business=business)
                invoice = tax.prepare_einvoice(business, request.user, sale, (request.POST.get("buyer_tin") or "").strip()[:30])
                audit(business, request.user, "einvoice_prepare", invoice, f"E-invoice {invoice.invoice_number} prepared for sale #{sale.pk}")
                return redirect("einvoice_detail", pk=invoice.pk)
            invoice = get_object_or_404(EInvoice.raw_objects, pk=request.POST.get("invoice"), business=business)
            if action == "submit":
                tax.submit_einvoice(invoice)
                audit(business, request.user, "einvoice_submit", invoice, f"E-invoice {invoice.invoice_number}: {invoice.status}")
            elif action == "record":
                tax.record_clearance(invoice, request.POST.get("irn"), request.POST.get("csid"), request.POST.get("qr_text"))
                audit(business, request.user, "einvoice_record", invoice, f"E-invoice {invoice.invoice_number} cleared (IRN recorded)")
            return redirect("einvoice_detail", pk=invoice.pk)
        except tax.EInvoiceError as exc:
            messages.error(request, str(exc))
        return redirect("einvoices")
    start = _date(request.GET.get("start"), timezone.localdate() - timedelta(days=30))
    end = _date(request.GET.get("end"), timezone.localdate())
    sales = list(Sale.raw_objects.filter(business=business, date__range=(start, end)).order_by("-date", "-id")[:LIST_LIMIT])
    existing = {i.sale_id: i for i in EInvoice.raw_objects.filter(business=business, sale__in=sales)}
    rows = [{"sale": s, "invoice": existing.get(s.pk)} for s in sales]
    return render(request, "core/einvoices.html", {"rows": rows, "settings": settings, "start": start, "end": end})


@login_required
@require_GET
def einvoice_detail(request, pk):
    invoice = get_object_or_404(EInvoice.raw_objects.select_related("sale"), pk=pk, business=request.business)
    if request.GET.get("format") == "json":
        response = HttpResponse(json.dumps({"invoice_number": invoice.invoice_number, **invoice.payload}, indent=2), content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="{invoice.invoice_number}.json"'
        return response
    return render(request, "core/einvoice_detail.html", {
        "inv": invoice, "p": invoice.payload, "settings": tax.get_settings(request.business),
        "payload_json": json.dumps(invoice.payload, indent=2),
    })
