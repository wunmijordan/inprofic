from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_business_fiscal_period"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LedgerCutover",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("cutover_date", models.DateField()),
                ("status", models.CharField(choices=[("open", "Open"), ("complete", "Complete"), ("merged", "Merged"), ("archived", "Archived")], default="open", max_length=12)),
                ("backlog_complete_at", models.DateTimeField(blank=True, null=True)),
                ("merged_at", models.DateTimeField(blank=True, null=True)),
                ("merge_snapshot", models.JSONField(blank=True, default=dict)),
                ("notes", models.TextField(blank=True, default="")),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "constraints": [models.UniqueConstraint(condition=~models.Q(status="archived"), fields=("business",), name="one_active_ledger_cutover_per_business")],
            },
        ),
        migrations.CreateModel(
            name="OpeningPosition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("segment", models.CharField(choices=[("backlog", "Backlog"), ("live", "Live")], max_length=10)),
                ("kind", models.CharField(choices=[("raw", "Raw material"), ("fg", "Finished good"), ("cash", "Cash account"), ("receivable", "Receivable"), ("payable", "Payable")], max_length=16)),
                ("object_id", models.PositiveBigIntegerField()),
                ("quantity", models.DecimalField(blank=True, decimal_places=3, max_digits=18, null=True)),
                ("amount", models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ("source", models.CharField(choices=[("derived", "Derived"), ("counted", "Counted"), ("merged", "Merged")], default="counted", max_length=10)),
                ("note", models.CharField(blank=True, default="", max_length=255)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("cutover", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="opening_positions", to="core.ledgercutover")),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("cutover", "segment", "kind", "object_id"), name="unique_ledger_opening_position")],
            },
        ),
    ]
