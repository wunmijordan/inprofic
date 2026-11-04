from decimal import Decimal
from .models import AuditLog, FinancialTransaction, CashAccount


def audit(business, user, action, obj, description, metadata=None):
    return AuditLog.objects.create(
        business=business, created_by=user, action=action,
        model_name=obj.__class__.__name__ if obj else "system",
        object_id=str(getattr(obj, "pk", "")), description=description,
        metadata=metadata or {},
    )


def record_cash(business, user, *, date, amount, transaction_type, category, description,
                payment_method="", reference="", account=None):
    amount = Decimal(amount or 0)
    if amount <= 0:
        return None
    if account is None:
        account = CashAccount.objects.filter(business=business, active=True).order_by("id").first()
        if account is None:
            account = CashAccount.objects.create(business=business, name="Main Cash", account_type="cash", created_by=user)
    elif account.business_id != business.id:
        raise ValueError("Cash account belongs to a different business.")
    return FinancialTransaction.objects.create(
        business=business, created_by=user, date=date, amount=amount,
        transaction_type=transaction_type, category=category,
        description=description, payment_method=payment_method or "",
        reference=reference or "", account=account,
    )

def tenant_backup_querysets(business):
    """Version-two coverage: explicit business ownership for every operating table."""
    from accounts.backup_restore import tenant_operational_models
    from .models import Business
    selected = tenant_operational_models()

    def ownership_path(model, seen=frozenset()):
        if model in seen:
            return None
        fields = model._meta.concrete_fields
        for field in fields:
            if field.is_relation and field.related_model is Business:
                return field.name
        # Required owning parents take precedence over optional traceability links.
        relations = sorted((f for f in fields if f.is_relation and f.related_model in selected), key=lambda f: f.null)
        for field in relations:
            path = ownership_path(field.related_model, seen | {model})
            if path:
                return field.name + '__' + path
        return None

    result = [Business.objects.filter(pk=business.pk), AuditLog.raw_objects.filter(business=business)]
    for model in sorted(selected, key=lambda m: m._meta.label_lower):
        path = ownership_path(model)
        if not path:
            raise ValueError(f'Backup model has no tenant ownership path: {model._meta.label_lower}')
        manager = getattr(model, 'raw_objects', model._base_manager)
        result.append(manager.filter(**{path: business}))
    return result


def tenant_backup_objects(business):
    """Lazy iterable for Django serialization; never crosses tenant ownership."""
    from itertools import chain
    return chain.from_iterable(tenant_backup_querysets(business))
