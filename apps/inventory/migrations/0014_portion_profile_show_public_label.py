from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0013_query_pattern_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="productportionprofile",
            name="show_public_label",
            field=models.BooleanField(
                default=True,
                help_text="Show the portion label (e.g. '1 pack of 4 buns') to customers on the storefront, POS and headless API. Pricing, stock conversion and ordering are unaffected.",
            ),
        ),
    ]
