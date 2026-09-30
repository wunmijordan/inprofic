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
    BusinessSubscription,
    PayrollAddonTier,
    PayrollCalculationRule,
    PayrollRecurringAdjustment,
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
    return list(staff.recurring_adjustments.filter(active=True).order_by("kind", "name", "id"))


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
        return {"enabled": False, "included": False, "reason": "No subscription", "staff_limit": 0, "subscription": None, "addon": None}
    tiers = list(PayrollAddonTier.objects.filter(plan=subscription.plan, active=True).order_by("staff_limit"))
    included = bool(subscription.founder_lifetime or (subscription.status == BusinessSubscription.STATUS_TRIAL and subscription.is_effectively_active))
    if included:
        return {
            "enabled": True,
            "included": True,
            "reason": "Founder lifetime" if subscription.founder_lifetime else "Included during free trial",
            "staff_limit": tiers[-1].staff_limit if tiers else None,
            "subscription": subscription,
            "addon": None,
            "tiers": tiers,
        }
    addon = BusinessPayrollAddon.objects.filter(business=business, active=True).select_related("tier__plan").first()
    paid_active = bool(
        addon and addon.tier.plan_id == subscription.plan_id and addon.paid_until and addon.paid_until >= timezone.now()
    )
    return {
        "enabled": paid_active,
        "included": False,
        "reason": "Paid add-on" if paid_active else "Payroll add-on required",
        "staff_limit": addon.tier.staff_limit if paid_active else 0,
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
        raise ValidationError(f"Your payroll access currently covers up to {limit} active staff. Choose a larger payroll add-on tier before adding another staff profile.")
    return state


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
    return addon


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
