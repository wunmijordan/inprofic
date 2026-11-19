from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("retail", "0003_quotationline_channel_unit_price")]
    operations = [
        migrations.AddField(
            model_name="customerquotation",
            name="title",
            field=models.CharField(default="", max_length=160),
        )
    ]
