from django.db import migrations


def copy_signup_contacts(apps, schema_editor):
    Business = apps.get_model("core", "Business")
    UserBusiness = apps.get_model("accounts", "UserBusiness")
    for business in Business.objects.all().iterator():
        membership = (
            UserBusiness.objects.filter(
                business_id=business.pk,
                active=True,
                role__key="business_admin",
            )
            .select_related("user")
            .order_by("id")
            .first()
        )
        if not membership:
            continue
        updates = []
        if not business.contact_email:
            business.contact_email = (membership.user.email or "").strip()
            updates.append("contact_email")
        if not business.contact_phone:
            business.contact_phone = (membership.user.phone or "").strip()
            updates.append("contact_phone")
        if updates:
            business.save(update_fields=updates)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0008_business_contact_address_business_contact_email_and_more"),
        ("accounts", "0027_payroll_batches_plan_independent"),
    ]

    operations = [
        migrations.RunPython(copy_signup_contacts, migrations.RunPython.noop),
    ]
