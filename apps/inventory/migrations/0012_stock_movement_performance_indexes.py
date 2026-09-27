from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0011_market_stock_cross_app"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="stockmovement",
            index=models.Index(fields=["business", "movement_type", "occurred_at"], name="stock_biz_type_time_idx"),
        ),
        migrations.AddIndex(
            model_name="stockmovement",
            index=models.Index(fields=["finished_good", "movement_type", "occurred_at"], name="stock_fg_type_time_idx"),
        ),
    ]
