from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("production", "0007_production_cost_source_lengths"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="production_basis",
            field=models.CharField(
                choices=[
                    ("product_quantity", "Product quantity"),
                    ("base_material", "Base material"),
                ],
                default="product_quantity",
                help_text=(
                    "Choose whether this production order is sized by product quantities "
                    "or a shared base-material allocation."
                ),
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="base_material_quantity",
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                default=0,
                help_text=(
                    "Total quantity of the shared base material allocated across all "
                    "products in this production order."
                ),
                max_digits=14,
            ),
        ),
    ]
