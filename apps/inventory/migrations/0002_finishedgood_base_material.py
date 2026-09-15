from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("inventory", "0001_baseline")]

    operations = [
        migrations.AddField(
            model_name="finishedgood",
            name="base_material",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="base_for_products",
                to="inventory.rawmaterial",
                help_text=(
                    "Optional. Choose the main recipe material that can be used to size a production run, "
                    "for example rice in a kitchen or flour in a bakery."
                ),
            ),
        ),
    ]
