from datetime import datetime
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.utils import timezone

from .opening_hours import opening_status, validate_order_time


def _at(y, m, d, h, minute=0):
    return timezone.make_aware(datetime(y, m, d, h, minute), timezone.get_current_timezone())


def _settings(**overrides):
    hours = {str(day): {"open": "09:00", "close": "21:00"} for day in range(0, 6)}  # Sunday closed
    base = dict(
        opening_hours_enabled=True, opening_hours=hours,
        closed_scheduling_enabled=True, closed_scheduling_window_minutes=60,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class OpeningHoursTests(SimpleTestCase):
    # 10 Oct 2026 is a Saturday; the business is closed on Sunday.
    def test_open_and_closed_headlines(self):
        self.assertTrue(opening_status(_settings(), _at(2026, 10, 10, 12))["is_open"])
        closed = opening_status(_settings(), _at(2026, 10, 10, 22))
        self.assertFalse(closed["is_open"])
        self.assertIn("opens Monday 9:00 AM", closed["headline"])

    def test_overnight_hours_stay_open_after_midnight(self):
        hours = {"4": {"open": "18:00", "close": "02:00"}}  # Friday
        status = opening_status(_settings(opening_hours=hours), _at(2026, 10, 10, 1))
        self.assertTrue(status["is_open"])

    def test_disabled_hours_never_block(self):
        validate_order_time(_settings(opening_hours_enabled=False), None, now=_at(2026, 10, 10, 23))

    def test_closed_order_must_be_scheduled_inside_window(self):
        # Closed Saturday 22:00; next opening Monday 09:00 with a 60 minute lead.
        now, cfg = _at(2026, 10, 10, 22), _settings()
        validate_order_time(cfg, _at(2026, 10, 12, 10, 0), now=now)
        validate_order_time(cfg, _at(2026, 10, 12, 21, 0), now=now)
        for bad in (None, _at(2026, 10, 12, 9, 59), _at(2026, 10, 12, 21, 1)):
            with self.assertRaises(ValidationError):
                validate_order_time(cfg, bad, now=now)

    def test_open_preferred_time_stops_at_closing_or_moves_to_next_opening(self):
        now, cfg = _at(2026, 10, 10, 12), _settings()
        validate_order_time(cfg, None, now=now)  # ordering now needs no preferred time
        validate_order_time(cfg, _at(2026, 10, 10, 20, 30), now=now)  # earlier than closing
        validate_order_time(cfg, _at(2026, 10, 12, 10, 0), now=now)  # next opening day (Sunday is closed)
        for bad in (_at(2026, 10, 10, 21, 30), _at(2026, 10, 12, 9, 30), _at(2026, 10, 14, 10, 0)):
            with self.assertRaises(ValidationError):
                validate_order_time(cfg, bad, now=now)

    def test_lead_time_applies_inside_current_opening(self):
        now, cfg = _at(2026, 10, 10, 9, 20), _settings()
        with self.assertRaises(ValidationError):
            validate_order_time(cfg, _at(2026, 10, 10, 9, 45), now=now)
        validate_order_time(cfg, _at(2026, 10, 10, 10, 0), now=now)

    def test_window_is_configurable_and_next_day_scheduling_can_be_off(self):
        now = _at(2026, 10, 10, 22)
        validate_order_time(_settings(closed_scheduling_window_minutes=0), _at(2026, 10, 12, 9, 0), now=now)
        with self.assertRaises(ValidationError):
            validate_order_time(_settings(closed_scheduling_enabled=False), _at(2026, 10, 12, 10, 0), now=now)
        open_now = _at(2026, 10, 10, 12)
        with self.assertRaises(ValidationError):
            validate_order_time(_settings(closed_scheduling_enabled=False), _at(2026, 10, 12, 10, 0), now=open_now)
        validate_order_time(_settings(closed_scheduling_enabled=False), _at(2026, 10, 10, 15, 0), now=open_now)