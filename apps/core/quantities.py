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
