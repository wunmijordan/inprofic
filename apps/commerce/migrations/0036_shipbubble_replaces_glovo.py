from django.db import migrations, models


def retire_glovo_accounts(apps, schema_editor):
    """Glovo is replaced by Shipbubble.

    Glovo credentials cannot be reused with Shipbubble, so every existing Glovo
    account becomes an inactive, manual custom partner. Deliveries, quotes and
    history that point at it are kept intact and still display normally.
    """
    Account = apps.get_model("commerce", "DeliveryProviderAccount")
    for account in Account.objects.filter(provider_code="glovo"):
        taken = set(Account.objects.filter(business_id=account.business_id).values_list("name", flat=True))
        name = "Archived delivery partner"
        if name in taken:
            name = f"Archived delivery partner {account.pk}"[:100]
        account.provider_code = "generic"
        account.active = False
        account.auto_dispatch = False
        account.use_live_quotes = False
        account.name = name
        account.save(update_fields=["provider_code", "active", "auto_dispatch", "use_live_quotes", "name"])

class Migration(migrations.Migration):

    dependencies = [
        ("commerce", "0035_opening_hours_scheduling_help"),
    ]

    operations = [
        # Data first: rows must leave the old "glovo" value before the choices change.
        migrations.RunPython(retire_glovo_accounts, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="deliveryprovideraccount",
            name="provider_code",
            field=models.CharField(choices=[("generic", "Custom delivery partner"), ("shipbubble", "Shipbubble couriers")], default="generic", max_length=20),
        ),
    ]
