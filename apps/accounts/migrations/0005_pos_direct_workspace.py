from django.db import migrations


def isolate_pos_role(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    RoleModulePermission = apps.get_model("accounts", "RoleModulePermission")
    for role in Role.objects.filter(key="pos_operator").iterator():
        RoleModulePermission.objects.update_or_create(
            role=role,
            module="pos",
            defaults={"can_view": True, "can_edit": True},
        )
        RoleModulePermission.objects.update_or_create(
            role=role,
            module="dashboard",
            defaults={"can_view": False, "can_edit": False},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_audit_delivery_entitlements"),
    ]

    operations = [
        migrations.RunPython(isolate_pos_role, migrations.RunPython.noop),
    ]
