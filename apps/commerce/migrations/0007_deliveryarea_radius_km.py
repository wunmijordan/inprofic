from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


def seed_area_radii(apps, schema_editor):
    DeliveryArea = apps.get_model("commerce", "DeliveryArea")
    for area in DeliveryArea.objects.select_related("rate_band").iterator():
        maximum = area.rate_band.max_distance_km if area.rate_band_id else None
        if maximum and maximum > 0:
            area.radius_km = min(maximum, Decimal("500.00"))
            area.save(update_fields=["radius_km"])


class Migration(migrations.Migration):

    dependencies = [("commerce", "0006_alter_deliveryprovideraccount_business_and_more")]

    operations = [
        migrations.AddField(
            model_name="deliveryarea",
            name="radius_km",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("5.00"),
                help_text="Maximum distance from this destination centre that an address may be delivered to.",
                max_digits=8,
                validators=[MinValueValidator(Decimal("0.10")), MaxValueValidator(Decimal("500.00"))],
            ),
        ),
        migrations.AddField(model_name="deliveryarea", name="extension_ne_km", field=models.DecimalField(decimal_places=2, default=0, max_digits=8, validators=[MinValueValidator(0), MaxValueValidator(500)])),
        migrations.AddField(model_name="deliveryarea", name="extension_se_km", field=models.DecimalField(decimal_places=2, default=0, max_digits=8, validators=[MinValueValidator(0), MaxValueValidator(500)])),
        migrations.AddField(model_name="deliveryarea", name="extension_sw_km", field=models.DecimalField(decimal_places=2, default=0, max_digits=8, validators=[MinValueValidator(0), MaxValueValidator(500)])),
        migrations.AddField(model_name="deliveryarea", name="extension_nw_km", field=models.DecimalField(decimal_places=2, default=0, max_digits=8, validators=[MinValueValidator(0), MaxValueValidator(500)])),
        migrations.RunPython(seed_area_radii, migrations.RunPython.noop),
    ]
