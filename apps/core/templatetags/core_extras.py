from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()


@register.filter
def money(value, symbol="₦"):
    try:
        return f"{symbol}{float(value):,.2f}"
    except (TypeError, ValueError):
        return f"{symbol}0.00"


@register.filter
def num(value):
    """Render every numeric value consistently to exactly 2 decimal places."""
    try:
        if value is None:
            return "0.00"
        v = Decimal(str(value))
        return f"{v:,.2f}"
    except (TypeError, ValueError, InvalidOperation):
        return value


@register.filter
def integration_safe_text(value):
    """Hide branding for founder-disabled optional integrations at render time."""
    from accounts.platform_integrations import redact_disabled_integrations
    return redact_disabled_integrations(value)


@register.filter
def unit_plural(unit, count):
    """Pluralize user-entered unit labels such as loaf, box or tray."""
    word = str(unit or "").strip()
    if not word:
        return word
    try:
        singular = Decimal(str(count)) == Decimal("1")
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
    elif lower.endswith(("s", "x", "z", "ch", "sh")):
        plural = word + "es"
    elif lower.endswith("y") and len(word) > 1 and lower[-2] not in "aeiou":
        plural = word[:-1] + "ies"
    elif lower.endswith("fe"):
        plural = word[:-2] + "ves"
    elif lower.endswith("f"):
        plural = word[:-1] + "ves"
    else:
        plural = word + "s"
    return plural.capitalize() if word.istitle() else plural.upper() if word.isupper() else plural
