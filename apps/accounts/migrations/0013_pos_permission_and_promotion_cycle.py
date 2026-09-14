from django.db import migrations, models


def migrate_storefront_access(apps, schema_editor):
    Business = apps.get_model("core", "Business")
    Role = apps.get_model("accounts", "Role")
    RoleModulePermission = apps.get_model("accounts", "RoleModulePermission")
    UserBusiness = apps.get_model("accounts", "UserBusiness")
    UserModulePermission = apps.get_model("accounts", "UserModulePermission")

    # Create a first-class POS operator role for every existing tenant. It can
    # enter the main dashboard and operate the POS, but it does not inherit
    # broad Commerce administration access. Role permissions remain editable
    # after migration from Roles & Access.
    for business_id in Business.objects.values_list("id", flat=True).iterator():
        role = Role.objects.filter(business_id=business_id, key="pos_operator").first()
        if role is None:
            role_name = "In-Premise POS"
            if Role.objects.filter(business_id=business_id, name=role_name).exists():
                role_name = "In-Premise POS (System)"
            role = Role.objects.create(
                business_id=business_id, key="pos_operator", name=role_name,
                is_system=True, active=True, visible_to_admin=True,
            )
        RoleModulePermission.objects.update_or_create(
            role_id=role.id, module="dashboard",
            defaults={"can_view": True, "can_edit": False},
        )
        RoleModulePermission.objects.update_or_create(
            role_id=role.id, module="pos",
            defaults={"can_view": True, "can_edit": True},
        )
        for module, _label in MODULE_CHOICES:
            if module in {"dashboard", "pos"}:
                continue
            RoleModulePermission.objects.get_or_create(
                role_id=role.id, module=module,
                defaults={"can_view": False, "can_edit": False},
            )

    # Preserve legacy per-user checkbox grants as hidden overrides so deploying
    # this migration never revokes access unexpectedly. New assignments are
    # role-based; POS is intentionally omitted from the per-user permission UI.
    for membership_id in UserBusiness.objects.filter(commerce_storefront_access=True).values_list("id", flat=True).iterator():
        UserModulePermission.objects.update_or_create(
            membership_id=membership_id,
            module="pos",
            defaults={"can_view": True, "can_edit": True},
        )


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
    ("pos", "In-Premise POS"),
]


class Migration(migrations.Migration):
    dependencies = [("accounts", "0012_subscriptionpromotion_all_plans")]

    operations = [
        migrations.AddField(
            model_name="subscriptionpromotion",
            name="billing_cycle",
            field=models.CharField(
                choices=[
                    ("both", "Monthly and yearly"),
                    ("monthly", "Monthly only"),
                    ("yearly", "Yearly only"),
                ],
                default="both",
                help_text="Limit this promotion to monthly payments, yearly payments, or both.",
                max_length=12,
            ),
        ),
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
        migrations.RunPython(migrate_storefront_access, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="userbusiness",
            name="commerce_storefront_access",
        ),
    ]
