from django.db import migrations


def one_active_batch_per_size(apps, schema_editor):
    """Prepare plan-specific extra-staff batches to become a single catalogue.

    Batches used to be priced per plan, so several plans could each have an
    active "+5 staff". In a plan-independent catalogue only one active batch per
    size can be offered. For each size the oldest active batch stays active; the
    others are *retired* (not deleted or merged): businesses already holding one
    keep it and it keeps renewing at the price they bought it for, but it is no
    longer offered to new buyers. The Founder should review pricing afterwards.
    """
    PayrollStaffBatch = apps.get_model("accounts", "PayrollStaffBatch")
    seen = set()
    for batch in PayrollStaffBatch.objects.filter(active=True).order_by("staff_count", "id"):
        if batch.staff_count in seen:
            batch.active = False
            batch.save(update_fields=["active"])
        else:
            seen.add(batch.staff_count)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0025_payroll_unlimited_tiers_and_staff_batches"),
    ]

    operations = [
        migrations.RunPython(one_active_batch_per_size, migrations.RunPython.noop),
    ]
