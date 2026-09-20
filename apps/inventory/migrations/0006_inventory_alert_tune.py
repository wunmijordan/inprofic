from django.db import migrations, models


TUNE_CHOICES = [
    ("gentle_chime", "Gentle chime"),
    ("double_ping", "Double ping"),
    ("urgent_pulse", "Urgent pulse"),
    ("hard_buzzer", "Hard buzzer · aggressive"),
    ("alarm_buzzer", "Alarm buzzer · very aggressive"),
]


class Migration(migrations.Migration):
    dependencies = [("inventory", "0005_inventory_alert_sound")]

    operations = [
        migrations.AddField(
            model_name="inventoryalertsettings",
            name="sound_tune",
            field=models.CharField(
                choices=TUNE_CHOICES,
                default="urgent_pulse",
                help_text="Foreground alert tune used while unsnoozed stock conditions need attention.",
                max_length=24,
            ),
        ),
    ]
