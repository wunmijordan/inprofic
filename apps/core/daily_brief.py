"""The morning brief: yesterday in a few lines an owner can read in WhatsApp or email.

Three ways to receive the same brief:

* **In the app**, with a copy button and a tap-to-send WhatsApp link (no API).
* **By email**, once a day for members who opted in.
* **By WhatsApp message**, once a day for members who gave their number and
  consent. WhatsApp only lets a business message someone first with a
  Meta-approved *template*, so this uses the template named in the business's
  WhatsApp connection (see ``TEMPLATE_BODY`` for the text to register). If the
  send fails, the member gets the email instead (when they have an address).
"""
import logging
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from . import ledger
from .accounting import _sale_expressions, daily_components, summarise

logger = logging.getLogger(__name__)
ZERO = Decimal("0")
SEND_AFTER_HOUR = 7


def _money(business, value):
    value = Decimal(value or 0)
    return f"{'-' if value < 0 else ''}{business.currency_symbol or ''}{abs(value):,.2f}"


def parse_day(raw, today=None):
    """The day a brief is for. Returns ``(day, note)``; ``note`` explains any adjustment."""
    today = today or timezone.localdate()
    default = today - timedelta(days=1)
    raw = (raw or "").strip()
    if not raw:
        return default, ""
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return default, "That date was not understood, so yesterday is shown."
    if day > today:
        return today, "Future dates cannot be reported yet, so today is shown."
    return day, ""


def week_start_of(day):
    """Monday of the week containing ``day``."""
    return day - timedelta(days=day.weekday())


def default_week_start(today=None):
    """Monday of the most recently completed week."""
    return week_start_of(today or timezone.localdate()) - timedelta(days=7)


def parse_week(raw, today=None):
    """The week (as its Monday) a weekly summary is for, from any date inside it."""
    today = today or timezone.localdate()
    raw = (raw or "").strip()
    if not raw:
        return default_week_start(today), ""
    try:
        start = week_start_of(date.fromisoformat(raw))
    except ValueError:
        return default_week_start(today), "That date was not understood, so last week is shown."
    if start > week_start_of(today):
        return week_start_of(today), "Future weeks cannot be reported yet, so this week is shown."
    return start, ""


def day_label(day, today=None):
    today = today or timezone.localdate()
    if day == today:
        return "Today so far"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return f"{day:%A}"


def week_label(start, end):
    if start.year != end.year:
        return f"{start:%a %d %b %Y} – {end:%a %d %b %Y}"
    return f"{start:%a %d %b} – {end:%a %d %b %Y}"


def _change(current, previous):
    """How a figure moved against the comparison period."""
    current, previous = Decimal(current or 0), Decimal(previous or 0)
    amount = current - previous
    pct = float(round(amount / abs(previous) * 100, 1)) if previous else None
    direction = "up" if amount > 0 else "down" if amount < 0 else "flat"
    return {"amount": amount, "pct": pct, "direction": direction}


def build_weekly_brief(business, week_start=None, today=None):
    """Profit and loss for one Monday-to-Sunday week, compared with the week before.

    A week still in progress is reported "so far" and compared with the same number
    of days of the previous week, so the comparison is fair. Figures come from the
    same ``profit_and_loss`` components as the Finance page, so they always agree.
    """
    from sales.models import Sale
    today = today or timezone.localdate()
    start = week_start or default_week_start(today)
    end = start + timedelta(days=6)
    through = min(end, today)
    elapsed = (through - start).days
    prev_start = start - timedelta(days=7)
    prev_through = prev_start + timedelta(days=elapsed)
    components = daily_components(business, prev_start, through)
    current = summarise(components, start, through)
    previous = summarise(components, prev_start, prev_through)
    revenue_by_day = {d: v for d, v in components["revenue"].items() if start <= d <= through and v > 0}
    best_day = max(revenue_by_day.items(), key=lambda kv: kv[1]) if revenue_by_day else None
    return {
        "business": business, "start": start, "end": end, "through": through,
        "partial": end >= today, "pl": current, "previous": previous,
        "prev_start": prev_start, "prev_through": prev_through,
        "revenue_change": _change(current["revenue"], previous["revenue"]),
        "net_change": _change(current["net_profit"], previous["net_profit"]),
        "sales_count": Sale.objects.filter(business=business, date__range=(start, through)).count(),
        "best_day": best_day,
        "other_costs": current["supplies"] + current["write_offs"],
    }


def build_brief(business, day=None):
    """Structured brief for ``day`` (default yesterday), read through scoped managers."""
    from expenses.models import Expense
    from inventory.models import FinishedGood, RawMaterial
    from production.models import Order
    from production.variance import variance_report
    from sales.models import Sale, SaleItem
    from .finance_views import _cash_account_balances, _finance_open_items

    today = timezone.localdate()
    day = day or today - timedelta(days=1)
    _m, line_total, _cogs, _units = _sale_expressions(prefix="")
    sales = Sale.objects.filter(business=business, date=day)
    sales_total = SaleItem.objects.filter(sale__in=sales).aggregate(t=Sum(line_total))["t"] or ZERO
    expenses = Expense.objects.filter(business=business, date=day).aggregate(t=Sum("amount"), n=Count("pk"))
    cash = sum((a._calculated_balance for a in _cash_account_balances(business, active_only=True)), ZERO)
    open_items = _finance_open_items(business)
    owing = {}
    for row in open_items["outstanding_sales"]:
        owing[row["sale"].customer or "Walk-in"] = owing.get(row["sale"].customer or "Walk-in", ZERO) + row["balance"]
    top_debtors = sorted(owing.items(), key=lambda kv: -kv[1])[:3]
    low_raw = RawMaterial.objects.filter(business=business, reorder_level__gt=0, stock__lte=F("reorder_level")).count()
    low_goods = FinishedGood.objects.filter(business=business, stock__isnull=False, reorder_level__gt=0,
                                            stock__lte=F("reorder_level")).count()
    pending = Order.objects.filter(business=business, status="pending").count()
    review = variance_report(business, today.replace(day=1), today)["totals"]["review_count"]
    cutover = ledger.latest_cutover(business)
    return {
        "business": business, "day": day,
        "sales_total": sales_total, "sales_count": sales.count(),
        "expenses_total": expenses["t"] or ZERO, "expenses_count": expenses["n"],
        "cash": cash, "receivables": open_items["receivables"], "payables": open_items["payables"],
        "top_debtors": top_debtors, "low_stock": low_raw + low_goods, "pending_orders": pending,
        "recipe_review": review,
        "backlog_open": bool(cutover and cutover.status == "open"),
    }


def time_greeting(now=None):
    """Greet for the viewing/sending time in the active application timezone."""
    hour = timezone.localtime(now).hour
    return 'Good morning' if hour < 12 else 'Good afternoon' if hour < 17 else 'Good evening'


def render_text(brief, today=None, *, now=None):
    b = brief["business"]
    today = today or timezone.localdate()
    is_yesterday = brief['day'] == today - timedelta(days=1)
    greeting = f"*{time_greeting(now)}, {b.name}*" if is_yesterday else f"*{b.name}: daily brief*"
    if 'executive_sections' in brief:
        permissions = brief['report_permissions']
        required = ['sales', 'finance', 'expenses', 'inventory'] + (['production'] if b.uses_production else [])
        from .executive_brief import full_text
        # Keep the familiar overview only when all its contributing areas are visible.
        if brief.get('selected_sections') is not None or not all(permissions[module] for module in required):
            return f"{greeting}\n{brief['day']:%a %d %b %Y}\n\n" + full_text(brief)
        original = dict(brief)
        original.pop('executive_sections')
        return render_text(original, today, now=now) + '\n\n*Full business report*\n' + full_text(brief)
    label = day_label(brief["day"], today)
    heading = f"{label}, {brief['day']:%a %d %b}" if label in {"Yesterday", "Today so far"} else f"{brief['day']:%a %d %b %Y}"
    lines = [greeting, heading, ""]
    lines.append(f"• Sales: {_money(b, brief['sales_total'])} from {brief['sales_count']} sale{'s' if brief['sales_count'] != 1 else ''}")
    lines.append(f"• Expenses: {_money(b, brief['expenses_total'])}")
    lines.append(f"• Cash on hand: {_money(b, brief['cash'])}")
    lines.append("")
    owed = f"Owed to you: {_money(b, brief['receivables'])}"
    if brief["top_debtors"]:
        owed += " (" + ", ".join(f"{name} {_money(b, amt)}" for name, amt in brief["top_debtors"]) + ")"
    lines.append(owed)
    creditors = brief.get('top_creditors', [])
    payable_text = f"You owe: {_money(b, brief['payables'])}"
    if creditors:
        payable_text += ' (' + ', '.join(f'{name} {_money(b, amount)}' for name, amount in creditors[:3]) + ')'
    lines.append(payable_text)
    attention = []
    if brief["low_stock"]:
        attention.append(f"{brief['low_stock']} item{'s' if brief['low_stock'] != 1 else ''} low on stock")
    if b.uses_production and brief["pending_orders"]:
        attention.append(f"{brief['pending_orders']} order{'s' if brief['pending_orders'] != 1 else ''} awaiting approval")
    if b.uses_production and brief["recipe_review"]:
        attention.append(f"{brief['recipe_review']} ingredient{'s' if brief['recipe_review'] != 1 else ''} off recipe this month")
    if brief["backlog_open"]:
        attention.append("backlog still open")
    lines.append("")
    lines.append("Needs attention: " + ("; ".join(attention) if attention else "nothing urgent"))
    if brief["day"] < today - timedelta(days=1):
        lines.append("")
        lines.append("Sales and expenses are for the date above; cash, owed, owing and stock are current.")
    return "\n".join(lines)


def trend(business, change, as_percent):
    if change["direction"] == "flat":
        return "no change"
    arrow = "▲" if change["direction"] == "up" else "▼"
    if as_percent and change["pct"] is not None:
        return f"{arrow} {abs(change['pct']):g}%"
    return f"{arrow} {_money(business, abs(change['amount']))}"


def weekly_rows(brief):
    """The week's profit and loss as display rows: ``kind`` is line, sub, subtotal or total."""
    b, pl = brief["business"], brief["pl"]
    money = lambda value: _money(b, value)
    rows = [{"label": "Revenue (paid sales)", "value": money(pl["revenue"]), "kind": "line"},
            {"label": "Cost of goods", "value": money(pl["cogs"]), "kind": "line"},
            {"label": "Gross profit", "value": money(pl["gross_profit"]), "kind": "subtotal",
             "note": f"{pl['gross_margin']:g}% margin" if pl["gross_margin"] is not None else ""},
            {"label": "Expenses", "value": money(pl["operating_expenses"]), "kind": "line"}]
    rows += [{"label": line["label"], "value": money(line["amount"]), "kind": "sub"} for line in pl["expense_lines"][:3]]
    if len(pl["expense_lines"]) > 3:
        rest = sum((line["amount"] for line in pl["expense_lines"][3:]), ZERO)
        rows.append({"label": "Other categories", "value": money(rest), "kind": "sub"})
    if pl["payroll"]:
        rows.append({"label": "Payroll", "value": money(pl["payroll"]), "kind": "line"})
    if brief["other_costs"]:
        rows.append({"label": "Stock supplies and write-offs", "value": money(brief["other_costs"]), "kind": "line"})
    rows.append({"label": "Net loss" if pl["net_profit"] < 0 else "Net profit", "value": money(pl["net_profit"]), "kind": "total",
                 "note": f"{pl['net_margin']:g}% margin" if pl["net_margin"] is not None else ""})
    return rows


def render_weekly_text(brief):
    b, pl = brief["business"], brief["pl"]
    if 'executive_sections' in brief:
        from .executive_brief import full_text
        if brief.get('selected_sections') is not None or not brief['report_permissions']['finance']:
            return f"*{b.name}: weekly summary*\n{week_label(brief['start'], brief['end'])}\n\n" + full_text(brief)
        original = dict(brief)
        original.pop('executive_sections')
        return render_weekly_text(original) + '\n\n*Full business report*\n' + full_text(brief)
    money = lambda value: _money(b, value)
    title = "weekly summary, so far" if brief["partial"] else "weekly summary"
    net_label = "Net loss" if pl["net_profit"] < 0 else "Net profit"
    gross = f" ({pl['gross_margin']:g}%)" if pl["gross_margin"] is not None else ""
    margin = f" ({pl['net_margin']:g}% margin)" if pl["net_margin"] is not None else ""
    lines = [f"*{b.name}: {title}*", week_label(brief["start"], brief["end"]), ""]
    lines += [f"• Revenue (paid sales): {money(pl['revenue'])}", f"• Cost of goods: {money(pl['cogs'])}",
              f"• Gross profit: {money(pl['gross_profit'])}{gross}", f"• Expenses: {money(pl['operating_expenses'])}"]
    if pl["payroll"]:
        lines.append(f"• Payroll: {money(pl['payroll'])}")
    if brief["other_costs"]:
        lines.append(f"• Stock supplies and write-offs: {money(brief['other_costs'])}")
    lines.append(f"• *{net_label}: {money(abs(pl['net_profit']))}*{margin}")
    lines.append("")
    when = "the same days last week" if brief["partial"] else "last week"
    lines.append(f"Against {when}: revenue {trend(b, brief['revenue_change'], True)}; "
                 f"{net_label.lower()} {trend(b, brief['net_change'], False)}")
    lines.append(f"{brief['sales_count']} sale{'s' if brief['sales_count'] != 1 else ''} recorded"
                 + (f"; best day {brief['best_day'][0]:%a %d %b} ({money(brief['best_day'][1])})" if brief["best_day"] else ""))
    if pl["unpaid_sales"]:
        lines.append(f"Not counted as revenue yet: {money(pl['unpaid_sales'])} of unpaid sales.")
    if pl["unpaid_expenses"]:
        lines.append(f"Of the expenses, {money(pl['unpaid_expenses'])} is still unpaid.")
    return "\n".join(lines)


# Register exactly this text as a *utility* template in WhatsApp Manager.
TEMPLATE_BODY = (
    "Good morning {{1}}. Yesterday ({{2}}): sales {{3}}, expenses {{4}}. "
    "Cash on hand {{5}}. Owed to you {{6}}; you owe {{7}}. {{8}}"
)
TEMPLATE_PARAM_COUNT = 8


def template_parameters(brief):
    """The eight values for TEMPLATE_BODY, each a single short line."""
    b = brief["business"]
    sales = f"{_money(b, brief['sales_total'])} from {brief['sales_count']} sale{'s' if brief['sales_count'] != 1 else ''}"
    attention = []
    if brief["low_stock"]:
        attention.append(f"{brief['low_stock']} low on stock")
    if b.uses_production and brief["pending_orders"]:
        attention.append(f"{brief['pending_orders']} order{'s' if brief['pending_orders'] != 1 else ''} to approve")
    if b.uses_production and brief["recipe_review"]:
        attention.append(f"{brief['recipe_review']} off recipe")
    values = [
        b.name, f"{brief['day']:%a %d %b}", sales, _money(b, brief["expenses_total"]), _money(b, brief["cash"]),
        _money(b, brief["receivables"]), _money(b, brief["payables"]),
        ("Needs attention: " + ", ".join(attention)) if attention else "Nothing urgent",
    ]
    permissions = brief.get('report_permissions')
    if permissions:
        for index, module in [(2, 'sales'), (3, 'expenses'), (4, 'finance'), (5, 'finance'), (6, 'finance')]:
            if not permissions[module]:
                values[index] = 'Not shared'
        # An approved template remains compact; full manual text and emails carry all sections.
        if not (permissions['inventory'] and permissions['production']):
            values[7] = 'Open the report for permitted alerts'
    selected = brief.get('selected_sections')
    if selected is not None:
        selected = set(selected)
        for index, groups in [(2, {'sales'}), (3, {'profit', 'expenses'}), (4, {'position'}), (5, {'position'}), (6, {'position'})]:
            if not groups.intersection(selected):
                values[index] = 'Not included'
        values[7] = 'Open the report for your selected alerts'
    return values


def normalise_number(raw):
    """Digits only, country code included. Returns '' when it cannot be a phone number."""
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    return digits if 8 <= len(digits) <= 15 else ""


def send_whatsapp_brief(pref, brief):
    """Send the brief as a template message. Returns (status, error) and never raises."""
    from whatsapp_storefront.meta import MetaWhatsAppError, send_template
    from whatsapp_storefront.models import WhatsAppConnection
    from whatsapp_storefront.security import WhatsAppTokenError
    if not pref.whatsapp_consent_at:
        return "no_consent", "No consent recorded."
    if not pref.whatsapp_number:
        return "no_number", "No WhatsApp number saved."
    connection = WhatsAppConnection.raw_objects.filter(
        business=pref.business, status=WhatsAppConnection.STATUS_CONNECTED).first()
    if connection is None:
        return "no_connection", "WhatsApp is not connected for this business."
    if not connection.brief_template_name:
        return "no_template", "No approved message template is set for the brief."
    try:
        send_template(connection, pref.whatsapp_number, connection.brief_template_name,
                      connection.brief_template_language, template_parameters(brief))
    except (MetaWhatsAppError, WhatsAppTokenError) as exc:
        return "failed", str(exc)[:255]
    except Exception:  # a network or encoding surprise must not stop other members' briefs
        logger.exception("WhatsApp brief crashed for business %s", pref.business_id)
        return "failed", "Unexpected error while sending."
    return "sent", ""


def whatsapp_link(text, phone=""):
    """A tap-to-send link. With a number it opens that chat; without, WhatsApp asks who to send to."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return f"https://wa.me/{digits}?text={quote(text)}" if digits else f"https://wa.me/?text={quote(text)}"


def send_due_briefs(now=None):
    """Deliver each opted-in member's brief once per local day, after SEND_AFTER_HOUR.

    Members who opted in to the weekly summary also get the last completed week once.

    Returns the number of messages delivered (emails plus WhatsApp messages).
    WhatsApp is attempted at most once a day per member; if it fails, the email goes
    instead so the member is not left without their brief.
    """
    from accounts.models import UserBusiness
    from accounts.services import user_has_permission
    from .executive_brief import enrich
    from .context import set_current_business
    from .models import DailyBriefPreference
    now = now or timezone.localtime()
    if now.hour < SEND_AFTER_HOUR:
        return 0
    today = now.date()
    delivered = 0
    week_start = default_week_start(today)
    due = (DailyBriefPreference.raw_objects.select_related("user", "business")
           .filter(Q(send_email=True) | Q(whatsapp_enabled=True) | Q(send_weekly_email=True)))
    for pref in due:
        wants_email = pref.send_email and pref.last_sent_on != today
        wants_whatsapp = pref.whatsapp_enabled and pref.last_whatsapp_on != today
        wants_weekly = pref.send_weekly_email and pref.last_weekly_for != week_start
        if not (wants_email or wants_whatsapp or wants_weekly):
            continue
        user, business = pref.user, pref.business
        if not user.is_active or not UserBusiness.objects.filter(user=user, business=business, active=True).exists():
            continue
        try:
            set_current_business(business)
            if not user_has_permission(user, business, 'dashboard', 'view'):
                continue
            if wants_email or wants_whatsapp:
                brief = build_brief(business)
                enrich(brief, brief["day"], brief["day"], user=user, selected_sections=(pref.report_sections or {}).get("daily"))
                text = render_text(brief, today, now=now)
                whatsapp_ok = False
                if wants_whatsapp:
                    status, error = send_whatsapp_brief(pref, brief)
                    DailyBriefPreference.raw_objects.filter(pk=pref.pk).update(
                        last_whatsapp_on=today, last_whatsapp_status=status, last_whatsapp_error=error)
                    whatsapp_ok = status == "sent"
                    delivered += int(whatsapp_ok)
                email_due = wants_email or (wants_whatsapp and not whatsapp_ok and pref.last_sent_on != today)
                if email_due and user.email:
                    send_mail(f"{business.name}: your daily brief", text.replace("*", ""), settings.DEFAULT_FROM_EMAIL, [user.email])
                    DailyBriefPreference.raw_objects.filter(pk=pref.pk).update(last_sent_on=today)
                    delivered += 1
            if wants_weekly and user.email:
                weekly = build_weekly_brief(business, week_start, today)
                enrich(weekly, weekly["start"], weekly["through"], user=user, selected_sections=(pref.report_sections or {}).get("weekly"))
                send_mail(f"{business.name}: your weekly summary ({week_label(weekly['start'], weekly['end'])})",
                          render_weekly_text(weekly).replace("*", ""), settings.DEFAULT_FROM_EMAIL, [user.email])
                DailyBriefPreference.raw_objects.filter(pk=pref.pk).update(last_weekly_for=week_start)
                delivered += 1
        except Exception:
            logger.exception("Daily brief failed for business %s user %s", business.pk, user.pk)
        finally:
            set_current_business(None)
    return delivered
