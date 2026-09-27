from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_scheduledjoblease"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="financialtransaction",
            index=models.Index(fields=["business", "date", "transaction_type"], name="fin_tx_biz_date_type_idx"),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["business", "model_name", "created_at"], name="audit_biz_model_time_idx"),
        ),
    ]
