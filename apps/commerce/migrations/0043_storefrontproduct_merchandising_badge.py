from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("commerce", "0042_commercecheckoutsession_recorded_at")]
    operations = [
        migrations.AddField(
            model_name="storefrontproduct",
            name="merchandising_badge",
            field=models.CharField(
                blank=True,
                default="",
                max_length=20,
                choices=[
                    ("featured", "Featured"),
                    ("new_arrival", "New arrival"),
                    ("staff_pick", "Staff pick"),
                ],
                help_text="Optional card ribbon. Otherwise, recent paid sales determine Bestseller automatically.",
            ),
        )
    ]
