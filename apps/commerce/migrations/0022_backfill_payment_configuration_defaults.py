from django.db import migrations


def backfill_payment_configuration_defaults(apps, schema_editor):
    PaymentConfiguration = apps.get_model("commerce", "CommercePaymentConfiguration")
    PaymentConfiguration.objects.filter(currency="").update(currency="NGN")
    PaymentConfiguration.objects.filter(monnify_base_url="").update(
        monnify_base_url="https://api.monnify.com"
    )
    PaymentConfiguration.objects.filter(bank_transfer_provider="").update(
        bank_transfer_provider="paystack"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0021_migrate_legacy_manual_transfer_route"),
    ]

    operations = [
        migrations.RunPython(
            backfill_payment_configuration_defaults,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
