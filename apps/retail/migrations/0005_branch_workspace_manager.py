from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("retail", "0004_customerquotation_title"),
        ("core", "0019_business_branch_and_labels"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(
            model_name="stocktransfer",
            name="destination_product",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="incoming_workspace_transfers",
                to="inventory.finishedgood",
            ),
        ),
        migrations.AddField(
            model_name="branch",
            name="catalogue_mode",
            field=models.CharField(
                max_length=12,
                choices=[
                    ("independent", "Independent branch catalogue"),
                    ("shared", "Shared headquarters catalogue"),
                ],
                default="independent",
            ),
        ),
        migrations.AddField(
            model_name="branch",
            name="catalogue_map",
            field=models.JSONField(default=dict, blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="branch",
            name="catalogue_signature",
            field=models.CharField(max_length=64, blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="branch",
            name="workspace",
            field=models.OneToOneField(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="location_branch",
                to="core.business",
            ),
        ),
        migrations.AddField(
            model_name="branch",
            name="manager",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="managed_branches",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
