from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("procurement", "0001_baseline"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="purchaseorder",
            index=models.Index(fields=["business", "status", "payment_status"], name="po_biz_status_pay_idx"),
        ),
        migrations.AddIndex(
            model_name="purchaseorder",
            index=models.Index(fields=["business", "date"], name="po_biz_date_idx"),
        ),
    ]
