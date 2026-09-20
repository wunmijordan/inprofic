from django.db import migrations, models
import django.db.models.deletion


def deactivate_unconfigured_seed_glovo(apps, schema_editor):
    Provider = apps.get_model("commerce", "DeliveryProviderAccount")
    Provider.objects.filter(
        provider_code="glovo",
        active=True,
        base_url="",
        api_key="",
        api_secret="",
        address_book_id="",
    ).update(active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0008_direct_transfer_payment_mode"),
    ]

    operations = [
        migrations.AlterField(
            model_name="deliveryprovideraccount",
            name="name",
            field=models.CharField(default="Delivery partner", max_length=100),
        ),
        migrations.AlterField(
            model_name="deliveryprovideraccount",
            name="provider_code",
            field=models.CharField(
                choices=[("generic", "Custom delivery partner"), ("glovo", "Glovo")],
                default="generic",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="deliveryprovideraccount",
            name="active",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="deliveryprovideraccount",
            name="use_live_quotes",
            field=models.BooleanField(
                default=False,
                help_text="Use the provider's live quote/ETA when a dedicated provider adapter supports it; otherwise INPROFIC rate bands remain authoritative.",
            ),
        ),
        migrations.AddField(
            model_name="deliveryprovideraccount",
            name="health_endpoint",
            field=models.CharField(blank=True, default="", help_text="Optional non-mutating endpoint used to test a custom provider or adapter connection.", max_length=160),
        ),
        migrations.AlterField(
            model_name="deliveryprovideraccount",
            name="store_id",
            field=models.CharField(blank=True, default="", help_text="Optional merchant, store or location identifier required by the configured delivery partner.", max_length=120),
        ),
        migrations.AlterField(
            model_name="deliveryprovideraccount",
            name="tracking_base_url",
            field=models.URLField(blank=True, default="", help_text="Optional public tracking URL or template. Use {external_reference} as the courier-reference placeholder.", max_length=255),
        ),
        migrations.AlterField(
            model_name="deliveryprovideraccount",
            name="auth_endpoint",
            field=models.CharField(blank=True, default="", help_text="Optional authentication endpoint used by a dedicated provider adapter.", max_length=160),
        ),
        migrations.AlterField(
            model_name="deliveryprovideraccount",
            name="quote_endpoint",
            field=models.CharField(blank=True, default="", help_text="Optional live-quote endpoint used by a dedicated provider adapter.", max_length=160),
        ),
        migrations.RunPython(deactivate_unconfigured_seed_glovo, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="deliveryprovideraccount",
            name="address_book_id",
            field=models.CharField(blank=True, default="", help_text="Optional pickup/location identifier used by a dedicated provider adapter.", max_length=120),
        ),
        migrations.AlterField(
            model_name="deliverysettings",
            name="default_provider_account",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional configured delivery-partner account. Leave blank for in-house dispatch.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="default_for_settings",
                to="commerce.deliveryprovideraccount",
            ),
        ),
        migrations.AlterField(
            model_name="deliverysettings",
            name="hybrid_routing_policy",
            field=models.CharField(
                choices=[
                    ("dispatcher_choice", "Dispatcher chooses per order"),
                    ("customer_choice", "Customer chooses at checkout"),
                    ("lowest_fee", "Automatically use the lowest fee"),
                    ("fastest_eta", "Automatically use the fastest ETA"),
                    ("inhouse_first", "Prefer in-house; delivery partner remains available"),
                    ("glovo_first", "Prefer delivery partner; in-house remains available"),
                ],
                default="dispatcher_choice",
                help_text="When Hybrid is enabled, decide who/what chooses between in-house delivery and the configured provider.",
                max_length=24,
            ),
        ),
    ]
