from django.db import migrations


def copy_legacy_receipt_branding(apps, schema_editor):
    CommerceSettings = apps.get_model("commerce", "CommerceSettings")
    Business = apps.get_model("core", "Business")
    for settings in CommerceSettings.objects.select_related("business").iterator():
        business = Business.objects.filter(pk=settings.business_id).first()
        if business is None:
            continue
        changed = []
        for old_name, business_name in (
            ("receipt_logo", "storefront_logo"),
            ("receipt_tagline", "tagline"),
            ("receipt_contact_phone", "contact_phone"),
            ("receipt_contact_email", "contact_email"),
            ("receipt_contact_address", "contact_address"),
            ("receipt_contact_website", "contact_website"),
        ):
            old_value = getattr(settings, old_name, "")
            old_value = getattr(old_value, "name", old_value)
            if old_value and not getattr(business, business_name, ""):
                setattr(business, business_name, old_value)
                changed.append(business_name)
        if changed:
            business.save(update_fields=changed)


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0028_commercecheckoutitem_preorder_lead_time_and_more"),
        ("core", "0009_backfill_business_profile_contacts"),
    ]

    operations = [
        migrations.RunPython(copy_legacy_receipt_branding, migrations.RunPython.noop),
    ]
