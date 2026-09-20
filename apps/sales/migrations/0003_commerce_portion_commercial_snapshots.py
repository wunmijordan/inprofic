from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0002_current"),
    ]

    operations = [
        migrations.AddField(model_name="saleitem", name="commercial_quantity", field=models.DecimalField(blank=True, decimal_places=2, help_text="Optional customer-facing quantity snapshot for commerce portion/bulk sales.", max_digits=14, null=True)),
        migrations.AddField(model_name="saleitem", name="commercial_unit", field=models.CharField(blank=True, default="", help_text="Customer-facing unit snapshot for commerce portion/bulk sales.", max_length=100)),
        migrations.AddField(model_name="saleitem", name="commercial_unit_price", field=models.DecimalField(blank=True, decimal_places=2, help_text="Exact customer-facing unit price snapshot for commerce portion/bulk sales.", max_digits=14, null=True)),
    ]
