from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0006_sale_service_mode_sale_table_reference"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sale",
            name="payment_method",
            field=models.CharField(
                blank=True,
                choices=[("Cash", "Cash"), ("Card", "Card"), ("Transfer", "Transfer")],
                default="Cash",
                help_text="Payment method actually received. May be blank while a sale remains an unpaid receivable.",
                max_length=10,
            ),
        ),
    ]
