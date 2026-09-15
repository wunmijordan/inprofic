from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_baseline"),
        ("inventory", "0002_finishedgood_base_material"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                ("slug", models.SlugField(max_length=100)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("active", models.BooleanField(default=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["sort_order", "name", "id"]},
        ),
        migrations.AddConstraint(
            model_name="productcategory",
            constraint=models.UniqueConstraint(fields=("business", "name"), name="unique_product_category_name_per_business"),
        ),
        migrations.AddConstraint(
            model_name="productcategory",
            constraint=models.UniqueConstraint(fields=("business", "slug"), name="unique_product_category_slug_per_business"),
        ),
        migrations.AddField(
            model_name="finishedgood",
            name="product_category",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional business-defined storefront category, e.g. Meals, Drinks, Pastries or Accessories.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="products",
                to="inventory.productcategory",
            ),
        ),
        migrations.CreateModel(
            name="RawMaterialMeasurementChange",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("conversion_ratio", models.DecimalField(decimal_places=8, default=1, max_digits=20)),
                ("reason", models.CharField(max_length=255)),
                ("old_measurement", models.JSONField(default=dict)),
                ("new_measurement", models.JSONField(default=dict)),
                ("converted_records", models.JSONField(blank=True, default=dict)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("raw_material", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="measurement_changes", to="inventory.rawmaterial")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]
