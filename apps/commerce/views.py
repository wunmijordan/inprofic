import hashlib
import hmac
import json
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from accounts.services import can_use_commerce_storefront, is_business_admin
from core.models import Business
from core.verticals import vertical_config
from inventory.models import FinishedGood, ProductCategory
from .forms import CommerceIntegrationForm, CommerceSettingsForm, StorefrontProductForm
from .models import (
    CommerceCheckoutSession, CommerceIntegration, CommerceIntake, CommercePayment,
    CommercePaymentReceipt, CommerceSettings, StorefrontCustomer, StorefrontProduct, DeliveryArea, DeliveryAssignment, DeliverySettings,
)
from .services import ChannelMinimumError, accept_intake, create_intake, switch_intake_to_preorder
from .checkout_services import (
    CheckoutAvailabilityError,
    available_physical_stock,
    create_checkout,
    cancel_unpaid_checkout,
    expire_checkout_if_needed,
    serialize_checkout,
)
from .payment_gateways import GatewayError
from .payment_services import (
    capture_checkout_gateway_email,
    current_checkout_payment,
    eligible_payment_methods,
    initiate_payment,
    record_verified_payment,
    serialize_payment,
    submit_bank_claim,
)


def _settings_for(business):
    settings, _ = CommerceSettings.raw_objects.get_or_create(business=business, defaults={"created_by": None})
    return settings


def _commerce_enabled(business):
    from accounts.services import business_has_module
    return business_has_module(business, "commerce") and _settings_for(business).enabled


@login_required
def commerce_dashboard(request):
    settings = _settings_for(request.business)

    # A storefront row is required for each product, but doing get_or_create()
    # once per FinishedGood made dashboard query count grow linearly. Discover
    # missing rows in one query and create only those, tolerating a concurrent
    # dashboard request racing on the OneToOne constraint.
    good_ids = list(FinishedGood.objects.values_list("pk", flat=True))
    existing_ids = set(
        StorefrontProduct.objects.filter(finished_good_id__in=good_ids).values_list(
            "finished_good_id", flat=True
        )
    ) if good_ids else set()
    missing_products = [
        StorefrontProduct(
            business=request.business,
            created_by=request.user,
            finished_good_id=good_id,
            allow_stock_order=True,
            allow_preorder=request.business.uses_production,
        )
        for good_id in good_ids
        if good_id not in existing_ids
    ]
    if missing_products:
        StorefrontProduct.objects.bulk_create(missing_products, ignore_conflicts=True)

    products = list(FinishedGood.objects.select_related("business", "storefront_product").order_by("name"))
    intakes = list(CommerceIntake.objects.select_related(
        "business", "accepted_order", "accepted_sale", "split_order"
    ).prefetch_related("items", "payments")[:50])
    checkouts = list(CommerceCheckoutSession.objects.select_related("materialized_intake").prefetch_related("payments")[:50])
    is_admin = is_business_admin(request.user, request.business)
    integrations = list(CommerceIntegration.objects.all().order_by("name")) if is_admin else []
    commerce_counts = {
        "recent_orders": len(intakes),
        "awaiting_payment": sum(1 for row in intakes if row.payment_state == CommerceIntake.PAYMENT_PENDING),
        "production_queue": sum(
            1 for row in intakes
            if (row.accepted_order_id and row.accepted_order and row.accepted_order.status in {"pending", "approved"})
            or (row.split_order_id and row.split_order and row.split_order.status in {"pending", "approved"})
        ),
        "needs_review": (
            sum(1 for row in checkouts if row.status == CommerceCheckoutSession.STATUS_PAID_REVIEW)
            + sum(
                1 for row in intakes
                if row.payment_state == CommerceIntake.PAYMENT_CONFIRMED
                and row.status == CommerceIntake.STATUS_PENDING
            )
        ),
    }
    return render(request, "commerce/dashboard.html", {
        "commerce_settings": settings,
        "products": products,
        "intakes": intakes,
        "checkouts": checkouts,
        "integrations": integrations,
        "commerce_counts": commerce_counts,
        "can_manage_commerce": is_admin,
    })


@login_required
def commerce_settings(request):
    if not is_business_admin(request.user, request.business): return render(request, "403.html", status=403)
    obj = _settings_for(request.business)
    form = CommerceSettingsForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False); saved.business=request.business; saved.created_by = saved.created_by or request.user; saved.save()
        messages.success(request, "Commerce settings saved.")
        return redirect("commerce_dashboard")
    return render(request, "commerce/settings.html", {"form": form})


@login_required
def storefront_product_edit(request, good_id):
    if not is_business_admin(request.user, request.business): return render(request, "403.html", status=403)
    good = get_object_or_404(FinishedGood, pk=good_id)
    obj, _ = StorefrontProduct.objects.get_or_create(finished_good=good, defaults={"business": request.business, "created_by": request.user, "allow_preorder": request.business.uses_production})
    form = StorefrontProductForm(request.POST or None, request.FILES or None, instance=obj, business=request.business)
    if request.method == "POST" and form.is_valid():
        saved=form.save(commit=False); saved.business=request.business; saved.save()
        messages.success(request, f"Storefront settings saved for {good.name}.")
        return redirect("commerce_dashboard")
    return render(request, "commerce/product_form.html", {"form":form,"good":good})


@login_required
def integration_add(request):
    if not is_business_admin(request.user, request.business): return render(request, "403.html", status=403)
    settings = _settings_for(request.business)
    form=CommerceIntegrationForm(request.POST or None)
    if request.method=="POST" and form.is_valid():
        integration_type = form.cleaned_data["integration_type"]
        if integration_type == CommerceIntegration.TYPE_API and not settings.api_enabled:
            form.add_error("integration_type", "Enable Connected website in Commerce Settings first.")
        elif integration_type == CommerceIntegration.TYPE_WEBHOOK and not settings.connector_enabled:
            form.add_error("integration_type", "Enable Connected sales platform in Commerce Settings first.")
        else:
            obj=form.save(commit=False); obj.business=request.business; obj.created_by=request.user; obj.save()
            messages.success(request,"Connection key created. Copy it now and keep it private.")
            return redirect("commerce_dashboard")
    return render(request,"commerce/integration_form.html",{"form":form})


@login_required
def intake_accept(request, public_id):
    intake=get_object_or_404(CommerceIntake, public_id=public_id)
    if request.method=="POST":
        try:
            accept_intake(intake,user=request.user); messages.success(request,f"{intake.public_number} processed using the business commerce policy.")
        except ValidationError as exc: messages.error(request,"; ".join(exc.messages))
    return redirect("commerce_dashboard")


CUSTOMER_SESSION_KEY = "inprofic_storefront_customers"


def _customer_session_map(request):
    value = request.session.get(CUSTOMER_SESSION_KEY) or {}
    return value if isinstance(value, dict) else {}


def _storefront_customer(request, business):
    public_id = _customer_session_map(request).get(str(business.pk))
    if not public_id:
        return None
    customer = StorefrontCustomer.raw_objects.filter(
        business=business, public_id=public_id, active=True
    ).first()
    if customer:
        return customer
    mapping = _customer_session_map(request).copy()
    mapping.pop(str(business.pk), None)
    request.session[CUSTOMER_SESSION_KEY] = mapping
    request.session.modified = True
    return None


def _sign_in_storefront_customer(request, business, customer):
    mapping = _customer_session_map(request).copy()
    mapping[str(business.pk)] = str(customer.public_id)
    request.session[CUSTOMER_SESSION_KEY] = mapping
    request.session.modified = True


def _storefront_customer_enabled(business):
    settings = _settings_for(business)
    return bool(_commerce_enabled(business) and settings.hosted_storefront_enabled)


def _public_products(business):
    return StorefrontProduct.raw_objects.filter(
        business=business, published=True
    ).select_related(
        "finished_good__business", "finished_good__product_category"
    ).prefetch_related("finished_good__channel_prices")


def _public_catalog_data(business):
    from .delivery_services import delivery_available

    products = list(_public_products(business))
    category_ids = {p.finished_good.product_category_id for p in products if p.finished_good.product_category_id}
    categories = list(
        ProductCategory.raw_objects.filter(business=business, active=True, pk__in=category_ids)
        .order_by("sort_order", "name", "id")
    ) if category_ids else []
    delivery_enabled = delivery_available(business)
    delivery_areas = list(
        DeliveryArea.raw_objects.filter(business=business, active=True)
        .select_related("rate_band").order_by("name", "id")
    ) if delivery_enabled else []
    return {
        "products": products,
        "product_categories": categories,
        "delivery_enabled": delivery_enabled,
        "delivery_areas": delivery_areas,
    }


def _public_catalog(request, business, settings, *, order_now_mode=False):
    storefront_copy = vertical_config(business)["storefront"]
    return render(request, "commerce/storefront.html", {
        "store_business": business,
        "commerce_settings": settings,
        **_public_catalog_data(business),
        "order_now_mode": order_now_mode,
        "commerce_channels": vertical_config(business)["commerce_channels"],
        "storefront_copy": storefront_copy,
        "storefront_customer": _storefront_customer(request, business),
        "checkout_key": uuid4().hex,
    })


def _public_storefront_context(business):
    return {
        "store_business": business,
        "commerce_settings": _settings_for(business),
        "commerce_channels": vertical_config(business)["commerce_channels"],
        "storefront_copy": vertical_config(business)["storefront"],
    }


def _validation_message(exc):
    return "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


def _public_checkout(business, checkout_id):
    checkout = get_object_or_404(
        CommerceCheckoutSession.raw_objects.select_related(
            "delivery_quote__area", "delivery_quote__provider_account", "materialized_intake"
        ).prefetch_related(
            "items__storefront_product", "items__finished_good"
        ),
        business=business,
        public_id=checkout_id,
    )
    expire_checkout_if_needed(checkout)
    return checkout


def _checkout_page_context(business, checkout, **extra):
    payment = current_checkout_payment(checkout)
    context = {
        **_public_storefront_context(business),
        "checkout": checkout,
        "checkout_data": serialize_checkout(checkout),
        "payment": payment,
        "payment_data": serialize_payment(payment) if payment else None,
        "payment_key": uuid4().hex,
        "checkout_channel_label": vertical_config(business)["commerce_channels"].get(
            checkout.sales_channel, checkout.get_sales_channel_display()
        ),
        "payment_methods": (
            eligible_payment_methods(business)
            if checkout.status == CommerceCheckoutSession.STATUS_AWAITING_PAYMENT
            else []
        ),
    }
    context.update(extra)
    return context


def storefront(request,business_slug):
    business=get_object_or_404(Business,slug=business_slug)
    settings=_settings_for(business)
    if not _commerce_enabled(business) or not settings.hosted_storefront_enabled:
        return render(request,"404.html",status=404)
    return _public_catalog(request, business, settings)


def order_now(request, business_slug):
    business = get_object_or_404(Business, slug=business_slug)
    settings = _settings_for(business)
    if not _commerce_enabled(business) or not settings.order_now_link_enabled:
        return render(request, "404.html", status=404)
    return _public_catalog(request, business, settings, order_now_mode=True)


@require_http_methods(["POST"])
def storefront_order(request,business_slug):
    business=get_object_or_404(Business,slug=business_slug)
    settings = _settings_for(business)
    if not _commerce_enabled(business) or not (settings.hosted_storefront_enabled or settings.order_now_link_enabled):
        return render(request,"404.html",status=404)
    try:
        storefront_customer = _storefront_customer(request, business)
        customer_phone = (request.POST.get("phone") or "").strip()
        customer_email = (request.POST.get("email") or "").strip()
        if not customer_phone:
            raise ValidationError("Phone number is required.")
        if customer_email:
            validate_email(customer_email)
        product_ids = request.POST.getlist("product_id")
        quantities = request.POST.getlist("quantity")
        if not product_ids or len(product_ids) != len(quantities):
            raise ValidationError("Choose at least one product and enter a quantity for each one.")
        products = {
            str(product.public_id): product
            for product in StorefrontProduct.raw_objects.filter(
                business=business,
                public_id__in=product_ids,
                published=True,
            ).select_related("finished_good__business").prefetch_related("finished_good__channel_prices")
        }
        items = []
        for product_id, quantity in zip(product_ids, quantities):
            product = products.get(product_id)
            if product is None:
                raise ValidationError("One of the selected products is no longer available.")
            items.append({"storefront_product": product, "quantity": quantity})
        delivery_quote_id = request.POST.get("delivery_quote_id") or None
        if request.POST.get("request_delivery") == "on" and not delivery_quote_id:
            raise ValidationError("Get a current delivery quote before continuing to payment.")
        checkout, _ = create_checkout(
            business=business,
            source=CommerceIntake.SOURCE_STOREFRONT,
            order_mode=request.POST.get("order_mode"),
            customer={
                "name": request.POST.get("customer_name"),
                "phone": customer_phone,
                "email": customer_email,
                "address": request.POST.get("address"),
            },
            items=items,
            idempotency_key=request.POST.get("checkout_key") or f"hosted-{uuid4().hex}",
            service_mode="delivery" if delivery_quote_id else request.POST.get("service_mode", ""),
            table_reference=request.POST.get("table_reference", ""),
            delivery_quote_id=delivery_quote_id,
            storefront_customer=storefront_customer,
        )
        return redirect("storefront_checkout", business_slug=business.slug, checkout_id=checkout.public_id)
    except (ValidationError, InvalidOperation, TypeError, ValueError) as exc:
        return render(request,"commerce/storefront.html",{
            "store_business":business,
            "commerce_settings":_settings_for(business),
            **_public_catalog_data(business),
            "commerce_channels":vertical_config(business)["commerce_channels"],
            "storefront_copy":vertical_config(business)["storefront"],
            "storefront_customer": _storefront_customer(request, business),
            "order_now_mode":request.POST.get("catalog_mode") == "order_now",
            "order_error":_validation_message(exc),
            "checkout_key":uuid4().hex,
        },status=400)


@require_http_methods(["GET"])
def storefront_checkout(request, business_slug, checkout_id):
    business = get_object_or_404(Business, slug=business_slug)
    if not _commerce_enabled(business):
        return render(request, "404.html", status=404)
    checkout = _public_checkout(business, checkout_id)
    return render(
        request,
        "commerce/storefront_checkout.html",
        _checkout_page_context(business, checkout),
    )


def _delivery_payload(intake):
    if not intake:
        return None
    assignment = DeliveryAssignment.raw_objects.filter(
        business=intake.business, intake=intake
    ).select_related("driver", "quote").first()
    if not assignment:
        return None
    return {
        "id": str(assignment.public_id),
        "status": assignment.status,
        "status_label": assignment.get_status_display(),
        "provider": assignment.provider,
        "driver": assignment.driver.name if assignment.driver_id else None,
        "eta_at": assignment.eta_at.isoformat() if assignment.eta_at else None,
        "delivered_at": assignment.delivered_at.isoformat() if assignment.delivered_at else None,
        "tracking_path": f"/shop/{intake.business.slug}/deliveries/{assignment.public_id}/",
        "external_tracking_url": assignment.external_tracking_url or None,
        "fee": f"{intake.delivery_fee:.2f}",
    }


@require_http_methods(["GET"])
def storefront_checkout_status(request, business_slug, checkout_id):
    """Small public polling response for a checkout's unguessable tracking link."""
    business = get_object_or_404(Business, slug=business_slug)
    if not _commerce_enabled(business):
        return JsonResponse({"detail": "Checkout unavailable."}, status=404)
    checkout = _public_checkout(business, checkout_id)
    payment = current_checkout_payment(checkout)
    intake = checkout.materialized_intake
    payment_data = serialize_payment(payment) if payment else None
    return JsonResponse({
        "checkout_status": checkout.status,
        "payment_status": payment.status if payment else None,
        "receipt_path": payment_data.get("receipt_path") if payment_data else None,
        "order_id": str(intake.public_id) if intake else None,
        "order_number": intake.public_number if intake else None,
        "delivery": _delivery_payload(intake),
        "updated_at": checkout.updated_at.isoformat(),
    })


@require_http_methods(["POST"])
def storefront_checkout_payment(request, business_slug, checkout_id):
    business = get_object_or_404(Business, slug=business_slug)
    if not _commerce_enabled(business):
        return render(request, "404.html", status=404)
    checkout = _public_checkout(business, checkout_id)
    method = (request.POST.get("method") or "").strip().lower()
    payment_key = (request.POST.get("payment_key") or uuid4().hex).strip()[:64]
    try:
        capture_checkout_gateway_email(checkout, method, request.POST.get("payment_email"))
        return_url = request.build_absolute_uri(
            reverse(
                "storefront_checkout",
                kwargs={"business_slug": business.slug, "checkout_id": checkout.public_id},
            )
        )
        payment = initiate_payment(
            checkout=checkout,
            method=method,
            idempotency_key=f"hosted:{checkout.public_id}:{payment_key}",
            return_url=return_url,
        )
        if payment.authorization_url:
            return redirect(payment.authorization_url)
        return redirect("storefront_checkout", business_slug=business.slug, checkout_id=checkout.public_id)
    except (ValidationError, GatewayError, TypeError, ValueError) as exc:
        checkout.refresh_from_db()
        return render(
            request,
            "commerce/storefront_checkout.html",
            _checkout_page_context(business, checkout, payment_error=_validation_message(exc)),
            status=400,
        )


@require_http_methods(["POST"])
def storefront_checkout_claim(request, business_slug, checkout_id):
    business = get_object_or_404(Business, slug=business_slug)
    if not _commerce_enabled(business):
        return render(request, "404.html", status=404)
    checkout = _public_checkout(business, checkout_id)
    payment = current_checkout_payment(checkout)
    try:
        if payment is None:
            raise ValidationError("Start a bank-transfer payment before submitting its reference.")
        submit_bank_claim(
            payment=payment,
            payer_name=request.POST.get("payer_name") or checkout.customer_name,
            transfer_reference=request.POST.get("transfer_reference"),
        )
        return redirect("storefront_checkout", business_slug=business.slug, checkout_id=checkout.public_id)
    except (ValidationError, TypeError, ValueError) as exc:
        checkout.refresh_from_db()
        return render(
            request,
            "commerce/storefront_checkout.html",
            _checkout_page_context(business, checkout, payment_error=_validation_message(exc)),
            status=400,
        )


@require_http_methods(["GET", "POST"])
def storefront_customer_register(request, business_slug):
    business = get_object_or_404(Business, slug=business_slug)
    if not _storefront_customer_enabled(business):
        return render(request, "404.html", status=404)
    if _storefront_customer(request, business):
        return redirect("storefront_customer_account", business_slug=business.slug)
    error = ""
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        email = (request.POST.get("email") or "").strip().lower()
        phone = (request.POST.get("phone") or "").strip()
        address = (request.POST.get("address") or "").strip()
        password = request.POST.get("password") or ""
        confirm = request.POST.get("password_confirm") or ""
        try:
            if not name:
                raise ValidationError("Name is required.")
            validate_email(email)
            if password != confirm:
                raise ValidationError("Passwords do not match.")
            validate_password(password)
            if StorefrontCustomer.raw_objects.filter(business=business, email=email).exists():
                raise ValidationError("An account with this email already exists for this storefront. Sign in instead.")
            customer = StorefrontCustomer.raw_objects.create(
                business=business, name=name, email=email, phone=phone, default_address=address,
                password_hash=make_password(password),
            )
            _sign_in_storefront_customer(request, business, customer)
            return redirect("storefront_customer_account", business_slug=business.slug)
        except ValidationError as exc:
            error = _validation_message(exc)
    return render(request, "commerce/storefront_customer_auth.html", {
        **_public_storefront_context(business), "mode": "register", "customer_error": error,
    })


@require_http_methods(["GET", "POST"])
def storefront_customer_login(request, business_slug):
    business = get_object_or_404(Business, slug=business_slug)
    if not _storefront_customer_enabled(business):
        return render(request, "404.html", status=404)
    if _storefront_customer(request, business):
        return redirect("storefront_customer_account", business_slug=business.slug)
    error = ""
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        password = request.POST.get("password") or ""
        customer = StorefrontCustomer.raw_objects.filter(business=business, email=email, active=True).first()
        if not customer or not check_password(password, customer.password_hash):
            error = "Email or password is incorrect for this storefront."
        else:
            customer.last_login_at = timezone.now()
            customer.save(update_fields=["last_login_at", "updated_at"])
            _sign_in_storefront_customer(request, business, customer)
            return redirect("storefront_customer_account", business_slug=business.slug)
    return render(request, "commerce/storefront_customer_auth.html", {
        **_public_storefront_context(business), "mode": "login", "customer_error": error,
    })


@require_POST
def storefront_customer_logout(request, business_slug):
    business = get_object_or_404(Business, slug=business_slug)
    mapping = _customer_session_map(request).copy()
    mapping.pop(str(business.pk), None)
    request.session[CUSTOMER_SESSION_KEY] = mapping
    request.session.modified = True
    return redirect("storefront", business_slug=business.slug)


@require_http_methods(["GET", "POST"])
def storefront_customer_account(request, business_slug):
    business = get_object_or_404(Business, slug=business_slug)
    if not _storefront_customer_enabled(business):
        return render(request, "404.html", status=404)
    customer = _storefront_customer(request, business)
    if not customer:
        return redirect("storefront_customer_login", business_slug=business.slug)
    error = ""
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        phone = (request.POST.get("phone") or "").strip()
        address = (request.POST.get("default_address") or "").strip()
        password = request.POST.get("new_password") or ""
        try:
            if not name:
                raise ValidationError("Name is required.")
            customer.name = name
            customer.phone = phone
            customer.default_address = address
            update_fields = ["name", "phone", "default_address", "updated_at"]
            if password:
                validate_password(password)
                customer.password_hash = make_password(password)
                update_fields.append("password_hash")
            customer.save(update_fields=update_fields)
            messages.success(request, "Your storefront profile was updated.")
            return redirect("storefront_customer_account", business_slug=business.slug)
        except ValidationError as exc:
            error = _validation_message(exc)
    checkouts = list(
        CommerceCheckoutSession.raw_objects.filter(business=business, storefront_customer=customer)
        .select_related("materialized_intake")
        .prefetch_related("items__finished_good")
        .order_by("-created_at")[:60]
    )
    delivery_by_intake = {
        row.intake_id: row
        for row in DeliveryAssignment.raw_objects.filter(
            business=business, intake_id__in=[c.materialized_intake_id for c in checkouts if c.materialized_intake_id]
        ).select_related("intake")
    }
    for checkout in checkouts:
        checkout.customer_delivery = delivery_by_intake.get(checkout.materialized_intake_id)
    return render(request, "commerce/storefront_customer_account.html", {
        **_public_storefront_context(business), "storefront_customer": customer,
        "customer_checkouts": checkouts, "customer_error": error,
    })


def _api_business_and_auth(request,business_slug,write=False):
    business=get_object_or_404(Business,slug=business_slug)
    settings=_settings_for(business)
    if not _commerce_enabled(business) or not settings.api_enabled: return business,False
    if not write: return business,True
    key = request.headers.get("X-INPROFIC-Key", "") or request.headers.get(
        "X-" + "Store" + "Track-Key", ""
    )
    return business,CommerceIntegration.raw_objects.filter(business=business,active=True,integration_type=CommerceIntegration.TYPE_API,api_key=key).exists()


def api_products(request,business_slug):
    business,ok=_api_business_and_auth(request,business_slug)
    if not ok:return JsonResponse({"detail":"Commerce API unavailable."},status=404)
    rows=[]
    channel_labels = vertical_config(business)["commerce_channels"]
    for p in StorefrontProduct.raw_objects.filter(business=business,published=True).select_related("finished_good__business", "finished_good__product_category").prefetch_related("finished_good__channel_prices"):
        order_modes=[]
        mode_config = [
            ("physical_store", p.allow_stock_order, p.min_quantity),
            ("online", p.allow_online_order, p.preorder_min_quantity),
            ("distribution", p.allow_distribution_order, p.distribution_min_quantity),
        ]
        for code, enabled, minimum in mode_config:
            if not enabled:
                continue
            fulfilment = (
                "stock"
                if (
                    not business.uses_production
                    or p.finished_good.is_purchased_for_resale
                    or code == "physical_store"
                )
                else "preorder"
            )
            order_modes.append({
                "code": code,
                "label": channel_labels[code],
                "price": str(p.finished_good.selling_price_for(code)),
                "min_quantity": str(minimum),
                "max_quantity": str(p.max_quantity) if p.max_quantity is not None else None,
                "fulfilment_mode": fulfilment,
                "available_now": str(available_physical_stock(p.finished_good)) if fulfilment == "stock" else None,
                "lead_time": p.preorder_lead_time if fulfilment == "preorder" else "",
            })
        submitted_modes=[]
        if p.allow_stock_order:submitted_modes.append("order")
        if business.uses_production and not p.finished_good.is_purchased_for_resale and (p.allow_online_order or p.allow_distribution_order):submitted_modes.append("preorder")
        image_url = p.public_image_url
        if image_url and "://" not in image_url:
            image_url = request.build_absolute_uri(f"/{image_url.lstrip('/')}")
        rows.append({"id":str(p.public_id),"name":p.display_name,"category":({"id": p.finished_good.product_category_id, "name": p.finished_good.product_category.name, "slug": p.finished_good.product_category.slug} if p.finished_good.product_category_id and p.finished_good.product_category.active else None),"description":p.description,"image":image_url,"image_url":image_url,"unit":p.finished_good.unit,"available_now":str(available_physical_stock(p.finished_good)),"order_modes":order_modes,"ordering_modes":submitted_modes,"min_quantity":str(p.min_quantity),"preorder_min_quantity":str(p.preorder_min_quantity),"distribution_min_quantity":str(p.distribution_min_quantity),"max_quantity":str(p.max_quantity) if p.max_quantity is not None else None,"preorder_lead_time":p.preorder_lead_time,"stock_price":str(p.finished_good.selling_price_for("physical_store")),"preorder_price":str(p.finished_good.selling_price_for("online")),"distribution_price":str(p.finished_good.selling_price_for("distribution"))})
    categories = [
        {"id": category.pk, "name": category.name, "slug": category.slug}
        for category in ProductCategory.raw_objects.filter(
            business=business, active=True, products__storefront_product__published=True
        ).distinct().order_by("sort_order", "name", "id")
    ]
    from .delivery_services import public_delivery_config
    delivery = public_delivery_config(business)
    delivery["location_url"] = f"/api/v1/storefronts/{business.slug}/delivery/location"
    delivery["quote_url"] = f"/api/v1/storefronts/{business.slug}/delivery/quote"
    return JsonResponse({"business":business.name,"business_slug":business.slug,"service":business.get_vertical_display(),"categories":categories,"delivery":delivery,"products":rows})


@csrf_exempt
@require_http_methods(["POST"])
def api_checkouts(request, business_slug):
    business, ok = _api_business_and_auth(request, business_slug, write=True)
    if not ok:
        return JsonResponse({"detail": "Invalid or disabled commerce API credential."}, status=403)
    try:
        data = json.loads(request.body or b"{}")
        customer = data.get("customer") or {}
        if not isinstance(customer, dict):
            raise ValidationError("Customer details are not in the expected format.")
        customer_phone = str(customer.get("phone") or "").strip()
        customer_email = str(customer.get("email") or "").strip()
        if not customer_phone:
            raise ValidationError("Customer phone number is required.")
        if customer_email:
            validate_email(customer_email)
        customer = {**customer, "phone": customer_phone, "email": customer_email}
        products = {
            str(p.public_id): p
            for p in StorefrontProduct.raw_objects.filter(
                business=business, published=True
            ).select_related("finished_good__business").prefetch_related("finished_good__channel_prices")
        }
        items = []
        for row in data.get("items") or []:
            if not isinstance(row, dict):
                raise ValidationError("One or more checkout items are not in the expected format.")
            product = products.get(str(row.get("product_id")))
            if not product:
                raise ValidationError("Unknown or unpublished product.")
            items.append({"storefront_product": product, "quantity": row.get("quantity")})
        checkout, created = create_checkout(
            business=business,
            source=CommerceIntake.SOURCE_API,
            order_mode=data.get("order_mode") or data.get("sales_channel"),
            ordering_mode=data.get("ordering_mode"),
            external_order_id=str(data.get("external_order_id") or ""),
            customer=customer,
            service_mode="delivery" if data.get("delivery_quote_id") else str(data.get("service_mode") or ""),
            table_reference=str(data.get("table_reference") or ""),
            items=items,
            idempotency_key=request.headers.get("Idempotency-Key", ""),
            delivery_quote_id=data.get("delivery_quote_id") or None,
        )
        payload = serialize_checkout(checkout)
        payload["created"] = created
        payload["payment_methods_url"] = f"/api/v1/storefronts/{business.slug}/payment-methods"
        payload["payment_url"] = f"/api/v1/storefronts/{business.slug}/checkouts/{checkout.public_id}/payments"
        return JsonResponse(payload, status=201 if created else 200)
    except ChannelMinimumError as exc:
        return JsonResponse({
            "detail": "; ".join(exc.messages),
            "code": "minimum_not_met",
            "suggested_order_modes": exc.alternatives,
        }, status=400)
    except CheckoutAvailabilityError as exc:
        return JsonResponse({
            "detail": "; ".join(exc.messages),
            "code": exc.code,
            "suggested_order_modes": exc.suggested_order_modes,
            "items": exc.details,
        }, status=409)
    except (json.JSONDecodeError, ValidationError, InvalidOperation, TypeError, ValueError) as exc:
        detail = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        return JsonResponse({"detail": detail}, status=400)


@require_http_methods(["GET"])
def api_checkout_detail(request, business_slug, checkout_id):
    business, ok = _api_business_and_auth(request, business_slug, write=True)
    if not ok:
        return JsonResponse({"detail": "Invalid or disabled commerce API credential."}, status=403)
    checkout = get_object_or_404(
        CommerceCheckoutSession.raw_objects.select_related(
            "delivery_quote__area", "delivery_quote__provider_account", "materialized_intake"
        ).prefetch_related("items__storefront_product", "items__finished_good"),
        business=business,
        public_id=checkout_id,
    )
    return JsonResponse(serialize_checkout(checkout))


@csrf_exempt
@require_http_methods(["POST"])
def api_orders(request, business_slug):
    """Retired intake-before-payment endpoint.

    New integrations must create a checkout first so no operational intake is
    materialized until a gateway-confirmed payment succeeds.
    """
    business, ok = _api_business_and_auth(request, business_slug, write=True)
    if not ok:
        return JsonResponse({"detail": "Invalid or disabled commerce API credential."}, status=403)
    return JsonResponse({
        "detail": "This earlier order route no longer creates orders before payment.",
        "code": "checkout_first_required",
        "checkout_endpoint": f"/api/v1/storefronts/{business.slug}/checkouts",
        "payment_methods_endpoint": f"/api/v1/storefronts/{business.slug}/payment-methods",
        "message": "Create a checkout, start a supported online payment, and wait for confirmed payment before the order is created.",
    }, status=410)


def api_order_detail(request,business_slug,public_id):
    business,ok=_api_business_and_auth(request,business_slug,write=True)
    if not ok:return JsonResponse({"detail":"Commerce API unavailable."},status=404)
    intake=get_object_or_404(CommerceIntake.raw_objects.prefetch_related("items__finished_good"),business=business,public_id=public_id)
    from .payment_services import current_payment, serialize_payment
    payment = current_payment(intake)
    compact_payment = None
    if payment:
        normalized = serialize_payment(payment)
        compact_payment = {
            key: normalized[key]
            for key in ("payment_id", "method", "status", "amount", "currency", "reference", "amount_paid", "balance", "verified_at")
        }
    return JsonResponse({"id":str(intake.public_id),"number":intake.public_number,"status":intake.status,"order_mode":intake.sales_channel,"fulfilment_mode":intake.ordering_mode,"ordering_mode":intake.ordering_mode,"payment_state":intake.payment_state,"payment":compact_payment,"fulfilment_state":intake.fulfilment_state,"subtotal":str(intake.total - intake.delivery_fee),"delivery_fee":str(intake.delivery_fee),"delivery":_delivery_payload(intake),"total":str(intake.total),"items":[{"product":row.finished_good.name,"requested":str(row.requested_quantity),"stock_fulfilled":str(row.accepted_stock_quantity),"production":str(row.production_quantity),"price":str(row.unit_price)} for row in intake.items.all()]})


@require_http_methods(["POST"])
def storefront_switch_preorder(request, business_slug, public_id):
    business = get_object_or_404(Business, slug=business_slug)
    if not _commerce_enabled(business):
        return render(request, "404.html", status=404)
    intake = get_object_or_404(CommerceIntake.raw_objects, business=business, public_id=public_id)
    try:
        intake = switch_intake_to_preorder(intake, user=None)
        return render(
            request,
            "commerce/storefront_success.html",
            {**_public_storefront_context(business), "intake": intake},
        )
    except ValidationError as exc:
        return JsonResponse({"detail": "; ".join(exc.messages)}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def api_order_switch_preorder(request, business_slug, public_id):
    business, ok = _api_business_and_auth(request, business_slug, write=True)
    if not ok:
        return JsonResponse({"detail": "Invalid or disabled commerce API credential."}, status=403)
    intake = get_object_or_404(CommerceIntake.raw_objects, business=business, public_id=public_id)
    try:
        switch_intake_to_preorder(intake, user=None)
        return JsonResponse({"id": str(intake.public_id), "status": "accepted", "order_mode": "online", "fulfilment_mode": "preorder", "ordering_mode": "preorder"})
    except ValidationError as exc:
        return JsonResponse({"detail": "; ".join(exc.messages)}, status=400)


def storefront_order_status(request, business_slug, public_id):
    business = get_object_or_404(Business, slug=business_slug)
    if not _commerce_enabled(business):
        return render(request, "404.html", status=404)
    intake = get_object_or_404(CommerceIntake.raw_objects.prefetch_related("items__finished_good"), business=business, public_id=public_id)
    return render(
        request,
        "commerce/storefront_status.html",
        {**_public_storefront_context(business), "intake": intake, "delivery": DeliveryAssignment.raw_objects.filter(business=business, intake=intake).select_related("driver", "quote").first()},
    )


@csrf_exempt
@require_http_methods(["POST"])
def connector_orders(request, business_slug, integration_id):
    """Receive a signed third-party basket and create a checkout, never an intake.

    Connector clients must settle the returned checkout through the normal
    verified payment flow. This preserves the same payment-before-intake guard
    used by the hosted and headless storefronts.
    """
    business = get_object_or_404(Business, slug=business_slug)
    settings = _settings_for(business)
    if not _commerce_enabled(business) or not settings.connector_enabled:
        return JsonResponse({"detail": "Commerce connector unavailable."}, status=404)
    integration = get_object_or_404(
        CommerceIntegration.raw_objects,
        pk=integration_id, business=business, active=True, integration_type=CommerceIntegration.TYPE_WEBHOOK,
    )
    signature = request.headers.get("X-INPROFIC-Signature", "") or request.headers.get(
        "X-" + "Store" + "Track-Signature", ""
    )
    expected = hmac.new(integration.webhook_secret.encode("utf-8"), request.body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        return JsonResponse({"detail": "Invalid connector signature."}, status=403)
    try:
        data = json.loads(request.body or b"{}")
        products = {
            str(p.public_id): p
            for p in StorefrontProduct.raw_objects.filter(
                business=business, published=True
            ).select_related("finished_good").prefetch_related("finished_good__channel_prices")
        }
        items = []
        for row in data.get("items") or []:
            product = products.get(str(row.get("product_id")))
            if not product:
                raise ValidationError("Unknown or unpublished product.")
            items.append({"storefront_product": product, "quantity": row.get("quantity")})
        idem = str(data.get("idempotency_key") or data.get("external_order_id") or "").strip()
        if not idem:
            raise ValidationError("idempotency_key or external_order_id is required.")
        checkout, created = create_checkout(
            business=business,
            source=CommerceIntake.SOURCE_CONNECTOR,
            order_mode=data.get("order_mode") or data.get("sales_channel"),
            ordering_mode=data.get("ordering_mode"),
            customer=data.get("customer") or {},
            items=items,
            external_order_id=str(data.get("external_order_id") or ""),
            idempotency_key=idem,
            service_mode="delivery" if data.get("delivery_quote_id") else str(data.get("service_mode") or ""),
            table_reference=str(data.get("table_reference") or ""),
            delivery_quote_id=data.get("delivery_quote_id") or None,
        )
        payload = serialize_checkout(checkout)
        payload.update({
            "created": created,
            "payment_methods": eligible_payment_methods(business),
            "payment_endpoint": f"/api/v1/storefronts/{business.slug}/checkouts/{checkout.public_id}/payments",
        })
        return JsonResponse(payload, status=201 if created else 200)
    except ChannelMinimumError as exc:
        return JsonResponse({"detail": "; ".join(exc.messages), "code": "minimum_not_met", "suggested_order_modes": exc.alternatives}, status=400)
    except (json.JSONDecodeError, ValidationError, InvalidOperation, TypeError, ValueError) as exc:
        detail = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        return JsonResponse({"detail": detail}, status=400)



def _receipt_details(receipt):
    """Return one tenant-safe, JSON-friendly receipt snapshot for web/POS rendering."""
    payment = receipt.payment
    checkout = payment.checkout
    intake = payment.intake
    if checkout is not None:
        items = [
            {
                "name": row.storefront_product.display_name,
                "quantity": f"{row.payable_quantity}",
                "unit_price": f"{row.unit_price:.2f}",
                "line_total": f"{row.line_total:.2f}",
            }
            for row in checkout.items.all()
        ]
        customer_name = checkout.customer_name
        customer_phone = checkout.customer_phone
        customer_email = checkout.customer_email
        delivery_fee = Decimal(checkout.delivery_fee or 0)
    elif intake is not None:
        items = [
            {
                "name": row.finished_good.name,
                "quantity": f"{row.requested_quantity}",
                "unit_price": f"{row.unit_price:.2f}",
                "line_total": f"{row.line_total:.2f}",
            }
            for row in intake.items.all()
        ]
        customer_name = intake.customer_name
        customer_phone = intake.customer_phone
        customer_email = getattr(intake, "customer_email", "")
        delivery_fee = Decimal(intake.delivery_fee or 0)
    else:
        items, customer_name, customer_phone, customer_email = [], "", "", ""
        delivery_fee = Decimal("0.00")
    subtotal = sum((Decimal(row["line_total"]) for row in items), Decimal("0.00"))
    return {
        "receipt_id": str(receipt.public_id),
        "business_name": receipt.business.name,
        "verified_at": receipt.verified_at.isoformat(),
        "reversed_at": receipt.reversed_at.isoformat() if receipt.reversed_at else None,
        "reversal_reason": receipt.reversal_reason or "",
        "customer": {
            "name": customer_name or "Customer",
            "phone": customer_phone or "",
            "email": customer_email or "",
        },
        "payment": {
            "method": payment.method,
            "method_label": payment.get_method_display(),
            "currency": payment.currency,
            "reference": payment.reference,
            "gateway_provider": payment.gateway_provider or "",
            "external_reference": receipt.external_reference or "",
            "amount": f"{receipt.amount:.2f}",
        },
        "items": items,
        "subtotal": f"{subtotal:.2f}",
        "delivery_fee": f"{delivery_fee:.2f}",
        "total": f"{receipt.amount:.2f}",
    }


def _receipt_queryset():
    return CommercePaymentReceipt.raw_objects.select_related(
        "business", "account", "payment__checkout", "payment__intake"
    ).prefetch_related(
        "payment__checkout__items__storefront_product__finished_good",
        "payment__intake__items__finished_good",
    )


@require_http_methods(["GET"])
def storefront_receipt(request, business_slug, receipt_id):
    """Public, unguessable receipt generated only from verified payment rows."""
    business = get_object_or_404(Business, slug=business_slug)
    receipt = get_object_or_404(_receipt_queryset(), business=business, public_id=receipt_id)
    details = _receipt_details(receipt)
    payment = receipt.payment
    checkout = payment.checkout
    intake = payment.intake
    response = render(request, "commerce/storefront_receipt.html", {
        **_public_storefront_context(business),
        "receipt": receipt,
        "payment": payment,
        "receipt_items": details["items"],
        "delivery_fee": Decimal(details["delivery_fee"]),
        "receipt_subtotal": Decimal(details["subtotal"]),
        "customer_name": details["customer"]["name"],
        "customer_phone": details["customer"]["phone"],
        "customer_email": details["customer"]["email"],
        "hide_storefront_header": bool(
            (checkout is not None and checkout.source == CommerceCheckoutSession.SOURCE_STAFF_POS)
            or (intake is not None and intake.source == CommerceIntake.SOURCE_STAFF_POS)
        ),
    })
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _staff_pos_products(business):
    vocabulary = vertical_config(business)
    channel = vocabulary.get("direct_sale_channel") or CommerceIntake.CHANNEL_PHYSICAL_STORE
    filters = {"business": business, "published": True}
    if channel == CommerceIntake.CHANNEL_DISTRIBUTION:
        filters["allow_distribution_order"] = True
    elif channel == CommerceIntake.CHANNEL_ONLINE:
        filters["allow_online_order"] = True
    else:
        filters["allow_stock_order"] = True
    products = list(
        StorefrontProduct.raw_objects.filter(**filters)
        .select_related("finished_good__business", "finished_good__product_category")
        .prefetch_related("finished_good__channel_prices")
        .order_by("public_name", "finished_good__name")
    )
    for product in products:
        product.pos_price = product.finished_good.selling_price_for(channel)
        product.pos_available = available_physical_stock(product.finished_good)
        product.pos_channel = channel
        product.pos_min_quantity = product.distribution_min_quantity if channel == CommerceIntake.CHANNEL_DISTRIBUTION else product.min_quantity
    return products


@login_required
@require_http_methods(["GET", "POST"])
def storefront_pos(request):
    pos_action = "edit" if request.method == "POST" else "view"
    if not can_use_commerce_storefront(request.user, request.business, pos_action):
        return render(request, "403.html", status=403)
    if not _commerce_enabled(request.business):
        messages.error(request, "Commerce is disabled for this business. A Business Admin can enable it from Commerce settings.")
        return redirect("commerce_dashboard")
    pos_ui = vertical_config(request.business)["pos"]
    pos_channel = vertical_config(request.business).get("direct_sale_channel") or CommerceIntake.CHANNEL_PHYSICAL_STORE
    products = list(_staff_pos_products(request.business))
    methods = eligible_payment_methods(request.business, surface="pos")
    from .delivery_services import delivery_available
    delivery_enabled = delivery_available(request.business)
    delivery_areas = list(
        DeliveryArea.raw_objects.filter(business=request.business, active=True)
        .select_related("rate_band").order_by("name", "id")
    ) if delivery_enabled else []
    error = ""
    active_checkout = None
    active_payment = None
    checkout_id = request.GET.get("checkout")
    if checkout_id:
        active_checkout = CommerceCheckoutSession.raw_objects.filter(
            business=request.business, public_id=checkout_id, source=CommerceIntake.SOURCE_STAFF_POS
        ).prefetch_related("items__storefront_product").first()
        if active_checkout:
            active_payment = current_checkout_payment(active_checkout)
    if request.method == "POST":
        checkout = None
        try:
            method = (request.POST.get("method") or "").strip().lower()
            if method not in {row["code"] for row in methods}:
                raise ValidationError("Choose an enabled in-premise payment method.")
            if method == CommercePayment.METHOD_CASH and request.POST.get("cash_received") != "on":
                raise ValidationError("Confirm that the cash has physically been received before completing this sale.")
            by_id = {str(product.public_id): product for product in products}
            items = []
            for product_id, product in by_id.items():
                raw_qty = (request.POST.get(f"qty_{product_id}") or "").strip()
                if not raw_qty:
                    continue
                try:
                    quantity = Decimal(raw_qty)
                except (InvalidOperation, TypeError, ValueError):
                    raise ValidationError(f"Enter a valid quantity for {product.display_name}.")
                if quantity > 0:
                    items.append({"storefront_product": product, "quantity": quantity})
            if not items:
                raise ValidationError("Add at least one product to the sale.")
            customer_default = pos_ui.get("customer_default") or "Walk-in Customer"
            customer_name = (request.POST.get("customer_name") or customer_default).strip() or customer_default
            customer_email = (request.POST.get("customer_email") or "").strip()
            if customer_email:
                validate_email(customer_email)
            delivery_requested = request.POST.get("request_delivery") == "on"
            delivery_quote_id = (request.POST.get("delivery_quote_id") or "").strip() or None
            if delivery_requested and not delivery_quote_id:
                raise ValidationError("Get a current delivery quote before completing this sale.")
            checkout, _ = create_checkout(
                business=request.business,
                source=CommerceIntake.SOURCE_STAFF_POS,
                order_mode=pos_channel,
                service_mode="delivery" if delivery_quote_id else ((request.POST.get("service_mode") or "") if pos_ui.get("show_service_mode") else ""),
                table_reference=(request.POST.get("table_reference") or "") if pos_ui.get("show_reference") else "",
                customer={
                    "name": customer_name,
                    "phone": (request.POST.get("customer_phone") or "").strip(),
                    "email": customer_email,
                    "address": (request.POST.get("customer_address") or "").strip(),
                },
                items=items,
                idempotency_key=(request.POST.get("pos_key") or f"staff-pos-{uuid4().hex}")[:120],
                delivery_quote_id=delivery_quote_id,
            )
            payment = initiate_payment(
                checkout=checkout,
                method=method,
                idempotency_key=f"staff-pos-payment:{checkout.public_id}:{method}",
                surface="pos",
            )
            if method == CommercePayment.METHOD_CASH:
                receipt, _ = record_verified_payment(
                    payment=payment,
                    amount=payment.balance,
                    actor=request.user,
                    idempotency_key=f"staff-pos-cash:{checkout.public_id}",
                    location="In-premise storefront",
                    note="Cash received by authorized storefront staff.",
                )
                return redirect(f"{reverse('commerce_storefront_pos')}?receipt={receipt.public_id}")
            messages.info(request, "Payment request sent to the configured POS terminal. Complete the card payment on the terminal; INPROFIC will mark it paid after automatic confirmation.")
            return redirect(f"{reverse('commerce_storefront_pos')}?checkout={checkout.public_id}")
        except (ValidationError, GatewayError, InvalidOperation, TypeError, ValueError) as exc:
            if checkout is not None:
                cancel_unpaid_checkout(checkout, reason=f"Staff POS payment initiation failed: {_validation_message(exc)}")
            error = _validation_message(exc)
    pos_categories = sorted(
        {p.finished_good.product_category for p in products if p.finished_good.product_category_id},
        key=lambda category: (category.sort_order, category.name.lower(), category.pk),
    )
    completed_receipt = None
    receipt_id = (request.GET.get("receipt") or "").strip()
    if receipt_id:
        receipt = _receipt_queryset().filter(
            business=request.business, public_id=receipt_id
        ).first()
        if receipt:
            source = receipt.payment.checkout.source if receipt.payment.checkout_id else (receipt.payment.intake.source if receipt.payment.intake_id else "")
            if source == CommerceIntake.SOURCE_STAFF_POS:
                completed_receipt = _receipt_details(receipt)
    return render(request, "commerce/storefront_pos.html", {
        "products": products,
        "product_categories": pos_categories,
        "payment_methods": methods,
        "pos_key": uuid4().hex,
        "pos_error": error,
        "active_checkout": active_checkout,
        "active_payment": active_payment,
        "active_payment_data": serialize_payment(active_payment) if active_payment else None,
        "pos_ui": pos_ui,
        "pos_channel": pos_channel,
        "pos_channel_label": vertical_config(request.business)["commerce_channels"].get(pos_channel, pos_channel.replace("_", " ").title()),
        "delivery_enabled": delivery_enabled,
        "delivery_areas": delivery_areas,
        "completed_receipt": completed_receipt,
    })


@login_required
@require_http_methods(["GET"])
def storefront_pos_status(request, checkout_id):
    if not can_use_commerce_storefront(request.user, request.business) or not _commerce_enabled(request.business):
        return JsonResponse({"detail": "Forbidden."}, status=403)
    checkout = get_object_or_404(
        CommerceCheckoutSession.raw_objects,
        business=request.business, public_id=checkout_id, source=CommerceIntake.SOURCE_STAFF_POS,
    )
    payment = current_checkout_payment(checkout)
    receipt_data = None
    if payment is not None:
        receipt = _receipt_queryset().filter(
            business=request.business, payment=payment, reversed_at__isnull=True
        ).order_by("-verified_at", "-id").first()
        if receipt:
            receipt_data = _receipt_details(receipt)
    return JsonResponse({
        "checkout": serialize_checkout(checkout),
        "payment": serialize_payment(payment) if payment else None,
        "receipt": receipt_data,
    })
