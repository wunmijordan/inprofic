from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0008_userbusiness_onboarding_tour"),
        ("inventory", "0003_product_categories_measurement_changes"),
    ]

    operations = [
        migrations.CreateModel(
            name="InventoryAlertSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("enabled", models.BooleanField(default=True)),
                ("raw_warning_enabled", models.BooleanField(default=True)),
                ("raw_warning_repeat_minutes", models.PositiveIntegerField(default=240)),
                ("raw_low_enabled", models.BooleanField(default=True)),
                ("raw_low_repeat_minutes", models.PositiveIntegerField(default=60)),
                ("finished_warning_enabled", models.BooleanField(default=True)),
                ("finished_warning_repeat_minutes", models.PositiveIntegerField(default=240)),
                ("finished_low_enabled", models.BooleanField(default=True)),
                ("finished_low_repeat_minutes", models.PositiveIntegerField(default=60)),
                ("poll_seconds", models.PositiveIntegerField(default=45)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name_plural": "inventory alert settings"},
        ),
        migrations.AddConstraint(
            model_name="inventoryalertsettings",
            constraint=models.UniqueConstraint(fields=("business",), name="one_inventory_alert_settings_per_business"),
        ),
        migrations.CreateModel(
            name="InventoryAlertState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("alert_type", models.CharField(choices=[("raw_warning", "Raw material warning"), ("raw_low", "Raw material low stock"), ("finished_warning", "Finished good warning"), ("finished_low", "Finished good low stock")], max_length=32)),
                ("object_id", models.PositiveBigIntegerField()),
                ("is_active", models.BooleanField(default=True)),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="inventory_alert_states", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["alert_type", "object_id"]},
        ),
        migrations.AddConstraint(
            model_name="inventoryalertstate",
            constraint=models.UniqueConstraint(fields=("business", "user", "alert_type", "object_id"), name="unique_inventory_alert_state_per_user_item"),
        ),
    ]
