from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0029_copy_legacy_receipt_branding_to_business_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="deliveryassignment",
            name="manual_rider_name",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="deliveryassignment",
            name="manual_rider_phone",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="deliveryassignment",
            name="manual_rider_vehicle",
            field=models.CharField(blank=True, default="", max_length=60),
        ),
    ]
