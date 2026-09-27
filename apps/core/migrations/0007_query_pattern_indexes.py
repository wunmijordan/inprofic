from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_performance_indexes"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="cashaccount",
            index=models.Index(fields=["business", "active", "account_type"], name="cash_biz_active_type_idx"),
        ),
        migrations.AddIndex(
            model_name="financialtransaction",
            index=models.Index(fields=["business", "transaction_type", "date"], name="fin_tx_biz_type_date_idx"),
        ),
    ]
