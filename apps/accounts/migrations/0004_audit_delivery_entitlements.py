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
    ("audit", "Audit Workspace"),
    ("pos", "In-Premise POS"),
]


def seed_audit_delivery(apps, schema_editor):
    Business = apps.get_model("core", "Business")
    Role = apps.get_model("accounts", "Role")
    RoleModulePermission = apps.get_model("accounts", "RoleModulePermission")
    BusinessModuleAccess = apps.get_model("accounts", "BusinessModuleAccess")
    SubscriptionPlan = apps.get_model("accounts", "SubscriptionPlan")
    SubscriptionPlanModule = apps.get_model("accounts", "SubscriptionPlanModule")

    for business in Business.objects.all().iterator():
        # These are intentionally disabled at the commercial ceiling until the
        # founder enables them on a plan from the Founder Console.
        for module in ("audit", "delivery"):
            BusinessModuleAccess.objects.get_or_create(
                business=business,
                module=module,
                defaults={"enabled": False, "source": "default"},
            )

        auditor, _ = Role.objects.get_or_create(
            business=business,
            key="auditor",
            defaults={"name": "External Auditor", "is_system": True, "active": True, "visible_to_admin": True},
        )
        coordinator, _ = Role.objects.get_or_create(
            business=business,
            key="delivery_coordinator",
            defaults={"name": "Delivery Coordinator", "is_system": True, "active": True, "visible_to_admin": True},
        )

        roles = list(Role.objects.filter(business=business))
        for role in roles:
            for module in ("audit", "delivery"):
                can_view = can_edit = False
                if role.key in {"business_admin", "superuser", "live_tester"}:
                    can_view = can_edit = True
                elif role.key == "auditor" and module == "audit":
                    can_view, can_edit = True, False
                elif role.key == "delivery_coordinator" and module == "delivery":
                    can_view = can_edit = True
                RoleModulePermission.objects.get_or_create(
                    role=role,
                    module=module,
                    defaults={"can_view": can_view, "can_edit": can_edit},
                )

    for plan in SubscriptionPlan.objects.all().iterator():
        for module in ("audit", "delivery"):
            SubscriptionPlanModule.objects.get_or_create(
                plan=plan,
                module=module,
                defaults={"enabled": False, "level": "none"},
            )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_current_labels"),
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
        migrations.RunPython(seed_audit_delivery, migrations.RunPython.noop),
    ]
