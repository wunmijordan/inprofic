from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("expenses", "0002_expense_performance_index"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="expense",
            index=models.Index(fields=["business", "date"], name="expense_biz_date_idx"),
        ),
        migrations.AddIndex(
            model_name="expensepayment",
            index=models.Index(fields=["business", "date"], name="exp_pay_biz_date_idx"),
        ),
    ]
