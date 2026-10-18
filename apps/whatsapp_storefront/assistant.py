import json
import re
from decimal import Decimal
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.crypto import salted_hmac

from commerce.models import (
    CommerceCheckoutSession, CommerceIntake, CommercePayment, DeliveryQuote,
    DeliverySettings,
)
from commerce.checkout_services import create_checkout
from commerce.delivery_services import create_delivery_quote_options, delivery_quote_choices
from commerce.payment_services import (
    capture_checkout_gateway_email, eligible_payment_methods, initiate_payment,
)
from commerce.views import _public_catalog_data, _public_products


def _catalogue(connection):
    business = connection.business
    products = _public_catalog_data(business)["products"]
    rows = []
    for product in products:
        good = product.finished_good
        available = product.public_stock_available
        online_price = good.selling_price_for("online") if product.allow_online_order else None
        variants = []
        for variant in product.public_variants:
            variant_price = variant.finished_good.selling_price_for("online")
            variants.append({
                "name": variant.display_name,
                "price": str(variant_price) if variant_price is not None else None,
            })
        rows.append({
            "id": str(product.public_id),
            "name": product.display_name,
            "description": product.description[:240],
            "unit": product.customer_unit,
            "price": str(online_price) if online_price is not None else None,
            "in_stock": str(available),
            "made_to_order": bool(good.is_made_in_house and business.uses_production),
            "variants": variants,
            "has_options": bool(
                product.finished_good.storefront_option_choices or variants
            ),
        })
    return rows[:40]


def _order_link(connection):
    from django.contrib.sites.models import Site
    from django.conf import settings as django_settings
    try:
        origin = django_settings.SITE_URL.rstrip("/")
    except AttributeError:
        origin = ""
    if not origin:
        domain = Site.objects.get_current().domain
        origin = f"https://{domain}"
    return f"{origin}{reverse('storefront', kwargs={'business_slug': connection.business.slug})}"


def _matching_recent_order(conversation):
    phone = "".join(ch for ch in conversation.customer_wa_id if ch.isdigit())
    if not phone:
        return None
    candidates = CommerceIntake.raw_objects.filter(
        business=conversation.business,
        customer_phone__endswith=phone[-10:],
    ).order_by("-created_at")[:100]
    for order in candidates:
        order_phone = "".join(ch for ch in (order.customer_phone or "") if ch.isdigit())
        # Do not use a shared national-number suffix as an identity check: two
        # customers with different country codes can otherwise see each other's
        # recent order state. Formatting is ignored, but the full number must match.
        if order_phone and order_phone == phone:
            return order
    return None


def _deterministic_reply(conversation, text, catalogue):
    lowered = text.casefold()
    state = conversation.state or {}
    command = text.strip()
    upper = command.upper()
    if upper in {"HUMAN", "PERSON", "AGENT", "STAFF", "ATTENDANT"}:
        conversation.status = conversation.STATUS_HANDOFF
        conversation.save(update_fields=["status", "updated_at"])
        return conversation.connection.handoff_message
    if upper in {"CANCEL", "RESET", "START OVER"}:
        if state.get("checkout_id"):
            checkout = CommerceCheckoutSession.raw_objects.filter(
                business=conversation.business, public_id=state["checkout_id"], source=CommerceIntake.SOURCE_WHATSAPP
            ).first()
            if checkout:
                return f"A Commerce checkout already exists and is not cancelled by clearing this chat. Continue or review it here: {_checkout_link(conversation, checkout)}"
        conversation.state = {}
        conversation.save(update_fields=["state", "updated_at"])
        return "Your WhatsApp basket and checkout conversation have been cleared. Reply MENU to browse products."
    if upper in {"BASKET", "CART"}:
        return _basket_summary(conversation, catalogue)
    if upper == "CHECKOUT" and state.get("checkout_id"):
        checkout = CommerceCheckoutSession.raw_objects.filter(
            business=conversation.business, public_id=state["checkout_id"], source=CommerceIntake.SOURCE_WHATSAPP
        ).first()
        if checkout:
            return f"Continue securely here: {_checkout_link(conversation, checkout)}"
    if upper in {"MENU", "CATALOG", "CATALOGUE", "PRODUCTS", "WHAT DO YOU SELL"}:
        if not catalogue:
            return f"The online catalogue is not available right now. Contact the business at {conversation.business.contact_phone or 'the business contact number'} instead."
        menu_rows = [row for row in catalogue[:12] if row["price"]]
        if not menu_rows:
            return f"There are no priced products available in the online catalogue right now. Browse the storefront: {_order_link(conversation.connection)}"
        lines = [
            f"{index}. {row['name']} — {conversation.business.currency_symbol}{row['price']} / {row['unit']}"
            for index, row in enumerate(menu_rows, start=1)
        ]
        return (
            "Choose an item and reply ADD <number> [quantity] (example: ADD 1 2). "
            "Reply CHECKOUT when your basket is ready.\n" + "\n".join(lines)
        )

    if state.get("step") == "name":
        if len(command) < 2 or len(command) > 160:
            return "Please send the name for this order (up to 160 characters)."
        state["customer_name"] = command
        state["step"] = "fulfilment"
        conversation.state = state
        conversation.save(update_fields=["state", "updated_at"])
        return "Would you like delivery or pickup? Reply DELIVERY or PICKUP."
    if state.get("step") == "fulfilment":
        if upper not in {"DELIVERY", "PICKUP"}:
            return "Reply DELIVERY or PICKUP so I can continue."
        if upper == "DELIVERY":
            state["step"] = "address"
            conversation.state = state
            conversation.save(update_fields=["state", "updated_at"])
            return "Send the full delivery address, including house/building number, street, area, city and state."
        state["service_mode"] = "pickup"
        state["step"] = "confirm"
        conversation.state = state
        conversation.save(update_fields=["state", "updated_at"])
        return _basket_summary(conversation, catalogue) + "\nReply CONFIRM to create this checkout, or CANCEL to clear it."
    if state.get("step") == "address":
        if len(command) < 8:
            return "Please send a fuller delivery address, including street, area, city and state."
        try:
            _, options = create_delivery_quote_options(
                business=conversation.business,
                subtotal=_basket_subtotal(conversation, catalogue),
                destination_address=command,
                actor=None,
            )
            delivery_settings = DeliverySettings.raw_objects.get(business=conversation.business)
            selected, presented = delivery_quote_choices(delivery_settings, options)
        except (ValidationError, ValueError) as exc:
            message = "; ".join(getattr(exc, "messages", [str(exc)]))
            return f"I couldn't quote that address: {message[:250]} Reply with a corrected full address or PICKUP."
        if selected is None and len(presented) > 1:
            state["step"] = "quote"
            state["address"] = command
            state["quotes"] = [str(row.public_id) for row in presented]
            conversation.state = state
            conversation.save(update_fields=["state", "updated_at"])
            from accounts.platform_integrations import redact_disabled_integrations
            choices = [
                f"{i}. {redact_disabled_integrations(row.provider_account.name if row.provider_account_id else row.get_provider_display())} · {row.vehicle_label} · "
                f"{conversation.business.currency_symbol}{row.fee:.2f} · {row.eta_min_minutes}–{row.eta_max_minutes} min"
                for i, row in enumerate(presented, 1)
            ]
            return "Choose a delivery option by number:\n" + "\n".join(choices)
        state["delivery_quote_id"] = str(selected.public_id if selected else presented[0].public_id)
        state["address"] = command
        state["service_mode"] = "delivery"
        state["step"] = "confirm"
        conversation.state = state
        conversation.save(update_fields=["state", "updated_at"])
        return _basket_summary(conversation, catalogue, quote=selected or presented[0]) + "\nReply CONFIRM to create this checkout, or CANCEL to clear it."
    if state.get("step") == "quote":
        try:
            index = int(command) - 1
            quote_id = state["quotes"][index]
            quote = DeliveryQuote.raw_objects.get(business=conversation.business, public_id=quote_id)
        except (ValueError, IndexError, KeyError, DeliveryQuote.DoesNotExist):
            return "Reply with one of the delivery option numbers shown above, or PICKUP."
        state.update({"delivery_quote_id": str(quote.public_id), "service_mode": "delivery", "step": "confirm"})
        conversation.state = state
        conversation.save(update_fields=["state", "updated_at"])
        return _basket_summary(conversation, catalogue, quote=quote) + "\nReply CONFIRM to create this checkout, or CANCEL to clear it."
    if state.get("step") == "confirm" and upper in {"CONFIRM", "YES"}:
        try:
            checkout = _create_whatsapp_checkout(conversation, catalogue)
        except (ValidationError, ValueError) as exc:
            return f"I couldn't create the checkout: {'; '.join(getattr(exc, 'messages', [str(exc)]))[:300]} Reply MENU to review the current catalogue or HUMAN for help."
        state.update({"step": "payment", "checkout_id": str(checkout.public_id)})
        methods = eligible_payment_methods(conversation.business, surface="public")
        if not methods:
            state["step"] = "hosted_payment"
            conversation.state = state
            conversation.save(update_fields=["state", "updated_at"])
            return f"Checkout created. This business has no online payment method available in WhatsApp right now. Continue securely here: {_checkout_link(conversation, checkout)}"
        state["payment_methods"] = methods
        conversation.state = state
        conversation.save(update_fields=["state", "updated_at"])
        return "Checkout created. Choose a payment method by number:\n" + "\n".join(
            f"{i}. {row['label']}" for i, row in enumerate(methods, 1)
        ) + "\nReply with its number."
    if state.get("step") == "payment":
        try:
            index = int(command) - 1
            method_data = state["payment_methods"][index]
            method = method_data["code"]
            checkout = CommerceCheckoutSession.raw_objects.get(
                business=conversation.business, public_id=state["checkout_id"], source=CommerceIntake.SOURCE_WHATSAPP
            )
            routes = method_data.get("routes") or []
            if method == CommercePayment.METHOD_TRANSFER and len(routes) > 1:
                state.update({"step": "transfer_route", "selected_method": method})
                state["transfer_routes"] = routes
                conversation.state = state
                conversation.save(update_fields=["state", "updated_at"])
                return "Choose a transfer account by number:\n" + "\n".join(
                    f"{route_index}. {route['name']} · {route['bank_name']} · {route['account_number']}"
                    for route_index, route in enumerate(routes, 1)
                )
            if method in {CommercePayment.METHOD_PAYSTACK, CommercePayment.METHOD_MONNIFY, CommercePayment.METHOD_BANK_TRANSFER} and not checkout.customer_email:
                state.update({"step": "email", "selected_method": method})
                conversation.state = state
                conversation.save(update_fields=["state", "updated_at"])
                return "This payment provider needs an email receipt address. Send the email address, or reply CHECKOUT to continue on the hosted page."
            return _start_whatsapp_payment(
                conversation, checkout, method, state,
                transfer_route_id=routes[0]["id"] if method == CommercePayment.METHOD_TRANSFER and routes else None,
            )
        except (ValueError, IndexError, KeyError, CommerceCheckoutSession.DoesNotExist):
            return "Reply with one of the payment method numbers shown above, or CHECKOUT to use the hosted checkout."
        except (ValidationError, ValueError) as exc:
            return f"Payment could not start: {'; '.join(getattr(exc, 'messages', [str(exc)]))[:250]} Continue on the hosted checkout: {_checkout_link(conversation, checkout)}"
    if state.get("step") == "payment_pending" and upper == "STATUS":
        checkout = CommerceCheckoutSession.raw_objects.filter(
            business=conversation.business, public_id=state.get("checkout_id"), source=CommerceIntake.SOURCE_WHATSAPP
        ).first()
        payment = CommercePayment.raw_objects.filter(
            business=conversation.business, public_id=state.get("payment_id"), checkout=checkout
        ).first() if checkout else None
        if payment and payment.status == CommercePayment.STATUS_PAID:
            return f"Payment confirmed for {checkout.currency} {checkout.amount:.2f}. Your order is being processed. Reference: {payment.reference}."
        if payment:
            return f"Payment status: {payment.get_status_display()}. Once the provider confirms it, reply STATUS again. Continue here: {_checkout_link(conversation, checkout)}"
    if state.get("step") == "email":
        if upper == "CHECKOUT":
            checkout = CommerceCheckoutSession.raw_objects.get(business=conversation.business, public_id=state["checkout_id"])
            return f"Continue securely here: {_checkout_link(conversation, checkout)}"
        method = state.get("selected_method")
        checkout = CommerceCheckoutSession.raw_objects.get(business=conversation.business, public_id=state["checkout_id"])
        try:
            capture_checkout_gateway_email(checkout, method, command)
            return _start_whatsapp_payment(conversation, checkout, method, state)
        except ValidationError as exc:
            return f"{'; '.join(exc.messages)} Or reply CHECKOUT to continue on the hosted page."
    if state.get("step") == "transfer_route":
        try:
            route = state["transfer_routes"][int(command) - 1]
            checkout = CommerceCheckoutSession.raw_objects.get(
                business=conversation.business, public_id=state["checkout_id"], source=CommerceIntake.SOURCE_WHATSAPP
            )
        except (ValueError, IndexError, KeyError, CommerceCheckoutSession.DoesNotExist):
            return "Reply with one of the transfer account numbers shown above, or CHECKOUT to continue on the hosted page."
        try:
            return _start_whatsapp_payment(
                conversation, checkout, state["selected_method"], state, transfer_route_id=route["id"]
            )
        except ValidationError as exc:
            return f"Payment could not start: {'; '.join(exc.messages)[:250]} Continue securely here: {_checkout_link(conversation, checkout)}"

    add_match = re.fullmatch(r"ADD\s+(\d{1,2})(?:\s+(?:X\s*)?(\d+(?:\.\d{1,2})?))?", upper)
    if add_match:
        if state.get("checkout_id"):
            return f"A checkout has already been created. Continue it here; use the storefront to start a separate order: {_checkout_link(conversation, CommerceCheckoutSession.raw_objects.get(business=conversation.business, public_id=state['checkout_id']))}"
        index = int(add_match.group(1)) - 1
        quantity = Decimal(add_match.group(2) or "1")
        menu_rows = [row for row in catalogue[:12] if row["price"]]
        if index < 0 or index >= len(menu_rows):
            return "That item number is not on the current menu. Reply MENU to see the latest catalogue."
        row = menu_rows[index]
        if row.get("has_options"):
            return f"{row['name']} needs product options selected. Choose them on the hosted storefront: {_order_link(conversation.connection)}"
        try:
            if quantity <= 0 or quantity > 999:
                raise ValueError
        except (ValueError, TypeError):
            return "Enter a quantity greater than zero, such as ADD 1 2."
        basket = state.setdefault("basket", {})
        item = basket.setdefault(row["id"], {"quantity": "0"})
        item["quantity"] = str(Decimal(item["quantity"]) + quantity)
        state.pop("step", None)
        conversation.state = state
        conversation.save(update_fields=["state", "updated_at"])
        return f"Added {quantity:g} × {row['name']}. Reply BASKET to review or CHECKOUT to continue."
    if upper == "CHECKOUT":
        if not state.get("basket"):
            return "Your basket is empty. Reply MENU to browse and add an item."
        if not conversation.customer_name:
            state["step"] = "name"
            conversation.state = state
            conversation.save(update_fields=["state", "updated_at"])
            return "What name should I put on the order?"
        state["customer_name"] = conversation.customer_name
        state["step"] = "fulfilment"
        conversation.state = state
        conversation.save(update_fields=["state", "updated_at"])
        return "Would you like delivery or pickup? Reply DELIVERY or PICKUP."
    if upper == "STATUS" and state.get("checkout_id"):
        checkout = CommerceCheckoutSession.raw_objects.filter(
            business=conversation.business, public_id=state["checkout_id"], source=CommerceIntake.SOURCE_WHATSAPP
        ).first()
        if checkout:
            payment = checkout.payments.exclude(status=CommercePayment.STATUS_CANCELLED).order_by("-created_at", "-id").first()
            if payment and payment.status == CommercePayment.STATUS_PAID:
                return f"Payment confirmed for {checkout.currency} {checkout.amount:.2f}. Your order is being processed. Reference: {payment.reference}."
            return f"Checkout {checkout.public_id} is {checkout.get_status_display().lower()}; payment is {payment.get_status_display().lower() if payment else 'not started'}. Reply CHECKOUT to open its secure page."
    if any(word in lowered for word in ("order status", "track order", "where is my order")):
        order = _matching_recent_order(conversation)
        if order:
            return f"Order {order.public_number} is {order.get_status_display().lower()}. For the latest order details, please contact the business team at {conversation.business.contact_phone or 'their business number'}."
        return "I couldn't match an order to this WhatsApp number. Please share the order number or ask for a person to help."
    matches = [row for row in catalogue if row["name"].casefold() in lowered and row["price"]]
    if matches:
        row = matches[0]
        if Decimal(row["in_stock"] or 0) > 0:
            stock_note = "Available now"
        elif row["made_to_order"]:
            stock_note = "Made to order"
        else:
            stock_note = "Not currently available"
        return f"{row['name']} is {conversation.business.currency_symbol}{row['price']} per {row['unit']}. {stock_note}. Reply MENU to add an item to your WhatsApp basket, or browse options here: {_order_link(conversation.connection)}"
    if any(word in lowered for word in ("price", "cost", "stock", "available", "delivery", "shipping", "fee", "payment", "pay", "checkout", "total", "status", "track")):
        return f"I can’t confirm that detail from this message. Please check the live catalogue and checkout here: {_order_link(conversation.connection)} or reply HUMAN for help."
    return "I can help you browse the catalogue, check product details or connect you with the team. What product are you looking for? Reply MENU to browse or HUMAN to speak with someone."


def _ai_reply(conversation, text):
    if not (settings.WHATSAPP_AI_API_KEY and conversation.connection.ai_enabled):
        return None
    recent = list(conversation.messages.exclude(message_type="assistant_pending").order_by("-created_at")[:8])
    messages = [{
        "role": "system",
        "content": (
            "You are a concise WhatsApp sales assistant for this INPROFIC business. "
            "Use only the approved business guidance below. Never invent prices, stock, "
            "delivery fees, payment confirmation, order status, or fulfilment promises. Do not create "
            "orders or claim an order exists. For option-heavy products and steps not supported by the WhatsApp "
            "checkout, direct the customer to the hosted storefront. "
            "For ambiguous or unavailable requests ask one clear question. If asked for a person, respond exactly: HUMAN_HANDOFF. "
            f"Business: {conversation.business.name}. Instructions: {conversation.connection.assistant_instructions[:1000]}\n"
            "If the approved guidance does not answer a question, say you will connect them with the business team."
        ),
    }]
    for message in reversed(recent):
        messages.append({"role": "user" if message.direction == "in" else "assistant", "content": message.body[:1000]})
    if not recent or recent[0].body != text:
        messages.append({"role": "user", "content": text[:1000]})
    payload = json.dumps({"model": settings.WHATSAPP_AI_MODEL, "messages": messages, "temperature": 0.2, "max_tokens": 220}).encode()
    request = Request(
        f"{settings.WHATSAPP_AI_BASE_URL}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {settings.WHATSAPP_AI_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode())
        reply = str(result["choices"][0]["message"]["content"]).strip()[:1200]
    except Exception:
        return None
    if reply == "HUMAN_HANDOFF":
        conversation.status = conversation.STATUS_HANDOFF
        conversation.save(update_fields=["status", "updated_at"])
        return conversation.connection.handoff_message
    return reply or None


def build_reply(conversation, text):
    catalogue = _catalogue(conversation.connection)
    deterministic = _deterministic_reply(conversation, text, catalogue)
    if conversation.status == conversation.STATUS_HANDOFF:
        return deterministic
    # Keep catalog, price, stock, order-status and checkout answers anchored to
    # Commerce records. The optional model is only for open-ended guidance where
    # the deterministic assistant has no specific verified answer.
    if deterministic.startswith("I can help you browse the catalogue"):
        return _ai_reply(conversation, text) or deterministic
    return deterministic


def _basket_subtotal(conversation, catalogue):
    by_id = {row["id"]: row for row in catalogue}
    state = conversation.state or {}
    total = Decimal("0")
    for product_id, item in state.get("basket", {}).items():
        row = by_id.get(product_id)
        if not row or row.get("has_options") or not row.get("price"):
            raise ValidationError("A basket item needs to be reselected from the current catalogue.")
        total += Decimal(row["price"]) * Decimal(item["quantity"])
    return total


def _basket_summary(conversation, catalogue, quote=None):
    state = conversation.state or {}
    by_id = {row["id"]: row for row in catalogue}
    lines = []
    for product_id, item in state.get("basket", {}).items():
        row = by_id.get(product_id)
        if row:
            quantity = Decimal(item["quantity"])
            lines.append(f"{quantity:g} × {row['name']} · {conversation.business.currency_symbol}{Decimal(row['price']) * quantity:.2f}")
    if not lines:
        return "Your basket is empty. Reply MENU to browse."
    subtotal = _basket_subtotal(conversation, catalogue)
    fee = Decimal(quote.fee) if quote else Decimal("0")
    lines.extend([
        f"Subtotal: {conversation.business.currency_symbol}{subtotal:.2f}",
        *( [f"Delivery: {conversation.business.currency_symbol}{fee:.2f}"] if quote else [] ),
        f"Total: {conversation.business.currency_symbol}{subtotal + fee:.2f}",
    ])
    return "Your basket:\n" + "\n".join(lines)


def _create_whatsapp_checkout(conversation, catalogue):
    state = conversation.state or {}
    by_id = {row["id"]: row for row in catalogue}
    basket = state.get("basket", {})
    products = {
        str(product.public_id): product
        for product in _public_products(conversation.business).filter(public_id__in=list(basket.keys()))
    }
    if len(products) != len(basket):
        raise ValidationError("One or more products are no longer available online.")
    items = []
    for product_id, item in basket.items():
        row = by_id.get(product_id)
        if not row or row.get("has_options"):
            raise ValidationError("One or more products need options selected on the hosted storefront.")
        items.append({"storefront_product": products[product_id], "quantity": item["quantity"]})
    quote = None
    if state.get("delivery_quote_id"):
        from commerce.models import DeliveryQuote
        quote = DeliveryQuote.raw_objects.get(
            business=conversation.business, public_id=state["delivery_quote_id"],
            status=DeliveryQuote.STATUS_ACTIVE,
        )
    return create_checkout(
        business=conversation.business,
        source=CommerceIntake.SOURCE_WHATSAPP,
        customer={
            "name": state.get("customer_name") or conversation.customer_name or "",
            "phone": conversation.customer_wa_id,
        },
        items=items,
        idempotency_key=f"wa:{conversation.pk}:{salted_hmac('whatsapp-checkout', str(conversation.state), algorithm='sha256').hexdigest()[:48]}",
        order_mode=CommerceIntake.CHANNEL_ONLINE,
        service_mode="delivery" if quote else "pickup",
        delivery_quote_id=quote.pk if quote else None,
        customer_note="Placed via WhatsApp storefront",
    )[0]


def _checkout_link(conversation, checkout):
    from django.conf import settings as django_settings
    from django.contrib.sites.models import Site
    origin = (getattr(django_settings, "SITE_URL", "") or "").rstrip("/")
    if not origin:
        origin = f"https://{Site.objects.get_current().domain}"
    return f"{origin}{reverse('storefront_checkout', kwargs={'business_slug': conversation.business.slug, 'checkout_id': checkout.public_id})}"


def _start_whatsapp_payment(conversation, checkout, method, state, *, transfer_route_id=None):
    payment = initiate_payment(
        checkout=checkout,
        method=method,
        idempotency_key=f"whatsapp:{checkout.public_id}:{method}",
        return_url=_checkout_link(conversation, checkout),
        surface="public",
        transfer_route_id=transfer_route_id,
    )
    state.update({"step": "payment_pending", "payment_id": str(payment.public_id)})
    conversation.state = state
    conversation.save(update_fields=["state", "updated_at"])
    if payment.authorization_url:
        return f"Your secure payment is ready for {checkout.currency} {checkout.amount:.2f}: {payment.authorization_url}\nPayment status updates here after provider confirmation. Reply STATUS to check."
    route = (payment.gateway_metadata or {}).get("manual_transfer_route") or {}
    bank = " · ".join(part for part in [route.get("bank_name"), route.get("account_name"), route.get("account_number")] if part)
    instructions = payment.instructions or route.get("instructions") or ""
    link = _checkout_link(conversation, checkout)
    return (
        f"Transfer {checkout.currency} {checkout.amount:.2f} using reference {payment.reference}.\n"
        + (f"{bank}\n" if bank else "")
        + (f"{instructions}\n" if instructions else "")
        + f"After transfer, open this secure page to upload proof: {link}"
    )
