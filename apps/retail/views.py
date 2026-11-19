import hashlib
import secrets
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods
from accounts.services import (
    is_business_admin,
    user_has_permission,
    can_use_commerce_storefront,
)
from core.models import Business
from core.services import audit
from inventory.models import FinishedGood, MarketStockLot
from .models import (
    Branch,
    StockTransfer,
    UserProfile,
    RegisteredDevice,
    RetailSettings,
    BranchAccess,
    CashierSession,
    ProductPromotion,
    CustomerQuotation,
    OfflineEntry,
    ParkedBasket,
)
from .forms import (
    BranchForm,
    TransferForm,
    ProfileForm,
    PolicyForm,
    PromotionForm,
    QuotationForm,
    QuotationLines,
)
from .services import (
    main_branch,
    permitted_branches,
    require_branch,
    create_branch,
    branch_stocks,
    dispatch_transfer,
    receive_transfer,
    pos_context,
    device_for_request,
    close_session,
    lock_business,
)


def admin(request):
    if not is_business_admin(request.user, request.business):
        raise PermissionDenied


def permission(request, module, action="view"):
    if not user_has_permission(request.user, request.business, module, action):
        raise PermissionDenied


def form_page(request, title, form, formset=None):
    return render(
        request, "retail/form.html", {"title": title, "form": form, "formset": formset}
    )


@login_required
def workspace(request):
    if not (
        is_business_admin(request.user, request.business)
        or user_has_permission(request.user, request.business, "inventory", "view")
        or can_use_commerce_storefront(request.user, request.business)
    ):
        raise PermissionDenied
    main_branch(request.business)
    branches = list(
        permitted_branches(request.user, request.business).select_related(
            "manager", "workspace"
        )
    )
    selected = next(
        (
            branch
            for branch in branches
            if branch.pk == request.session.get(f"branch:{request.business.pk}")
        ),
        branches[0] if branches else None,
    )
    products = list(
        FinishedGood.raw_objects.filter(business=request.business, stock__isnull=False)
        .select_related("business")
        .order_by("name")[:500]
    )
    balances = branch_stocks(products, selected) if selected else {}
    stock_rows = [
        {
            "id": product.pk,
            "barcode": product.barcode,
            "name": product.name,
            "unit": product.unit,
            "quantity": balances.get(product.pk, 0),
        }
        for product in products
    ]
    return render(
        request,
        "retail/workspace.html",
        {
            "selected_branch": selected,
            "stock_rows": stock_rows,
            "branches": branches,
            "transfers": StockTransfer.raw_objects.filter(
                Q(business=request.business, source__in=branches)
                | Q(business=request.business, destination__in=branches)
                | Q(
                    destination__workspace=request.business,
                    business_id=request.business.headquarters_id,
                )
            )
            .select_related("source", "destination", "product")
            .order_by("-pk")[:100],
            "sessions": CashierSession.raw_objects.filter(
                business=request.business, branch=selected
            )
            .filter(
                Q()
                if is_business_admin(request.user, request.business)
                else Q(user=request.user)
            )
            .select_related("branch", "user", "device", "reviewed_by")
            .order_by("-pk")[:20],
            "open_cashier_session": CashierSession.raw_objects.filter(
                business=request.business, user=request.user, closed_at__isnull=True
            )
            .select_related("branch")
            .first(),
        },
    )


@login_required
def branch_add(request):
    admin(request)
    form = BranchForm(request.POST or None, business=request.business)
    if request.method == "POST" and form.is_valid():
        try:
            create_branch(request.business, request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Branch created.")
            return redirect("retail_workspace")
    return form_page(request, "Add branch", form)


@login_required
@require_POST
def branch_switch(request):
    branch = get_object_or_404(
        Branch.raw_objects, business=request.business, pk=request.POST.get("branch")
    )
    require_branch(request.user, request.business, branch)
    if branch.workspace_id:
        from accounts.services import accessible_businesses

        if (
            not accessible_businesses(request.user)
            .filter(pk=branch.workspace_id)
            .exists()
        ):
            raise PermissionDenied
        request.session["active_business_id"] = branch.workspace_id
        return redirect("dashboard")
    request.session[f"branch:{request.business.pk}"] = branch.pk
    return redirect("retail_workspace")


@login_required
def branch_settings(request, pk):
    from django import forms
    from accounts.models import CustomUser, UserBusiness, Role
    from .catalogue import synchronize_catalogue

    admin(request)
    branch = get_object_or_404(
        Branch.raw_objects, pk=pk, business=request.business, workspace__isnull=False
    )

    class Settings(forms.Form):
        manager = forms.ModelChoiceField(
            queryset=CustomUser.objects.filter(
                is_active=True,
                business_memberships__business=request.business,
                business_memberships__active=True,
            ).distinct()
        )
        catalogue_mode = forms.ChoiceField(
            choices=Branch._meta.get_field("catalogue_mode").choices,
            help_text="Switching to independent keeps the existing catalogue. Shared mode follows headquarters; existing independent duplicates must first be resolved.",
        )

    form = Settings(
        request.POST or None,
        initial={"manager": branch.manager_id, "catalogue_mode": branch.catalogue_mode},
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                branch = Branch.raw_objects.select_for_update().get(
                    pk=branch.pk, business=request.business
                )
                previous = branch.manager_id
                branch.manager = form.cleaned_data["manager"]
                branch.catalogue_mode = form.cleaned_data["catalogue_mode"]
                branch.catalogue_signature = ""
                branch.save(
                    update_fields=[
                        "manager",
                        "catalogue_mode",
                        "catalogue_signature",
                        "updated_at",
                    ]
                )
                if previous and previous != branch.manager_id:
                    UserBusiness.objects.filter(
                        user_id=previous, business=branch.workspace
                    ).update(active=False)
                    BranchAccess.objects.filter(
                        user_id=previous, branch=branch
                    ).delete()
                role = Role.objects.get(
                    business=branch.workspace, key=CustomUser.ROLE_BUSINESS_ADMIN
                )
                UserBusiness.objects.update_or_create(
                    user=branch.manager,
                    business=branch.workspace,
                    defaults={"role": role, "active": True},
                )
                BranchAccess.objects.get_or_create(branch=branch, user=branch.manager)
                synchronize_catalogue(branch)
                audit(
                    request.business,
                    request.user,
                    "branch_settings",
                    branch,
                    "Branch manager and catalogue policy updated",
                    {
                        "manager": branch.manager_id,
                        "catalogue_mode": branch.catalogue_mode,
                    },
                )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Branch settings updated.")
            return redirect("retail_workspace")
    return form_page(request, "Branch settings", form)


@login_required
def branch_workspace_enable(request, pk):
    from django import forms
    from accounts.models import CustomUser
    from .services import provision_branch_workspace

    admin(request)
    branch = get_object_or_404(
        Branch.raw_objects,
        pk=pk,
        business=request.business,
        is_main=False,
        workspace__isnull=True,
    )

    class Enable(forms.Form):
        catalogue_mode = forms.ChoiceField(
            choices=Branch._meta.get_field("catalogue_mode").choices
        )
        manager = forms.ModelChoiceField(
            queryset=CustomUser.objects.filter(
                is_active=True,
                business_memberships__business=request.business,
                business_memberships__active=True,
            ).distinct()
        )

    form = Enable(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            branch.catalogue_mode = form.cleaned_data["catalogue_mode"]
            with transaction.atomic():
                branch.save(update_fields=["catalogue_mode"])
                provision_branch_workspace(
                    branch, request.user, form.cleaned_data["manager"]
                )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Full branch workspace enabled.")
            return redirect("retail_workspace")
    return form_page(request, "Enable branch workspace", form)


@login_required
def transfer_add(request):
    permission(request, "inventory", "edit")
    main_branch(request.business)
    form = TransferForm(
        request.POST or None, business=request.business, user=request.user
    )
    if request.method == "POST" and form.is_valid():
        try:
            dispatch_transfer(
                business=request.business, user=request.user, **form.cleaned_data
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(
                request, "Stock dispatched; destination confirmation is required."
            )
            return redirect("retail_workspace")
    return form_page(request, "Transfer branch stock", form)


@login_required
def transfer_receive(request, pk):
    from django import forms

    permission(request, "inventory", "edit")
    transfer = get_object_or_404(
        StockTransfer.raw_objects.filter(
            Q(business=request.business)
            | Q(
                destination__workspace=request.business,
                business_id=request.business.headquarters_id,
            )
        ),
        pk=pk,
    )
    require_branch(request.user, transfer.business, transfer.destination)

    class Receive(forms.Form):
        quantity = forms.DecimalField(
            max_digits=16, decimal_places=2, min_value=0, initial=transfer.quantity
        )
        note = forms.CharField(
            required=False, max_length=500, label="Shortage explanation / note"
        )

    form = Receive(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            receive_transfer(transfer, request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Branch receipt recorded.")
            return redirect("retail_workspace")
    return form_page(request, f"Receive transfer #{transfer.pk}", form)


@login_required
def profile(request):
    instance, _ = UserProfile.objects.get_or_create(user=request.user)
    form = ProfileForm(
        request.POST or None,
        request.FILES or None,
        instance=instance,
        user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your profile was updated.")
        return redirect("retail_profile")
    return render(
        request,
        "retail/profile.html",
        {
            "form": form,
            "profile": instance,
            "devices": RegisteredDevice.raw_objects.filter(
                business=request.business, user=request.user
            ),
        },
    )


@login_required
@require_POST
def register_device(request):
    if not request.business:
        raise PermissionDenied
    branch = main_branch(request.business)
    token_key = f"retail_device_token:{request.business.pk}:{request.user.pk}"
    token = request.session.get(token_key) or secrets.token_urlsafe(48)
    digest = hashlib.sha256(token.encode()).hexdigest()
    existing = RegisteredDevice.raw_objects.filter(token_hash=digest).first()
    if existing and (
        existing.status == "revoked"
        or existing.business_id != request.business.pk
        or existing.user_id != request.user.pk
    ):
        messages.error(
            request,
            "This registration cannot be changed. Ask your business administrator to review it.",
        )
        return redirect("retail_profile")
    device, _ = RegisteredDevice.raw_objects.get_or_create(
        token_hash=digest,
        defaults={
            "business": request.business,
            "user": request.user,
            "branch": None,
            "name": (request.POST.get("name") or "My device")[:100],
            "created_by": request.user,
        },
    )
    request.session[token_key] = token
    audit(
        request.business,
        request.user,
        "device_register",
        device,
        "Device registration requested",
    )
    return redirect("retail_profile")


@login_required
def devices(request):
    admin(request)
    instance, _ = RetailSettings.raw_objects.get_or_create(business=request.business)
    form = PolicyForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Cashier access settings saved.")
        return redirect("retail_devices")
    return render(
        request,
        "retail/devices.html",
        {
            "form": form,
            "devices": RegisteredDevice.raw_objects.filter(
                business=request.business
            ).select_related("user", "branch"),
            "branches": Branch.raw_objects.filter(
                business=request.business, active=True
            ),
            "staff": request.business.user_memberships.filter(
                active=True
            ).select_related("user"),
        },
    )


@login_required
@require_POST
def device_review(request, pk):
    admin(request)
    device = get_object_or_404(
        RegisteredDevice.raw_objects, business=request.business, pk=pk
    )
    action = request.POST.get("action")
    if action not in ("approved", "revoked"):
        return HttpResponse("Choose an approval action.", status=400)
    branch_id = request.POST.get("branch")
    device.branch = (
        get_object_or_404(
            Branch.raw_objects, business=request.business, pk=branch_id, active=True
        )
        if branch_id
        else None
    )
    device.status = action
    device.reviewed_by = request.user
    device.reviewed_at = timezone.now()
    device.save()
    audit(
        request.business,
        request.user,
        "device_" + action,
        device,
        "Device access " + action,
    )
    return redirect("retail_devices")


@login_required
@require_POST
def branch_staff(request):
    admin(request)
    branch = get_object_or_404(
        Branch.raw_objects,
        business=request.business,
        pk=request.POST.get("branch"),
        active=True,
    )
    membership = get_object_or_404(
        request.business.user_memberships, user_id=request.POST.get("user"), active=True
    )
    if request.POST.get("action") == "remove":
        BranchAccess.objects.filter(branch=branch, user=membership.user).delete()
    else:
        BranchAccess.objects.get_or_create(branch=branch, user=membership.user)
    audit(
        request.business,
        request.user,
        "branch_staff",
        branch,
        "Branch staff access updated",
        {"user": membership.user_id},
    )
    return redirect("retail_devices")


def cashier_page(request, title, form, *, session=None, branch=None):
    from .services import cashier_summary

    return render(
        request,
        "retail/cashier_form.html",
        {
            "title": title,
            "form": form,
            "cashier": session,
            "branch": branch,
            "summary": cashier_summary(session) if session else None,
        },
    )


@login_required
def cashier_detail(request, pk):
    from .services import cashier_summary, cashier_receipts

    session = get_object_or_404(
        CashierSession.raw_objects.select_related(
            "branch", "user", "device", "reviewed_by"
        ),
        business=request.business,
        pk=pk,
    )
    if not is_business_admin(request.user, request.business):
        if session.user_id != request.user.pk or not can_use_commerce_storefront(
            request.user, request.business
        ):
            raise PermissionDenied
    return render(
        request,
        "retail/cashier_detail.html",
        {
            "cashier": session,
            "summary": cashier_summary(session),
            "receipts": cashier_receipts(session)[:100],
            "pending_checkouts": session.checkouts.filter(
                checkout__status__in=["awaiting_payment", "paid", "paid_review"]
            ).select_related("checkout"),
        },
    )


@login_required
def cashier_open(request):
    from django import forms

    if not can_use_commerce_storefront(request.user, request.business, "edit"):
        raise PermissionDenied

    class Open(forms.Form):
        opening_float = forms.DecimalField(
            min_value=0,
            max_digits=16,
            decimal_places=2,
            initial=0,
            help_text="Count the cash already in this drawer before the first sale. This is recorded for reconciliation, not as new revenue.",
        )

    branch, device, _ = pos_context(request, require_session=False)
    form = Open(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            branch, device, _ = pos_context(request, require_session=False)
            with transaction.atomic():
                lock_business(request.business)
                if CashierSession.raw_objects.filter(
                    business=request.business, user=request.user, closed_at__isnull=True
                ).exists():
                    raise ValidationError("You already have an open cashier session.")
                session = CashierSession.raw_objects.create(
                    business=request.business,
                    user=request.user,
                    branch=branch,
                    device=device,
                    opening_float=form.cleaned_data["opening_float"],
                    expected_cash=form.cleaned_data["opening_float"],
                    created_by=request.user,
                )
                audit(
                    request.business,
                    request.user,
                    "cashier_open",
                    session,
                    "Cashier drawer opened",
                    {
                        "branch": branch.pk,
                        "opening_float": str(session.opening_float),
                        "device": device.pk if device else None,
                    },
                )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Cashier session opened.")
            return redirect("retail_cashier_detail", pk=session.pk)
    return cashier_page(request, "Open cashier session", form, branch=branch)


@login_required
def cashier_close(request, pk):
    from django import forms

    session = get_object_or_404(
        CashierSession.raw_objects, business=request.business, pk=pk
    )
    if session.user_id != request.user.pk and not is_business_admin(
        request.user, request.business
    ):
        raise PermissionDenied

    class Close(forms.Form):
        counted = forms.DecimalField(
            min_value=0, max_digits=16, decimal_places=2, label="Cash counted"
        )
        note = forms.CharField(
            max_length=500, required=False, label="Variance explanation"
        )

    form = Close(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            close_session(session, request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Drawer count recorded.")
            return redirect("retail_cashier_detail", pk=session.pk)
    return cashier_page(
        request,
        "Count & close cashier session",
        form,
        session=session,
        branch=session.branch,
    )


@login_required
@require_POST
@transaction.atomic
def cashier_review(request, pk):
    admin(request)
    session = get_object_or_404(
        CashierSession.raw_objects.select_for_update(),
        business=request.business,
        pk=pk,
        closed_at__isnull=False,
    )
    if session.reviewed_at:
        return redirect("retail_cashier_detail", pk=session.pk)
    session.reviewed_by = request.user
    session.reviewed_at = timezone.now()
    session.save(update_fields=["reviewed_by", "reviewed_at"])
    audit(
        request.business,
        request.user,
        "cashier_review",
        session,
        "Cash variance reviewed",
    )
    return redirect("retail_cashier_detail", pk=session.pk)


@login_required
def promotions(request):
    permission(request, "sales")
    from .promotion_portions import portion_choices, selected_portion

    campaigns = list(
        ProductPromotion.raw_objects.filter(business=request.business)
        .prefetch_related(
            "products__portion_profile",
            "products__channel_prices",
            "products__bulk_pack_profiles",
            "products__individual_sale_options",
            "reward_products__portion_profile",
            "reward_products__channel_prices",
            "reward_products__bulk_pack_profiles",
            "reward_products__individual_sale_options",
        )
        .order_by("-pk")
    )
    for campaign in campaigns:
        for reward, relation in ((False, "products"), (True, "reward_products")):
            labels = [
                f"{good.name} · {portion_choices(good).get(selected_portion(campaign, good.pk, reward=reward), {}).get('label', 'Unavailable portion')}"
                for good in getattr(campaign, relation).all()
                if good.business_id == request.business.pk
            ]
            setattr(campaign, "reward_labels" if reward else "product_labels", labels)
    return render(request, "retail/promotions.html", {"promotions": campaigns})


@login_required
@transaction.atomic
def promotion_form(request, pk=None):
    permission(request, "sales", "edit")
    instance = (
        get_object_or_404(
            ProductPromotion.raw_objects, business=request.business, pk=pk
        )
        if pk
        else ProductPromotion(business=request.business, created_by=request.user)
    )
    previous = {
        "mode": instance.mode,
        "channels": instance.channel_codes,
        "product_prices": instance.product_prices,
        "product_portions": instance.product_portions,
        "reward_portions": instance.reward_portions,
        "eligibility_rule": instance.eligibility_rule,
        "pricing_rule": instance.pricing_rule,
    }
    form = PromotionForm(
        request.POST or None, instance=instance, business=request.business
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(
            request.business,
            request.user,
            "product_promotion_save",
            instance,
            "Product campaign saved",
            {
                "before": previous,
                "mode": instance.mode,
                "channels": instance.channel_codes,
                "product_prices": instance.product_prices,
                "product_portions": instance.product_portions,
                "reward_portions": instance.reward_portions,
                "eligibility_rule": instance.eligibility_rule,
                "pricing_rule": instance.pricing_rule,
            },
        )
        messages.success(request, "Promotion saved.")
        return redirect("retail_promotions")
    goods = FinishedGood.raw_objects.filter(business=request.business).prefetch_related(
        "channel_prices",
        "bulk_pack_profiles",
        "individual_sale_options",
        "portion_profile",
    )
    from .promotion_portions import portion_choices

    catalog = {
        str(g.pk): {
            "name": g.name,
            "prices": {
                code: str(g.selling_price_for(code))
                for code in ("physical_store", "online", "distribution")
            },
            "portions": portion_choices(g),
        }
        for g in goods
    }
    return render(
        request, "retail/promotion_form.html", {"form": form, "price_catalog": catalog}
    )


@login_required
def quotations(request):
    permission(request, "sales")
    return render(
        request,
        "retail/quotations.html",
        {
            "quotes": CustomerQuotation.raw_objects.filter(business=request.business)
            .prefetch_related("lines")
            .order_by("-pk")
        },
    )


@login_required
@transaction.atomic
def quotation_add(request):
    permission(request, "sales", "edit")
    instance = CustomerQuotation(business=request.business, created_by=request.user)
    form = QuotationForm(request.POST or None, instance=instance)
    lines = QuotationLines(
        request.POST or None,
        instance=instance,
        prefix="lines",
        form_kwargs={"business": request.business},
    )
    if request.method == "POST" and form.is_valid() and lines.is_valid():
        quote = form.save()
        lines.instance = quote
        saved_lines = lines.save()
        adjustments = []
        for line in saved_lines:
            line.channel_unit_price = line.product.finished_good.selling_price_for(
                quote.channel
            )
            line.save(update_fields=["channel_unit_price"])
            if line.unit_price != line.channel_unit_price:
                adjustments.append(
                    {
                        "product_id": line.product_id,
                        "product": line.product.display_name,
                        "channel": quote.channel,
                        "channel_price": str(line.channel_unit_price),
                        "quoted_price": str(line.unit_price),
                    }
                )
        quote.status = "sent"
        quote.save(update_fields=["status"])
        audit(
            request.business,
            request.user,
            "quotation_create",
            quote,
            "Customer quotation issued; no stock or cash posted",
            metadata={"price_adjustments": adjustments, "channel": quote.channel},
        )
        messages.success(request, "Quotation ready to share.")
        return redirect("retail_quotations")
    return render(
        request,
        "retail/quotation_form.html",
        {
            "form": form,
            "formset": lines,
            "quotation_prices": {
                str(product.pk): {
                    channel: str(product.finished_good.selling_price_for(channel))
                    for channel, _ in CustomerQuotation._meta.get_field(
                        "channel"
                    ).choices
                }
                for product in lines.empty_form.fields["product"]
                .queryset.select_related("finished_good")
                .prefetch_related("finished_good__channel_prices")
            },
        },
    )


def quotation_document_context(quote):
    from commerce.views import _public_storefront_context

    return {
        **_public_storefront_context(quote.business),
        "quote": quote,
        "hide_storefront_header": True,
        "customer_name": quote.customer_name,
        "customer_phone": quote.customer_phone,
        "customer_email": quote.customer_email,
        "customer_note": quote.notes,
        "receipt_items": [
            {
                "name": line.product.display_name,
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "line_total": line.line_total,
            }
            for line in quote.lines.all()
        ],
    }


@login_required
def quotation_detail(request, pk):
    permission(request, "sales")
    quote = get_object_or_404(
        CustomerQuotation.raw_objects.prefetch_related("lines__product"),
        business=request.business,
        pk=pk,
    )
    return render(
        request,
        "retail/quotation_detail.html",
        {
            **quotation_document_context(quote),
            "share_url": request.build_absolute_uri(
                reverse("retail_quotation_public", args=[quote.public_id])
            ),
        },
    )


def quotation_public(request, public_id):
    # Unpredictable bearer link exposes only this quotation, never tenant navigation.
    quote = get_object_or_404(
        CustomerQuotation.raw_objects.prefetch_related("lines__product"),
        public_id=public_id,
        status__in=["sent", "accepted", "converted"],
    )
    error = ""
    if request.method == "POST":
        try:
            with transaction.atomic():
                quote = CustomerQuotation.raw_objects.select_for_update().get(
                    pk=quote.pk
                )
                if quote.valid_until < timezone.localdate():
                    raise ValidationError(
                        "This quotation has expired. Ask for a replacement."
                    )
                if request.POST.get("accept") != "on":
                    raise ValidationError("Confirm acceptance of this quotation.")
                if quote.status == "sent":
                    quote.status = "accepted"
                    quote.accepted_at = timezone.now()
                    quote.save()
        except ValidationError as exc:
            error = " ".join(exc.messages)
    response = render(
        request,
        "retail/quotation_public.html",
        {**quotation_document_context(quote), "error": error},
    )
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


@login_required
@require_POST
@transaction.atomic
def quotation_convert(request, pk):
    permission(request, "sales", "edit")
    quote = get_object_or_404(
        CustomerQuotation.raw_objects.select_for_update(),
        business=request.business,
        pk=pk,
    )
    if quote.checkout_id:
        return redirect("retail_quotation_detail", pk=pk)
    if quote.status != "accepted" or quote.valid_until < timezone.localdate():
        messages.error(request, "An accepted, current quotation is required.")
        return redirect("retail_quotation_detail", pk=pk)
    from commerce.checkout_services import create_checkout
    from commerce.models import CommerceIntake

    try:
        lines = list(quote.lines.select_related("product"))
        checkout, _ = create_checkout(
            business=request.business,
            source=CommerceIntake.SOURCE_STAFF_POS,
            customer={
                "name": quote.customer_name,
                "email": quote.customer_email,
                "phone": quote.customer_phone,
            },
            items=[
                {"storefront_product": line.product, "quantity": line.quantity}
                for line in lines
            ],
            order_mode=quote.channel,
            idempotency_key=f"quotation:{quote.pk}",
            quoted_prices={str(line.product_id): line.unit_price for line in lines},
        )
        quote.checkout = checkout
        quote.status = "converted"
        quote.save()
        audit(
            request.business,
            request.user,
            "quotation_convert",
            quote,
            "Accepted quotation converted to unpaid checkout",
        )
        messages.success(
            request, "Checkout created. Payment verification is still required."
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("retail_quotation_detail", pk=pk)


@login_required
def expiration(request):
    permission(request, "inventory")
    lots = (
        MarketStockLot.raw_objects.filter(
            business=request.business, active=True, quantity_available__gt=0
        )
        .select_related("finished_good")
        .order_by("expiry_date", "pk")
    )
    return render(
        request,
        "retail/expiration.html",
        {
            "lots": lots,
            "today": timezone.localdate(),
            "flags": OfflineEntry.raw_objects.filter(
                business=request.business, kind="expiry_flag"
            ).order_by("-pk")[:100],
        },
    )


@login_required
def expiration_flag(request, pk):
    from django import forms

    permission(request, "inventory", "edit")
    lot = get_object_or_404(
        MarketStockLot.raw_objects, business=request.business, pk=pk
    )

    class Flag(forms.Form):
        note = forms.CharField(
            max_length=500,
            widget=forms.Textarea(attrs={"rows": 3}),
            label="Review note",
        )

    form = Flag(request.POST or None)
    if request.method == "POST" and form.is_valid():
        entry = OfflineEntry.raw_objects.create(
            business=request.business,
            user=request.user,
            branch=main_branch(request.business),
            created_by=request.user,
            kind="expiry_flag",
            payload={"lot": lot.pk, **form.cleaned_data},
            result={"lot": lot.pk, **form.cleaned_data},
        )
        audit(
            request.business,
            request.user,
            "expiry_flag",
            entry,
            "Batch flagged for review; no stock write-off",
        )
        messages.success(request, "Expiry review flag recorded.")
        return redirect("retail_expiration")
    return form_page(request, f"Flag {lot.finished_good.name} for review", form)


def _positive_quantity(value):
    from decimal import Decimal, InvalidOperation

    try:
        quantity = Decimal(str(value))
        return quantity.is_finite() and quantity > 0
    except (InvalidOperation, ValueError, TypeError):
        return False


@login_required
@require_http_methods(["GET", "POST"])
def parked_baskets(request):
    import json

    if not can_use_commerce_storefront(request.user, request.business, "edit"):
        raise PermissionDenied
    branch, _, _ = pos_context(request, require_session=False)
    rows = ParkedBasket.raw_objects.filter(
        business=request.business,
        user=request.user,
        branch=branch,
        resumed_at__isnull=True,
    )
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValidationError("Enter a valid basket.")
            if data.get("resume"):
                row = get_object_or_404(rows, public_id=data["resume"])
                row.resumed_at = timezone.now()
                row.save(update_fields=["resumed_at"])
                return JsonResponse({"contents": row.contents})
            contents = data.get("contents", {})
            if not isinstance(contents, dict) or len(contents) > 500:
                raise ValidationError("The basket is too large.")
            contents = {
                key: str(value)[:500]
                for key, value in contents.items()
                if key.startswith(("qty_", "bulk_pack_"))
                or key
                in {"customer_name", "customer_phone", "customer_email", "order_mode"}
            }
            row = ParkedBasket.raw_objects.create(
                business=request.business,
                user=request.user,
                branch=branch,
                created_by=request.user,
                label=str(data.get("label") or "Parked sale")[:100],
                contents=contents,
            )
            return JsonResponse({"id": str(row.public_id)})
        except (ValueError, TypeError, ValidationError):
            return JsonResponse({"detail": "Could not park this basket."}, status=400)
    return JsonResponse(
        {
            "baskets": [
                {
                    "id": str(row.public_id),
                    "label": row.label,
                    "created_at": row.created_at.isoformat(),
                    "product_count": sum(
                        1
                        for key, value in row.contents.items()
                        if key.startswith("qty_") and _positive_quantity(value)
                    ),
                }
                for row in rows.order_by("-pk")[:100]
            ]
        }
    )


@login_required
def barcode_label(request, pk):
    permission(request, "inventory")
    product = get_object_or_404(
        FinishedGood.raw_objects, business=request.business, pk=pk
    )
    if not product.barcode:
        return HttpResponse("Set a barcode on the product first.", status=400)
    from reportlab.graphics.barcode import createBarcodeDrawing
    from reportlab.graphics import renderSVG

    drawing = createBarcodeDrawing(
        "Code128", value=product.barcode, barHeight=36, humanReadable=True
    )
    return HttpResponse(renderSVG.drawToString(drawing), content_type="image/svg+xml")
