import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("production", "0018_production_day_setting"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="color_tag",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional production-day color marker shown on this order and its products.",
                max_length=7,
                validators=[django.core.validators.RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Choose a six-digit hex color.")],
            ),
        ),
        migrations.AlterField(
            model_name="productiondaysetting",
            name="color",
            field=models.CharField(
                max_length=7,
                validators=[django.core.validators.RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Choose a six-digit hex color.")],
            ),
        ),
    ]
