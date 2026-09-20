from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0012_checkout_portion_snapshots"),
        ("inventory", "0008_individual_sale_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="commercecheckoutitem",
            name="individual_option",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="commerce_checkout_items", to="inventory.individualsaleoption"),
        ),
        migrations.AddField(
            model_name="commerceintakeitem",
            name="individual_option",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="commerce_intake_items", to="inventory.individualsaleoption"),
        ),
    ]
