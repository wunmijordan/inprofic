from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("retail", "0007_productpromotion_portions_tooltips")]
    operations = [
        migrations.RenameField(
            model_name="productpromotion", old_name="kind", new_name="mode"
        )
    ]
