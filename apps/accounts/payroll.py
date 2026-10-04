from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from urllib.parse import quote
from xml.sax.saxutils import escape

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from .models import (
    BusinessPayrollAddon,
    BusinessPayrollBatch,
    BusinessSubscription,
    PayrollAddonTier,
    PayrollCalculationRule,
    PayrollRecurringAdjustment,
    PayrollRun,
    PayrollRunFunding,
    Payslip,
    PayslipAdjustmentLine,
    PayslipCalculationLine,
    PayrollStaffProfile,
)


MONEY_QUANT = Decimal("0.01")


def _money(value):
    return Decimal(value or 0).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _calculation_amount(rule, *, base_pay, gross_pay):
    basis_amount = _money(base_pay if rule.basis == PayrollCalculationRule.BASIS_BASE_PAY else gross_pay)
    threshold = _money(rule.threshold_amount)
    if threshold and basis_amount <= threshold:
        return basis_amount, Decimal("0.00")
    chargeable = max(Decimal("0.00"), basis_amount - threshold)
    if rule.method == PayrollCalculationRule.METHOD_FIXED:
        amount = _money(getattr(rule, "fixed_amount", 0))
    else:
        amount = _money(chargeable * Decimal(rule.rate or 0) / Decimal("100"))
    cap = _money(rule.cap_amount)
    if cap > 0:
        amount = min(amount, cap)
    return basis_amount, _money(amount)


def _calculate_with_rules(
    rules,
    *,
    base_pay,
    recurring_allowances=0,
    recurring_deductions=0,
    manual_allowances=0,
    manual_deductions=0,
):
    """Calculate one payslip using the supplied rule definitions.

    ``rules`` may be live PayrollCalculationRule rows or frozen
    PayslipCalculationLine rows.  This is what lets an edited issued payslip
    recalculate against the rules that were frozen when it was originally
    issued instead of silently adopting rules configured later.
    """
    base_pay = _money(base_pay)
    recurring_allowances = _money(recurring_allowances)
    recurring_deductions = _money(recurring_deductions)
    manual_allowances = _money(manual_allowances)
    manual_deductions = _money(manual_deductions)
    rules = list(rules)

    lines = []
    gross = _money(base_pay + recurring_allowances + manual_allowances)
    rule_earnings = Decimal("0.00")

    for rule in (row for row in rules if row.effect == PayrollCalculationRule.EFFECT_EARNING):
        calculation_base, amount = _calculation_amount(rule, base_pay=base_pay, gross_pay=gross)
        if amount:
            gross = _money(gross + amount)
            rule_earnings = _money(rule_earnings + amount)
        lines.append((rule, calculation_base, amount))

    rule_deductions = Decimal("0.00")
    employer_contributions = Decimal("0.00")
    for rule in (row for row in rules if row.effect != PayrollCalculationRule.EFFECT_EARNING):
        calculation_base, amount = _calculation_amount(rule, base_pay=base_pay, gross_pay=gross)
        if rule.effect == PayrollCalculationRule.EFFECT_EMPLOYEE_DEDUCTION:
            rule_deductions = _money(rule_deductions + amount)
        elif rule.effect == PayrollCalculationRule.EFFECT_EMPLOYER_CONTRIBUTION:
            employer_contributions = _money(employer_contributions + amount)
        lines.append((rule, calculation_base, amount))

    total_allowances = _money(recurring_allowances + manual_allowances + rule_earnings)
    total_deductions = _money(recurring_deductions + manual_deductions + rule_deductions)
    net_pay = _money(max(Decimal("0.00"), gross - total_deductions))
    employer_cost = _money(gross + employer_contributions)
    return {
        "base_pay": base_pay,
        "recurring_allowances": recurring_allowances,
        "recurring_deductions": recurring_deductions,
        "manual_allowances": manual_allowances,
        "manual_deductions": manual_deductions,
        "allowances": total_allowances,
        "deductions": total_deductions,
        "gross_pay": gross,
        "net_pay": net_pay,
        "employer_contributions": employer_contributions,
        "employer_cost": employer_cost,
        "lines": lines,
    }


def calculate_payslip(
    *, business, base_pay, recurring_allowances=0, recurring_deductions=0,
    manual_allowances=0, manual_deductions=0,
):
    """Return frozen totals and rule lines for a newly issued payslip."""
    rules = list(
        PayrollCalculationRule.objects.filter(business=business, active=True)
        .order_by("sort_order", "name", "id")
    )
    return _calculate_with_rules(
        rules,
        base_pay=base_pay,
        recurring_allowances=recurring_allowances,
        recurring_deductions=recurring_deductions,
        manual_allowances=manual_allowances,
        manual_deductions=manual_deductions,
    )


def recurring_adjustments_for_staff(staff):
    prefetched = getattr(staff, "_prefetched_objects_cache", {}).get(
        "recurring_adjustments"
    )
    if prefetched is not None:
        rows = [row for row in prefetched if row.active]
        return sorted(rows, key=lambda row: (row.kind, row.name.lower(), row.pk or 0))
    return list(
        staff.recurring_adjustments.filter(active=True).order_by("kind", "name", "id")
    )


def recurring_adjustment_totals(adjustments):
    allowances = Decimal("0.00")
    deductions = Decimal("0.00")
    for row in adjustments:
        amount = _money(row.amount)
        if row.kind == PayrollRecurringAdjustment.KIND_ALLOWANCE:
            allowances = _money(allowances + amount)
        elif row.kind == PayrollRecurringAdjustment.KIND_DEDUCTION:
            deductions = _money(deductions + amount)
    return allowances, deductions


def sync_staff_recurring_totals(staff):
    """Keep the pre-line-item aggregate fields accurate for old code/data."""
    adjustments = recurring_adjustments_for_staff(staff)
    allowances, deductions = recurring_adjustment_totals(adjustments)
    updates = []
    if _money(staff.recurring_allowances) != allowances:
        staff.recurring_allowances = allowances
        updates.append("recurring_allowances")
    if _money(staff.recurring_deductions) != deductions:
        staff.recurring_deductions = deductions
        updates.append("recurring_deductions")
    if updates:
        updates.append("updated_at")
        staff.save(update_fields=updates)
    return allowances, deductions


def persist_payslip_adjustment_lines(payslip, adjustments):
    rows = [
        PayslipAdjustmentLine(
            payslip=payslip,
            recurring_adjustment=row,
            kind=row.kind,
            name=row.name,
            amount=_money(row.amount),
        )
        for row in adjustments
    ]
    if rows:
        PayslipAdjustmentLine.objects.bulk_create(rows)
    return rows


def persist_payslip_calculation_lines(payslip, lines):
    rows = []
    for rule, calculation_base, amount in lines:
        rows.append(PayslipCalculationLine(
            payslip=payslip,
            rule=rule,
            name=rule.name,
            category=rule.category,
            effect=rule.effect,
            method=rule.method,
            basis=rule.basis,
            rate=rule.rate,
            fixed_amount=rule.fixed_amount,
            threshold_amount=rule.threshold_amount,
            cap_amount=rule.cap_amount,
            calculation_base=calculation_base,
            amount=amount,
            statutory=rule.statutory,
            sort_order=rule.sort_order,
        ))
    if rows:
        PayslipCalculationLine.objects.bulk_create(rows)
    return rows


def payroll_preview_for_staff(staff, *, business=None, rules=None):
    """Calculate the current saved payroll structure without issuing anything."""
    business = business or staff.business
    adjustments = recurring_adjustments_for_staff(staff)
    recurring_allowances, recurring_deductions = recurring_adjustment_totals(
        adjustments
    )
    if rules is None:
        calculation = calculate_payslip(
            business=business,
            base_pay=staff.base_pay,
            recurring_allowances=recurring_allowances,
            recurring_deductions=recurring_deductions,
        )
    else:
        calculation = _calculate_with_rules(
            rules,
            base_pay=staff.base_pay,
            recurring_allowances=recurring_allowances,
            recurring_deductions=recurring_deductions,
        )
    return {
        "staff": staff,
        "adjustments": adjustments,
        "calculation": calculation,
    }


def _post_payroll_funding(*, payroll_run, funding, user):
    from core.models import FinancialTransaction
    from core.services import record_cash

    description = (
        f"Payroll wages · {payroll_run.period_start:%d %b %Y}–"
        f"{payroll_run.period_end:%d %b %Y} · {payroll_run.staff_count} staff"
    )
    for account, amount in funding:
        tx = record_cash(
            payroll_run.business,
            user,
            date=payroll_run.pay_date,
            amount=_money(amount),
            transaction_type=FinancialTransaction.OUTFLOW,
            category="Payroll / Wages",
            description=description,
            payment_method=account.get_account_type_display(),
            reference=f"PAYRUN-{payroll_run.pk}",
            account=account,
        )
        PayrollRunFunding.objects.create(
            payroll_run=payroll_run,
            account=account,
            amount=_money(amount),
            finance_transaction=tx,
        )


@transaction.atomic
def issue_bulk_payroll_run(
    *,
    business,
    user,
    staff_members,
    period_start,
    period_end,
    pay_date,
    funding,
    notes="",
    kind=PayrollRun.KIND_BULK,
    manual_values=None,
):
    """Issue payslips and post the matching wage outflow as one transaction."""
    submitted_staff = list(staff_members)
    if not submitted_staff:
        raise ValidationError("Select at least one active staff member.")
    if period_end < period_start:
        raise ValidationError("Pay period end cannot be before the start date.")

    staff_ids = [staff.pk for staff in submitted_staff]
    if len(staff_ids) != len(set(staff_ids)):
        raise ValidationError("Each staff member can appear only once in a payroll run.")

    # Lock selected profiles in a stable order. Two concurrent pay runs for the
    # same staff/period cannot both pass the duplicate-payslip check.
    locked_staff = list(
        PayrollStaffProfile.objects.select_for_update()
        .filter(
            business=business,
            active=True,
            pk__in=staff_ids,
        )
        .prefetch_related("recurring_adjustments")
        .order_by("pk")
    )
    if len(locked_staff) != len(staff_ids):
        raise ValidationError(
            "Every selected payroll profile must be active and belong to this business."
        )
    staff_by_id = {staff.pk: staff for staff in locked_staff}
    staff_members = [staff_by_id[staff_id] for staff_id in staff_ids]

    duplicate_names = list(
        Payslip.objects.filter(
            business=business,
            staff_id__in=staff_ids,
            period_start=period_start,
            period_end=period_end,
        )
        .values_list("staff__full_name", flat=True)
    )
    if duplicate_names:
        unique_names = sorted(set(duplicate_names))
        names = ", ".join(unique_names[:5])
        suffix = "…" if len(unique_names) > 5 else ""
        raise ValidationError(
            f"A payslip already exists for this exact period for: {names}{suffix}. "
            "Open the existing payslip instead of issuing a duplicate."
        )

    rules = list(
        PayrollCalculationRule.objects.filter(
            business=business,
            active=True,
        ).order_by("sort_order", "name", "id")
    )
    prepared = []
    total_gross = Decimal("0.00")
    total_net = Decimal("0.00")
    total_employer_cost = Decimal("0.00")
    manual_values = manual_values or {}

    for staff in staff_members:
        values = manual_values.get(staff.pk, {})
        adjustments = recurring_adjustments_for_staff(staff)
        recurring_allowances, recurring_deductions = recurring_adjustment_totals(
            adjustments
        )
        calculation = _calculate_with_rules(
            rules,
            base_pay=values.get("base_pay", staff.base_pay),
            recurring_allowances=recurring_allowances,
            recurring_deductions=recurring_deductions,
            manual_allowances=values.get("manual_allowances", 0),
            manual_deductions=values.get("manual_deductions", 0),
        )
        prepared.append(
            (
                staff,
                adjustments,
                calculation,
                values.get("notes", ""),
            )
        )
        total_gross = _money(total_gross + calculation["gross_pay"])
        total_net = _money(total_net + calculation["net_pay"])
        total_employer_cost = _money(
            total_employer_cost + calculation["employer_cost"]
        )

    if total_net <= 0:
        raise ValidationError(
            "The selected staff have no positive net wages to post to Finance."
        )

    funding = list(funding)
    if len(funding) == 1 and funding[0][1] is None:
        funding = [(funding[0][0], total_net)]

    cleaned_funding = []
    funding_total = Decimal("0.00")
    seen_accounts = set()
    for account, amount in funding:
        amount = _money(amount)
        if amount <= 0:
            continue
        if account.business_id != business.pk or not account.active:
            raise ValidationError(
                "Every Finance account must be active and belong to this business."
            )
        if account.pk in seen_accounts:
            raise ValidationError(
                "Use each Finance account only once in a payroll run."
            )
        seen_accounts.add(account.pk)
        cleaned_funding.append((account, amount))
        funding_total = _money(funding_total + amount)

    if not cleaned_funding:
        raise ValidationError("Choose at least one Finance account for wage payment.")
    if funding_total != total_net:
        raise ValidationError(
            f"Finance account allocations total {funding_total:,.2f}, but staff net "
            f"pay is {total_net:,.2f}. The allocation must match the net wages exactly."
        )

    payroll_run = PayrollRun.objects.create(
        business=business,
        created_by=user,
        kind=kind,
        period_start=period_start,
        period_end=period_end,
        pay_date=pay_date,
        staff_count=len(prepared),
        total_gross_pay=total_gross,
        total_net_pay=total_net,
        total_employer_cost=total_employer_cost,
        notes=(notes or "").strip(),
    )

    payslips = []
    for staff, adjustments, calculation, row_notes in prepared:
        payslip = Payslip.objects.create(
            business=business,
            created_by=user,
            staff=staff,
            payroll_run=payroll_run,
            period_start=period_start,
            period_end=period_end,
            base_pay=calculation["base_pay"],
            manual_allowances=calculation["manual_allowances"],
            manual_deductions=calculation["manual_deductions"],
            allowances=calculation["allowances"],
            deductions=calculation["deductions"],
            gross_pay=calculation["gross_pay"],
            net_pay=calculation["net_pay"],
            employer_contributions=calculation["employer_contributions"],
            employer_cost=calculation["employer_cost"],
            notes=(row_notes or notes or "").strip(),
        )
        persist_payslip_adjustment_lines(payslip, adjustments)
        persist_payslip_calculation_lines(payslip, calculation["lines"])
        payslips.append(payslip)

    _post_payroll_funding(
        payroll_run=payroll_run,
        funding=cleaned_funding,
        user=user,
    )

    from core.services import audit

    audit(
        business,
        user,
        "create",
        payroll_run,
        f"Payroll run {payroll_run.pk} posted to Finance",
        {
            "staff_count": payroll_run.staff_count,
            "net_pay": str(payroll_run.total_net_pay),
            "finance_accounts": [account.pk for account, _ in cleaned_funding],
        },
    )
    return payroll_run, payslips


def post_payslip_finance_adjustment(*, payslip, delta, account, user, revision):
    """Post an auditable ledger delta after correcting a Finance-posted payslip."""
    from core.models import FinancialTransaction
    from core.services import record_cash

    delta = _money(delta)
    if not delta:
        return None
    if account is None or account.business_id != payslip.business_id or not account.active:
        raise ValidationError("Choose an active Finance account from this business for the payroll correction.")
    transaction_type = FinancialTransaction.OUTFLOW if delta > 0 else FinancialTransaction.INCOME
    tx = record_cash(
        payslip.business,
        user,
        date=timezone.localdate(),
        amount=abs(delta),
        transaction_type=transaction_type,
        category="Payroll adjustment",
        description=f"Payroll correction · {payslip.staff.full_name} · payslip #{payslip.pk}",
        payment_method=account.get_account_type_display(),
        reference=f"PAYSLIP-{payslip.pk}-REV-{revision.pk}",
        account=account,
    )
    revision.finance_delta = delta
    revision.finance_transaction = tx
    revision.save(update_fields=["finance_delta", "finance_transaction"])
    if payslip.payroll_run_id:
        run = PayrollRun.objects.select_for_update().get(pk=payslip.payroll_run_id)
        run_payslips = list(
            run.payslips.all().only(
                "gross_pay",
                "net_pay",
                "employer_cost",
            )
        )
        run.total_gross_pay = _money(
            sum((row.gross_pay for row in run_payslips), Decimal("0.00"))
        )
        run.total_net_pay = _money(
            sum((row.net_pay for row in run_payslips), Decimal("0.00"))
        )
        run.total_employer_cost = _money(
            sum((row.employer_cost for row in run_payslips), Decimal("0.00"))
        )
        run.save(
            update_fields=[
                "total_gross_pay",
                "total_net_pay",
                "total_employer_cost",
                "updated_at",
            ]
        )
    return tx


def recalculate_issued_payslip(payslip):
    """Recalculate an edited payslip from its own frozen lines and rules."""
    adjustment_lines = list(payslip.adjustment_lines.all().order_by("id"))
    recurring_allowances, recurring_deductions = recurring_adjustment_totals(adjustment_lines)
    frozen_rules = list(payslip.calculation_lines.all().order_by("sort_order", "id"))
    calculation = _calculate_with_rules(
        frozen_rules,
        base_pay=payslip.base_pay,
        recurring_allowances=recurring_allowances,
        recurring_deductions=recurring_deductions,
        manual_allowances=payslip.manual_allowances,
        manual_deductions=payslip.manual_deductions,
    )
    changed_lines = []
    for line, calculation_base, amount in calculation["lines"]:
        line.calculation_base = calculation_base
        line.amount = amount
        changed_lines.append(line)
    if changed_lines:
        PayslipCalculationLine.objects.bulk_update(changed_lines, ["calculation_base", "amount"])

    for field in (
        "allowances", "deductions", "gross_pay", "net_pay",
        "employer_contributions", "employer_cost",
    ):
        setattr(payslip, field, calculation[field])
    payslip.save(update_fields=[
        "allowances", "deductions", "gross_pay", "net_pay",
        "employer_contributions", "employer_cost", "updated_at",
    ])
    return calculation


def payslip_revision_snapshot(payslip):
    """JSON-safe copy of an issued payslip before an intentional edit."""
    def money(value):
        return str(_money(value))

    return {
        "period_start": payslip.period_start.isoformat(),
        "period_end": payslip.period_end.isoformat(),
        "base_pay": money(payslip.base_pay),
        "manual_allowances": money(payslip.manual_allowances),
        "manual_deductions": money(payslip.manual_deductions),
        "allowances": money(payslip.allowances),
        "deductions": money(payslip.deductions),
        "gross_pay": money(payslip.gross_pay),
        "net_pay": money(payslip.net_pay),
        "employer_contributions": money(payslip.employer_contributions),
        "employer_cost": money(payslip.employer_cost),
        "notes": payslip.notes or "",
        "adjustment_lines": [
            {"name": row.name, "kind": row.kind, "amount": money(row.amount)}
            for row in payslip.adjustment_lines.all().order_by("id")
        ],
        "calculation_lines": [
            {
                "name": row.name,
                "category": row.category,
                "effect": row.effect,
                "method": row.method,
                "basis": row.basis,
                "rate": str(row.rate),
                "fixed_amount": money(row.fixed_amount),
                "threshold_amount": money(row.threshold_amount),
                "cap_amount": money(row.cap_amount),
                "calculation_base": money(row.calculation_base),
                "amount": money(row.amount),
                "statutory": bool(row.statutory),
            }
            for row in payslip.calculation_lines.all().order_by("sort_order", "id")
        ],
    }


def payroll_subscription_for_business(business):
    service = getattr(business, "subscription_service", None)
    if service:
        return service.subscription
    return BusinessSubscription.objects.filter(primary_business=business).select_related("plan").first()


def payroll_access_state(business):
    subscription = payroll_subscription_for_business(business)
    if not subscription:
        return {
            "enabled": False, "included": False, "reason": "No subscription", "staff_limit": 0,
            "unlimited": False, "base_limit": 0, "extra_staff": 0, "batches": [],
            "subscription": None, "addon": None,
        }
    tiers = list(PayrollAddonTier.objects.filter(plan=subscription.plan, active=True))
    included = bool(subscription.founder_lifetime or (subscription.status == BusinessSubscription.STATUS_TRIAL and subscription.is_effectively_active))
    if included:
        # Tiers are ordered with the unlimited tier last, so an unlimited tier
        # (staff_limit None) correctly lifts the cap for trial/Founder access.
        limit = tiers[-1].staff_limit if tiers else None
        return {
            "enabled": True,
            "included": True,
            "reason": "Founder lifetime" if subscription.founder_lifetime else "Included during free trial",
            "staff_limit": limit,
            "unlimited": limit is None,
            "base_limit": limit,
            "extra_staff": 0,
            "batches": [],
            "subscription": subscription,
            "addon": None,
            "tiers": tiers,
        }
    addon = (
        BusinessPayrollAddon.objects.filter(business=business, active=True)
        .select_related("tier__plan").prefetch_related("batches__batch").first()
    )
    paid_active = bool(
        addon and addon.tier.plan_id == subscription.plan_id and addon.paid_until and addon.paid_until >= timezone.now()
    )
    held = list(addon.batches.all()) if paid_active else []
    unlimited = bool(paid_active and addon.tier.unlimited)
    base_limit = addon.tier.staff_limit if paid_active and not unlimited else 0
    extra = 0 if unlimited else sum(h.staff for h in held)
    if not paid_active:
        limit = 0
    else:
        limit = None if unlimited else base_limit + extra
    return {
        "enabled": paid_active,
        "included": False,
        "reason": "Paid add-on" if paid_active else "Payroll add-on required",
        "staff_limit": limit,
        "unlimited": unlimited,
        "base_limit": base_limit,
        "extra_staff": extra,
        "batches": held,
        "subscription": subscription,
        "addon": addon,
        "tiers": tiers,
    }


def assert_payroll_capacity(business, *, excluding_profile=None):
    state = payroll_access_state(business)
    if not state["enabled"]:
        raise ValidationError("Payroll is available during a free trial or Founder lifetime access; otherwise choose a paid payroll add-on tier.")
    limit = state["staff_limit"]
    if limit is None:
        return state
    qs = PayrollStaffProfile.objects.filter(business=business, active=True)
    if excluding_profile and excluding_profile.pk:
        qs = qs.exclude(pk=excluding_profile.pk)
    if qs.count() >= limit:
        hint = "Buy an extra-staff batch or choose a larger payroll add-on tier" if not state["included"] else "Choose a larger payroll add-on tier"
        raise ValidationError(f"Your payroll access currently covers up to {limit} active staff. {hint} before adding another staff profile.")
    return state


# --- Extra-staff batches and renewal pricing ---------------------------------

MAX_BATCH_QUANTITY = 50
_DAYS_PER_YEAR = Decimal("365")


def _cents(value):
    return Decimal(value).quantize(Decimal("0.01"))


def held_batches_for_renewal(addon):
    """Batches that renew with the primary package (empty for unlimited tiers)."""
    if not addon or addon.tier.unlimited:
        return []
    return list(addon.batches.select_related("batch"))


def renewal_monthly_total(tier, addon):
    """Monthly price of renewing ``tier`` plus every held batch at today's price."""
    total = Decimal(tier.monthly_price or 0)
    if not tier.unlimited:
        total += sum((Decimal(h.batch.monthly_price or 0) * h.quantity for h in held_batches_for_renewal(addon)), Decimal("0"))
    return _cents(total)


def batch_snapshot(holdings):
    return [
        {
            "batch_id": h.batch_id, "staff_count": h.batch.staff_count, "quantity": h.quantity,
            "unit_monthly_price": str(_cents(h.batch.monthly_price or 0)),
        }
        for h in holdings
    ]


def prorated_batch_amount(batch, quantity, paid_until, *, now=None):
    """Charge for adding ``quantity`` of ``batch`` until the primary period ends.

    Priced per day on an annualised basis (monthly price x 12 / 365) so a batch
    bought at the start of a yearly period costs exactly 12 months and one
    bought on the last day costs a single day. Part-days round up.
    """
    now = now or timezone.now()
    seconds = (paid_until - now).total_seconds()
    if seconds <= 0:
        raise ValidationError("Your payroll add-on has expired. Renew it before adding extra staff.")
    days = max(1, int(-(-seconds // 86400)))
    amount = Decimal(batch.monthly_price or 0) * 12 * Decimal(quantity) * Decimal(days) / _DAYS_PER_YEAR
    return _cents(amount), days


def assert_can_buy_batch(business, batch, quantity):
    """Return the paid, current addon if ``quantity`` of ``batch`` can be bought now."""
    state = payroll_access_state(business)
    subscription = state["subscription"]
    if state["included"]:
        raise ValidationError("Payroll is already included with the current free-trial or Founder lifetime access.")
    if not subscription or not state["enabled"]:
        raise ValidationError("Buy or renew a primary payroll package before adding extra staff.")
    addon = state["addon"]
    if addon.tier.unlimited:
        raise ValidationError("Your payroll package already covers unlimited staff.")
    if not batch.active or batch.plan_id != subscription.plan_id:
        raise ValidationError("Choose an active extra-staff batch configured for your current plan.")
    if not 1 <= int(quantity) <= MAX_BATCH_QUANTITY:
        raise ValidationError(f"Choose between 1 and {MAX_BATCH_QUANTITY} batches.")
    return addon


@transaction.atomic
def activate_paid_payroll_addon(payment):
    tier = payment.payroll_tier
    if not tier or tier.plan_id != payment.subscription.plan_id:
        raise ValidationError("This payroll tier no longer matches the business's active plan.")
    addon, _ = BusinessPayrollAddon.objects.select_for_update().get_or_create(
        business=payment.subscription.primary_business,
        defaults={"tier": tier},
    )
    addon.tier = tier
    addon.active = True
    now = timezone.now()
    base = max([value for value in (addon.paid_until, now) if value is not None])
    duration_days = 365 if payment.billing_cycle == payment.CYCLE_YEARLY else 30 * payment.months
    addon.paid_until = base + timezone.timedelta(days=duration_days)
    addon.save(update_fields=["tier", "active", "paid_until", "updated_at"])
    if tier.unlimited:
        # Batches are meaningless under an unlimited package and were not billed.
        addon.batches.all().delete()
    return addon


@transaction.atomic
def activate_paid_payroll_batch(payment):
    """Add the purchased batch(es). ``paid_until`` is deliberately untouched:
    the pro-rata charge covers the batch only until the current period ends,
    after which it renews together with the primary package."""
    batch = payment.payroll_batch
    if not batch or batch.plan_id != payment.subscription.plan_id:
        raise ValidationError("This payroll batch no longer matches the business's active plan.")
    addon = BusinessPayrollAddon.objects.select_for_update().select_related("tier").filter(
        business=payment.subscription.primary_business, active=True,
    ).first()
    if not addon or addon.tier.unlimited or not addon.paid_until or addon.paid_until < timezone.now():
        raise ValidationError("The primary payroll add-on is no longer active, so the extra staff batch could not be applied.")
    holding, created = BusinessPayrollBatch.objects.select_for_update().get_or_create(
        addon=addon, batch=batch, defaults={"quantity": payment.payroll_batch_quantity},
    )
    if not created:
        holding.quantity = min(holding.quantity + payment.payroll_batch_quantity, 32767)
        holding.save(update_fields=["quantity", "updated_at"])
    return holding


def normalize_whatsapp_number(value):
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if digits.startswith("0") and len(digits) == 11:
        digits = "234" + digits[1:]
    return digits


def payslip_public_url(request, payslip):
    from django.urls import reverse
    return request.build_absolute_uri(reverse("payroll_payslip_public", args=[payslip.public_id]))


def payslip_public_pdf_url(request, payslip):
    from django.urls import reverse
    return request.build_absolute_uri(reverse("payroll_payslip_public_pdf", args=[payslip.public_id]))


def payslip_filename(payslip):
    staff = slugify(payslip.staff.full_name) or "staff"
    return f"payslip-{staff}-{payslip.period_end:%Y-%m-%d}.pdf"


def payslip_pdf_bytes(payslip):
    """Generate the attachment version using the same rows shown in-app."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from core.pdf_fonts import PDF_BODY_FONT, PDF_BODY_BOLD_FONT, PDF_DISPLAY_FONT, PDF_MONO_FONT

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "payroll_title", parent=styles["Title"], fontName=PDF_DISPLAY_FONT,
        fontSize=20, leading=24, textColor=colors.HexColor("#182433"), spaceAfter=2,
    )
    eyebrow_style = ParagraphStyle(
        "payroll_eyebrow", parent=styles["Normal"], fontName=PDF_BODY_BOLD_FONT,
        fontSize=7.5, leading=10, textColor=colors.HexColor("#78716C"),
    )
    body_style = ParagraphStyle(
        "payroll_body", parent=styles["Normal"], fontName=PDF_BODY_FONT,
        fontSize=8.5, leading=11, textColor=colors.HexColor("#292524"),
    )
    small_style = ParagraphStyle(
        "payroll_small", parent=styles["Normal"], fontName=PDF_BODY_FONT,
        fontSize=6.8, leading=9, textColor=colors.HexColor("#78716C"),
    )
    money_style = ParagraphStyle(
        "payroll_money", parent=body_style, fontName=PDF_MONO_FONT, alignment=TA_RIGHT,
    )
    net_label_style = ParagraphStyle(
        "payroll_net_label", parent=small_style, textColor=colors.white, alignment=TA_RIGHT,
    )
    net_money_style = ParagraphStyle(
        "payroll_net_money", parent=money_style, fontSize=14, leading=17, textColor=colors.white,
    )

    symbol = payslip.business.currency_symbol

    def money(value):
        return f"{escape(symbol)}{_money(value):,.2f}"

    def row(label, amount, *, prefix="", detail="", background=None, bold=False):
        label_html = f"<b>{escape(str(label))}</b>" if bold else escape(str(label))
        if detail:
            label_html += f"<br/><font size='6.6' color='#78716C'>{escape(str(detail))}</font>"
        amount_html = f"{escape(prefix)}{money(amount)}"
        cells = [Paragraph(label_html, body_style), Paragraph(amount_html, money_style)]
        return cells, background

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm, title=payslip_filename(payslip),
    )
    story = []
    story.append(Paragraph(f"{escape(payslip.business.name)} · PAYSLIP", eyebrow_style))
    story.append(Spacer(1, 1.5 * mm))

    net_box = Table([
        [Paragraph(escape(payslip.staff.full_name), title_style),
         Table([[Paragraph("NET PAY", net_label_style)], [Paragraph(money(payslip.net_pay), net_money_style)]], colWidths=[48 * mm])],
        [Paragraph(f"{payslip.period_start:%d %b %Y} – {payslip.period_end:%d %b %Y}", body_style), ""],
    ], colWidths=[118 * mm, 48 * mm])
    net_box.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#050733")),
        ("LEFTPADDING", (1, 0), (1, 0), 9),
        ("RIGHTPADDING", (1, 0), (1, 0), 9),
        ("TOPPADDING", (1, 0), (1, 0), 8),
        ("BOTTOMPADDING", (1, 0), (1, 0), 8),
    ]))
    story.append(net_box)
    story.append(Spacer(1, 7 * mm))

    table_rows = []
    row_backgrounds = []

    def add_row(label, amount, **kwargs):
        cells, background = row(label, amount, **kwargs)
        table_rows.append(cells)
        row_backgrounds.append(background)

    add_row("Base pay", payslip.base_pay)
    for adjustment in payslip.adjustment_lines.all().order_by("id"):
        if adjustment.kind == PayslipAdjustmentLine.KIND_ALLOWANCE:
            add_row(adjustment.name, adjustment.amount, prefix="+ ", detail="Recurring allowance")
    if payslip.manual_allowances:
        add_row("Other allowance / earning", payslip.manual_allowances, prefix="+ ", detail="This pay period only")

    for line in payslip.calculation_lines.all().order_by("sort_order", "id"):
        if line.method == PayrollCalculationRule.METHOD_PERCENTAGE:
            detail = f"{line.get_category_display()} · {format(Decimal(line.rate or 0).normalize(), 'f')}% of {line.get_basis_display()}"
        else:
            detail = f"{line.get_category_display()} · fixed amount"
        if line.statutory:
            detail += " · statutory"
        if line.threshold_amount:
            detail += f" · threshold {symbol}{_money(line.threshold_amount):,.2f}"
        if line.cap_amount:
            detail += f" · cap {symbol}{_money(line.cap_amount):,.2f}"
        prefix = "- " if line.effect == PayrollCalculationRule.EFFECT_EMPLOYEE_DEDUCTION else "+ " if line.effect == PayrollCalculationRule.EFFECT_EARNING else ""
        if line.effect == PayrollCalculationRule.EFFECT_EMPLOYER_CONTRIBUTION:
            detail += " · employer-funded"
        add_row(line.name, line.amount, prefix=prefix, detail=detail)

    for adjustment in payslip.adjustment_lines.all().order_by("id"):
        if adjustment.kind == PayslipAdjustmentLine.KIND_DEDUCTION:
            add_row(adjustment.name, adjustment.amount, prefix="- ", detail="Recurring deduction")
    if payslip.manual_deductions:
        add_row("Other deduction", payslip.manual_deductions, prefix="- ", detail="This pay period only")

    add_row("Gross pay", payslip.gross_pay, background="#FAFAF9", bold=True)
    add_row("Total staff deductions", payslip.deductions, prefix="- ", bold=True)
    if payslip.employer_contributions:
        add_row("Employer contributions", payslip.employer_contributions, bold=True)
        add_row("Total employer cost", payslip.employer_cost, background="#FFF8E8", bold=True)

    table = Table(table_rows, colWidths=[120 * mm, 46 * mm], repeatRows=0)
    style_cmds = [
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E7E5E4")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]
    for index, background in enumerate(row_backgrounds):
        if background:
            style_cmds.append(("BACKGROUND", (0, index), (-1, index), colors.HexColor(background)))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    if payslip.notes:
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph(f"<b>Note</b><br/>{escape(payslip.notes)}", body_style))
    story.append(Spacer(1, 7 * mm))
    story.append(Paragraph(
        "Generated by INPROFIC. This document reproduces the issued payslip details and does not grant access to the business workspace.",
        small_style,
    ))

    doc.build(story)
    return buffer.getvalue()


def send_payslip_email(*, request, payslip):
    if not payslip.staff.email:
        raise ValidationError("This staff profile does not have an email address.")
    url = payslip_public_url(request, payslip)
    subject = f"Payslip from {payslip.business.name} · {payslip.period_end:%d %b %Y}"
    text = (
        f"Hello {payslip.staff.full_name},\n\n"
        f"Your payslip for {payslip.period_start:%d %b %Y} to {payslip.period_end:%d %b %Y} is ready.\n"
        f"Net pay: {payslip.business.currency_symbol}{payslip.net_pay:,.2f}\n\n"
        "The payslip is attached as a PDF in the same detailed format used in INPROFIC.\n"
        f"You can also view the secure shared copy here: {url}\n"
    )
    message = EmailMultiAlternatives(
        subject=subject,
        body=text,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[payslip.staff.email],
    )
    message.attach(payslip_filename(payslip), payslip_pdf_bytes(payslip), "application/pdf")
    message.send(fail_silently=False)
    return url


def payslip_whatsapp_url(*, request, payslip):
    number = normalize_whatsapp_number(payslip.staff.whatsapp_number)
    if not number:
        raise ValidationError("This staff profile does not have a WhatsApp number.")
    pdf_url = payslip_public_pdf_url(request, payslip)
    text = (
        f"Hello {payslip.staff.full_name}, your {payslip.business.name} payslip for "
        f"{payslip.period_start:%d %b %Y}–{payslip.period_end:%d %b %Y} is ready. "
        f"Net pay: {payslip.business.currency_symbol}{payslip.net_pay:,.2f}. "
        f"Payslip PDF: {pdf_url}"
    )
    return f"https://wa.me/{number}?text={quote(text)}"
