"""Shared quantity labels for workspace, exports and summary text."""
from decimal import Decimal, InvalidOperation


def plural_unit(unit, count):
    """Pluralize user-entered unit labels such as loaf, box or tray."""
    word = str(unit or "").strip()
    if not word:
        return word
    try:
        singular = abs(Decimal(str(count))) == Decimal("1")
    except (InvalidOperation, TypeError, ValueError):
        singular = False
    if singular:
        return word

    lower = word.lower()
    if lower in {"kg", "g", "mg", "lb", "lbs", "oz", "ml", "cl", "l", "mm", "cm", "m", "km", "hr", "min"}:
        return word
    irregular = {
        "loaf": "loaves", "leaf": "leaves", "knife": "knives",
        "life": "lives", "wife": "wives", "shelf": "shelves",
        "half": "halves", "calf": "calves", "wolf": "wolves",
        "foot": "feet", "tooth": "teeth", "person": "people",
        "child": "children", "man": "men", "woman": "women",
    }
    if lower in irregular:
        plural = irregular[lower]
    elif lower in irregular.values() or (lower.endswith("s") and not lower.endswith(("ss", "us"))):
        return word
    elif lower.endswith(("s", "x", "z", "ch", "sh")):
        plural = word + "es"
    elif lower.endswith("y") and len(word) > 1 and lower[-2] not in "aeiou":
        plural = word[:-1] + "ies"
    else:
        plural = word + "s"
    return plural.capitalize() if word.istitle() else plural.upper() if word.isupper() else plural



def format_quantity(value, unit):
    amount = Decimal(str(value or 0))
    number = f"{amount:,.3f}".rstrip('0').rstrip('.')
    return f"{number} {plural_unit(unit, abs(amount))}".strip()


def product_portion(product):
    """Display unit and base-unit factor from the storefront's standard portion."""
    from inventory.portioning import customer_unit, standard_multiplier, standard_profile
    profile = standard_profile(product) if product is not None else None
    if profile and (profile.customer_unit or '').strip():
        factor = standard_multiplier(product)
        if factor > 0:
            return customer_unit(product), factor
    return getattr(product, 'unit', ''), Decimal('1')


def format_product_quantity(value, product=None, *, unit=None):
    """Express base stock as selling portions, retaining loose base-unit remainders.

    This is presentation only. It never changes stock, prices or saved commercial
    quantities. A hidden public portion caption does not disable conversion.
    """
    base_unit = getattr(product, 'unit', None) or unit or ''
    selling_unit, factor = product_portion(product)
    selling_unit = selling_unit or base_unit
    amount = Decimal(str(value or 0))
    if factor == 1:
        return format_quantity(amount, selling_unit)
    whole, remainder = divmod(abs(amount), factor)
    if not remainder:
        return format_quantity(amount / factor, selling_unit)
    if not whole:
        return format_quantity(amount, base_unit)
    sign = Decimal('-1') if amount < 0 else Decimal('1')
    return (format_quantity(sign * whole, selling_unit)
            + (' - ' if amount < 0 else ' + ') + format_quantity(remainder, base_unit))


def sale_quantity_display(item):
    """Quantity/rate labels whose units agree, preserving commercial snapshots."""
    if item.commercial_quantity is not None and item.commercial_unit_price is not None:
        return {'quantity': format_quantity(item.commercial_quantity, item.commercial_unit or item.finished_good.unit),
                'price': item.commercial_unit_price, 'discount': Decimal('0'),
                'price_unit': item.commercial_unit or item.finished_good.unit, 'operator': '×'}
    unit, factor = product_portion(item.finished_good)
    stored_factor = getattr(item, 'price_unit_quantity', Decimal('1'))
    stored_unit = getattr(item, 'price_unit', '')
    if stored_unit:
        return {'quantity': format_quantity(item.total_units / stored_factor, stored_unit),
                'price': item.price, 'discount': item.discount, 'price_unit': stored_unit, 'operator': '×'}
    mixed = bool(item.total_units % factor)
    if mixed:
        unit, factor = item.finished_good.unit, Decimal('1')
    return {'quantity': format_product_quantity(item.total_units, item.finished_good),
            'price': item.price * factor, 'discount': item.discount * factor, 'price_unit': unit,
            'operator': '·' if mixed else '×'}


def product_price_fields(product):
    """Snapshot the configured selling unit while stock stays in base units."""
    unit, factor = product_portion(product)
    return {'price_unit_quantity': factor, 'price_unit': unit}


def set_product_price(item, channel, customer=None):
    item.price = item.finished_good.selling_price_for(channel, customer)
    for name, value in product_price_fields(item.finished_good).items():
        setattr(item, name, value)


def product_choice_label(product, *, label=None):
    from inventory.portioning import standard_profile
    text = str(label) if label is not None else product.name
    profile = standard_profile(product)
    if profile and (profile.customer_unit or '').strip():
        unit, _factor = product_portion(product)
        return f'{text} · {unit}'
    return text


def material_stock_label(material):
    stock = material.stock
    if stock is None:
        return ''
    factor = material.total_conversion_factor
    if material.purchase_unit and stock >= 0 and factor and factor > 0 and material.purchase_unit != material.usage_unit:
        whole, remainder = material.stock_breakdown
        parts = []
        if whole:
            parts.append(format_quantity(whole, material.purchase_unit))
        if remainder:
            parts.append(format_quantity(remainder, material.usage_unit))
        if parts:
            return ', '.join(parts)
    return format_quantity(stock, material.usage_unit)


def has_value(value):
    """Whether a summary value carries a nonzero amount (labels stay intact)."""
    import re
    if value is None or value == '':
        return False
    if isinstance(value, (int, float, Decimal)):
        return value != 0
    text = str(value).strip()
    numeric = re.sub(r'[^\d.,+\-]', '', text).replace(',', '')
    if not numeric or '/' in text:
        return True
    try:
        return Decimal(numeric) != 0
    except InvalidOperation:
        return True
