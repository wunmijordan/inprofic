"""Founder Console controls for the staff assistant (superuser-only; the caller enforces that)."""
import logging
import re

from core.models import Business
from . import config, quickfacts
from .models import AssistantPlatformSettings

logger = logging.getLogger(__name__)


def context():
    platform = AssistantPlatformSettings.load()
    disabled = set(platform.disabled_quick_facts or [])
    from django.conf import settings
    return {
        "row": platform, "env_enabled": settings.ASSISTANT_ENABLED, "model_configured": bool(settings.ASSISTANT_AI_API_KEY),
        "groups": [{"name": g["name"], "facts": [{**f, "checked": f["key"] not in disabled} for f in g["facts"]]}
                   for g in quickfacts.grouped(list(quickfacts.QUICK_FACTS))],
        "pilots": list(Business.objects.filter(assistant_enabled=True).order_by("name")),
    }


def save(user, post):
    """Apply the Founder Console form. Returns the message to show."""
    row = AssistantPlatformSettings.load()
    valid = {f.key for f in quickfacts.QUICK_FACTS}
    row.assistant_enabled = post.get("assistant_enabled") == "on"
    row.quick_facts_enabled = post.get("quick_facts_enabled") == "on"
    row.free_text_enabled = post.get("free_text_enabled") == "on"
    row.open_to_all_businesses = post.get("open_to_all_businesses") == "on"
    row.disabled_quick_facts = sorted(valid - (set(post.getlist("quick_fact")) & valid))
    row.updated_by = user
    row.save()
    keep = {int(v) for v in post.getlist("pilot_keep") if v.isdigit()}
    removed = Business.objects.filter(assistant_enabled=True).exclude(pk__in=keep).update(assistant_enabled=False)
    slugs = {s.lower() for s in re.split(r"[\s,;]+", post.get("pilot_add", "")) if s}
    found = {b.slug: b for b in Business.objects.filter(slug__in=slugs)}
    Business.objects.filter(pk__in=[b.pk for b in found.values()]).update(assistant_enabled=True)
    unknown = sorted(slugs - set(found))
    config.invalidate()
    logger.info("Assistant platform settings changed by user %s: enabled=%s quick_facts=%s free_text=%s open=%s off=%s pilots +%s -%s",
                user.pk, row.assistant_enabled, row.quick_facts_enabled, row.free_text_enabled,
                row.open_to_all_businesses, row.disabled_quick_facts, sorted(found), removed)
    state = "on" if row.assistant_enabled else "OFF for every business"
    message = f"Assistant settings saved. The assistant is {state}."
    if unknown:
        message += f" No business matched: {', '.join(unknown)}."
    return message
