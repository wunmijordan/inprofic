from django.db import migrations


def copy_legacy_manual_transfer_routes(apps, schema_editor):
    PaymentConfig = apps.get_model("commerce", "CommercePaymentConfiguration")
    TransferRoute = apps.get_model("commerce", "CommerceDirectTransferRoute")
    CashAccount = apps.get_model("core", "CashAccount")

    for config in PaymentConfig.objects.exclude(transfer_account_id=None).iterator():
        bank_name = (config.bank_name or "").strip()
        account_name = (config.bank_account_name or "").strip()
        account_number = (config.bank_account_number or "").strip()
        if not (bank_name and account_name and account_number):
            continue
        if not CashAccount.objects.filter(
            pk=config.transfer_account_id, business_id=config.business_id, active=True
        ).exists():
            continue
        if TransferRoute.objects.filter(business_id=config.business_id).exists():
            continue
        TransferRoute.objects.create(
            business_id=config.business_id,
            created_by_id=config.created_by_id,
            name="Primary transfer account",
            bank_name=bank_name,
            account_name=account_name,
            account_number=account_number,
            instructions=(config.bank_instructions or "").strip(),
            transfer_account_id=config.transfer_account_id,
            active=True,
            sort_order=10,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0020_alter_commercedirecttransferroute_business_and_more"),
    ]

    operations = [
        migrations.RunPython(copy_legacy_manual_transfer_routes, migrations.RunPython.noop),
    ]
