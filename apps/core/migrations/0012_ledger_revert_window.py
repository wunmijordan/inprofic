from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_ledger_cutover"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ledgercutover",
            name="status",
            field=models.CharField(
                choices=[
                    ("open", "Open"),
                    ("complete", "Complete"),
                    ("merged", "Merged"),
                    ("archived", "Archived"),
                    ("reverted", "Reverted to combined mode"),
                ],
                default="open",
                max_length=12,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="ledgercutover",
            name="one_active_ledger_cutover_per_business",
        ),
        migrations.AddConstraint(
            model_name="ledgercutover",
            constraint=models.UniqueConstraint(
                condition=~models.Q(status__in=["archived", "reverted"]),
                fields=("business",),
                name="one_active_ledger_cutover_per_business",
            ),
        ),
    ]
