"""Authenticated workspace panel and per-business settings for the read-only staff assistant."""
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.services import audit
from . import config, engine, quickfacts
from .models import AssistantBusinessSettings, AssistantConversation

RECENT_MESSAGES = 40


def _business(request):
    business = getattr(request, "business", None)
    if not engine.assistant_available(request.user, business):
        raise PermissionDenied
    return business


def serialize(message):
    fact = quickfacts.FACTS.get(message.quick_fact)
    return {"id": message.pk, "role": message.role, "content": message.content, "status": message.status,
            "sources": message.sources or [], "scope": message.scope, "via": fact.label if fact else "",
            "suggestions": [{"key": k, "label": quickfacts.FACTS[k].label} for k in (message.suggestions or []) if k in quickfacts.FACTS],
            "time": timezone.localtime(message.created_at).strftime("%H:%M")}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@login_required
@require_GET
def assistant_page(request):
    business = _business(request)
    if request.GET.get("panel") != "1":
        return redirect("/dashboard/?assistant=1")
    requested = _int(request.GET.get("c"))
    if requested is not None:
        conversation = engine.conversation_for(request.user, business, requested)
    else:
        conversation = AssistantConversation.raw_objects.filter(business=business, user=request.user).first()
    messages_ = list(conversation.messages.order_by("-created_at", "-pk")[:RECENT_MESSAGES])[::-1] if conversation else []
    facts = quickfacts.enabled_facts(request.user, business)
    free_ready = bool(config.free_text_allowed(business) and settings.ASSISTANT_AI_API_KEY)
    return render(request, "assistant/panel.html", {
        "can_configure": engine.can_configure(request.user, business),
        "conversation": conversation, "messages_json": [serialize(m) for m in messages_],
        "quick_groups": quickfacts.grouped(facts), "free_text_ready": free_ready, "can_ask": free_ready or bool(facts),
        "usage": engine.usage_today(business), "usage_limit": settings.ASSISTANT_DAILY_MODEL_CALLS_PER_BUSINESS,
        "recent": AssistantConversation.raw_objects.filter(business=business, user=request.user).exclude(title="")[:8],
    })


@login_required
@require_POST
def assistant_ask(request):
    try:
        business = _business(request)
        body = json.loads(request.body.decode() or "{}") if request.content_type == "application/json" else request.POST
        conversation = (engine.conversation_for(request.user, business, _int(body.get("conversation")))
                        if _int(body.get("conversation")) is not None else engine.new_conversation(request.user, business))
        if body.get("quick"):
            message = engine.quick(request.user, business, conversation, str(body.get("quick")))
        else:
            message = engine.ask(request.user, business, conversation, str(body.get("question") or ""))
    except PermissionDenied:
        return JsonResponse({"error": "The assistant is not available to you here."}, status=403)
    except (ValueError, UnicodeDecodeError, AttributeError):
        return JsonResponse({"error": "Type a question first."}, status=400)
    return JsonResponse({"conversation": conversation.pk, "message": serialize(message), "usage": engine.usage_today(business)})


@login_required
@require_http_methods(["GET", "POST"])
def assistant_settings(request):
    """A business admin's own choices: on/off, AI questions, and which quick facts to offer."""
    business = getattr(request, "business", None)
    if not engine.can_configure(request.user, business):
        raise PermissionDenied
    platform = config.platform_config()
    row = AssistantBusinessSettings.raw_objects.filter(business=business).first()
    current = config.tenant_config(business)
    valid = {f.key for f in quickfacts.QUICK_FACTS}
    if request.method == "POST":
        custom_questions = []
        seen = set()
        questions, reports = request.POST.getlist("custom_question"), request.POST.getlist("custom_report")
        if len(questions) != len(reports):
            messages.error(request, "Each question needs a report. Your settings were not changed.")
            return redirect("assistant_settings")
        built_in_labels = {quickfacts.normalize_question(f.label): f.key for f in quickfacts.QUICK_FACTS}
        for question, report in zip(questions, reports):
            question = " ".join(question.split())
            if not question:
                continue
            normalized = quickfacts.normalize_question(question)
            if (not normalized or len(question) > 160 or report not in valid or normalized in seen
                    or (normalized in built_in_labels and built_in_labels[normalized] != report)
                    or len(custom_questions) >= 30):
                messages.error(request, "Use up to 30 distinct questions (160 characters each), each mapped to a listed report. Built-in question names must keep their original report.")
                return redirect("assistant_settings")
            seen.add(normalized)
            custom_questions.append({"question": question, "report": report})
        offered = set(request.POST.getlist("quick_fact")) & valid
        # Facts the platform has switched off are not on the form, so keep this business's earlier choice for them.
        disabled = (valid - platform.disabled_facts - offered) | (set(current.disabled_facts) & platform.disabled_facts)
        values = {"enabled": request.POST.get("enabled") == "1",
                  "free_text_enabled": request.POST.get("free_text_enabled") == "1",
                  "disabled_quick_facts": sorted(disabled), "custom_questions": custom_questions}
        if row is None:
            row = AssistantBusinessSettings.raw_objects.create(business=business, created_by=request.user, **values)
        else:
            for field, value in values.items():
                setattr(row, field, value)
            row.save()
        config.invalidate(business)
        audit(business, request.user, "update", row, "Assistant settings changed",
              {"enabled": values["enabled"], "free_text": values["free_text_enabled"], "quick_facts_off": values["disabled_quick_facts"], "custom_questions": custom_questions})
        messages.success(request, "Assistant settings saved.")
        return redirect("assistant_settings")
    current = config.tenant_config(business)
    groups = [{"name": g["name"], "facts": [{
        "key": f["key"], "label": f["label"], "checked": f["key"] not in current.disabled_facts,
        "platform_off": f["key"] in platform.disabled_facts or not platform.quick_facts_enabled}
        for f in g["facts"]]} for g in quickfacts.grouped(list(quickfacts.QUICK_FACTS))]
    return render(request, "assistant/settings.html", {
        "tenant": current, "groups": groups,
        "custom_questions": (row.custom_questions if row else []) or [],
        "report_choices": quickfacts.QUICK_FACTS, "platform": platform, "model_configured": bool(settings.ASSISTANT_AI_API_KEY),
    })
