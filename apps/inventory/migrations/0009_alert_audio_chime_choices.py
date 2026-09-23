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
    dependencies = [("inventory", "0008_individual_sale_options")]

    operations = [
        migrations.AlterField(
            model_name="inventoryalertsettings",
            name="sound_tune",
            field=models.CharField(choices=ALERT_TUNE_CHOICES, default="urgent_pulse", help_text="Foreground alert tune used while unsnoozed stock conditions need attention.", max_length=24),
        ),
    ]
