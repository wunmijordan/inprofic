import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_backfill_business_profile_contacts"),
    ]

    operations = [
        migrations.AddField(
            model_name="business",
            name="fiscal_year_start_month",
            field=models.PositiveSmallIntegerField(
                default=1,
                validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(12)],
                help_text="Month your business year begins (1 = January). Profit and loss periods are counted from here.",
            ),
        ),
        migrations.AddField(
            model_name="business",
            name="profit_period",
            field=models.CharField(
                choices=[("year", "Business year"), ("half", "Half year"), ("quarter", "Quarter")],
                default="year", max_length=10,
                help_text="How long each profit and loss reporting period runs.",
            ),
        ),
    ]
