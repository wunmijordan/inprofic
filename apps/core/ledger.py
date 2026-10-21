"""Ledger cut-over scoping, date validation, and opening balance helpers."""
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone


class LedgerDateError(ValidationError):
    def __init__(self, scope, cutover_date):
        self.scope = scope
        self.cutover_date = cutover_date
        expected = "before" if scope == "live" else "on or after"
        super().__init__(f"This date belongs to the other ledger period. In {scope.title()} mode, use a date {expected} {cutover_date}.")


def active_cutover(business):
    from .models import LedgerCutover
    cutover = LedgerCutover.raw_objects.filter(business=business).order_by("-id").first()
    return None if cutover and cutover.status == LedgerCutover.STATUS_ARCHIVED else cutover


def segment_for(business, on_date):
    from .models import LedgerCutover
    latest = LedgerCutover.raw_objects.filter(business=business).order_by("-id").first()
    if latest and latest.status == LedgerCutover.STATUS_ARCHIVED and on_date < latest.cutover_date:
        raise ValidationError("Backlog entry is sealed because this ledger period was archived.")
    cutover = active_cutover(business)
    if not cutover or cutover.status == "merged" or on_date >= cutover.cutover_date:
        return "live"
    return "backlog"


def validate_entry_date(business, on_date, *, scope=None):
    from .models import LedgerCutover
    latest = LedgerCutover.raw_objects.filter(business=business).order_by("-id").first()
    if latest and latest.status == LedgerCutover.STATUS_ARCHIVED and on_date < latest.cutover_date:
        raise LedgerDateError(scope or "live", latest.cutover_date)
    cutover = active_cutover(business)
    if not cutover or cutover.status == "merged":
        return "live"
    if cutover.status == "archived":
        raise LedgerDateError(scope or "live", cutover.cutover_date)
    scope = scope or "live"
    segment = "backlog" if on_date < cutover.cutover_date else "live"
    if segment != scope:
        raise LedgerDateError(scope, cutover.cutover_date)
    return segment


def registered_ledger_models():
    """Models whose primary business date participates in automatic scoping."""
    return tuple(model for model in apps.get_models() if getattr(model, "ledger_date_field", None))


def validate_registry():
    """Fail loudly if an opted-in model's configured date field is missing."""
    errors = []
    for model in registered_ledger_models():
        field_name = model.ledger_date_field
        try:
            field = model._meta.get_field(field_name)
        except Exception:
            errors.append(f"{model._meta.label}.{field_name}")
            continue
        if not (field.get_internal_type() == "DateField" or field.get_internal_type() == "DateTimeField"):
            errors.append(f"{model._meta.label}.{field_name} is not a date field")
    if errors:
        raise AssertionError("Invalid ledger date registry: " + ", ".join(errors))
    return True


def guard_ledger_date(sender, instance, **kwargs):
    if not getattr(sender, "ledger_date_field", None) or not instance.business_id:
        return
    # AuditLog.created_at describes when the action was recorded, not the
    # business event's effective date. A legitimate backlog entry is audited
    # now even though its source record is dated before the cut-over.
    if sender._meta.label_lower == "core.auditlog":
        return
    from .context import get_ledger_cutover_date, get_ledger_scope
    scope = get_ledger_scope()
    cutover_date = get_ledger_cutover_date()
    if not scope or not cutover_date:
        return
    # Combined is a read scope. Writes still need the explicit Backlog mode
    # before a pre-cut-over date can be saved.
    if scope == "all":
        scope = "live"
    value = getattr(instance, sender.ledger_date_field, None)
    if value is None:
        return
    if hasattr(value, "date"):
        value = timezone.localtime(value).date()
    if (scope == "live" and value < cutover_date) or (scope == "backlog" and value >= cutover_date):
        raise LedgerDateError(scope, cutover_date)


def movement_balance(business, item, kind, segment):
    """Derive a segment balance from its opening position and dated movements."""
    from inventory.models import StockMovement
    from .models import LedgerCutover, OpeningPosition
    cutover = active_cutover(business)
    if not cutover:
        return item.__dict__.get("stock") or Decimal("0")
    opening = OpeningPosition.raw_objects.filter(
        business=business, cutover=cutover, segment=segment, kind=kind, object_id=item.pk,
    ).first()
    balance = (opening.quantity if opening and opening.quantity is not None else Decimal("0"))
    movements = StockMovement.raw_objects.filter(
        business=business, business_date__isnull=False,
        **({"raw_material": item} if kind == "raw" else {"finished_good": item}),
    )
    if segment == "backlog":
        movements = movements.filter(business_date__lt=cutover.cutover_date)
    else:
        movements = movements.filter(business_date__gte=cutover.cutover_date)
    if kind == "fg":
        movements = movements.filter(affects_stock=True)
    return balance + (movements.aggregate(total=Sum("quantity"))["total"] or Decimal("0"))


@transaction.atomic
def rebuild_live_stock(business):
    """Rebuild stored inventory balances from D openings and live movements."""
    from inventory.models import FinishedGood, RawMaterial, StockMovement
    from .models import LedgerCutover, OpeningPosition
    cutover = LedgerCutover.raw_objects.select_for_update().filter(
        business=business,
    ).exclude(status=LedgerCutover.STATUS_ARCHIVED).first()
    if not cutover:
        return {"raw": 0, "fg": 0}
    counts = {"raw": 0, "fg": 0}
    for kind, model, attr in (("raw", RawMaterial, "stock"), ("fg", FinishedGood, "stock")):
        for item in model.raw_objects.select_for_update().filter(business=business):
            if kind == "fg" and item.stock is None:
                continue
            opening = OpeningPosition.raw_objects.filter(
                business=business, cutover=cutover, segment="live", kind=kind, object_id=item.pk,
            ).first()
            balance = opening.quantity if opening and opening.quantity is not None else Decimal("0")
            movement_filter = {
                "business": business,
                "business_date__gte": cutover.cutover_date,
                "raw_material" if kind == "raw" else "finished_good": item,
            }
            movements = StockMovement.raw_objects.filter(**movement_filter)
            if kind == "fg":
                movements = movements.filter(affects_stock=True)
            balance += movements.aggregate(total=Sum("quantity"))["total"] or Decimal("0")
            balance = balance.quantize(Decimal("0.001" if kind == "raw" else "0.01"))
            model.raw_objects.filter(pk=item.pk, business=business).update(**{attr: balance})
            counts[kind] += 1
    return counts
