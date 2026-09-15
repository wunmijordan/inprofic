from django.db import migrations, models


MODULE_CHOICES = [
    ("dashboard", "Dashboard"),
    ("inventory", "Inventory"),
    ("procurement", "Procurement"),
    ("production", "Production Orders"),
    ("sales", "Sales"),
    ("expenses", "Expenses"),
    ("finance", "Finance"),
    ("reports", "Reports"),
    ("users", "User Management"),
    ("commerce", "Commerce"),
    ("delivery", "Delivery"),
    ("delivery_rider", "Delivery Rider"),
    ("audit", "Audit Workspace"),
    ("pos", "In-Premise POS"),
]


def seed_delivery_rider_role(apps, schema_editor):
    Business = apps.get_model("core", "Business")
    Role = apps.get_model("accounts", "Role")
    RoleModulePermission = apps.get_model("accounts", "RoleModulePermission")

    for business in Business.objects.all().iterator():
        rider, _ = Role.objects.get_or_create(
            business=business,
            key="delivery_rider",
            defaults={
                "name": "Delivery Rider",
                "is_system": True,
                "active": True,
                "visible_to_admin": True,
            },
        )
        changed = []
        if rider.name != "Delivery Rider":
            rider.name = "Delivery Rider"; changed.append("name")
        if not rider.is_system:
            rider.is_system = True; changed.append("is_system")
        if not rider.active:
            rider.active = True; changed.append("active")
        if not rider.visible_to_admin:
            rider.visible_to_admin = True; changed.append("visible_to_admin")
        if changed:
            rider.save(update_fields=changed)

        RoleModulePermission.objects.update_or_create(
            role=rider,
            module="delivery_rider",
            defaults={"can_view": True, "can_edit": True},
        )
        # Purpose-specific rider access must remain isolated from the normal app.
        for module, _label in MODULE_CHOICES:
            if module == "delivery_rider":
                continue
            RoleModulePermission.objects.update_or_create(
                role=rider,
                module=module,
                defaults={"can_view": False, "can_edit": False},
            )

        # Business Admin retains operational oversight of the rider workspace.
        for role in Role.objects.filter(business=business, key__in=["business_admin", "superuser", "live_tester"]):
            RoleModulePermission.objects.update_or_create(
                role=role,
                module="delivery_rider",
                defaults={"can_view": True, "can_edit": True},
            )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_pos_direct_workspace"),
        ("core", "0001_baseline"),
    ]

    operations = [
        migrations.AlterField(
            model_name="rolemodulepermission",
            name="module",
            field=models.CharField(choices=MODULE_CHOICES, max_length=30),
        ),
        migrations.AlterField(
            model_name="usermodulepermission",
            name="module",
            field=models.CharField(choices=MODULE_CHOICES, max_length=30),
        ),
        migrations.AlterField(
            model_name="businessmoduleaccess",
            name="module",
            field=models.CharField(choices=MODULE_CHOICES, max_length=30),
        ),
        migrations.AlterField(
            model_name="subscriptionplanmodule",
            name="module",
            field=models.CharField(choices=MODULE_CHOICES, max_length=30),
        ),
        migrations.RunPython(seed_delivery_rider_role, migrations.RunPython.noop),
    ]
