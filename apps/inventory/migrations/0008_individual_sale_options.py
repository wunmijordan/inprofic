import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0007_product_portions_bulk_packs"),
    ]

    operations = [
        migrations.AddField(
            model_name="bulkpackprofile",
            name="package_type",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Customer-facing container/pack type selected from the business vertical's vocabulary.",
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="IndividualSaleOption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("name", models.CharField(help_text="Customer-facing name, e.g. Extra Jollof Rice or Single Chicken.", max_length=120)),
                ("customer_quantity", models.DecimalField(decimal_places=2, default=1, max_digits=12)),
                ("customer_unit", models.CharField(blank=True, default="", help_text="e.g. scoop, piece, serving, bottle.", max_length=40)),
                ("base_quantity", models.DecimalField(decimal_places=3, default=1, max_digits=14)),
                ("public_note", models.CharField(blank=True, default="", max_length=160)),
                ("physical_store_enabled", models.BooleanField(default=True)),
                ("online_enabled", models.BooleanField(default=True)),
                ("distribution_enabled", models.BooleanField(default=False)),
                ("physical_store_price", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("online_price", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("distribution_price", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("physical_store_min_quantity", models.DecimalField(decimal_places=2, default=1, max_digits=12)),
                ("online_min_quantity", models.DecimalField(decimal_places=2, default=1, max_digits=12)),
                ("distribution_min_quantity", models.DecimalField(decimal_places=2, default=1, max_digits=12)),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("finished_good", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="individual_sale_options", to="inventory.finishedgood")),
            ],
            options={"ordering": ["sort_order", "name", "id"]},
        ),
        migrations.AddConstraint(
            model_name="individualsaleoption",
            constraint=models.UniqueConstraint(fields=("business", "finished_good", "name"), name="unique_individual_sale_option_name"),
        ),
    ]
