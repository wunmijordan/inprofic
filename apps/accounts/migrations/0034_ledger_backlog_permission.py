from django.db import migrations, models


MODULE_CHOICES = [
    ("dashboard", "Dashboard"), ("inventory", "Inventory"),
    ("procurement", "Procurement"), ("production", "Production Orders"),
    ("sales", "Sales"), ("expenses", "Expenses"), ("finance", "Finance"),
    ("reports", "Reports"), ("users", "User Management"), ("commerce", "Commerce"),
    ("delivery", "Delivery"), ("delivery_rider", "Delivery Rider"),
    ("kitchen", "Production Entry"), ("audit", "Audit Workspace"),
    ("ledger", "Ledger Backlog Entry"), ("pos", "In-Premise POS"),
]


def seed_ledger_permission(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    RoleModulePermission = apps.get_model("accounts", "RoleModulePermission")
    for role in Role.objects.all().iterator():
        RoleModulePermission.objects.get_or_create(
            role_id=role.pk, module="ledger",
            defaults={
                "can_view": role.key == "business_admin",
                "can_edit": role.key == "business_admin",
            },
        )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0033_businessresetsnapshot")]
    operations = [
        migrations.AlterField(model_name="rolemodulepermission", name="module", field=models.CharField(choices=MODULE_CHOICES, max_length=30)),
        migrations.AlterField(model_name="usermodulepermission", name="module", field=models.CharField(choices=MODULE_CHOICES, max_length=30)),
        migrations.AlterField(model_name="businessmoduleaccess", name="module", field=models.CharField(choices=MODULE_CHOICES, max_length=30)),
        migrations.AlterField(model_name="subscriptionplanmodule", name="module", field=models.CharField(choices=MODULE_CHOICES, max_length=30)),
        migrations.RunPython(seed_ledger_permission, migrations.RunPython.noop),
    ]
