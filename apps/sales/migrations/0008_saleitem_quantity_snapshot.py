from decimal import Decimal
from django.db import migrations, models


def freeze_existing(apps, schema_editor):
    SaleItem = apps.get_model('sales', 'SaleItem')
    alias = schema_editor.connection.alias
    pending = []
    for item in SaleItem.objects.using(alias).select_related('finished_good').iterator(chunk_size=1000):
        # Historical yields were not stored. Freeze the best available value,
        # explicitly marking its provenance rather than claiming reconstruction.
        item.units_per_batch_snapshot = item.finished_good.units_per_batch or Decimal('1')
        item.quantity_snapshot_source = 'legacy_current_yield'
        pending.append(item)
        if len(pending) >= 1000:
            SaleItem.objects.using(alias).bulk_update(pending, ['units_per_batch_snapshot', 'quantity_snapshot_source'])
            pending = []
    if pending:
        SaleItem.objects.using(alias).bulk_update(pending, ['units_per_batch_snapshot', 'quantity_snapshot_source'])


class Migration(migrations.Migration):
    dependencies = [('sales', '0007_saleitem_commerce_addon_name')]
    operations = [
        migrations.AddField(model_name='saleitem', name='units_per_batch_snapshot', field=models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True, editable=False)),
        migrations.AddField(model_name='saleitem', name='quantity_snapshot_source', field=models.CharField(max_length=30, default='sale', editable=False)),
        migrations.RunPython(freeze_existing, migrations.RunPython.noop),
    ]
