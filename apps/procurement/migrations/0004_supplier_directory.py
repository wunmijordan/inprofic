from django.db import migrations, models
import django.db.models.deletion
from django.db.models.functions import Lower


def create_supplier_directory(apps, schema_editor):
    Supplier = apps.get_model("procurement", "Supplier")
    PurchaseOrder = apps.get_model("procurement", "PurchaseOrder")
    SupplierPayment = apps.get_model("procurement", "SupplierPayment")
    CostSnapshot = apps.get_model("procurement", "RawMaterialCostSnapshot")
    alias = schema_editor.connection.alias

    for model in (PurchaseOrder, SupplierPayment, CostSnapshot):
        for business_id, raw_name in (
            model.objects.using(alias).exclude(supplier="").values_list("business_id", "supplier").distinct()
        ):
            name = (raw_name or "").strip()
            if not name:
                continue
            supplier = Supplier.objects.using(alias).filter(
                business_id=business_id, name__iexact=name
            ).first()
            if supplier is None:
                supplier = Supplier.objects.using(alias).create(business_id=business_id, name=name)
            model.objects.using(alias).filter(
                business_id=business_id, supplier__iexact=raw_name
            ).update(supplier_master_id=supplier.pk)


class Migration(migrations.Migration):
    dependencies = [
        ("procurement", "0003_query_pattern_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="Supplier",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120)),
                ("contact_name", models.CharField(blank=True, default="", max_length=120)),
                ("phone", models.CharField(blank=True, default="", max_length=40)),
                ("email", models.EmailField(blank=True, default="", max_length=254)),
                ("address", models.CharField(blank=True, default="", max_length=255)),
                ("notes", models.TextField(blank=True, default="")),
                ("active", models.BooleanField(default=True)),
                ("business", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_set", to="core.business")),
                ("created_by", models.ForeignKey(blank=True, help_text="Person who created this record.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to="accounts.customuser")),
            ],
            options={"ordering": ["name", "id"]},
        ),
        migrations.AddField(
            model_name="purchaseorder", name="supplier_master",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="purchase_orders", to="procurement.supplier"),
        ),
        migrations.AddField(
            model_name="supplierpayment", name="supplier_master",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="payments", to="procurement.supplier"),
        ),
        migrations.AddField(
            model_name="rawmaterialcostsnapshot", name="supplier_master",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="cost_snapshots", to="procurement.supplier"),
        ),
        migrations.RunPython(create_supplier_directory, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="supplier",
            constraint=models.UniqueConstraint(Lower("name"), "business", name="supplier_biz_name_ci_uniq"),
        ),
    ]
