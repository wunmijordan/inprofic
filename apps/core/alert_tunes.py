"""Stable alert tune choices shared by Commerce, Inventory and Delivery rider alerts.

Synthesized tones remain available without static media.  The bundled chime choices
use local project audio files so businesses can pick a more distinctive sound.
Foreground browsers may require one prior user interaction before custom audio can
play; background Web Push still uses the operating-system notification sound because
web browsers do not expose a portable custom notification-sound API.
"""

ALERT_TUNE_GENTLE = "gentle_chime"
ALERT_TUNE_DOUBLE = "double_ping"
ALERT_TUNE_URGENT = "urgent_pulse"
ALERT_TUNE_BUZZER = "hard_buzzer"
ALERT_TUNE_ALARM = "alarm_buzzer"

ALERT_TUNE_CHIME_1 = "audio_chime_1"
ALERT_TUNE_CHIME_2 = "audio_chime_2"
ALERT_TUNE_CHIME_3 = "audio_chime_3"
ALERT_TUNE_CHIME_4 = "audio_chime_4"
ALERT_TUNE_CHIME_5 = "audio_chime_5"
ALERT_TUNE_CHIME_6 = "audio_chime_6"
ALERT_TUNE_CHIME_7 = "audio_chime_7"
ALERT_TUNE_CHIME_8 = "audio_chime_8"

ALERT_TUNE_CHOICES = [
    (ALERT_TUNE_GENTLE, "Gentle chime"),
    (ALERT_TUNE_DOUBLE, "Double ping"),
    (ALERT_TUNE_URGENT, "Urgent pulse"),
    (ALERT_TUNE_BUZZER, "Hard buzzer · aggressive"),
    (ALERT_TUNE_ALARM, "Alarm buzzer · very aggressive"),
    (ALERT_TUNE_CHIME_1, "Chime 1"),
    (ALERT_TUNE_CHIME_2, "Chime 2"),
    (ALERT_TUNE_CHIME_3, "Chime 3"),
    (ALERT_TUNE_CHIME_4, "Chime 4"),
    (ALERT_TUNE_CHIME_5, "Chime 5"),
    (ALERT_TUNE_CHIME_6, "Chime 6"),
    (ALERT_TUNE_CHIME_7, "Chime 7"),
    (ALERT_TUNE_CHIME_8, "Chime 8"),
]

ALERT_TUNE_FILE_MAP = {
    ALERT_TUNE_CHIME_1: "core/audio/alerts/chime1.mp3",
    ALERT_TUNE_CHIME_2: "core/audio/alerts/chime2.mp3",
    ALERT_TUNE_CHIME_3: "core/audio/alerts/chime3.mp3",
    ALERT_TUNE_CHIME_4: "core/audio/alerts/chime4.mp3",
    ALERT_TUNE_CHIME_5: "core/audio/alerts/chime5.mp3",
    ALERT_TUNE_CHIME_6: "core/audio/alerts/chime6.wav",
    ALERT_TUNE_CHIME_7: "core/audio/alerts/chime7.wav",
    ALERT_TUNE_CHIME_8: "core/audio/alerts/chime8.wav",
}

ALERT_TUNE_CODES = {code for code, _label in ALERT_TUNE_CHOICES}
