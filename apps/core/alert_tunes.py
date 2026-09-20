"""Stable alert tune choices shared by Commerce, Inventory and Delivery rider alerts.

The browser foreground implementation synthesizes these patterns with Web Audio so
there are no codec/file-format differences between Android, iOS, macOS and Windows.
Background Web Push uses the operating system notification sound because browsers do
not expose a portable custom notification-sound API.
"""

ALERT_TUNE_GENTLE = "gentle_chime"
ALERT_TUNE_DOUBLE = "double_ping"
ALERT_TUNE_URGENT = "urgent_pulse"
ALERT_TUNE_BUZZER = "hard_buzzer"
ALERT_TUNE_ALARM = "alarm_buzzer"

ALERT_TUNE_CHOICES = [
    (ALERT_TUNE_GENTLE, "Gentle chime"),
    (ALERT_TUNE_DOUBLE, "Double ping"),
    (ALERT_TUNE_URGENT, "Urgent pulse"),
    (ALERT_TUNE_BUZZER, "Hard buzzer · aggressive"),
    (ALERT_TUNE_ALARM, "Alarm buzzer · very aggressive"),
]

ALERT_TUNE_CODES = {code for code, _label in ALERT_TUNE_CHOICES}
