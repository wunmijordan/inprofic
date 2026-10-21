from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("procurement", "0004_supplier_directory")]
    operations = [migrations.AddField(
        model_name="purchaseorder", name="carried_from_date",
        field=models.DateField(blank=True, null=True),
    )]
