from django.db import migrations


def merge_legacy_manual_transfer_routes(apps, schema_editor):
    PaymentConfig = apps.get_model("commerce", "CommercePaymentConfiguration")
    TransferRoute = apps.get_model("commerce", "CommerceDirectTransferRoute")
    CashAccount = apps.get_model("core", "CashAccount")

    for config in PaymentConfig.objects.exclude(transfer_account_id=None).iterator():
        bank_name = (config.bank_name or "").strip()
        account_name = (config.bank_account_name or "").strip()
        account_number = (config.bank_account_number or "").strip()
        instructions = (config.bank_instructions or "").strip()
        if not (bank_name and account_name and account_number):
            continue
        if not CashAccount.objects.filter(
            pk=config.transfer_account_id,
            business_id=config.business_id,
            active=True,
        ).exists():
            continue

        equivalent = TransferRoute.objects.filter(
            business_id=config.business_id,
            transfer_account_id=config.transfer_account_id,
            bank_name=bank_name,
            account_name=account_name,
            account_number=account_number,
        ).exists()
        if equivalent:
            continue

        preferred_names = [
            "Primary transfer account",
            f"Legacy transfer {account_number[-4:]}",
        ]
        route_name = None
        for candidate in preferred_names:
            if not TransferRoute.objects.filter(
                business_id=config.business_id,
                name=candidate,
            ).exists():
                route_name = candidate
                break
        if route_name is None:
            suffix = 2
            while True:
                candidate = f"Legacy transfer {account_number[-4:]} {suffix}"
                if not TransferRoute.objects.filter(
                    business_id=config.business_id,
                    name=candidate,
                ).exists():
                    route_name = candidate
                    break
                suffix += 1

        TransferRoute.objects.create(
            business_id=config.business_id,
            created_by_id=config.created_by_id,
            name=route_name,
            bank_name=bank_name,
            account_name=account_name,
            account_number=account_number,
            instructions=instructions,
            transfer_account_id=config.transfer_account_id,
            active=True,
            sort_order=10,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0022_backfill_payment_configuration_defaults"),
    ]

    operations = [
        migrations.RunPython(
            merge_legacy_manual_transfer_routes,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
