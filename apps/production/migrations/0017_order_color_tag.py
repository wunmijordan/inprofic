from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("production", "0016_order_base_material_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="color_tag",
            field=models.CharField(
                blank=True,
                choices=[
                    ("#ef4444", "Red"),
                    ("#f97316", "Orange"),
                    ("#eab308", "Yellow"),
                    ("#22c55e", "Green"),
                    ("#06b6d4", "Cyan"),
                    ("#3b82f6", "Blue"),
                    ("#8b5cf6", "Violet"),
                    ("#ec4899", "Pink"),
                ],
                default="",
                help_text="Optional production-day color marker shown on this order and its products.",
                max_length=7,
            ),
        ),
    ]
