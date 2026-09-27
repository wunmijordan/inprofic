from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("expenses", "0001_baseline"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="expense",
            index=models.Index(fields=["business", "payment_status", "date"], name="expense_biz_pay_date_idx"),
        ),
    ]
