from django.db import migrations, models


class Migration(migrations.Migration):
    """The founder's optional delivery-partner switch now controls Shipbubble (it replaces Glovo)."""

    dependencies = [
        ("accounts", "0030_billing_cycles_quarterly_biannual"),
    ]

    operations = [
        migrations.RenameField(
            model_name="platformintegrationsettings",
            old_name="glovo_enabled",
            new_name="shipbubble_enabled",
        ),
        migrations.AlterField(
            model_name="platformintegrationsettings",
            name="shipbubble_enabled",
            field=models.BooleanField(default=False, help_text="Expose and allow the optional Shipbubble delivery-provider integration across INPROFIC."),
        ),
    ]
