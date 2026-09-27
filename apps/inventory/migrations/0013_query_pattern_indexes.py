from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0012_stock_movement_performance_indexes"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="rawmaterialmeasurementchange",
            index=models.Index(fields=["business", "created_at"], name="rawchange_biz_time_idx"),
        ),
        migrations.AddIndex(
            model_name="stockadjustment",
            index=models.Index(fields=["business", "date"], name="stockadj_biz_date_idx"),
        ),
        migrations.AddIndex(
            model_name="operationalsupplydispense",
            index=models.Index(fields=["business", "date"], name="opsdisp_biz_date_idx"),
        ),
        migrations.AddIndex(
            model_name="distributionreturn",
            index=models.Index(fields=["business", "date"], name="distret_biz_date_idx"),
        ),
        migrations.AddIndex(
            model_name="rawmaterial",
            index=models.Index(fields=["business", "category", "name"], name="raw_biz_category_name_idx"),
        ),
        migrations.AddIndex(
            model_name="finishedgood",
            index=models.Index(fields=["business", "source_type", "name"], name="fg_biz_source_name_idx"),
        ),
        migrations.AddIndex(
            model_name="marketstocklot",
            index=models.Index(fields=["business", "finished_good", "active", "received_date"], name="mktlot_biz_fg_active_idx"),
        ),
        migrations.AddIndex(
            model_name="marketstockmovement",
            index=models.Index(fields=["business", "date"], name="mktmove_biz_date_idx"),
        ),
        migrations.AddIndex(
            model_name="inventoryalertstate",
            index=models.Index(fields=["business", "user", "is_active"], name="inv_alert_user_active_idx"),
        ),
    ]
