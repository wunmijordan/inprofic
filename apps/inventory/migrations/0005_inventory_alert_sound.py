from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0004_inventory_alerts"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventoryalertsettings",
            name="sound_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Play a persistent in-app sound while visible stock alerts need attention.",
            ),
        ),
        migrations.AddField(
            model_name="inventoryalertsettings",
            name="sound_repeat_minutes",
            field=models.PositiveSmallIntegerField(
                default=5,
                help_text="Repeat the stock alert sound while an unsnoozed condition remains visible. Use 0 to sound only when it becomes visible.",
            ),
        ),
    ]
