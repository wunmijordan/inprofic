from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("production", "0006_query_pattern_indexes"),
    ]

    operations = [
        migrations.AlterField(
            model_name="productioncostline",
            name="source",
            field=models.CharField(
                choices=[
                    ("latest_procurement", "Latest procurement"),
                    ("current_cost_fallback", "Current-cost fallback"),
                ],
                default="latest_procurement",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="productioncostsnapshot",
            name="cost_source",
            field=models.CharField(
                choices=[
                    ("latest_procurement", "Latest procurement"),
                    (
                        "latest_procurement_with_current_cost_fallback",
                        "Latest procurement with current-cost fallback",
                    ),
                ],
                default="latest_procurement",
                max_length=64,
            ),
        ),
    ]
