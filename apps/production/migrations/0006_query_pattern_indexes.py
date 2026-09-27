from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("production", "0005_commerce_portion_commercial_snapshots"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="order",
            index=models.Index(fields=["business", "status", "completed_date"], name="order_biz_status_done_idx"),
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(fields=["business", "order_type", "status", "date"], name="order_biz_type_state_idx"),
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(fields=["business", "-date"], name="order_biz_recent_idx"),
        ),
        migrations.AddIndex(
            model_name="productionrun",
            index=models.Index(fields=["business", "status", "date"], name="prun_biz_status_date_idx"),
        ),
        migrations.AddIndex(
            model_name="productionrun",
            index=models.Index(fields=["business", "-date"], name="prun_biz_recent_idx"),
        ),
        migrations.AddIndex(
            model_name="productionrunmaterial",
            index=models.Index(fields=["business", "raw_material"], name="prunmat_biz_raw_idx"),
        ),
        migrations.AddIndex(
            model_name="productionbatch",
            index=models.Index(fields=["business", "production_date"], name="pbatch_biz_date_idx"),
        ),
        migrations.AddIndex(
            model_name="productionbatch",
            index=models.Index(fields=["business", "finished_good", "is_reversed", "production_date"], name="pbatch_biz_fg_rev_idx"),
        ),
        migrations.AddIndex(
            model_name="productioncostsnapshot",
            index=models.Index(fields=["finished_good", "-production_date", "-id"], name="pcost_fg_date_idx"),
        ),
        migrations.AddIndex(
            model_name="productioncostsnapshot",
            index=models.Index(fields=["business", "production_date"], name="pcost_biz_date_idx"),
        ),
    ]
