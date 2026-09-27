from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("procurement", "0002_purchase_order_performance_indexes"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="purchaseorder",
            index=models.Index(fields=["business", "status", "received_date"], name="po_biz_status_recv_idx"),
        ),
        migrations.AddIndex(
            model_name="rawmaterialcostsnapshot",
            index=models.Index(fields=["raw_material", "-effective_date", "-id"], name="rawcost_mat_date_idx"),
        ),
        migrations.AddIndex(
            model_name="rawmaterialcostsnapshot",
            index=models.Index(fields=["business", "effective_date"], name="rawcost_biz_date_idx"),
        ),
        migrations.AddIndex(
            model_name="supplierpayment",
            index=models.Index(fields=["business", "date"], name="suppay_biz_date_idx"),
        ),
    ]
