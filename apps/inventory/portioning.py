from __future__ import annotations

from decimal import Decimal

from .models import BulkPackProfile, IndividualSaleOption, ProductCompositionItem


STANDARD_PROFILE_KEY = "standard"


def _prefetched_or_related(obj, related_name):
    cache = getattr(obj, "_prefetched_objects_cache", {}) or {}
    if related_name in cache:
        return list(cache[related_name])
    try:
        related = getattr(obj, related_name)
    except AttributeError:
        return []
    try:
        return list(related.all())
    except (AttributeError, TypeError):
        return []


def standard_profile(good):
    try:
        profile = good.portion_profile
    except Exception:
        return None
    return profile if getattr(profile, "active", False) else None


def customer_unit(good):
    profile = standard_profile(good)
    if profile and (profile.customer_unit or "").strip():
        return profile.customer_unit.strip()
    return good.unit


def standard_multiplier(good):
    profile = standard_profile(good)
    if not profile:
        return Decimal("1")
    return Decimal(profile.base_quantity or 1)


def active_bulk_packs(good):
    rows = _prefetched_or_related(good, "bulk_pack_profiles")
    return [row for row in rows if row.active]




def active_individual_options(good, channel=None):
    rows = _prefetched_or_related(good, "individual_sale_options")
    rows = [row for row in rows if row.active]
    if channel:
        rows = [row for row in rows if row.channel_enabled(channel) and row.price_for(channel) is not None]
    return rows


def find_individual_option(good, public_id, *, channel=None):
    if not public_id:
        return None
    needle = str(public_id)
    for row in active_individual_options(good, channel=channel):
        if str(row.public_id) == needle:
            return row
    queryset = IndividualSaleOption.raw_objects.filter(
        business=good.business, finished_good=good, public_id=public_id, active=True
    )
    row = queryset.first()
    if row is None or (channel and (not row.channel_enabled(channel) or row.price_for(channel) is None)):
        return None
    return row

def find_bulk_pack(good, public_id):
    if not public_id:
        return None
    needle = str(public_id)
    for row in active_bulk_packs(good):
        if str(row.public_id) == needle:
            return row
    return BulkPackProfile.raw_objects.filter(
        business=good.business,
        finished_good=good,
        public_id=public_id,
        active=True,
    ).first()


def _component_rows(good, profile_key):
    rows = _prefetched_or_related(good, "composition_items")
    if rows:
        return [row for row in rows if row.profile_key == profile_key]
    return list(
        ProductCompositionItem.objects.filter(
            finished_good=good,
            profile_key=profile_key,
        ).select_related("component_finished_good", "component_raw_material")
    )


def public_contents(good, *, profile_key=STANDARD_PROFILE_KEY):
    """Customer-safe composition summary.

    Internal conversion quantities are intentionally not exposed unless the
    business explicitly supplied a public quantity label.
    """
    result = [{
        "name": good.name,
        "quantity_label": "",
        "scope": ProductCompositionItem.SCOPE_ALL,
        "kind": "base_product",
    }]
    for row in _component_rows(good, profile_key):
        if not row.include_in_public_contents:
            continue
        label = row.display_label
        if not label:
            continue
        result.append({
            "name": label,
            "quantity_label": (row.public_quantity_label or "").strip(),
            "scope": row.fulfilment_scope,
            "kind": "finished_good" if row.component_finished_good_id else "material",
        })
    return result


def internal_contents_snapshot(good, *, profile_key=STANDARD_PROFILE_KEY, base_multiplier=Decimal("1"), customer_quantity=Decimal("1")):
    """Freeze internal composition demand without mutating production.

    The existing recipe/production engine remains authoritative for the base
    FinishedGood. Additional contents are a separate assembly/yield snapshot so
    future changes do not rewrite historical commerce orders.
    """
    customer_quantity = Decimal(customer_quantity or 0)
    base_multiplier = Decimal(base_multiplier or 1)
    snapshot = [{
        "kind": "base_product",
        "id": good.pk,
        "name": good.name,
        "quantity_per_customer_unit": str(base_multiplier),
        "total_quantity": str((base_multiplier * customer_quantity).quantize(Decimal("0.001"))),
        "unit": good.unit,
        "scope": ProductCompositionItem.SCOPE_ALL,
    }]
    for row in _component_rows(good, profile_key):
        component = row.component
        if component is None:
            continue
        qty = Decimal(row.quantity or 0)
        snapshot.append({
            "kind": "finished_good" if row.component_finished_good_id else "raw_material",
            "id": component.pk,
            "name": component.name,
            "quantity_per_customer_unit": str(qty),
            "total_quantity": str((qty * customer_quantity).quantize(Decimal("0.001"))),
            "unit": row.internal_unit,
            "scope": row.fulfilment_scope,
            "public_label": (row.public_label or "").strip(),
            "public_quantity_label": (row.public_quantity_label or "").strip(),
        })
    return snapshot


def selection_for(good, *, bulk_pack=None, individual_option=None, channel=None):
    if bulk_pack is not None and individual_option is not None:
        raise ValueError("Choose either a bulk pack or an individual option, not both.")
    if individual_option is not None:
        if channel and not individual_option.channel_enabled(channel):
            raise ValueError("This individual option is not available for the selected sales channel.")
        unit_price = individual_option.price_for(channel) if channel else None
        return {
            "profile_key": individual_option.profile_key,
            "customer_unit": individual_option.customer_unit or individual_option.name,
            "multiplier": Decimal(individual_option.base_quantity or 1),
            "unit_price": Decimal(unit_price) if unit_price is not None else None,
            "minimum": Decimal(individual_option.minimum_for(channel) or 1) if channel else Decimal("1"),
            "bulk_pack": None,
            "individual_option": individual_option,
            "contents": [{
                "name": good.name,
                "quantity_label": (individual_option.public_note or "").strip(),
                "scope": ProductCompositionItem.SCOPE_ALL,
                "kind": "base_product",
            }],
            "portion_note": individual_option.public_note or "",
        }
    if bulk_pack is not None:
        return {
            "profile_key": bulk_pack.profile_key,
            "customer_unit": bulk_pack.name,
            "multiplier": Decimal(bulk_pack.base_quantity or 1),
            "unit_price": Decimal(bulk_pack.price),
            "minimum": Decimal(bulk_pack.min_order_quantity or 1),
            "bulk_pack": bulk_pack,
            "individual_option": None,
            "contents": public_contents(good, profile_key=bulk_pack.profile_key),
        }
    profile = standard_profile(good)
    return {
        "profile_key": STANDARD_PROFILE_KEY,
        "customer_unit": customer_unit(good),
        "multiplier": standard_multiplier(good),
        "unit_price": None,
        "minimum": None,
        "bulk_pack": None,
        "individual_option": None,
        "contents": public_contents(good, profile_key=STANDARD_PROFILE_KEY),
        "portion_note": (profile.public_note if profile else ""),
    }
