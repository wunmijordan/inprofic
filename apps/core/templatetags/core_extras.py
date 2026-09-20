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
