from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("retail", "0005_branch_workspace_manager")]
    operations = [
        migrations.AddField(
            model_name="productpromotion",
            name="channels",
            field=models.JSONField(default=list, blank=True),
        ),
        migrations.AddField(
            model_name="productpromotion",
            name="product_prices",
            field=models.JSONField(default=dict, blank=True, editable=False),
        ),
    ]
