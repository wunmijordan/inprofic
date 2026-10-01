from django.db import migrations, models


def backfill_driver_assigned_at(apps, schema_editor):
    DeliveryAssignment = apps.get_model("commerce", "DeliveryAssignment")
    DeliveryEvent = apps.get_model("commerce", "DeliveryEvent")

    assignments = (
        DeliveryAssignment.objects.exclude(driver_id=None)
        .only("id", "driver_id", "batch_id", "created_at", "updated_at")
        .iterator()
    )
    for assignment in assignments:
        assigned_at = None
        events = (
            DeliveryEvent.objects.filter(assignment_id=assignment.id)
            .only("created_at", "metadata", "note")
            .order_by("-created_at", "-id")
        )
        for event in events:
            metadata = event.metadata or {}
            try:
                event_driver_id = int(metadata.get("driver_id")) if metadata.get("driver_id") is not None else None
            except (TypeError, ValueError):
                event_driver_id = None
            if event_driver_id == assignment.driver_id:
                assigned_at = event.created_at
                break
            if assigned_at is None and "rider assigned" in (event.note or "").lower():
                assigned_at = event.created_at

        # Older records may not have a dedicated assignment event. In that case
        # retain a conservative timestamp rather than inventing a precise time.
        if assigned_at is None:
            assigned_at = assignment.updated_at or assignment.created_at

        DeliveryAssignment.objects.filter(pk=assignment.pk).update(
            driver_assigned_at=assigned_at
        )


class Migration(migrations.Migration):
    dependencies = [
        ("commerce", "0023_merge_legacy_manual_transfer_into_routes"),
    ]

    operations = [
        migrations.AddField(
            model_name="deliveryassignment",
            name="driver_assigned_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            backfill_driver_assigned_at,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
