from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import FinishedGood, InventoryAlertSettings, InventoryAlertState, RawMaterial


ALERT_META = {
    InventoryAlertState.RAW_WARNING: {
        "settings_enabled": "raw_warning_enabled",
        "settings_repeat": "raw_warning_repeat_minutes",
        "resource": "raw_material",
        "condition": "warning",
        "label": "Raw material warning",
        "severity": "warning",
    },
    InventoryAlertState.RAW_LOW: {
        "settings_enabled": "raw_low_enabled",
        "settings_repeat": "raw_low_repeat_minutes",
        "resource": "raw_material",
        "condition": "low",
        "label": "Raw material low stock",
        "severity": "low",
    },
    InventoryAlertState.FINISHED_WARNING: {
        "settings_enabled": "finished_warning_enabled",
        "settings_repeat": "finished_warning_repeat_minutes",
        "resource": "finished_good",
        "condition": "warning",
        "label": "Finished good warning",
        "severity": "warning",
    },
    InventoryAlertState.FINISHED_LOW: {
        "settings_enabled": "finished_low_enabled",
        "settings_repeat": "finished_low_repeat_minutes",
        "resource": "finished_good",
        "condition": "low",
        "label": "Finished good low stock",
        "severity": "low",
    },
}


def alert_settings(business):
    settings, _ = InventoryAlertSettings.raw_objects.get_or_create(
        business=business,
        defaults={"created_by": None},
    )
    return settings


def _condition_rows(business):
    rows = []
    for item in RawMaterial.raw_objects.filter(business=business).order_by("name", "id"):
        alert_type = InventoryAlertState.RAW_LOW if item.is_low else InventoryAlertState.RAW_WARNING if item.is_warning else None
        if alert_type:
            rows.append((alert_type, item))
    for item in FinishedGood.raw_objects.filter(business=business).order_by("name", "id"):
        alert_type = InventoryAlertState.FINISHED_LOW if item.is_low else InventoryAlertState.FINISHED_WARNING if item.is_warning else None
        if alert_type:
            rows.append((alert_type, item))
    return rows


def _serialize(alert_type, item, state):
    meta = ALERT_META[alert_type]
    unit = item.usage_unit if meta["resource"] == "raw_material" else item.unit
    return {
        "id": f"{alert_type}:{item.pk}",
        "type": alert_type,
        "label": meta["label"],
        "severity": meta["severity"],
        "resource": meta["resource"],
        "item_id": item.pk,
        "item_name": item.name,
        "stock": str(Decimal(item.stock or 0)),
        "reorder_level": str(Decimal(item.reorder_level or 0)),
        "unit": unit or "unit",
        "target_url": "/inventory/",
        "acknowledged_at": state.acknowledged_at.isoformat() if state.acknowledged_at else None,
    }


@transaction.atomic
def inventory_alert_feed(*, business, user):
    settings = alert_settings(business)
    if not settings.enabled:
        InventoryAlertState.raw_objects.filter(business=business, user=user, is_active=True).update(is_active=False, acknowledged_at=None)
        return {
            "enabled": False, "poll_seconds": settings.poll_seconds,
            "sound_enabled": False, "sound_repeat_minutes": 0, "sound_tune": settings.sound_tune,
            "alerts": [], "count": 0, "raw_count": 0, "finished_count": 0,
        }

    current = _condition_rows(business)
    active_keys = {(alert_type, item.pk) for alert_type, item in current}
    existing = {
        (state.alert_type, state.object_id): state
        for state in InventoryAlertState.raw_objects.select_for_update().filter(business=business, user=user)
    }
    now = timezone.now()
    visible = []
    for alert_type, item in current:
        meta = ALERT_META[alert_type]
        if not getattr(settings, meta["settings_enabled"]):
            continue
        key = (alert_type, item.pk)
        state = existing.get(key)
        if state is None:
            state = InventoryAlertState.raw_objects.create(
                business=business, created_by=user, user=user,
                alert_type=alert_type, object_id=item.pk, is_active=True,
            )
            existing[key] = state
        elif not state.is_active:
            state.is_active = True
            state.acknowledged_at = None
            state.save(update_fields=["is_active", "acknowledged_at", "updated_at"])
        repeat_minutes = getattr(settings, meta["settings_repeat"])
        is_due = state.acknowledged_at is None or (
            repeat_minutes > 0 and now >= state.acknowledged_at + timedelta(minutes=repeat_minutes)
        )
        if is_due:
            visible.append(_serialize(alert_type, item, state))

    for key, state in existing.items():
        if state.is_active and key not in active_keys:
            state.is_active = False
            state.acknowledged_at = None
            state.save(update_fields=["is_active", "acknowledged_at", "updated_at"])

    raw_count = sum(1 for row in visible if row["resource"] == "raw_material")
    finished_count = sum(1 for row in visible if row["resource"] == "finished_good")
    return {
        "enabled": True,
        "poll_seconds": min(300, max(15, settings.poll_seconds or 45)),
        "sound_enabled": bool(settings.sound_enabled),
        "sound_repeat_minutes": min(1440, max(0, settings.sound_repeat_minutes or 0)),
        "sound_tune": settings.sound_tune,
        "alerts": visible,
        "count": len(visible),
        "raw_count": raw_count,
        "finished_count": finished_count,
    }


@transaction.atomic
def acknowledge_inventory_alerts(*, business, user, alert_ids):
    now = timezone.now()
    updated = 0
    for token in set(alert_ids or []):
        try:
            alert_type, raw_id = str(token).split(":", 1)
            object_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if alert_type not in ALERT_META:
            continue
        state = InventoryAlertState.raw_objects.select_for_update().filter(
            business=business, user=user, alert_type=alert_type, object_id=object_id, is_active=True,
        ).first()
        if state:
            state.acknowledged_at = now
            state.save(update_fields=["acknowledged_at", "updated_at"])
            updated += 1
    return updated
