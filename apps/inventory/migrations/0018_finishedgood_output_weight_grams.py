from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0017_alter_finishedgood_transferred_market_stock"),
    ]

    operations = [
        migrations.AddField(
            model_name="finishedgood",
            name="output_weight_grams",
            field=models.DecimalField(
                decimal_places=3,
                default=0,
                help_text="Finished weight of one base production unit in grams. Used to split shared base-material production across products.",
                max_digits=12,
            ),
        ),
    ]
