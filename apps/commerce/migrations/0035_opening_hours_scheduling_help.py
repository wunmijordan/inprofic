import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("commerce", "0034_opening_hours"),
    ]

    operations = [
        migrations.AlterField(
            model_name="commercesettings",
            name="closed_scheduling_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Let customers schedule an order for the next opening day, not only for the rest of today.",
            ),
        ),
        migrations.AlterField(
            model_name="commercesettings",
            name="closed_scheduling_window_minutes",
            field=models.PositiveIntegerField(
                default=60,
                validators=[django.core.validators.MaxValueValidator(1440)],
                help_text="Scheduled and preferred times cannot be earlier than this many minutes after opening time, and cannot be later than closing time.",
            ),
        ),
    ]
