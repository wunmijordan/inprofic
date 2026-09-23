from django.db import migrations, models


ALERT_TUNE_CHOICES = [
    ("gentle_chime", "Gentle chime"),
    ("double_ping", "Double ping"),
    ("urgent_pulse", "Urgent pulse"),
    ("hard_buzzer", "Hard buzzer · aggressive"),
    ("alarm_buzzer", "Alarm buzzer · very aggressive"),
    ("audio_chime_1", "Chime 1"),
    ("audio_chime_2", "Chime 2"),
    ("audio_chime_3", "Chime 3"),
    ("audio_chime_4", "Chime 4"),
    ("audio_chime_5", "Chime 5"),
    ("audio_chime_6", "Chime 6"),
    ("audio_chime_7", "Chime 7"),
    ("audio_chime_8", "Chime 8"),
]


class Migration(migrations.Migration):
    dependencies = [("commerce", "0013_individual_sale_option_snapshots")]

    operations = [
        migrations.AlterField(
            model_name="commercesettings",
            name="notification_sound_tune",
            field=models.CharField(choices=ALERT_TUNE_CHOICES, default="double_ping", help_text="Foreground alert tune used by open INPROFIC pages and the installed app.", max_length=24),
        ),
        migrations.AlterField(
            model_name="deliverysettings",
            name="rider_alert_sound_tune",
            field=models.CharField(choices=ALERT_TUNE_CHOICES, default="urgent_pulse", help_text="Foreground alert tune used for rider-only notification sessions.", max_length=24),
        ),
    ]
