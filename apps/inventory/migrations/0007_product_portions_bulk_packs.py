import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0006_inventory_alert_tune"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductPortionProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("active", models.BooleanField(default=False, help_text="Use a customer-facing standard portion instead of exposing the base production/stock unit.")),
                ("customer_quantity", models.DecimalField(decimal_places=2, default=1, max_digits=12)),
                ("customer_unit", models.CharField(blank=True, default="", help_text="What the customer buys, e.g. plate, serving, bottle, set.", max_length=40)),
                ("base_quantity", models.DecimalField(decimal_places=3, default=1, help_text="How many Finished Good base units make one standard customer portion.", max_digits=14)),
                ("public_note", models.CharField(blank=True, default="", help_text="Optional short note shown with the portion, e.g. 'serves one'.", max_length=160)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("finished_good", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="portion_profile", to="inventory.finishedgood")),
            ],
            options={"ordering": ["finished_good__name"]},
        ),
        migrations.CreateModel(
            name="BulkPackProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("name", models.CharField(help_text="Customer-facing pack name, e.g. 2 L Bowl or Carton of 24.", max_length=100)),
                ("customer_quantity", models.DecimalField(decimal_places=2, default=1, max_digits=12)),
                ("customer_unit", models.CharField(blank=True, default="", max_length=40)),
                ("base_quantity", models.DecimalField(decimal_places=3, default=1, help_text="How many Finished Good base units one bulk pack represents.", max_digits=14)),
                ("price", models.DecimalField(decimal_places=2, max_digits=14)),
                ("min_order_quantity", models.DecimalField(decimal_places=2, default=1, help_text="Minimum number of this bulk pack per order.", max_digits=12)),
                ("active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("finished_good", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="bulk_pack_profiles", to="inventory.finishedgood")),
            ],
            options={"ordering": ["sort_order", "name", "id"]},
        ),
        migrations.AddConstraint(
            model_name="bulkpackprofile",
            constraint=models.UniqueConstraint(fields=("business", "finished_good", "name"), name="unique_bulk_pack_name_per_product_business"),
        ),
        migrations.CreateModel(
            name="ProductCompositionItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("profile_key", models.CharField(default="standard", help_text="'standard' for the regular portion or bulk:<uuid> for a bulk pack.", max_length=80)),
                ("quantity", models.DecimalField(decimal_places=3, default=1, max_digits=14)),
                ("fulfilment_scope", models.CharField(choices=[("all", "All fulfilment modes"), ("dine_in", "Dine-in only"), ("takeaway", "Takeaway / pickup only"), ("delivery", "Delivery only"), ("bulk", "Bulk / distribution only")], default="all", max_length=16)),
                ("include_in_public_contents", models.BooleanField(default=True)),
                ("public_label", models.CharField(blank=True, default="", help_text="Optional customer-facing name. Leave blank to use the component name.", max_length=120)),
                ("public_quantity_label", models.CharField(blank=True, default="", help_text="Optional customer-facing amount such as '1 piece'. Internal quantities remain private when blank.", max_length=80)),
                ("component_finished_good", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="used_in_product_compositions", to="inventory.finishedgood")),
                ("component_raw_material", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="used_in_product_compositions", to="inventory.rawmaterial")),
                ("finished_good", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="composition_items", to="inventory.finishedgood")),
            ],
            options={"ordering": ["profile_key", "id"]},
        ),
    ]
