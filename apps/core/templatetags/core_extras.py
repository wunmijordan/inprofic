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
    from core.quantities import plural_unit
    return plural_unit(unit, count)


@register.filter
def split_pairs(value):
    """Turn ``"key:Label,key2:Label 2"`` into ``[("key", "Label"), ...]`` for headers."""
    return [tuple(part.split(":", 1)) for part in str(value).split(",") if ":" in part]


@register.filter
def dict_get(mapping, key):
    """Look up ``mapping[key]`` in a template (empty string when missing)."""
    try:
        return mapping.get(key, "")
    except AttributeError:
        return ""


@register.filter
def get_item(mapping, key):
    """{{ mapping|get_item:key }} for dicts whose keys are not template-safe."""
    try:
        return mapping.get(key, [])
    except AttributeError:
        return []
