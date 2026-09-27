from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0004_sale_performance_indexes"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="customer",
            index=models.Index(fields=["business", "active", "name"], name="customer_biz_active_idx"),
        ),
        migrations.AddIndex(
            model_name="sale",
            index=models.Index(fields=["customer_master", "source", "transaction_type"], name="sale_customer_state_idx"),
        ),
        migrations.AddIndex(
            model_name="customerpayment",
            index=models.Index(fields=["business", "date"], name="custpay_biz_date_idx"),
        ),
    ]
