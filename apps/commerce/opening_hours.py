"""Business opening hours for the hosted storefront and checkout.

Hours are stored on ``CommerceSettings.opening_hours`` as
``{"0": {"open": "09:00", "close": "21:00"}, ...}`` keyed by weekday
(0 = Monday). A missing weekday means closed all day. A ``close`` that is
earlier than (or equal to) ``open`` runs past midnight into the next day.
All times are read in the project's active time zone.
"""
from datetime import date, datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

WEEKDAYS = [
    ("0", "mon", "Monday"),
    ("1", "tue", "Tuesday"),
    ("2", "wed", "Wednesday"),
    ("3", "thu", "Thursday"),
    ("4", "fri", "Friday"),
    ("5", "sat", "Saturday"),
    ("6", "sun", "Sunday"),
]
DEFAULT_OPEN = "09:00"
DEFAULT_CLOSE = "18:00"


def parse_clock(value):
    """Return a ``time`` for ``HH:MM`` text, or ``None`` when invalid."""
    try:
        hour, minute = str(value).strip().split(":")[:2]
        return time(int(hour), int(minute))
    except (ValueError, TypeError):
        return None


def format_clock(value):
    return datetime.combine(date(2000, 1, 1), value).strftime("%I:%M %p").lstrip("0")


def _aware(day, clock):
    return timezone.make_aware(datetime.combine(day, clock), timezone.get_current_timezone())


def _intervals(hours, now):
    """Open intervals as ``(start, end)`` around ``now``, sorted by start."""
    today = timezone.localtime(now).date()
    rows = []
    for offset in range(-1, 9):
        day = today + timedelta(days=offset)
        entry = (hours or {}).get(str(day.weekday()))
        if not entry:
            continue
        opens, closes = parse_clock(entry.get("open")), parse_clock(entry.get("close"))
        if opens is None or closes is None:
            continue
        start, end = _aware(day, opens), _aware(day, closes)
        if end <= start:
            end += timedelta(days=1)
        rows.append((start, end))
    return sorted(rows)


def _day_word(moment, now):
    local_day, today = timezone.localtime(moment).date(), timezone.localtime(now).date()
    if local_day == today:
        return "today"
    if local_day == today + timedelta(days=1):
        return "tomorrow"
    return timezone.localtime(moment).strftime("%A")


def _input_value(moment):
    return timezone.localtime(moment).strftime("%Y-%m-%dT%H:%M")


def _slot_label(start, end, now):
    return (
        f"{_day_word(start, now)} {format_clock(timezone.localtime(start).time())} – "
        f"{format_clock(timezone.localtime(end).time())}"
    )


def opening_status(settings, now=None):
    """Describe whether the business is open and which times can be scheduled.

    ``slots`` lists the time ranges a customer may pick for a preferred or
    scheduled time: the rest of the current opening (if open) and the next
    opening. Each range starts ``closed_scheduling_window_minutes`` after its
    opening time (never earlier) and ends at closing time.
    """
    now = now or timezone.now()
    status = {
        "enabled": bool(getattr(settings, "opening_hours_enabled", False)),
        "is_open": True, "closes_at": None, "next_open": None,
        "scheduling_allowed": False, "headline": "", "detail": "", "weekly": [], "slots": [],
        "schedule_min": "", "schedule_max": "", "schedule_hint": "", "window_minutes": 0,
    }
    if not status["enabled"]:
        return status

    hours = settings.opening_hours or {}
    window = int(settings.closed_scheduling_window_minutes or 0)
    status["window_minutes"] = window
    intervals = _intervals(hours, now)
    current = next(((s, e) for s, e in intervals if s <= now < e), None)
    upcoming = next(((s, e) for s, e in intervals if s > now), None)
    today_key = str(timezone.localtime(now).weekday())
    for key, _short, name in WEEKDAYS:
        entry = hours.get(key)
        opens = parse_clock(entry.get("open")) if entry else None
        closes = parse_clock(entry.get("close")) if entry else None
        closed = opens is None or closes is None
        status["weekly"].append({
            "name": name, "today": key == today_key, "closed": closed,
            "open_label": "" if closed else format_clock(opens),
            "close_label": "" if closed else format_clock(closes),
        })

    # Ranges a customer may schedule into. The later opening is only offered
    # when the owner allows scheduling ahead.
    candidates = []
    if current:
        candidates.append(current)
    if upcoming and settings.closed_scheduling_enabled:
        candidates.append(upcoming)
    for start, end in candidates:
        earliest = start + timedelta(minutes=window)
        if earliest <= end:
            status["slots"].append({
                "min": earliest, "max": end,
                "min_input": _input_value(earliest), "max_input": _input_value(end),
                "label": _slot_label(earliest, end, now),
            })
    if status["slots"]:
        status["scheduling_allowed"] = True
        status["schedule_min"] = status["slots"][0]["min_input"]
        status["schedule_max"] = status["slots"][-1]["max_input"]
        status["schedule_hint"] = " or ".join(slot["label"] for slot in status["slots"])

    if current:
        status["closes_at"] = current[1]
        status["headline"] = f"Open now, till {format_clock(timezone.localtime(current[1]).time())}"
        return status

    status.update(is_open=False, next_open=upcoming[0] if upcoming else None)
    if not upcoming:
        status["headline"] = "Closed"
        status["detail"] = "We are not taking orders right now."
        return status
    opens_at = upcoming[0]
    status["headline"] = f"Closed, till {_day_word(opens_at, now)} {format_clock(timezone.localtime(opens_at).time())}"
    if status["slots"]:
        status["detail"] = f"You can browse the menu and schedule an order for {status['schedule_hint']}."
    else:
        status["detail"] = "You can browse the menu, but orders are not accepted while we are closed."
    return status


def validate_order_time(settings, requested_at, *, business_name="This business", now=None):
    """Raise ``ValidationError`` if a customer order cannot be accepted.

    Hours disabled: always fine. Open: an order with no preferred time is fine;
    a preferred time must fall in an allowed range. Closed: the order needs a
    scheduled time inside an allowed range.
    """
    now = now or timezone.now()
    status = opening_status(settings, now)
    if not status["enabled"]:
        return
    if requested_at is None:
        if status["is_open"]:
            return
        if not status["scheduling_allowed"]:
            raise ValidationError(f"{business_name} is closed and is not accepting orders. {status['headline']}.")
        raise ValidationError(f"{business_name} is closed. Choose a time between {status['schedule_hint']} to schedule this order.")
    if any(slot["min"] <= requested_at <= slot["max"] for slot in status["slots"]):
        return
    if not status["slots"]:
        raise ValidationError(f"{business_name} is closed and is not accepting orders. {status['headline']}.")
    prefix = f"{business_name} is open until {format_clock(timezone.localtime(status['closes_at']).time())}. " if status["is_open"] else f"{business_name} is closed. "
    raise ValidationError(
        f"{prefix}The preferred time must be {status['schedule_hint']}, "
        f"and no earlier than {status['window_minutes']} minute{'s' if status['window_minutes'] != 1 else ''} after opening."
    )


def clean_hours(raw):
    """Validate/normalise a weekly schedule dict; returns the cleaned dict."""
    cleaned = {}
    for key, _short, name in WEEKDAYS:
        entry = (raw or {}).get(key)
        if not entry:
            continue
        opens, closes = parse_clock(entry.get("open")), parse_clock(entry.get("close"))
        if opens is None or closes is None:
            raise ValidationError(f"{name}: enter valid opening and closing times.")
        if opens == closes:
            raise ValidationError(f"{name}: opening and closing time cannot be the same.")
        cleaned[key] = {"open": opens.strftime("%H:%M"), "close": closes.strftime("%H:%M")}
    return cleaned