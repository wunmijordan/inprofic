from django.db import migrations, models


def classify_non_production_products(apps, schema_editor):
    FinishedGood = apps.get_model("inventory", "FinishedGood")
    Business = apps.get_model("core", "Business")
    non_production_ids = Business.objects.filter(vertical__in=["wholesale", "retail"]).values_list("id", flat=True)
    FinishedGood.objects.filter(business_id__in=non_production_ids).update(source_type="purchased_for_resale")


class Migration(migrations.Migration):
    dependencies = [("inventory", "0007_widen_usage_conversion_precision")]

    operations = [
        migrations.AddField(
            model_name="finishedgood",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("made_in_house", "Made in-house"),
                    ("purchased_for_resale", "Purchased for resale"),
                ],
                default="made_in_house",
                help_text="Whether this sellable product is made by the business or bought from a supplier for resale.",
                max_length=24,
            ),
        ),
        migrations.RunPython(classify_non_production_products, migrations.RunPython.noop),
    ]
