from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0002_finishedgood_base_material"),
        ("production", "0003_current"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="production_basis",
            field=models.CharField(
                choices=[("product_quantity", "Product quantity"), ("base_material", "Base material")],
                default="product_quantity",
                help_text="Choose whether this production run is sized by product quantity or by the product's base material.",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="base_material_quantity",
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                default=0,
                help_text="Quantity of the product's selected base material to use for this production run.",
                max_digits=14,
            ),
        ),
    ]
