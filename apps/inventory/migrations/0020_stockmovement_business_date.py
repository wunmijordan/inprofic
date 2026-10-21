from django.db import migrations, models
from django.utils import timezone


def backfill_business_date(apps, schema_editor):
    StockMovement = apps.get_model("inventory", "StockMovement")
    batch = []
    for row in StockMovement.objects.filter(business_date__isnull=True).only("pk", "occurred_at").iterator(chunk_size=1000):
        if row.occurred_at:
            row.business_date = timezone.localtime(row.occurred_at).date()
            batch.append(row)
        if len(batch) >= 1000:
            StockMovement.objects.bulk_update(batch, ["business_date"], batch_size=1000)
            batch = []
    if batch:
        StockMovement.objects.bulk_update(batch, ["business_date"], batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [("inventory", "0019_rawmaterial_output_grams_per_usage_unit")]
    operations = [
        migrations.AddField(
            model_name="stockmovement", name="business_date",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(backfill_business_date, migrations.RunPython.noop),
    ]
