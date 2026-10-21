from django.db import IntegrityError, transaction

from .models import Supplier


def resolve_supplier(business, name, *, created_by=None):
    """Find or safely create this tenant's supplier for a typed name."""
    name = (name or "").strip()
    if not name:
        return None
    supplier = Supplier.raw_objects.filter(business_id=business.pk, name__iexact=name).first()
    if supplier:
        return supplier
    try:
        with transaction.atomic():
            return Supplier.raw_objects.create(
                business=business, created_by=created_by, name=name,
            )
    except IntegrityError:
        # A concurrent request may have created the case-insensitive match.
        supplier = Supplier.raw_objects.filter(business_id=business.pk, name__iexact=name).first()
        if supplier is None:
            raise
        return supplier
