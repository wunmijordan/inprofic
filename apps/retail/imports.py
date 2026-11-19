"""Previewed CSV onboarding; opening stock uses the existing movement ledger."""

import csv
import io
from decimal import Decimal, InvalidOperation
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import render, redirect
from core.services import audit
from inventory.models import FinishedGood, StockMovement
from inventory.services import record_finished_good_movement
from .views import permission


class Upload(forms.Form):
    file = forms.FileField(
        help_text="CSV columns: name, unit, selling_price, opening_stock, barcode. Up to 500 new products; existing products are never overwritten."
    )


@login_required
def import_products(request):
    permission(request, "inventory", "edit")
    form = Upload(request.POST or None, request.FILES or None)
    preview = []
    token = ""
    if request.method == "POST":
        try:
            if request.POST.get("preview_token"):
                data = signing.loads(
                    request.POST["preview_token"],
                    salt="retail-product-import",
                    max_age=1800,
                )
                if (
                    data["business"] != request.business.pk
                    or data["user"] != request.user.pk
                ):
                    raise ValidationError("This preview belongs to another account.")
                with transaction.atomic():
                    from core.models import Business

                    Business.objects.select_for_update().get(pk=request.business.pk)
                    for row in data["rows"]:
                        if (
                            row["barcode"]
                            and FinishedGood.raw_objects.filter(
                                business=request.business, barcode=row["barcode"]
                            ).exists()
                        ):
                            raise ValidationError(
                                "A barcode was added after this preview. Prepare a new preview."
                            )
                        if FinishedGood.raw_objects.filter(
                            business=request.business, name__iexact=row["name"]
                        ).exists():
                            raise ValidationError(
                                f"{row['name']} already exists; prepare a new preview."
                            )
                        good = FinishedGood.raw_objects.create(
                            business=request.business,
                            created_by=request.user,
                            name=row["name"],
                            unit=row["unit"],
                            selling_price=Decimal(row["selling_price"]),
                            barcode=row["barcode"],
                            stock=0,
                            reorder_level=1,
                            source_type=FinishedGood.SOURCE_PURCHASED_FOR_RESALE,
                        )
                        quantity = Decimal(row["opening_stock"])
                        if quantity:
                            record_finished_good_movement(
                                good,
                                quantity,
                                StockMovement.FG_PURCHASE,
                                note="Confirmed CSV opening stock",
                            )
                        audit(
                            request.business,
                            request.user,
                            "product_import",
                            good,
                            "New product imported with opening stock",
                            {"quantity": str(quantity)},
                        )
                messages.success(request, f"Imported {len(data['rows'])} products.")
                return redirect("retail_workspace")
            if form.is_valid():
                upload = form.cleaned_data["file"]
                if upload.size > 2 * 1024 * 1024:
                    raise ValidationError("Choose a CSV smaller than 2 MB.")
                reader = csv.DictReader(io.StringIO(upload.read().decode("utf-8-sig")))
                names = set()
                barcodes = set()
                for row in reader:
                    if len(preview) >= 500:
                        raise ValidationError("Import at most 500 products at a time.")
                    normalized = {
                        str(key or "")
                        .strip()
                        .lower()
                        .replace(" ", "_"): str(value or "")
                        .strip()
                        for key, value in row.items()
                    }
                    name = (
                        normalized.get("name")
                        or normalized.get("product_name")
                        or normalized.get("product")
                    )
                    if not name or len(name) > 120:
                        raise ValidationError(
                            "Each row needs a product name, up to 120 characters."
                        )
                    if (
                        name.casefold() in names
                        or FinishedGood.raw_objects.filter(
                            business=request.business, name__iexact=name
                        ).exists()
                    ):
                        raise ValidationError(f"Duplicate product: {name}.")
                    names.add(name.casefold())
                    unit = normalized.get("unit") or "piece"
                    barcode = normalized.get("barcode", "")
                    if barcode and (
                        barcode in barcodes
                        or FinishedGood.raw_objects.filter(
                            business=request.business, barcode=barcode
                        ).exists()
                    ):
                        raise ValidationError(
                            "A barcode is already assigned to another product."
                        )
                    if barcode:
                        barcodes.add(barcode)
                    if len(unit) > 20 or len(barcode) > 80:
                        raise ValidationError("Unit or barcode is too long.")
                    price = Decimal(
                        normalized.get("selling_price") or normalized.get("price") or 0
                    )
                    stock = Decimal(
                        normalized.get("opening_stock") or normalized.get("stock") or 0
                    )
                    if (
                        not price.is_finite()
                        or not stock.is_finite()
                        or min(price, stock) < 0
                        or stock != stock.quantize(Decimal(".01"))
                        or price != price.quantize(Decimal(".01"))
                    ):
                        raise ValidationError(
                            "Use non-negative prices and quantities with at most two decimal places."
                        )
                    if max(price, stock) >= Decimal("1000000000"):
                        raise ValidationError("Price or opening stock is too large.")
                    preview.append(
                        {
                            "name": name,
                            "unit": unit,
                            "barcode": barcode,
                            "selling_price": str(price),
                            "opening_stock": str(stock),
                        }
                    )
                if not preview:
                    raise ValidationError("The CSV has no product rows.")
                token = signing.dumps(
                    {
                        "business": request.business.pk,
                        "user": request.user.pk,
                        "rows": preview,
                    },
                    salt="retail-product-import",
                    compress=True,
                )
        except (
            ValidationError,
            ValueError,
            InvalidOperation,
            UnicodeDecodeError,
            signing.BadSignature,
        ) as exc:
            form.add_error(
                None,
                (
                    " ".join(exc.messages)
                    if isinstance(exc, ValidationError)
                    else "The CSV or preview is invalid. Prepare a fresh preview."
                ),
            )
    return render(
        request,
        "retail/import.html",
        {"form": form, "preview": preview, "preview_token": token},
    )
