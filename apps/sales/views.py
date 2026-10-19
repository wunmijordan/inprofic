from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import SaleForm, SaleItemFormSet, CustomerForm, CustomerProductPriceFormSet
from .models import Customer, CustomerProductPrice, Sale, SaleItem
from inventory.models import FinishedGood
from production.models import ProductionCostSnapshot
from inventory.services import consume_transferred_physical_stock, record_finished_good_movement
from inventory.models import StockMovement
from core.invoice import sale_invoice_pdf
from core.services import record_cash, audit
from core.models import FinancialTransaction
from core.verticals import vertical_config


def today():
    return timezone.localdate()


def _direct_sale_prices(channel, saleable_products):
    goods = FinishedGood.objects.filter(saleable_products).prefetch_related("channel_prices")
    prices = {str(g.pk): str(g.selling_price_for(channel)) for g in goods}
    customer_prices = {}
    if channel == "distribution":
        for customer_id, good_id, price in CustomerProductPrice.objects.filter(
            channel=channel,
            customer__active=True,
        ).values_list("customer_id", "finished_good_id", "price"):
            customer_prices.setdefault(str(customer_id), {})[str(good_id)] = str(price)
    return prices, customer_prices


@login_required
def customer_list(request):
    open_sales = (
        Sale.objects.filter(
            source__in=("distribution_order", "online_order"),
            transaction_type__in=("unpaid", "partial"),
        )
        .prefetch_related(
            Prefetch("items", queryset=SaleItem.objects.select_related("finished_good")),
            "payments",
        )
    )
    customers = Customer.objects.prefetch_related(
        Prefetch("sales_records", queryset=open_sales, to_attr="_open_sales_for_balance")
    )
    return render(request, "sales/customers_list.html", {"customers": customers})


@login_required
def customer_form(request, pk=None):
    obj = get_object_or_404(Customer, pk=pk) if pk else None
    customer_instance = obj if obj else Customer(business=request.business, created_by=request.user)
    if request.method == "POST":
        form = CustomerForm(request.POST, instance=obj)
        price_formset = CustomerProductPriceFormSet(
            request.POST, instance=customer_instance, prefix="prices"
        )
        parent_valid = form.is_valid()
        pricing_valid = price_formset.is_valid()
        if parent_valid and pricing_valid:
            with transaction.atomic():
                customer = form.save(commit=False)
                customer.business = request.business
                if obj is None:
                    customer.created_by = request.user
                customer.save()
                price_formset.instance = customer
                prices = price_formset.save(commit=False)
                for price in prices:
                    price.business = request.business
                    if not price.pk:
                        price.created_by = request.user
                    price.save()
                for deleted in price_formset.deleted_objects:
                    deleted.delete()
            messages.success(request, "Customer saved.")
            return redirect("customer_list")
    else:
        form = CustomerForm(instance=obj)
        price_formset = CustomerProductPriceFormSet(instance=customer_instance, prefix="prices")
    return render(request, "sales/customer_form.html", {
        "form": form, "price_formset": price_formset, "obj": obj
    })


@login_required
def customer_toggle_active(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == "POST":
        customer.active = not customer.active
        customer.save(update_fields=["active", "updated_at"])
        messages.success(request, f"Customer {'activated' if customer.active else 'archived'}.")
    return redirect("customer_list")


@login_required
def sales_list(request):
    sales = list(
        Sale.objects.select_related("business", "created_by", "linked_order").prefetch_related(
            Prefetch("items", queryset=SaleItem.objects.select_related("finished_good"))
        )
    )
    total_revenue = sum((s.total for s in sales if s.transaction_type == "paid"), Decimal("0"))
    unpaid_value = sum((s.total for s in sales if s.transaction_type == "unpaid"), Decimal("0"))
    return render(request, "sales/sales_list.html", {"sales": sales, "total_revenue": total_revenue, "unpaid_value": unpaid_value})


@login_required
def sale_form(request):
    """Physical store stock only — immediate, deducts from existing shelf
    stock right away. For a customer order or a physical store restock
    (production needed first), use the Orders page instead."""
    sale_channel = vertical_config(request.business)["direct_sale_channel"]
    saleable_products = (
        Q(stock__isnull=False, reorder_level__gt=0)
        | Q(stock__isnull=False, business__vertical__in=("wholesale", "retail"))
        | Q(transferred_market_stock__gt=0)
    )
    if request.method == "POST":
        form = SaleForm(request.POST, business=request.business)
        formset = SaleItemFormSet(request.POST, instance=Sale())
        if form.is_valid() and formset.is_valid():
            # Defence in depth: the form queryset already restricts choices,
            # but never rely on browser-side/form rendering restrictions alone.
            invalid_products = [
                f.cleaned_data["finished_good"].name
                for f in formset.forms
                if f.cleaned_data and not f.cleaned_data.get("DELETE")
                and f.cleaned_data.get("finished_good")
                and not f.cleaned_data["finished_good"].can_sell_from_physical_store
            ]
            if invalid_products:
                formset.forms[0].add_error(
                    "finished_good",
                    "Only products in physical-store shelf stock can be sold here.",
                )
            force = request.POST.get("force") == "1"
            shortages = []
            for f in formset.forms:
                if not f.cleaned_data or f.cleaned_data.get("DELETE"):
                    continue
                good = f.cleaned_data["finished_good"]
                if not good.can_sell_from_physical_store:
                    shortages.append({"name": good.name, "unit": good.unit, "needed": Decimal("0"), "have": Decimal("0"), "short": Decimal("0"), "message": "Not available for physical-store sales."})
                    continue
                upb = good.units_per_batch or Decimal("1")
                total_units = (f.cleaned_data.get("batch_qty") or Decimal("0")) * upb + (f.cleaned_data.get("piece_qty") or Decimal("0"))
                # Customer checkout reservations are a temporary hard hold.
                # Direct/POS sales must not consume those units while a website
                # customer is paying, otherwise a fully paid checkout could
                # become unfulfillable before intake materialization.
                from commerce.checkout_services import available_physical_stock
                physical_available = good.physical_saleable_stock
                available = available_physical_stock(good)
                reserved_for_checkout = max(Decimal("0"), physical_available - available)
                if total_units > available:
                    shortages.append({
                        "name": good.name,
                        "unit": good.unit,
                        "needed": total_units,
                        "have": available,
                        "short": total_units - available,
                        "reserved_for_checkout": reserved_for_checkout,
                        # Reserved website checkout stock is never force-sellable.
                        # The ordinary force option otherwise preserves the
                        # existing configured-shelf workflow.
                        "restricted": (not good.is_physical_store_configured) or reserved_for_checkout > 0,
                    })
            restricted_shortage = any(row.get("restricted") for row in shortages)
            if invalid_products or restricted_shortage or (shortages and not force):
                prices, customer_prices = _direct_sale_prices(sale_channel, saleable_products)
                return render(request, "sales/sale_form.html", {
                    "form": form,
                    "formset": formset,
                    "shortages": shortages,
                    "prices": prices,
                    "customer_prices": customer_prices,
                    "force_allowed": bool(shortages and not restricted_shortage and not invalid_products),
                })
            with transaction.atomic():
                sale = form.save(commit=False)
                sale.business = request.business
                sale.created_by = request.user
                sale.source = "distribution_order" if sale_channel == "distribution" else "walkin"
                sale.save()
                formset.instance = sale
                items = formset.save(commit=False)
                for item in items:
                    item.price = item.finished_good.selling_price_for(
                        sale_channel,
                        sale.customer_master if sale_channel == "distribution" else None,
                    )
                    item.save()
                for obj in formset.deleted_objects:
                    obj.delete()
                for item in sale.items.select_related("finished_good"):
                    good = item.finished_good
                    latest_cost = ProductionCostSnapshot.objects.filter(
                        finished_good=good, production_date__lte=sale.date
                    ).order_by("-production_date", "-id").first()
                    latest_purchase = good.stock_movements.filter(
                        movement_type=StockMovement.FG_PURCHASE,
                        occurred_at__date__lte=sale.date,
                        quantity__gt=0,
                    ).order_by("-occurred_at", "-id").first()
                    if not request.business.uses_production and latest_purchase:
                        item.unit_cost = latest_purchase.unit_value
                    else:
                        item.unit_cost = (
                            latest_cost.unit_cost if latest_cost
                            else latest_purchase.unit_value if latest_purchase
                            else good.est_cost
                        )
                    item.save(update_fields=["unit_cost"])
                    record_finished_good_movement(
                        good,
                        -item.total_units,
                        StockMovement.FG_SALE,
                        note="Wholesale stock release" if sale_channel == "distribution" else "Walk-in sale",
                        affects_stock=True,
                        unit_value=item.unit_cost,
                    )
                    consume_transferred_physical_stock(good, item.total_units)

                if sale.transaction_type == "paid":
                    record_cash(request.business, request.user, date=sale.date, amount=sale.total, transaction_type=FinancialTransaction.INCOME, category="Sales revenue", description=f"Sale #{sale.pk}", payment_method=sale.payment_method, reference=f"SALE-{sale.pk}", account=sale.account)
                audit(request.business, request.user, "create", sale, f"Sale #{sale.pk} recorded", {"transaction_type": sale.transaction_type, "unpaid_reason": sale.unpaid_description})
            messages.success(request, "Sale recorded.")
            return redirect("sales_list")
    else:
        form = SaleForm(initial={"date": today(), "customer": "Walk-in"}, business=request.business)
        formset = SaleItemFormSet(instance=Sale())
    prices, customer_prices = _direct_sale_prices(sale_channel, saleable_products)
    return render(request, "sales/sale_form.html", {
        "form": form,
        "formset": formset,
        "prices": prices,
        "customer_prices": customer_prices,
    })


@login_required
def sale_delete(request, pk):
    obj = get_object_or_404(Sale, pk=pk)
    if request.method == "POST":
        if obj.market_stock_movements.exists():
            messages.error(request, "A Market Stock release sale cannot be deleted because it has inventory allocation history. Record a Distribution return instead.")
        elif obj.items.filter(distribution_returns__isnull=False).exists():
            messages.error(request, "This Distribution sale has recorded returns and cannot be deleted.")
        else:
            obj.delete()
            messages.success(request, "Removed.")
    return redirect("sales_list")

@login_required
def sale_invoice(request, pk):
    sale = get_object_or_404(Sale.objects.prefetch_related("items__finished_good"), pk=pk)
    return sale_invoice_pdf(sale)
