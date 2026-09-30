import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0018_query_pattern_indexes"),
        ("core", "0007_query_pattern_indexes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CommerceDirectTransferRoute",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(help_text="Customer-facing route label, e.g. Main account or Opay.", max_length=80)),
                ("bank_name", models.CharField(max_length=120)),
                ("account_name", models.CharField(max_length=160)),
                ("account_number", models.CharField(max_length=40)),
                ("instructions", models.CharField(blank=True, default="", max_length=255)),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveSmallIntegerField(default=50)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="commerce_commercedirecttransferroute_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="commerce_commercedirecttransferroute_created", to=settings.AUTH_USER_MODEL)),
                ("transfer_account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="commerce_direct_transfer_routes", to="core.cashaccount")),
            ],
            options={"ordering": ["sort_order", "name", "id"]},
        ),
        migrations.AddConstraint(
            model_name="commercedirecttransferroute",
            constraint=models.UniqueConstraint(fields=("business", "name"), name="unique_transfer_route_name_per_business"),
        ),
    ]
