from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0008_userbusiness_onboarding_tour"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformIntegrationSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("glovo_enabled", models.BooleanField(default=False, help_text="Expose and allow the optional Glovo delivery-provider integration across INPROFIC.")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="platform_integration_settings_updates", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "platform integration setting",
                "verbose_name_plural": "platform integration settings",
            },
        ),
    ]
