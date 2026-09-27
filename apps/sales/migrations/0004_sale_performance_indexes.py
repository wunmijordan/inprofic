from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0003_commerce_portion_commercial_snapshots"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="sale",
            index=models.Index(fields=["business", "transaction_type", "source"], name="sale_biz_state_src_idx"),
        ),
        migrations.AddIndex(
            model_name="sale",
            index=models.Index(fields=["business", "date"], name="sale_biz_date_idx"),
        ),
    ]
