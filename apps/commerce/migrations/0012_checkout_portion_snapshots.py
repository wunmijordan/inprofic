from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0007_product_portions_bulk_packs"),
        ("commerce", "0011_alert_tunes_rider_inventory_push"),
    ]

    operations = [
        migrations.AddField(
            model_name="commercecheckoutitem",
            name="bulk_pack",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="commerce_checkout_items", to="inventory.bulkpackprofile"),
        ),
        migrations.AddField(model_name="commercecheckoutitem", name="customer_unit", field=models.CharField(blank=True, default="", max_length=100)),
        migrations.AddField(model_name="commercecheckoutitem", name="fulfilment_quantity_per_unit", field=models.DecimalField(decimal_places=3, default=1, max_digits=14)),
        migrations.AddField(model_name="commercecheckoutitem", name="contents_snapshot", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(
            model_name="commerceintakeitem",
            name="bulk_pack",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="commerce_intake_items", to="inventory.bulkpackprofile"),
        ),
        migrations.AddField(model_name="commerceintakeitem", name="customer_unit", field=models.CharField(blank=True, default="", max_length=100)),
        migrations.AddField(model_name="commerceintakeitem", name="fulfilment_quantity_per_unit", field=models.DecimalField(decimal_places=3, default=1, max_digits=14)),
        migrations.AddField(model_name="commerceintakeitem", name="contents_snapshot", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="commerceintakeitem", name="assembly_consumed_at", field=models.DateTimeField(blank=True, null=True)),
    ]
