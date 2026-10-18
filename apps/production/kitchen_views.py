"""Assigning a production order's output to channels.

One production usually serves several purposes at once - a customer's online
order, shelf stock, a bulk distribution customer - and the people entering the
order (floor staff or not) rarely know the split yet. A manager therefore says,
product by product, how many go to which channel, right on this page.

The order stays ONE order: it is approved once, releases its materials once and
produces one batch per product, whatever its production basis. What is saved is
an ``OrderAllocation`` per channel (with the customer / payment / stock details
that channel needs) and an ``OrderAllocationLine`` per product. The allocations
are posted when the order is completed (see ``allocations.py``).

Quantities are handled as *units*: a batch is ``units_per_batch`` pieces and
loose pieces are only the part of a batch that is left over, so the two inputs
always add up to one number and a batch can be broken to fill a channel.
"""
from decimal import InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from core.models import CashAccount
from core.services import audit
from core.verticals import vertical_config
from sales.models import Customer

from . import allocations as channel_allocations
from .allocations import ZERO, cents, item_units, per_batch, to_batches_and_pieces
from .forms import OrderForm
from .models import Order

MARKET_STOCK = "distribution_market_stock"
MAX_DESTINATIONS = 5
MIN_VISIBLE_DESTINATIONS = 2
# Channel fields decided by OrderForm and copied onto an order that takes a single channel.
CHANNEL_FIELDS = (
    "order_type", "is_market_stock", "production_destination", "non_stock_purpose", "customer",
    "customer_name", "customer_region", "customer_group", "transaction_type", "unpaid_description",
    "payment_method", "account", "customer_payment_status", "customer_payment_method",
    "customer_payment_account",
)


def _read_decimal(raw):
    text = (raw or "").strip()
    return cents(text) if text else ZERO


def _plain(value):
    """A quantity as the plain text an input expects ('' for zero)."""
    return "" if not value else format(value.normalize(), "f")


def _channel_choices(business):
    config = vertical_config(business)
    choices = [(value, label) for value, label in config["order_types"] if value not in (Order.TYPE_UNASSIGNED, Order.TYPE_MULTI)]
    labels = dict(choices)
    if "distribution" in labels:
        choices.append((MARKET_STOCK, f"{labels['distribution']} — market stock (no customer yet)"))
    return choices


def _order_defaults(order):
    """Pre-fill destination A with the channel the order already has, if any."""
    if order.order_type in (Order.TYPE_UNASSIGNED, Order.TYPE_MULTI):
        return {}
    return {
        "channel": MARKET_STOCK if order.is_market_stock else order.order_type,
        "customer": str(order.customer_id or ""), "customer_name": order.customer_name or "",
        "store_mode": order.production_destination or "store", "purpose": order.non_stock_purpose or "",
        "pay_status": order.customer_payment_status or "paid", "pay_method": order.customer_payment_method or "Transfer",
        "pay_account": str(order.customer_payment_account_id or ""),
    }


def _allocation_defaults(allocation):
    return {
        "channel": MARKET_STOCK if allocation.is_market else allocation.channel,
        "customer": str(allocation.customer_id or ""), "customer_name": allocation.customer_name or "",
        "store_mode": allocation.production_destination or "store", "purpose": allocation.non_stock_purpose or "",
        "pay_status": allocation.payment_status or "paid", "pay_method": allocation.payment_method or "Transfer",
        "pay_account": str(allocation.payment_account_id or ""),
    }


def _read_destination(post, index, defaults=None):
    prefix = f"d{index}_"
    defaults = defaults or {}
    get = lambda key, default="": (post.get(prefix + key, defaults.get(key, default)) or default).strip()
    return {
        "index": index, "letter": chr(65 + index), "channel": get("channel"), "customer": get("customer"),
        "customer_name": get("customer_name"), "store_mode": get("store_mode", "store"), "purpose": get("purpose"),
        "pay_status": get("pay_status", "paid"), "pay_method": get("pay_method", "Transfer"),
        "pay_account": get("pay_account"), "removed": get("removed", "0") == "1",
    }


def _order_form_data(order, dest):
    market = dest["channel"] == MARKET_STOCK
    return {
        "date": order.date.isoformat(), "order_type": "distribution" if market else dest["channel"],
        "is_market_stock": "1" if market else "", "production_destination": dest["store_mode"],
        "non_stock_purpose": dest["purpose"], "production_basis": Order.BASIS_PRODUCT_QUANTITY,
        "base_material_quantity": "0", "customer": dest["customer"], "customer_name": dest["customer_name"],
        "customer_payment_status": dest["pay_status"], "customer_payment_method": dest["pay_method"],
        "customer_payment_account": dest["pay_account"], "notes": order.notes,
        # Present-but-blank so OrderForm's own rules decide them, rather than model defaults leaking in.
        "customer_region": "", "customer_group": "", "transaction_type": "", "unpaid_description": "",
        "payment_method": "", "account": "",
    }


def _page(request, order, items, channels, post=None):
    """Context for the assignment page; ``post`` re-fills it after a failed save."""
    saved = list(order.allocations.select_related("customer").prefetch_related("lines")) if order.is_multi_channel else []
    if post is not None:
        destinations = [_read_destination(post, i) for i in range(MAX_DESTINATIONS)]
    elif saved:
        destinations = [
            _read_destination({}, i, _allocation_defaults(saved[i]) if i < len(saved) else None)
            for i in range(MAX_DESTINATIONS)
        ]
    else:
        destinations = [_read_destination({}, i, _order_defaults(order) if i == 0 else None) for i in range(MAX_DESTINATIONS)]
    saved_units = {}
    for index, allocation in enumerate(saved[:MAX_DESTINATIONS]):
        for line in allocation.lines.all():
            saved_units[(index, line.order_item_id)] = line
    rows = []
    for item in items:
        size = per_batch(item.finished_good)
        total = item_units(item)
        total_batches, total_pieces = to_batches_and_pieces(total, size)
        cells = []
        for d in destinations:
            batch_name, piece_name = f"d{d['index']}_b_{item.pk}", f"d{d['index']}_p_{item.pk}"
            if post is not None:
                batch_value, piece_value = post.get(batch_name, ""), post.get(piece_name, "")
            else:
                line = saved_units.get((d["index"], item.pk))
                batch_value, piece_value = (_plain(line.batch_qty), _plain(line.piece_qty)) if line else ("", "")
            cells.append({"dest": d["index"], "batch_name": batch_name, "piece_name": piece_name,
                          "batch": batch_value, "piece": piece_value})
        rows.append({"item": item, "per_batch": size, "has_pieces": size > 1, "total": total,
                     "total_batches": total_batches, "total_pieces": total_pieces, "cells": cells})

    def has_data(d):
        return bool(d["channel"]) or any(
            post.get(c["batch_name"]) or post.get(c["piece_name"])
            for r in rows for c in r["cells"] if c["dest"] == d["index"]
        )

    if post is None:
        shown = [d["index"] < max(MIN_VISIBLE_DESTINATIONS, len(saved)) for d in destinations]
    else:
        # A destination the manager removed stays gone; at least one always remains.
        shown = [not d["removed"] and (d["index"] < MIN_VISIBLE_DESTINATIONS or has_data(d)) for d in destinations]
        if not any(shown):
            shown[0] = True
    for d, flag in zip(destinations, shown):
        d["visible"] = flag
    for r in rows:
        for c in r["cells"]:
            c["visible"] = shown[c["dest"]]
    return {
        "order": order, "items": items, "rows": rows, "destinations": destinations, "channels": channels,
        "customers": Customer.objects.filter(active=True).order_by("name"),
        "accounts": CashAccount.objects.filter(active=True).order_by("name"),
        "stock_location": vertical_config(request.business)["stock_location"],
        "market_stock_value": MARKET_STOCK, "visible_destinations": sum(shown), "max_destinations": MAX_DESTINATIONS,
    }


def _refusal(order):
    """Why this order can't be assigned across channels here, or ''."""
    if order.status != "pending":
        return "Only pending orders can be assigned to channels."
    if order.commerce_intakes.exists() or order.split_commerce_intakes.exists() or any(
        i.commercial_quantity is not None or i.commerce_addon_name or i.product_option_value for i in order.items.all()
    ):
        return "This order came from a storefront request and carries customer-facing quantities, so its channel can't be changed here."
    return ""


@login_required
def order_split(request, pk):
    """Assign a pending order's output to channels, product by product.

    Every unit must be assigned. One destination simply gives the order that
    channel; two or more make it a multi-channel order whose allocations are
    posted at completion. The order itself - its items, recipes, materials,
    recorded kitchen amounts and basis - is never altered or divided.
    """
    order = get_object_or_404(Order.objects.prefetch_related("items__finished_good"), pk=pk)
    refusal = _refusal(order)
    if refusal:
        messages.error(request, refusal)
        return redirect("order_detail", pk=pk)
    items = list(order.items.select_related("finished_good"))
    channels = _channel_choices(request.business)
    channel_labels = dict(channels)
    if request.method != "POST":
        return render(request, "production/order_split.html", _page(request, order, items, channels))

    post = request.POST
    errors, units = [], {}
    destinations = [_read_destination(post, i) for i in range(MAX_DESTINATIONS)]
    for dest in destinations:
        if dest["removed"]:
            continue  # taken off the page by the manager: ignore whatever it still holds
        for item in items:
            size = per_batch(item.finished_good)
            try:
                batches = _read_decimal(post.get(f"d{dest['index']}_b_{item.pk}"))
                pieces = _read_decimal(post.get(f"d{dest['index']}_p_{item.pk}"))
            except (InvalidOperation, ValueError):
                errors.append(f"Destination {dest['letter']}: enter valid quantities for {item.finished_good.name}.")
                continue
            if batches < 0 or pieces < 0:
                errors.append(f"Destination {dest['letter']}: quantities for {item.finished_good.name} can't be negative.")
                continue
            units[(dest["index"], item.pk)] = cents(batches * size + pieces)
    used = [d for d in destinations if any(units.get((d["index"], it.pk), ZERO) > 0 for it in items)]
    if not errors and not used:
        errors.append("Enter how many of each product go to which channel.")
    totals = {item.pk: item_units(item) for item in items}
    for item in items:
        assigned = sum((units.get((d["index"], item.pk), ZERO) for d in used), ZERO)
        if assigned > totals[item.pk]:
            errors.append(f"{item.finished_good.name}: you assigned {assigned.normalize():f} pieces but only {totals[item.pk].normalize():f} were ordered.")
        elif used and assigned < totals[item.pk] and not errors:
            errors.append(
                f"{item.finished_good.name}: {(totals[item.pk] - assigned).normalize():f} pieces are still unassigned. "
                "Assign every unit - use “Put the rest here” on the channel that should take them."
            )
    forms = {}
    for dest in used:
        if dest["channel"] not in channel_labels:
            errors.append(f"Destination {dest['letter']}: choose a channel for the quantities you entered.")
            continue
        form = OrderForm(_order_form_data(order, dest), instance=Order(business=order.business, date=order.date), business=request.business)
        if not form.is_valid():
            for _field, field_errors in form.errors.items():
                errors.extend(f"Destination {dest['letter']}: {message}" for message in field_errors)
            continue
        forms[dest["index"]] = form
    if len(forms) == len(used) > 1:
        errors.extend(channel_allocations.duplicate_kind_errors(
            [(d["letter"], channel_allocations.kind_of(forms[d["index"]].instance)) for d in used]
        ))
    if (
        len(used) == 1 and order.production_basis == Order.BASIS_BASE_MATERIAL
        and used[0]["channel"] not in ("physical_store", MARKET_STOCK)
    ):
        errors.append(
            "A base-material order sized for stock can't go whole to a customer channel. "
            "Assign part of it to stock too, or change the order's sizing first."
        )
    if errors:
        for error in errors:
            messages.error(request, error)
        return render(request, "production/order_split.html", _page(request, order, items, channels, post))

    with transaction.atomic():
        if len(used) == 1:
            # One channel takes everything: an ordinary single-channel order.
            channel = forms[used[0]["index"]]
            channel_allocations.clear_allocations(order)
            for name in CHANNEL_FIELDS:
                setattr(order, name, getattr(channel.instance, name))
            order.save()
            for item in items:
                item.price = item.finished_good.selling_price_for(order.order_type, order.customer)
                item.save(update_fields=["price", "updated_at"])
            summary = channel_labels[used[0]["channel"]]
            audit(request.business, request.user, "update", order,
                  f"Order #{order.display_number} assigned to {summary}", {"multi_channel": False})
            messages.success(request, f"Assigned to {summary}. Review and approve it from the Orders page.")
        else:
            made = channel_allocations.replace_allocations(
                order,
                [(forms[d["index"]].instance, {item.pk: units.get((d["index"], item.pk), ZERO) for item in items}) for d in used],
            )
            summary = ", ".join(a.label for a in made)
            audit(request.business, request.user, "update", order,
                  f"Order #{order.display_number} assigned across channels: {summary}",
                  {"multi_channel": True, "allocations": [a.label for a in made]})
            messages.success(
                request,
                f"Assigned across {len(made)} channels: {summary}. It is still one order: approve it once, and each "
                "channel receives its share when production is completed.",
            )
    return redirect("order_detail", pk=order.pk)