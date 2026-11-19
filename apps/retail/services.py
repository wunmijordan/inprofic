from decimal import Decimal, InvalidOperation, ROUND_DOWN
import hashlib
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Sum, Q, F
from django.utils import timezone
from accounts.services import is_business_admin, business_subscription_for
from core.models import Business
from core.services import audit
from inventory.models import FinishedGood, InventoryLocation, StockMovement
from .models import (
    Branch,
    BranchAccess,
    BranchStock,
    StockTransfer,
    CashierSession,
    ProductPromotion,
    RegisteredDevice,
)

ZERO = Decimal("0")


def money(value):
    try:
        result = Decimal(str(value)).quantize(Decimal(".01"))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("Enter a valid amount.")
    if not result.is_finite() or result < 0:
        raise ValidationError("Enter a non-negative amount.")
    return result


def lock_business(business):
    # SQLite ignores SELECT FOR UPDATE. Acquire its writer lock before reads,
    # avoiding a deferred read-to-write upgrade during concurrent requests.
    if connection.vendor == "sqlite":
        Business.objects.filter(pk=business.pk).update(name=F("name"))
    else:
        Business.objects.select_for_update().get(pk=business.pk)


def main_branch(business):
    branch = Branch.raw_objects.filter(business=business, is_main=True).first()
    if branch:
        return branch
    with transaction.atomic():
        lock_business(business)
        return _create_main_branch(business)


def _create_main_branch(business):
    branch = Branch.raw_objects.filter(business=business, is_main=True).first()
    if branch:
        return branch
    location, _ = InventoryLocation.raw_objects.get_or_create(
        business=business, name="Main Store", defaults={"location_type": "store"}
    )
    return Branch.raw_objects.create(
        business=business,
        name="Main Store",
        code="main",
        location=location,
        is_main=True,
    )


@transaction.atomic
def create_branch(
    business,
    user,
    *,
    name,
    code,
    address="",
    location_type="branch",
    manager=None,
    catalogue_mode="independent",
):
    if business.headquarters_id:
        raise ValidationError("Create branches from headquarters.")
    lock_business(business)
    main_branch(business)
    subscription = business_subscription_for(business)
    limit = subscription.plan.branch_limit if subscription else None
    if (
        limit is not None
        and Branch.raw_objects.filter(business=business).count() >= limit
    ):
        raise ValidationError("The Founder-configured branch limit has been reached.")
    location = InventoryLocation.raw_objects.create(
        business=business,
        name=f"{location_type.title()}: {name}",
        location_type=location_type,
        created_by=user,
    )
    branch = Branch.raw_objects.create(
        business=business,
        name=name,
        code=code,
        address=address,
        catalogue_mode=catalogue_mode,
        location=location,
        created_by=user,
    )
    if manager is not None:
        provision_branch_workspace(branch, user, manager)
        branch.refresh_from_db()
    audit(business, user, "branch_create", branch, "Branch created")
    return branch


@transaction.atomic
def provision_branch_workspace(branch, user, manager):
    """Create an isolated location tenant beneath its existing business, not a service."""
    from accounts.models import CustomUser, UserBusiness, Role
    from accounts.services import seed_business_roles

    headquarters = branch.business
    if not is_business_admin(user, headquarters) or headquarters.headquarters_id:
        raise PermissionDenied
    branch = Branch.raw_objects.select_for_update().get(
        pk=branch.pk, business=headquarters
    )
    if branch.is_main or branch.workspace_id:
        raise ValidationError("This location already has a full workspace.")
    if not UserBusiness.objects.filter(
        user=manager, user__is_active=True, business=headquarters, active=True
    ).exists():
        raise ValidationError("Choose an active headquarters staff member.")
    if (
        BranchStock.raw_objects.filter(branch=branch, quantity__gt=0).exists()
        or StockTransfer.raw_objects.filter(
            Q(source=branch) | Q(destination=branch), status="dispatched"
        ).exists()
    ):
        raise ValidationError(
            "Receive outstanding transfers and return allocated stock to headquarters before enabling a full workspace. Historical location records are retained."
        )
    from uuid import uuid4

    workspace = Business.objects.create(
        headquarters=headquarters,
        name=f"{headquarters.name[:65]} · {branch.name[:50]}",
        slug=f"branch-{headquarters.pk}-{uuid4().hex[:16]}",
        vertical=headquarters.vertical,
        currency_symbol=headquarters.currency_symbol,
        accent_color=headquarters.accent_color,
        background_color=headquarters.background_color,
        contact_address=branch.address,
        contact_phone=headquarters.contact_phone,
        contact_email=headquarters.contact_email,
        contact_website=headquarters.contact_website,
        tagline=headquarters.tagline,
        restaurant_table_service=headquarters.restaurant_table_service,
        fiscal_year_start_month=headquarters.fiscal_year_start_month,
        profit_period=headquarters.profit_period,
    )
    seed_business_roles(workspace)
    from commerce.models import CommerceSettings

    parent_commerce = CommerceSettings.raw_objects.filter(business=headquarters).first()
    if parent_commerce:
        CommerceSettings.raw_objects.create(
            business=workspace, enabled=parent_commerce.enabled
        )
    role = Role.objects.get(business=workspace, key=CustomUser.ROLE_BUSINESS_ADMIN)
    UserBusiness.objects.create(user=manager, business=workspace, role=role)
    branch.workspace = workspace
    branch.manager = manager
    branch.save(update_fields=["workspace", "manager", "updated_at"])
    BranchAccess.objects.get_or_create(branch=branch, user=manager)
    main_branch(workspace)
    if branch.catalogue_mode == "shared":
        from .catalogue import synchronize_catalogue

        synchronize_catalogue(branch)
    audit(
        headquarters,
        user,
        "branch_workspace_create",
        branch,
        "Full branch workspace created",
        {"workspace": workspace.pk, "manager": manager.pk},
    )
    audit(
        workspace,
        user,
        "branch_workspace_create",
        workspace,
        "Branch linked to headquarters",
        {"headquarters": headquarters.pk, "manager": manager.pk},
    )
    return workspace


def permitted_branches(user, business):
    branches = Branch.raw_objects.filter(business=business, active=True)
    if is_business_admin(user, business):
        return branches
    if not user.business_memberships.filter(business=business, active=True).exists():
        return branches.none()
    assigned = BranchAccess.objects.filter(
        user=user, branch__business=business
    ).exists()
    return (
        branches.filter(staff_access__user=user).distinct()
        if assigned
        else branches.filter(is_main=True)
    )


def require_branch(user, business, branch):
    if (
        branch
        and branch.business_id == business.pk
        and branch.workspace_id
        and branch.active
    ):
        from accounts.services import user_has_permission

        if user_has_permission(user, branch.workspace, "inventory", "edit"):
            return
    if (
        not branch
        or branch.business_id != business.pk
        or not permitted_branches(user, business).filter(pk=branch.pk).exists()
    ):
        raise PermissionDenied("This branch is not available to your account.")


def allocated_stock(good):
    allocated = (
        BranchStock.raw_objects.filter(
            business_id=good.business_id, product=good, branch__is_main=False
        ).aggregate(n=Sum("quantity"))["n"]
        or ZERO
    )
    transit = (
        StockTransfer.raw_objects.filter(
            business_id=good.business_id, product=good, status="dispatched"
        ).aggregate(n=Sum("quantity"))["n"]
        or ZERO
    )
    return allocated + transit


def branch_stock(good, branch=None):
    if branch is None or branch.is_main:
        return max(
            ZERO, Decimal(good.physical_saleable_stock or 0) - allocated_stock(good)
        )
    if branch.business_id != good.business_id:
        raise ValidationError("Product and branch must belong to the same business.")
    return (
        BranchStock.raw_objects.filter(
            business_id=good.business_id, branch=branch, product=good
        )
        .values_list("quantity", flat=True)
        .first()
        or ZERO
    )


def branch_stocks(goods, branch=None):
    goods = list(goods)
    if not goods:
        return {}
    business_id = goods[0].business_id
    ids = [good.pk for good in goods]
    rows = BranchStock.raw_objects.filter(business_id=business_id, product_id__in=ids)
    if branch and not branch.is_main:
        quantities = dict(
            rows.filter(branch=branch).values_list("product_id", "quantity")
        )
        return {good.pk: quantities.get(good.pk, ZERO) for good in goods}
    allocations = dict(
        rows.filter(branch__is_main=False)
        .values_list("product_id")
        .annotate(n=Sum("quantity"))
    )
    transit = dict(
        StockTransfer.raw_objects.filter(
            business_id=business_id, product_id__in=ids, status="dispatched"
        )
        .values_list("product_id")
        .annotate(n=Sum("quantity"))
    )
    return {
        good.pk: max(
            ZERO,
            Decimal(good.physical_saleable_stock or 0)
            - allocations.get(good.pk, ZERO)
            - transit.get(good.pk, ZERO),
        )
        for good in goods
    }


@transaction.atomic
def adjust_branch_stock(good, location, quantity):
    branch = Branch.raw_objects.filter(
        business_id=good.business_id, location=location
    ).first()
    if branch and not branch.is_main:
        row, _ = BranchStock.raw_objects.get_or_create(
            business_id=good.business_id, branch=branch, product=good
        )
        row = BranchStock.raw_objects.select_for_update().get(pk=row.pk)
        if row.quantity + quantity < 0:
            raise ValidationError(f"Insufficient stock at {branch.name}.")
        row.quantity += quantity
        row.save(update_fields=["quantity", "updated_at"])
    elif (
        quantity < 0
        and allocated_stock(good) > 0
        and Decimal(good.stock or 0) + quantity < allocated_stock(good)
    ):
        raise ValidationError(
            "This stock is allocated to another branch or is in transit."
        )


@transaction.atomic
def dispatch_transfer(
    *,
    business,
    user,
    source,
    destination,
    product,
    quantity,
    note="",
    destination_product=None,
):
    from core.ledger import validate_entry_date

    validate_entry_date(business, timezone.localdate())
    for branch in (source, destination):
        if branch.business_id != business.pk or not branch.active:
            raise ValidationError("Choose active branches from this business.")
    require_branch(user, business, source)
    if source.workspace_id:
        raise ValidationError(
            "Dispatch from the stock-owning workspace. This transfer form dispatches headquarters or allocated-location stock."
        )
    if destination.workspace_id:
        if not is_business_admin(user, business):
            raise PermissionDenied(
                "Headquarters administrators dispatch to full branch workspaces."
            )
        if destination.catalogue_mode == "shared":
            from .catalogue import synchronize_catalogue

            synchronize_catalogue(destination)
            destination.refresh_from_db()
            destination_id = destination.catalogue_map.get("finishedgood", {}).get(
                str(product.pk)
            )
            destination_product = FinishedGood.raw_objects.filter(
                pk=destination_id, business=destination.workspace
            ).first()
        if (
            not destination_product
            or destination_product.business_id != destination.workspace_id
            or destination_product.stock is None
        ):
            raise ValidationError(
                "Choose a stock-tracked receiving product from the destination branch."
            )
        if destination_product.unit.casefold() != product.unit.casefold():
            raise ValidationError(
                "The source and destination products must use the same stock unit."
            )
    elif destination_product is not None:
        raise ValidationError(
            "A separate receiving product is only used for full branch workspaces."
        )
    if source.pk == destination.pk:
        raise ValidationError("Choose two different branches.")
    quantity = money(quantity)
    if quantity <= 0:
        raise ValidationError("Transfer quantity must be greater than zero.")
    product = FinishedGood.raw_objects.select_for_update().get(
        pk=product.pk, business=business
    )
    # Respect checkout holds before allocating stock to transit.
    from commerce.checkout_services import available_physical_stock

    if available_physical_stock(product, branch=source) < quantity:
        raise ValidationError("Transfer exceeds unreserved stock at the source branch.")
    if not source.is_main:
        row = BranchStock.raw_objects.select_for_update().get(
            business=business, branch=source, product=product
        )
        row.quantity -= quantity
        row.save(update_fields=["quantity", "updated_at"])
    transfer = StockTransfer.raw_objects.create(
        business=business,
        created_by=user,
        source=source,
        destination=destination,
        destination_product=destination_product,
        product=product,
        quantity=quantity,
        note=note,
    )
    StockMovement.objects.create(
        business=business,
        created_by=user,
        finished_good=product,
        quantity=-quantity,
        movement_type=StockMovement.FG_BRANCH_DISPATCH,
        affects_stock=False,
        location=source.location,
        reference=f"Branch transfer {transfer.pk}",
        note="Dispatched to " + destination.name,
        business_date=timezone.localdate(),
    )
    audit(
        business,
        user,
        "branch_dispatch",
        transfer,
        "Stock dispatched",
        {"quantity": str(quantity)},
    )
    return transfer


@transaction.atomic
def receive_transfer(transfer, user, quantity, note=""):
    transfer = (
        StockTransfer.raw_objects.select_for_update()
        .select_related("product", "destination__location")
        .get(pk=transfer.pk, business_id=transfer.business_id)
    )
    from core.ledger import validate_entry_date

    validate_entry_date(transfer.business, timezone.localdate())
    require_branch(user, transfer.business, transfer.destination)
    if transfer.status != "dispatched":
        raise ValidationError("This transfer has already been received.")
    good = FinishedGood.raw_objects.select_for_update().get(
        pk=transfer.product_id, business_id=transfer.business_id
    )
    quantity = money(quantity)
    if quantity > transfer.quantity:
        raise ValidationError("Received quantity cannot exceed dispatched quantity.")
    difference = transfer.quantity - quantity
    if difference and not note.strip():
        raise ValidationError("Explain the receiving shortage.")
    if not transfer.destination.is_main and not transfer.destination.workspace_id:
        row, _ = BranchStock.raw_objects.get_or_create(
            business=transfer.business, branch=transfer.destination, product=good
        )
        row = BranchStock.raw_objects.select_for_update().get(pk=row.pk)
        row.quantity += quantity
        row.save(update_fields=["quantity", "updated_at"])
    transfer.status = "received"
    transfer.received_quantity = quantity
    transfer.received_by = user
    transfer.received_at = timezone.now()
    transfer.note = (transfer.note + "\n" + note).strip()
    transfer.save()
    if transfer.destination.workspace_id:
        from inventory.services import record_finished_good_movement

        target = FinishedGood.raw_objects.select_for_update().get(
            pk=transfer.destination_product_id,
            business_id=transfer.destination.workspace_id,
        )
        validate_entry_date(target.business, timezone.localdate())
        record_finished_good_movement(
            good,
            -quantity,
            StockMovement.FG_BRANCH_DISPATCH,
            reference=f"Branch transfer {transfer.pk}",
        )
        record_finished_good_movement(
            target,
            quantity,
            StockMovement.FG_BRANCH_RECEIPT,
            unit_value=good.est_cost,
            reference=f"Headquarters branch transfer {transfer.pk}",
        )
        audit(
            target.business,
            user,
            "branch_receive",
            transfer,
            "Headquarters stock received",
            {
                "headquarters": transfer.business_id,
                "product": target.pk,
                "quantity": str(quantity),
            },
        )
    StockMovement.objects.create(
        business=transfer.business,
        created_by=user,
        finished_good=good,
        quantity=quantity,
        movement_type=StockMovement.FG_BRANCH_RECEIPT,
        affects_stock=False,
        location=transfer.destination.location,
        reference=f"Branch transfer {transfer.pk}",
        note="Branch receipt",
        business_date=timezone.localdate(),
    )
    if difference:
        from inventory.services import record_finished_good_movement

        record_finished_good_movement(
            good,
            -difference,
            StockMovement.FG_WASTAGE,
            note="Branch transit shortage: " + note,
            reference=f"Branch transfer {transfer.pk}",
        )
    audit(
        transfer.business,
        user,
        "branch_receive",
        transfer,
        "Transfer received",
        {"received": str(quantity), "shortage": str(difference)},
    )
    return transfer


def device_for_request(request):
    token = request.session.get(
        f"retail_device_token:{request.business.pk}:{request.user.pk}", ""
    )
    if not token:
        return None
    return RegisteredDevice.raw_objects.filter(
        business=request.business,
        user=request.user,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
    ).first()


def pos_context(request, *, require_session=True):
    from .models import RetailSettings

    main = main_branch(request.business)
    allowed = permitted_branches(request.user, request.business)
    branch = (
        allowed.filter(pk=request.session.get(f"branch:{request.business.pk}")).first()
        or allowed.filter(pk=main.pk).first()
        or allowed.first()
    )
    if branch is None:
        raise PermissionDenied("No active branch is assigned to this user.")
    require_branch(request.user, request.business, branch)
    device = device_for_request(request)
    if device and device.status == "revoked":
        raise PermissionDenied(
            "This device has been revoked by your business administrator."
        )
    policy = RetailSettings.raw_objects.filter(business=request.business).first()
    if (
        policy
        and policy.require_approved_devices
        and not is_business_admin(request.user, request.business)
    ):
        if (
            not device
            or device.status != "approved"
            or (device.branch_id and device.branch_id != branch.pk)
        ):
            raise PermissionDenied(
                "Register this device from My profile and ask your business administrator to approve it."
            )
    session = CashierSession.raw_objects.filter(
        business=request.business,
        user=request.user,
        branch=branch,
        closed_at__isnull=True,
    ).first()
    if (
        require_session
        and request.method == "POST"
        and not session
        and CashierSession.raw_objects.filter(
            business=request.business, user=request.user, closed_at__isnull=True
        ).exists()
    ):
        raise ValidationError(
            "Close your cashier session in its original branch before selling in another branch."
        )
    if (
        require_session
        and policy
        and policy.require_cashier_session
        and not session
        and request.method == "POST"
    ):
        raise ValidationError("Open a cashier session before selling.")
    return branch, device, session


def cashier_summary(session):
    """Current verified tender totals, shared by review and the closing calculation."""
    from commerce.models import CommerceCheckoutSession

    checkouts = session.checkouts.values_list("checkout_id", flat=True)
    receipts = cashier_receipts(session).filter(reversed_at__isnull=True)
    tenders = list(
        receipts.values("payment__method")
        .annotate(amount=Sum("amount"))
        .order_by("payment__method")
    )
    cash = sum(
        (row["amount"] for row in tenders if row["payment__method"] == "cash"), ZERO
    )
    pending = CommerceCheckoutSession.raw_objects.filter(
        business=session.business,
        pk__in=checkouts,
        status__in=["awaiting_payment", "paid", "paid_review"],
    ).count()
    return {
        "cash_receipts": cash,
        "expected_cash": session.opening_float + cash,
        "tenders": tenders,
        "receipt_count": receipts.count(),
        "pending_count": pending,
        "checkout_count": session.checkouts.count(),
    }


def cashier_receipts(session):
    from commerce.models import CommercePaymentReceipt, CommerceCheckoutSession

    checkouts = session.checkouts.values_list("checkout_id", flat=True)
    intakes = CommerceCheckoutSession.raw_objects.filter(
        business=session.business, pk__in=checkouts
    ).values_list("materialized_intake_id", flat=True)
    return (
        CommercePaymentReceipt.raw_objects.filter(business=session.business)
        .filter(
            Q(payment__checkout_id__in=checkouts) | Q(payment__intake_id__in=intakes)
        )
        .select_related("payment")
        .order_by("-verified_at")
    )


@transaction.atomic
def close_session(session, user, counted, note):
    from commerce.models import CommercePaymentReceipt

    session = CashierSession.raw_objects.select_for_update().get(
        pk=session.pk, business=session.business
    )
    if session.closed_at:
        raise ValidationError("This session is already closed.")
    if session.user_id != user.pk and not is_business_admin(user, session.business):
        raise PermissionDenied
    summary = cashier_summary(session)
    if summary["pending_count"]:
        raise ValidationError("Resolve pending payments before closing this session.")
    session.expected_cash = summary["expected_cash"]
    session.counted_cash = money(counted)
    session.variance = session.counted_cash - session.expected_cash
    if session.variance and not note.strip():
        raise ValidationError("Explain the cash variance.")
    session.close_note = note
    session.closed_at = timezone.now()
    session.save()
    audit(
        session.business,
        user,
        "cashier_close",
        session,
        "Drawer counted; no automatic cash adjustment",
        {
            "expected": str(session.expected_cash),
            "counted": str(session.counted_cash),
            "variance": str(session.variance),
        },
    )
    return session


def promotion_prices(
    business,
    channel,
    prepared,
    *,
    reward_rows=None,
    campaigns=None,
    prepare_rewards=None,
):
    """Apply scheduled prices per selection and offers to distinct paid quantities."""
    from commerce.merchandising import (
        active_promotions,
        promotion_metadata,
        campaign_unit_price,
    )
    from .promotion_portions import matches_portion, row_portion

    campaigns = active_promotions(business, channel) if campaigns is None else campaigns
    reward_rows = reward_rows or {}
    originals = [(row[3] * row[6]).quantize(Decimal(".01")) for row in prepared]
    original = sum(originals, ZERO)
    best_prices = [row[6] for row in prepared]
    price_changes = {}
    applied = {}
    for i, row in enumerate(prepared):
        configured = []
        for campaign in campaigns:
            if not any(
                g.pk == row[1].pk and g.business_id == business.pk
                for g in campaign.products.all()
            ):
                continue
            price = campaign_unit_price(campaign, row[1].pk, channel, row_portion(row))
            if price is not None:
                configured.append((price, campaign.pk, campaign))
        if configured:
            price, _, campaign = min(
                configured, key=lambda choice: (choice[0], choice[1])
            )
            best_prices[i] = price
            if price != row[6]:
                applied[campaign.pk] = campaign
                price_changes[str(row[0].public_id)] = {
                    "original": str(row[6]),
                    "price": str(price),
                }
    base = [
        (row[3] * best_prices[i]).quantize(Decimal(".01"))
        for i, row in enumerate(prepared)
    ]
    best_targets = base.copy()
    best_rewards = []
    occupied = set()
    reward_value = ZERO

    def evaluate(campaign, blocked, previous_rewards=None):
        ids = {g.pk for g in campaign.products.all() if g.business_id == business.pk}
        eligible = [
            i
            for i, row in enumerate(prepared)
            if i not in blocked
            and row[1].pk in ids
            and row[3] > 0
            and row[3] == row[3].to_integral_value()
            and matches_portion(campaign, row)
        ]
        quantity = sum((prepared[i][3] for i in eligible), ZERO)
        groups = int(quantity) // campaign.qualifying_quantity
        if not groups:
            return None
        targets = {}
        rewards = []
        value = ZERO
        if campaign.mode == "buy_get":
            rewards = (
                prepare_rewards(campaign, previous_rewards, eligible)
                if prepare_rewards is not None and previous_rewards is not None
                else reward_rows.get(campaign.pk, [])
            )
            if not rewards:
                return None
            value = sum((row[3] * row[6] for row in rewards), ZERO)
        elif campaign.bundle_price:
            amount = sum((base[i] for i in eligible), ZERO)
            if not amount:
                return None
            reduction = max(
                ZERO,
                (
                    amount / quantity * campaign.qualifying_quantity
                    - campaign.bundle_price
                )
                * groups,
            ).quantize(Decimal(".01"))
            if not reduction:
                return None
            remaining = amount - reduction
            for i in eligible[:-1]:
                targets[i] = (base[i] * (amount - reduction) / amount).quantize(
                    Decimal(".01"), rounding=ROUND_DOWN
                )
                remaining -= targets[i]
            targets[eligible[-1]] = remaining
        else:
            return None
        benefit = sum((base[i] - target for i, target in targets.items()), ZERO) + value
        return benefit, eligible, targets, rewards, value

    candidates = []
    for campaign in campaigns:
        candidate = evaluate(campaign, set())
        if candidate is not None:
            candidates.append((candidate[0], campaign))
    for _, campaign in sorted(candidates, key=lambda item: (-item[0], item[1].pk)):
        candidate = evaluate(campaign, occupied, best_rewards)
        if candidate is None:
            continue
        _, eligible, targets, rewards, value = candidate
        occupied.update(eligible)
        for i, target in targets.items():
            best_targets[i] = target
        best_rewards.extend(rewards)
        reward_value += value
        applied[campaign.pk] = campaign

    if not applied:
        return prepared, original, {}
    details = [
        promotion_metadata(campaign, business)
        for _, campaign in sorted(applied.items())
    ]
    first = details[0]
    best_total = sum(best_targets, ZERO)
    snapshot = {
        "id": first["id"],
        "name": first["name"] if len(details) == 1 else f"{len(details)} offers",
        "mode": first["mode"],
        "kind": first["mode"],
        "description": " ".join(offer["description"] for offer in details),
        "help_title": (
            first["help_title"] if len(details) == 1 else f"{len(details)} offers"
        ),
        "help_items": first["help_items"] if len(details) == 1 else [],
        "campaigns": details,
        "eligibility_rule": first["eligibility_rule"],
        "pricing_rule": first["pricing_rule"],
        "automatic_rewards": bool(best_rewards),
        "rewards": [
            {
                "product_id": str(row[0].public_id),
                "name": row[8].name if row[8] is not None else row[0].display_name,
                "quantity": str(row[3]),
                "unit": row[9],
                "bulk_pack_id": str(row[7].public_id) if row[7] is not None else None,
                "individual_option_id": (
                    str(row[8].public_id) if row[8] is not None else None
                ),
                "is_promotion_reward": True,
                "unit_price": "0.00",
                "line_total": "0.00",
            }
            for row in best_rewards
        ],
        "saving": f"{max(ZERO,original-best_total+reward_value):.2f}",
        "price_changes": price_changes,
    }
    best_rewards = [tuple([*row[:6], ZERO, *row[7:]]) for row in best_rewards]
    result = []
    for index, row in enumerate(prepared):
        target = best_targets[index]
        if target == originals[index]:
            result.append(row)
            continue
        qty = row[3]
        if target == (qty * best_prices[index]).quantize(Decimal(".01")):
            changed = list(row)
            changed[6] = best_prices[index]
            result.append(tuple(changed))
            continue
        low = (target / qty).quantize(Decimal(".01"), rounding=ROUND_DOWN)
        high_count = int((target - low * qty) / Decimal(".01"))
        portions = [
            (qty - high_count, low),
            (Decimal(high_count), low + Decimal(".01")),
        ]
        remaining_requested = row[2]
        nonempty = [part for part in portions if part[0] > 0]
        for offset, (quantity, price) in enumerate(nonempty):
            split = list(row)
            requested = (
                (row[2] * quantity / qty).quantize(Decimal(".01"))
                if offset < len(nonempty) - 1
                else remaining_requested
            )
            remaining_requested -= requested
            split[2] = requested
            split[3] = quantity
            split[4] = row[4] * quantity / qty
            split[5] = row[5] * quantity / qty
            split[6] = price
            split[11] = [
                {
                    **item,
                    "total_quantity": str(
                        (
                            Decimal(item["quantity_per_customer_unit"]) * quantity
                        ).quantize(Decimal(".001"))
                    ),
                }
                for item in row[11]
            ]
            result.append(tuple(split))
    return result + best_rewards, best_total, snapshot
