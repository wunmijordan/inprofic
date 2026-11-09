"""Morning brief screen: read it, email opt-in, WhatsApp delivery set-up."""
import re
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.mail import send_mail
from django.shortcuts import redirect, render
from django.utils import timezone
from django.urls import reverse
from urllib.parse import urlencode
from django.views.decorators.http import require_http_methods

from . import daily_brief, ledger
from .models import DailyBriefPreference
from .quantities import has_value

TEST_SENDS_PER_DAY = 3
EMAIL_SENDS_PER_DAY = 5
TEMPLATE_NAME = re.compile(r"[a-z0-9_]{1,100}")
TEMPLATE_LANGUAGE = re.compile(r"[a-z]{2,3}(_[A-Z]{2})?")


def _connection(business):
    from whatsapp_storefront.models import WhatsAppConnection
    return WhatsAppConnection.raw_objects.filter(business=business, status=WhatsAppConnection.STATUS_CONNECTED).first()


def _save_whatsapp(request, pref):
    number = daily_brief.normalise_number(request.POST.get("whatsapp_number"))
    consent = request.POST.get("whatsapp_consent") == "1"
    enable = request.POST.get("whatsapp_enabled") == "1"
    if (consent or enable) and not number:
        raise ValueError("Enter your WhatsApp number with the country code, for example 2348012345678.")
    if enable and not consent:
        raise ValueError("Tick the consent box to receive your brief on WhatsApp.")
    pref.whatsapp_number = number
    if consent and not pref.whatsapp_consent_at:
        pref.whatsapp_consent_at = timezone.now()
    if not consent:
        pref.whatsapp_consent_at, enable = None, False
    pref.whatsapp_enabled = enable
    pref.save()
    messages.success(request, "WhatsApp delivery is on." if enable else "WhatsApp delivery is off.")


def _save_template(request, business):
    if not ledger.can_manage(request.user, business):
        raise PermissionError
    connection = _connection(business)
    if connection is None:
        raise ValueError("Connect WhatsApp for this business first.")
    name = (request.POST.get("brief_template_name") or "").strip()
    language = (request.POST.get("brief_template_language") or "en").strip()
    if name and not TEMPLATE_NAME.fullmatch(name):
        raise ValueError("A template name uses lowercase letters, numbers and underscores only.")
    if not TEMPLATE_LANGUAGE.fullmatch(language):
        raise ValueError("Enter a language code such as en or en_US.")
    connection.brief_template_name, connection.brief_template_language = name, language
    connection.save(update_fields=["brief_template_name", "brief_template_language", "updated_at"])
    messages.success(request, "Brief template saved." if name else "Brief template cleared.")


def _send_test(request, pref, business):
    key = f"brief-test:{request.user.pk}:{timezone.localdate()}"
    used = cache.get(key, 0)
    if used >= TEST_SENDS_PER_DAY:
        raise ValueError(f"You can send up to {TEST_SENDS_PER_DAY} test messages a day. Try again tomorrow.")
    cache.set(key, used + 1, 60 * 60 * 26)
    status, error = daily_brief.send_whatsapp_brief(pref, _report(business, {}, user=request.user)["brief"])
    if status == "sent":
        messages.success(request, "Test sent. Check WhatsApp on your phone.")
    else:
        raise ValueError(f"The test was not sent: {error}")


def _report(business, params, today=None, *, user=None):
    """What the page shows and shares: the daily brief for a chosen date, or a week's P&L."""
    from .executive_brief import enrich, selected_report_sections
    today = today or timezone.localdate()
    weekly = params.get("view") == "weekly"
    mode = 'weekly' if weekly else 'daily'
    selection = None
    if user is not None:
        preferences = DailyBriefPreference.raw_objects.filter(business=business, user=user).values_list('report_sections', flat=True).first() or {}
        selection = selected_report_sections(preferences, mode)
    if weekly:
        start, note = daily_brief.parse_week(params.get("date"), today)
        brief = daily_brief.build_weekly_brief(business, start, today)
        enrich(brief, brief["start"], brief["through"], user=user, selected_sections=selection)
        text = daily_brief.render_weekly_text(brief)
        this_week = daily_brief.week_start_of(today)
        prev_start, next_start = start - timedelta(days=7), start + timedelta(days=7)
        return {
            "view": "weekly", "brief": brief, "text": text, "note": note, "anchor": start,
            "subject": f"{business.name}: weekly summary ({daily_brief.week_label(brief['start'], brief['end'])})",
            "prev_date": prev_start, "next_date": next_start if next_start <= this_week else None,
            "is_default": start == daily_brief.default_week_start(today),
            "label": daily_brief.week_label(brief["start"], brief["end"]),
            "rows": daily_brief.weekly_rows(brief),
            "visible_rows": [row for row in daily_brief.weekly_rows(brief) if has_value(row["value"])],
            "revenue_trend": daily_brief.trend(business, brief["revenue_change"], True),
            "net_trend": daily_brief.trend(business, brief["net_change"], False),
            "against": "the same days last week" if brief["partial"] else "last week",
            "net_value": daily_brief.weekly_rows(brief)[-1]["value"],
            "unpaid_sales_text": daily_brief._money(business, brief["pl"]["unpaid_sales"]),
            "unpaid_expenses_text": daily_brief._money(business, brief["pl"]["unpaid_expenses"]),
        }
    day, note = daily_brief.parse_day(params.get("date"), today)
    brief = daily_brief.build_brief(business, day)
    enrich(brief, day, day, user=user, selected_sections=selection)
    text = daily_brief.render_text(brief, today)
    return {
        "view": "daily", "brief": brief, "text": text, "note": note, "anchor": day,
        "subject": f"{business.name}: brief for {day:%a %d %b %Y}",
        "prev_date": day - timedelta(days=1), "next_date": day + timedelta(days=1) if day < today else None,
        "is_default": day == today - timedelta(days=1),
        "label": f"{day:%A %d %B %Y}",
    }


def report_dashboard_url(request, report):
    finance_view = report["view"] == "weekly" and bool(report["brief"].get("executive_pl"))
    dashboard_params = {"group": "finance" if finance_view else "pulse",
                        "section": "profit_loss" if finance_view else "indicators",
                        "start": str(report["brief"]["start"] if report["view"] == "weekly" else report["anchor"]),
                        "end": str(report["brief"]["end"] if report["view"] == "weekly" else report["anchor"])}
    return request.build_absolute_uri(reverse("complete_dashboard") + "?" + urlencode(dashboard_params))


def _email_now(request, business):
    """One-off email of the brief or weekly summary currently on screen."""
    if not request.user.email:
        raise ValueError("Add an email address to your profile so the brief can reach you.")
    key = f"brief-email:{request.user.pk}:{timezone.localdate()}"
    used = cache.get(key, 0)
    if used >= EMAIL_SENDS_PER_DAY:
        raise ValueError(f"You can email yourself up to {EMAIL_SENDS_PER_DAY} briefs a day. Try again tomorrow.")
    cache.set(key, used + 1, 60 * 60 * 26)
    report = _report(business, request.GET, user=request.user)
    send_mail(report["subject"], report["text"].replace("*", ""), settings.DEFAULT_FROM_EMAIL, [request.user.email])
    messages.success(request, f"Sent to {request.user.email}.")


@login_required
@require_http_methods(["GET", "POST"])
def daily_brief_page(request):
    from accounts.services import user_has_permission
    if not user_has_permission(request.user, request.business, 'dashboard', 'view'):
        return render(request, '403.html', status=403)
    business = request.business
    pref, _ = DailyBriefPreference.raw_objects.get_or_create(
        business=business, user=request.user, defaults={"created_by": request.user})
    if request.method == "POST":
        action = request.POST.get("action", "email")
        try:
            if action == 'report_sections':
                from .executive_brief import SECTION_LABELS
                selected = request.POST.getlist('section')
                if any(value not in SECTION_LABELS for value in selected):
                    raise ValueError('Choose only the listed report sections.')
                mode = 'weekly' if request.GET.get('view') == 'weekly' else 'daily'
                preferences = dict(pref.report_sections or {})
                preferences[mode] = list(dict.fromkeys(selected))
                known = preferences.get('known_sections', {})
                known = dict(known) if isinstance(known, dict) else {}
                known[mode] = list(SECTION_LABELS)
                preferences['known_sections'] = known
                pref.report_sections = preferences
                pref.save(update_fields=['report_sections', 'updated_at'])
                messages.success(request, 'Report contents saved for your account in this business.')
            elif action == "whatsapp":
                _save_whatsapp(request, pref)
            elif action == "template":
                _save_template(request, business)
            elif action == "test":
                _send_test(request, pref, business)
            elif action == "email_now":
                _email_now(request, business)
            elif action == "weekly":
                pref.send_weekly_email = request.POST.get("send_weekly_email") == "1"
                # Start from the next Monday rather than emailing last week straight away.
                pref.last_weekly_for = daily_brief.default_week_start() if pref.send_weekly_email else pref.last_weekly_for
                pref.save(update_fields=["send_weekly_email", "last_weekly_for", "updated_at"])
                if pref.send_weekly_email and not request.user.email:
                    messages.warning(request, "Saved, but add an email address to your profile so the summary can reach you.")
                else:
                    messages.success(request, "Weekly summary email turned on." if pref.send_weekly_email else "Weekly summary email turned off.")
            else:
                pref.send_email = request.POST.get("send_email") == "1"
                pref.save(update_fields=["send_email", "updated_at"])
                if pref.send_email and not request.user.email:
                    messages.warning(request, "Saved, but add an email address to your profile so the brief can reach you.")
                else:
                    messages.success(request, "Morning email turned on." if pref.send_email else "Morning email turned off.")
        except PermissionError:
            return render(request, "403.html", status=403)
        except ValueError as exc:
            messages.error(request, str(exc))
        # Forms post to the page's own URL, so the chosen date/week survives the redirect.
        return redirect(request.get_full_path())
    report = _report(business, request.GET, user=request.user)
    text = report["text"] + "\n\nOpen Executive dashboard (sign-in required):\n" + report_dashboard_url(request, report)
    connection = _connection(business)
    return render(request, "core/daily_brief.html", {
        **report, "share_text": text, "share_image_url": reverse("daily_brief_snapshot") + "?" + request.GET.urlencode(), "pref": pref, "today": timezone.localdate(),
        "yesterday": timezone.localdate() - timedelta(days=1),
        "wa_link": daily_brief.whatsapp_link(text, request.user.phone),
        "connection": connection, "template_body": daily_brief.TEMPLATE_BODY,
        "template_ready": bool(connection and connection.brief_template_name),
        "can_manage": ledger.can_manage(request.user, business),
    })
