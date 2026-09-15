from django.db import migrations, models


GLOVO_STATUS_MAP = {
    "CREATED": "assigned",
    "SCHEDULED": "assigned",
    "ACTIVATED": "assigned",
    "ACCEPTED": "assigned",
    "WAITING_FOR_PICKUP": "ready",
    "PICKED": "picked_up",
    "WAITING_FOR_DELIVERY": "out_for_delivery",
    "DELIVERED": "delivered",
    "REJECTED": "failed",
    "CANCELLED": "cancelled",
    "RETURNED": "returned",
}


def upgrade_glovo_accounts(apps, schema_editor):
    Provider = apps.get_model("commerce", "DeliveryProviderAccount")
    for provider in Provider.objects.filter(provider_code="glovo"):
        changed = []
        defaults = {
            "auth_endpoint": "/oauth/token",
            "quote_endpoint": "/v2/laas/quotes",
            "order_endpoint": "/v2/laas/quotes/{quote_id}/parcels",
            "cancel_endpoint": "/v2/laas/parcels/{external_reference}/cancel",
        }
        for field, value in defaults.items():
            if not getattr(provider, field, ""):
                setattr(provider, field, value)
                changed.append(field)
        current = provider.status_mapping if isinstance(provider.status_mapping, dict) else {}
        merged = {**current, **GLOVO_STATUS_MAP}
        if merged != current:
            provider.status_mapping = merged
            changed.append("status_mapping")
        if changed:
            provider.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = [("commerce", "0003_delivery_provider_plugins")]
    operations = [
        migrations.AddField(
            model_name="deliveryprovideraccount",
            name="auth_endpoint",
            field=models.CharField(blank=True, default="/oauth/token", help_text="OAuth token endpoint. Glovo LaaS v2 uses /oauth/token.", max_length=160),
        ),
        migrations.AddField(
            model_name="deliveryprovideraccount",
            name="use_live_quotes",
            field=models.BooleanField(default=True, help_text="Use the provider's live quote/ETA when configured; hybrid mode can fall back to INPROFIC rate bands."),
        ),
        migrations.AddField(
            model_name="deliveryprovideraccount",
            name="address_book_id",
            field=models.CharField(blank=True, default="", help_text="Glovo LaaS Address Book pickup ID. Required for live Glovo quotes.", max_length=120),
        ),
        migrations.AlterField(
            model_name="deliveryprovideraccount",
            name="base_url",
            field=models.URLField(blank=True, default="", help_text="Provider API base URL issued for this tenant/environment by the provider.", max_length=255),
        ),
        migrations.AlterField(
            model_name="deliveryprovideraccount",
            name="quote_endpoint",
            field=models.CharField(blank=True, default="", help_text="Relative/absolute quote endpoint. Glovo LaaS v2 uses /v2/laas/quotes.", max_length=160),
        ),
        migrations.AlterField(
            model_name="deliveryprovideraccount",
            name="store_id",
            field=models.CharField(blank=True, default="", help_text="Optional legacy/provider store identifier.", max_length=120),
        ),
        migrations.RunPython(upgrade_glovo_accounts, migrations.RunPython.noop),
    ]
