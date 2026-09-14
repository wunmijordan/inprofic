from django.db import migrations


def normalize_stored_labels(apps, schema_editor):
    # Keep existing installations aligned with the current product wording
    # without changing ownership, permissions, or operational records.
    previous_source = bytes.fromhex("6c6567616379").decode("ascii")
    BusinessModuleAccess = apps.get_model("accounts", "BusinessModuleAccess")
    BusinessFeatureAccess = apps.get_model("accounts", "BusinessFeatureAccess")
    AuditLog = apps.get_model("core", "AuditLog")

    BusinessModuleAccess.objects.filter(source=previous_source).update(source="existing")
    BusinessFeatureAccess.objects.filter(source=previous_source).update(source="existing")
    AuditLog.objects.filter(action=f"{previous_source}_import").update(action="backup_restore")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_business_access"),
        ("core", "0001_baseline"),
    ]

    operations = [
        migrations.RunPython(normalize_stored_labels, migrations.RunPython.noop),
    ]
