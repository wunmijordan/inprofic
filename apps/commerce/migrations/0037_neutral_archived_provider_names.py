from django.db import migrations


def neutralize_archived_provider_names(apps, schema_editor):
    Account = apps.get_model("commerce", "DeliveryProviderAccount")
    accounts = Account.objects.filter(
        provider_code="generic",
        active=False,
        name__icontains="retired Glovo",
    ).order_by("business_id", "pk")
    for account in accounts.iterator():
        base_name = "Archived delivery partner"
        existing_names = set(
            Account.objects.filter(business_id=account.business_id)
            .exclude(pk=account.pk)
            .values_list("name", flat=True)
        )
        account.name = base_name
        if account.name in existing_names:
            account.name = f"{base_name} {account.pk}"[:100]
        account.save(update_fields=["name"])


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0036_shipbubble_replaces_glovo"),
    ]

    operations = [
        migrations.RunPython(neutralize_archived_provider_names, migrations.RunPython.noop),
    ]
