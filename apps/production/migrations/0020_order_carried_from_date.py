from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("production", "0019_production_day_custom_color")]
    operations = [migrations.AddField(
        model_name="order", name="carried_from_date",
        field=models.DateField(blank=True, null=True),
    )]
