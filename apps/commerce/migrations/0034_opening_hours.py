import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("commerce", "0033_delivery_car_on_by_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="commercesettings",
            name="opening_hours_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Show opening hours on the hosted storefront and apply the closed-hours ordering rules below.",
            ),
        ),
        migrations.AddField(
            model_name="commercesettings",
            name="opening_hours",
            field=models.JSONField(
                blank=True, default=dict,
                help_text="Weekly schedule keyed by weekday (0 = Monday). A missing day means closed all day.",
            ),
        ),
        migrations.AddField(
            model_name="commercesettings",
            name="closed_scheduling_enabled",
            field=models.BooleanField(
                default=True,
                help_text="While closed, let customers browse and schedule an order for when you next open.",
            ),
        ),
        migrations.AddField(
            model_name="commercesettings",
            name="closed_scheduling_window_minutes",
            field=models.PositiveIntegerField(
                default=60,
                validators=[django.core.validators.MaxValueValidator(1440)],
                help_text="A scheduled order must be set for no later than this many minutes after the business next opens.",
            ),
        ),
    ]
