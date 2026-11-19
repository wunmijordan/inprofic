"""Copy master definitions to stable branch-local IDs, never balances or transactions."""

import hashlib
import json
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction, IntegrityError
from inventory import models as inventory
from commerce.models import StorefrontProduct
from .models import Branch

MODELS = (
    inventory.RawMaterial,
    inventory.ProductCategory,
    inventory.FinishedGood,
    inventory.ProductPortionProfile,
    inventory.BulkPackProfile,
    inventory.RecipeItem,
    inventory.FinishedGoodChannelPrice,
    inventory.ProductCompositionItem,
    StorefrontProduct,
)
EXCLUDED = {
    "id",
    "created_at",
    "updated_at",
    "created_by",
    "business",
    "public_id",
    "stock",
    "cost_per_unit",
    "total_produced",
    "total_delivered_to_customers",
    "transferred_market_stock",
}


def source_rows(headquarters):
    result = {}
    for model in MODELS:
        manager = getattr(model, "raw_objects", model.objects)
        scope = (
            {"business": headquarters}
            if any(f.name == "business" for f in model._meta.fields)
            else {"finished_good__business": headquarters}
        )
        result[model._meta.model_name] = list(manager.filter(**scope).order_by("pk"))
    return result


def definition(obj):
    return {
        f.attname: getattr(obj, f.attname)
        for f in obj._meta.fields
        if f.name not in EXCLUDED
    }


@transaction.atomic
def synchronize_catalogue(branch):
    if branch.catalogue_mode != "shared" or not branch.workspace_id:
        return
    branch = (
        Branch.raw_objects.select_for_update()
        .select_related("business", "workspace")
        .get(pk=branch.pk)
    )
    rows = source_rows(branch.business)
    signature = hashlib.sha256(
        json.dumps(
            {
                key: [(obj.pk, definition(obj)) for obj in values]
                for key, values in rows.items()
            },
            cls=DjangoJSONEncoder,
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    if signature == branch.catalogue_signature:
        return
    mapping = dict(branch.catalogue_map)
    for model in MODELS:
        key = model._meta.model_name
        mapping.setdefault(key, {})
        for source in rows[key]:
            manager = getattr(model, "raw_objects", model.objects)
            existing = mapping[key].get(str(source.pk))
            scope = (
                {"business": branch.workspace}
                if any(f.name == "business" for f in model._meta.fields)
                else {"finished_good__business": branch.workspace}
            )
            target = manager.filter(pk=existing, **scope).first() if existing else None
            values = definition(source)
            for field in model._meta.fields:
                if field.name in EXCLUDED or not field.is_relation:
                    continue
                source_id = values.get(field.attname)
                if not source_id:
                    continue
                if field.name == "variant_of":
                    values[field.attname] = (
                        None  # connected after all product IDs exist
                    )
                    continue
                target_id = mapping.get(field.related_model._meta.model_name, {}).get(
                    str(source_id)
                )
                if target_id is None:
                    raise ValidationError(
                        "A headquarters catalogue component is missing or belongs to another business."
                    )
                values[field.attname] = target_id
            if (
                model is inventory.ProductCompositionItem
                and source.profile_key.startswith("bulk:")
            ):
                source_bulk = next(
                    (
                        row
                        for row in rows["bulkpackprofile"]
                        if str(row.public_id) == source.profile_key[5:]
                    ),
                    None,
                )
                if source_bulk is None:
                    raise ValidationError(
                        "A product composition references an unknown bulk pack."
                    )
                target_bulk = inventory.BulkPackProfile.raw_objects.get(
                    pk=mapping["bulkpackprofile"][str(source_bulk.pk)],
                    business=branch.workspace,
                )
                values["profile_key"] = target_bulk.profile_key
            try:
                if target:
                    units = (
                        {"unit"}
                        if model is inventory.FinishedGood
                        else (
                            {
                                "purchase_unit",
                                "package_qty",
                                "package_unit",
                                "usage_unit",
                                "usage_conversion_factor",
                            }
                            if model is inventory.RawMaterial
                            else set()
                        )
                    )
                    if any(getattr(target, name) != values[name] for name in units):
                        raise ValidationError(
                            "Headquarters changed a stock measurement. Convert this branch's measurement explicitly before sharing the updated catalogue."
                        )
                    for name, value in values.items():
                        setattr(target, name, value)
                    target.save(update_fields=[*values, "updated_at"])
                else:
                    if "business" in scope:
                        values["business"] = branch.workspace
                    if model is inventory.FinishedGood:
                        values["stock"] = None if source.stock is None else 0
                    target = manager.create(**values)
                    mapping[key][str(source.pk)] = target.pk
            except IntegrityError as exc:
                raise ValidationError(
                    "A branch catalogue entry conflicts with headquarters. Resolve duplicate names/codes before using a shared catalogue."
                ) from exc
        # Only editable master relationships can disappear; products and material history stay intact.
        if model in (
            inventory.RecipeItem,
            inventory.FinishedGoodChannelPrice,
            inventory.ProductCompositionItem,
        ):
            current = {str(row.pk) for row in rows[key]}
            for source_id in set(mapping[key]) - current:
                manager.filter(pk=mapping[key].pop(source_id), **scope).delete()
    for source in rows["finishedgood"]:
        inventory.FinishedGood.raw_objects.filter(
            pk=mapping["finishedgood"][str(source.pk)], business=branch.workspace
        ).update(
            variant_of_id=(
                mapping["finishedgood"].get(str(source.variant_of_id))
                if source.variant_of_id
                else None
            )
        )
    for model, inactive in (
        (inventory.ProductPortionProfile, {"active": False}),
        (inventory.ProductCategory, {"active": False}),
        (inventory.BulkPackProfile, {"active": False}),
        (StorefrontProduct, {"published": False}),
    ):
        key = model._meta.model_name
        missing = set(mapping.get(key, {})) - {str(row.pk) for row in rows[key]}
        if missing:
            manager = getattr(model, "raw_objects", model.objects)
            manager.filter(
                business=branch.workspace,
                pk__in=[mapping[key][source_id] for source_id in missing],
            ).update(**inactive)
    removed_products = set(mapping["finishedgood"]) - {
        str(row.pk) for row in rows["finishedgood"]
    }
    if removed_products:
        StorefrontProduct.raw_objects.filter(
            business=branch.workspace,
            finished_good_id__in=[
                mapping["finishedgood"][source_id] for source_id in removed_products
            ],
        ).update(published=False)
    branch.catalogue_map = mapping
    branch.catalogue_signature = signature
    branch.save(update_fields=["catalogue_map", "catalogue_signature", "updated_at"])
