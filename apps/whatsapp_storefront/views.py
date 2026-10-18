import json
import hmac
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods

from accounts.services import is_business_admin
from accounts.platform_integrations import whatsapp_storefront_platform_enabled
from commerce.models import CommerceSettings
from .meta import MetaWhatsAppError, exchange_signup_code, graph_request, validate_phone_number
from .models import WhatsAppConnection, WhatsAppConversation, WhatsAppMessage, WhatsAppWebhookEvent
from .security import WhatsAppTokenError, encrypt_access_token
from .services import persist_webhook_events, verify_meta_signature


def _commerce_is_enabled(business):
    from accounts.services import business_has_module
    config = CommerceSettings.objects.filter(business=business).first()
    return bool(config and config.enabled and config.hosted_storefront_enabled and business_has_module(business, "commerce"))


@login_required
def settings_page(request):
    business = request.business
    if not is_business_admin(request.user, business):
        return render(request, "403.html", status=403)
    if not whatsapp_storefront_platform_enabled():
        messages.info(request, "WhatsApp Storefront is not currently available on this platform.")
        return redirect("commerce_dashboard")
    if not _commerce_is_enabled(business):
        messages.warning(request, "Enable Commerce before setting up WhatsApp Storefront.")
        return redirect("commerce_dashboard")
    connection = WhatsAppConnection.objects.filter(business=business).first()
    if request.method == "POST":
        connection = connection or WhatsAppConnection(business=business, created_by=request.user)
        connection.assistant_enabled = request.POST.get("assistant_enabled") == "on"
        connection.ai_enabled = request.POST.get("ai_enabled") == "on"
        connection.welcome_message = request.POST.get("welcome_message", "").strip()[:500]
        connection.assistant_instructions = request.POST.get("assistant_instructions", "").strip()[:4000]
        connection.handoff_message = request.POST.get("handoff_message", "").strip()[:500] or WhatsAppConnection._meta.get_field("handoff_message").default
        connection.business = business
        connection.created_by = connection.created_by or request.user
        if connection.pk:
            connection.save(update_fields=["assistant_enabled", "ai_enabled", "welcome_message", "assistant_instructions", "handoff_message", "updated_at"])
        else:
            messages.error(request, "Connect a WhatsApp Business account before saving assistant settings.")
            return redirect("whatsapp_settings")
        messages.success(request, "WhatsApp assistant settings saved.")
        return redirect("whatsapp_settings")

    event_failures = WhatsAppWebhookEvent.objects.filter(
        payload__phone_number_id=getattr(connection, "phone_number_id", ""),
        processed_at__isnull=True,
    ).count() if connection else 0
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    usage = connection.business.whatsapp_usage.filter(occurred_at__gte=month_start).values("direction").annotate(total=Count("id")) if connection else []
    return render(request, "whatsapp_storefront/settings.html", {
        "connection": connection,
        "meta_app_id": settings.META_WHATSAPP_APP_ID,
        "meta_config_id": settings.META_WHATSAPP_CONFIG_ID,
        "meta_graph_version": settings.META_WHATSAPP_GRAPH_VERSION,
        "meta_ready": bool(
            settings.META_WHATSAPP_APP_ID and settings.META_WHATSAPP_CONFIG_ID
            and settings.META_WHATSAPP_APP_SECRET and settings.META_WHATSAPP_VERIFY_TOKEN
            and len(settings.WHATSAPP_TOKEN_ENCRYPTION_KEY) >= 32
        ),
        "ai_ready": bool(settings.WHATSAPP_AI_API_KEY),
        "event_failures": event_failures,
        "usage": {row["direction"]: row["total"] for row in usage},
    })


@login_required
@require_POST
def embedded_signup_complete(request):
    if not is_business_admin(request.user, request.business):
        return JsonResponse({"detail": "Only business administrators can connect WhatsApp."}, status=403)
    if not whatsapp_storefront_platform_enabled():
        return JsonResponse({"detail": "WhatsApp Storefront is not currently available on this platform."}, status=403)
    if not _commerce_is_enabled(request.business):
        return JsonResponse({"detail": "Commerce is not enabled for this business."}, status=403)
    try:
        data = json.loads(request.body or b"{}")
        if not (settings.META_WHATSAPP_APP_ID and settings.META_WHATSAPP_APP_SECRET
                and settings.META_WHATSAPP_VERIFY_TOKEN and settings.WHATSAPP_TOKEN_ENCRYPTION_KEY):
            raise MetaWhatsAppError("Complete the Meta webhook and token-encryption configuration before connecting a number.")
        code = str(data.get("code") or "").strip()
        waba_id = str(data.get("waba_id") or "").strip()
        phone_id = str(data.get("phone_number_id") or "").strip()
        if not (code and waba_id.isdigit() and phone_id.isdigit()):
            raise ValueError("Meta did not return a valid authorization code, WhatsApp account and phone number.")
        token_result = exchange_signup_code(code)
        access_token = str(token_result.get("access_token") or "")
        phone_data = validate_phone_number(phone_id, access_token)
        if str(phone_data.get("id")) != phone_id:
            raise MetaWhatsAppError("Meta returned a different phone number than the one selected during setup.")
        account_data = graph_request(waba_id, token=access_token, params={"fields": "id"})
        if str(account_data.get("id")) != waba_id:
            raise MetaWhatsAppError("Meta returned a different WhatsApp Business Account than the one selected during setup.")
        # Subscribe the approved Meta app to this account before recording a usable connection.
        graph_request(f"{waba_id}/subscribed_apps", method="POST", token=access_token, body={})
        encrypted_token = encrypt_access_token(access_token)
        with transaction.atomic():
            existing = WhatsAppConnection.objects.select_for_update().filter(business=request.business).first()
            if existing and existing.phone_number_id != phone_id:
                existing.status = WhatsAppConnection.STATUS_DISCONNECTED
                existing.save(update_fields=["status", "updated_at"])
            connection, _ = WhatsAppConnection.objects.update_or_create(
                business=request.business,
                defaults={
                    "created_by": request.user,
                    "waba_id": waba_id,
                    "phone_number_id": phone_id,
                    "display_phone_number": str(phone_data.get("display_phone_number") or "")[:40],
                    "encrypted_access_token": encrypted_token,
                    "status": WhatsAppConnection.STATUS_CONNECTED,
                    "connected_at": timezone.now(),
                    "last_error": "",
                },
            )
        return JsonResponse({"ok": True, "number": connection.display_phone_number, "status": connection.get_status_display()})
    except (ValueError, json.JSONDecodeError, MetaWhatsAppError, WhatsAppTokenError, IntegrityError) as exc:
        return JsonResponse({"detail": str(exc)[:500]}, status=400)


@login_required
@require_POST
def disconnect(request):
    if not is_business_admin(request.user, request.business):
        return render(request, "403.html", status=403)
    WhatsAppConnection.objects.filter(business=request.business).update(
        status=WhatsAppConnection.STATUS_DISCONNECTED,
        assistant_enabled=False,
        encrypted_access_token="",
    )
    messages.success(request, "WhatsApp was disconnected. Conversation history is retained.")
    return redirect("whatsapp_settings")


@login_required
def inbox(request, conversation_id=None):
    if not is_business_admin(request.user, request.business):
        return render(request, "403.html", status=403)
    if not whatsapp_storefront_platform_enabled():
        messages.info(request, "WhatsApp Storefront is not currently available on this platform.")
        return redirect("commerce_dashboard")
    if not _commerce_is_enabled(request.business):
        messages.warning(request, "Enable Commerce before opening the WhatsApp inbox.")
        return redirect("commerce_dashboard")
    conversations = WhatsAppConversation.objects.filter(
        business=request.business
    ).select_related("connection", "assigned_to").prefetch_related("messages")[:100]
    conversation = None
    if conversation_id:
        conversation = get_object_or_404(
            WhatsAppConversation.objects.select_related("connection", "assigned_to").prefetch_related("messages"),
            business=request.business,
            pk=conversation_id,
        )
    return render(request, "whatsapp_storefront/inbox.html", {
        "conversations": conversations,
        "conversation": conversation,
    })


@login_required
@require_POST
def conversation_action(request, conversation_id):
    if not is_business_admin(request.user, request.business):
        return render(request, "403.html", status=403)
    if not whatsapp_storefront_platform_enabled():
        messages.info(request, "WhatsApp Storefront is not currently available on this platform.")
        return redirect("commerce_dashboard")
    if not _commerce_is_enabled(request.business):
        messages.warning(request, "Enable Commerce before managing WhatsApp conversations.")
        return redirect("commerce_dashboard")
    conversation = get_object_or_404(
        WhatsAppConversation.objects.select_related("connection"),
        business=request.business,
        pk=conversation_id,
    )
    action = request.POST.get("action")
    if action == "reply":
        body = request.POST.get("body", "").strip()
        if conversation.connection.status != WhatsAppConnection.STATUS_CONNECTED:
            messages.error(request, "Reconnect the WhatsApp number before sending replies.")
        elif not body or len(body) > 4096:
            messages.error(request, "Enter a message up to 4,096 characters.")
        elif not conversation.last_customer_message_at or conversation.last_customer_message_at < timezone.now() - timedelta(hours=24):
            messages.error(request, "The WhatsApp customer service window is closed. Use an approved template to message this customer.")
        else:
            WhatsAppMessage.objects.create(
                business=request.business,
                conversation=conversation,
                direction=WhatsAppMessage.DIRECTION_OUT,
                body=body,
                status=WhatsAppMessage.STATUS_QUEUED,
                next_attempt_at=timezone.now(),
            )
            conversation.status = WhatsAppConversation.STATUS_HANDOFF
            conversation.assigned_to = request.user
            conversation.last_message_at = timezone.now()
            conversation.save(update_fields=["status", "assigned_to", "last_message_at", "updated_at"])
            messages.success(request, "Reply queued for delivery.")
    elif action == "resume":
        conversation.status = WhatsAppConversation.STATUS_OPEN
        conversation.assigned_to = None
        conversation.save(update_fields=["status", "assigned_to", "updated_at"])
    elif action == "close":
        conversation.status = WhatsAppConversation.STATUS_CLOSED
        conversation.save(update_fields=["status", "updated_at"])
    return redirect("whatsapp_conversation", conversation_id=conversation.pk)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook(request):
    if request.method == "POST":
        return _webhook_receive(request)
    if not settings.META_WHATSAPP_VERIFY_TOKEN:
        return HttpResponse("Webhook verification is not configured.", status=503)
    if (
        request.GET.get("hub.mode") == "subscribe"
        and hmac_compare(request.GET.get("hub.verify_token", ""), settings.META_WHATSAPP_VERIFY_TOKEN)
        and request.GET.get("hub.challenge")
    ):
        return HttpResponse(request.GET["hub.challenge"], content_type="text/plain")
    return HttpResponse("Forbidden", status=403)


def hmac_compare(candidate, expected):
    return hmac.compare_digest(candidate.encode(), expected.encode())


def _webhook_receive(request):
    if len(request.body) > 2_000_000:
        return HttpResponse("Payload too large", status=413)
    if request.content_type != "application/json" or not verify_meta_signature(
        request.body, request.headers.get("X-Hub-Signature-256", "")
    ):
        return HttpResponse("Forbidden", status=403)
    try:
        payload = json.loads(request.body)
        if payload.get("object") != "whatsapp_business_account":
            return HttpResponse("Accepted", status=200)
        persist_webhook_events(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return HttpResponse("Bad request", status=400)
    return HttpResponse("Accepted", status=200)
