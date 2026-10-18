import hashlib
import hmac
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from commerce.models import CommerceSettings

from .assistant import build_reply
from .meta import MetaWhatsAppError, send_text
from .models import WhatsAppConnection, WhatsAppConversation, WhatsAppMessage, WhatsAppUsageEvent, WhatsAppWebhookEvent

logger = logging.getLogger(__name__)


def _commerce_channel_enabled(business):
    from accounts.services import business_has_module
    from accounts.platform_integrations import whatsapp_storefront_platform_enabled

    config = CommerceSettings.raw_objects.filter(business=business).first()
    return bool(
        whatsapp_storefront_platform_enabled()
        and
        business_has_module(business, "commerce")
        and config and config.enabled and config.hosted_storefront_enabled
    )


def verify_meta_signature(raw_body, supplied_signature):
    secret = settings.META_WHATSAPP_APP_SECRET
    if not secret or not supplied_signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied_signature)


def persist_webhook_events(payload):
    """Persist every message/status event before acknowledging Meta."""
    accepted = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            phone_id = str(metadata.get("phone_number_id") or "")
            for message in value.get("messages", []):
                key = f"message:{phone_id}:{message.get('id', '')}"
                if not message.get("id"):
                    key = "message:payload:" + hashlib.sha256(json.dumps(message, sort_keys=True).encode()).hexdigest()
                event_payload = {"kind": "message", "phone_number_id": phone_id, "message": message,
                                 "contact": (value.get("contacts") or [{}])[0]}
                try:
                    WhatsAppWebhookEvent.objects.create(event_key=key, payload=event_payload)
                    accepted += 1
                except IntegrityError:
                    continue
            for status in value.get("statuses", []):
                stamp = str(status.get("timestamp") or "")
                key = f"status:{phone_id}:{status.get('id', '')}:{status.get('status', '')}:{stamp}"
                try:
                    WhatsAppWebhookEvent.objects.create(
                        event_key=key,
                        payload={"kind": "status", "phone_number_id": phone_id, "status": status},
                    )
                    accepted += 1
                except IntegrityError:
                    continue
    return accepted


def _message_text(message):
    message_type = message.get("type", "text")
    if message_type == "text":
        return (message.get("text") or {}).get("body", "")[:4096], message_type
    interactive = message.get("interactive") or {}
    reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
    if reply:
        return str(reply.get("title") or reply.get("id") or "")[:4096], "interactive"
    if message_type == "button":
        return str((message.get("button") or {}).get("text") or "")[:4096], "button"
    return f"[{message_type} message received]", message_type


@transaction.atomic
def process_one_event(event):
    event = WhatsAppWebhookEvent.objects.select_for_update().get(pk=event.pk)
    if event.processed_at:
        return
    data = event.payload
    connection = WhatsAppConnection.raw_objects.select_related("business").filter(
        phone_number_id=data.get("phone_number_id")
    ).first()
    if not connection:
        event.last_error = "No active tenant connection matches this WhatsApp phone number."
        event.attempts += 1
        if event.attempts >= 3:
            event.processed_at = timezone.now()
            event.save(update_fields=["last_error", "attempts", "processed_at"])
        else:
            event.save(update_fields=["last_error", "attempts"])
        return

    if data.get("kind") == "status":
        status_data = data.get("status") or {}
        message = WhatsAppMessage.objects.filter(
            business_id=connection.business_id,
            provider_message_id=status_data.get("id"),
            direction=WhatsAppMessage.DIRECTION_OUT,
        ).first()
        if message:
            state = status_data.get("status")
            mapping = {"sent": message.STATUS_SENT, "delivered": message.STATUS_DELIVERED,
                       "read": message.STATUS_READ, "failed": message.STATUS_FAILED}
            rank = {
                message.STATUS_QUEUED: 0,
                message.STATUS_SENDING: 0,
                message.STATUS_SENT: 1,
                message.STATUS_DELIVERED: 2,
                message.STATUS_READ: 3,
                message.STATUS_FAILED: 4,
            }
            if state in mapping and rank[mapping[state]] >= rank.get(message.status, 0):
                message.status = mapping[state]
                if state == "failed":
                    message.error = str((status_data.get("errors") or [{}])[0].get("title") or "Meta send failed")[:500]
                message.save(update_fields=["status", "error", "updated_at"])
        event.processed_at = timezone.now()
        event.save(update_fields=["processed_at"])
        return

    source = data.get("message") or {}
    sender = str(source.get("from") or "")
    message_id = str(source.get("id") or "")
    if not sender or not message_id:
        event.processed_at = timezone.now()
        event.save(update_fields=["processed_at"])
        return
    name = str(((data.get("contact") or {}).get("profile") or {}).get("name") or "")[:160]
    conversation, _ = WhatsAppConversation.raw_objects.get_or_create(
        business=connection.business,
        connection=connection,
        customer_wa_id=sender,
        defaults={"customer_name": name},
    )
    body, message_type = _message_text(source)
    message, created = WhatsAppMessage.raw_objects.get_or_create(
        provider_message_id=message_id,
        defaults={
            "business": connection.business,
            "conversation": conversation,
            "direction": WhatsAppMessage.DIRECTION_IN,
            "body": body,
            "message_type": message_type,
            "status": WhatsAppMessage.STATUS_DELIVERED,
            "provider_payload": source,
        },
    )
    if created:
        now = timezone.now()
        conversation.customer_name = name or conversation.customer_name
        conversation.status = WhatsAppConversation.STATUS_OPEN if conversation.status == WhatsAppConversation.STATUS_CLOSED else conversation.status
        conversation.last_customer_message_at = now
        conversation.last_message_at = now
        conversation.save(update_fields=["customer_name", "status", "last_customer_message_at", "last_message_at", "updated_at"])
        WhatsAppUsageEvent.objects.create(business=connection.business, direction="in", conversation=conversation)
        if (connection.assistant_enabled
                and conversation.status != WhatsAppConversation.STATUS_HANDOFF
                and connection.status == WhatsAppConnection.STATUS_CONNECTED
                and _commerce_channel_enabled(connection.business)):
            WhatsAppMessage.raw_objects.create(
                business=connection.business,
                conversation=conversation,
                direction=WhatsAppMessage.DIRECTION_OUT,
                in_reply_to=message,
                body="",
                message_type="assistant_pending",
                status=WhatsAppMessage.STATUS_QUEUED,
                next_attempt_at=now,
            )
            conversation.last_message_at = now
            conversation.save(update_fields=["last_message_at", "updated_at"])
    event.processed_at = timezone.now()
    event.last_error = ""
    event.save(update_fields=["processed_at", "last_error"])


def process_pending_events(limit=100):
    processed = 0
    pending = list(WhatsAppWebhookEvent.objects.filter(processed_at__isnull=True).order_by("received_at")[:limit])
    for event in pending:
        try:
            process_one_event(event)
            processed += 1
        except Exception as exc:
            WhatsAppWebhookEvent.objects.filter(pk=event.pk, processed_at__isnull=True).update(
                attempts=event.attempts + 1, last_error=str(exc)[:500]
            )
            logger.exception("WhatsApp webhook event processing failed (event_id=%s)", event.pk)
    return processed


def process_assistant_replies(limit=100):
    """Generate replies outside database transactions from durable placeholders."""
    pending = list(WhatsAppMessage.raw_objects.select_related(
        "business", "conversation__connection", "in_reply_to"
    ).filter(
        direction=WhatsAppMessage.DIRECTION_OUT,
        message_type="assistant_pending",
        status=WhatsAppMessage.STATUS_QUEUED,
        next_attempt_at__lte=timezone.now(),
    ).order_by("created_at")[:limit])
    generated = 0
    for message in pending:
        claim_until = timezone.now() + timedelta(minutes=10)
        claimed = WhatsAppMessage.raw_objects.filter(
            pk=message.pk,
            message_type="assistant_pending",
            status=WhatsAppMessage.STATUS_QUEUED,
        ).update(status=WhatsAppMessage.STATUS_SENDING, next_attempt_at=claim_until)
        if not claimed:
            continue
        conversation = message.conversation
        conversation.refresh_from_db()
        conversation.connection.refresh_from_db()
        if (not message.in_reply_to_id
                or conversation.status in {WhatsAppConversation.STATUS_HANDOFF, WhatsAppConversation.STATUS_CLOSED}
                or conversation.connection.status != WhatsAppConnection.STATUS_CONNECTED
                or not conversation.connection.assistant_enabled
                or not _commerce_channel_enabled(conversation.business)):
            WhatsAppMessage.raw_objects.filter(pk=message.pk).update(
                status=WhatsAppMessage.STATUS_FAILED,
                error="Assistant reply was stopped because the conversation needs a person or the number is disconnected.",
            )
            continue
        newer_customer_message = conversation.messages.filter(
            direction=WhatsAppMessage.DIRECTION_IN,
            pk__gt=message.in_reply_to_id,
        ).exists()
        if newer_customer_message:
            WhatsAppMessage.raw_objects.filter(pk=message.pk).update(
                status=WhatsAppMessage.STATUS_FAILED,
                error="A newer customer message arrived; the assistant will answer the latest message instead.",
            )
            continue
        try:
            reply = build_reply(conversation, message.in_reply_to.body)
            first_inbound = not conversation.messages.filter(
                direction=WhatsAppMessage.DIRECTION_IN,
                pk__lt=message.in_reply_to_id,
            ).exists()
            if first_inbound and conversation.connection.welcome_message:
                reply = f"{conversation.connection.welcome_message}\n\n{reply}"[:4096]
        except Exception as exc:
            logger.exception("WhatsApp assistant reply generation failed (message_id=%s)", message.pk)
            WhatsAppMessage.raw_objects.filter(pk=message.pk).update(
                status=WhatsAppMessage.STATUS_FAILED,
                error=f"Assistant response generation failed: {str(exc)[:440]}",
            )
            continue
        WhatsAppMessage.raw_objects.filter(pk=message.pk).update(
            body=reply,
            message_type="text",
            status=WhatsAppMessage.STATUS_QUEUED,
            next_attempt_at=timezone.now(),
        )
        generated += 1
    return generated


def process_outbound_messages(limit=100):
    now = timezone.now()
    # A process may die after Meta accepted a message but before its response was
    # committed. Do not blindly resend an ambiguous request and risk duplicates.
    WhatsAppMessage.raw_objects.filter(
        direction=WhatsAppMessage.DIRECTION_OUT,
        status=WhatsAppMessage.STATUS_SENDING,
        next_attempt_at__lt=now,
        message_type="assistant_pending",
    ).update(
        status=WhatsAppMessage.STATUS_FAILED,
        error="Assistant response generation exceeded its worker lease; retry with a new customer message.",
    )
    WhatsAppMessage.raw_objects.filter(
        direction=WhatsAppMessage.DIRECTION_OUT,
        status=WhatsAppMessage.STATUS_SENDING,
        next_attempt_at__lt=now,
    ).exclude(message_type="assistant_pending").update(
        status=WhatsAppMessage.STATUS_FAILED,
        error="Send outcome is unknown after a worker timeout; review the conversation before retrying.",
    )
    queued = list(WhatsAppMessage.raw_objects.select_related(
        "business", "conversation__connection", "in_reply_to"
    ).filter(direction=WhatsAppMessage.DIRECTION_OUT, status=WhatsAppMessage.STATUS_QUEUED,
             next_attempt_at__lte=now).exclude(message_type="assistant_pending").order_by("created_at")[:limit])
    sent = 0
    for message in queued:
        message.conversation.refresh_from_db()
        message.conversation.connection.refresh_from_db()
        if (message.in_reply_to_id
                and message.conversation.status in {WhatsAppConversation.STATUS_HANDOFF, WhatsAppConversation.STATUS_CLOSED}
                and not message.body.endswith(message.conversation.connection.handoff_message)):
            WhatsAppMessage.raw_objects.filter(pk=message.pk, status=WhatsAppMessage.STATUS_QUEUED).update(
                status=WhatsAppMessage.STATUS_FAILED,
                error="A person took over this conversation before the assistant reply was sent.",
            )
            continue
        if message.conversation.connection.status != WhatsAppConnection.STATUS_CONNECTED:
            WhatsAppMessage.raw_objects.filter(pk=message.pk, status=WhatsAppMessage.STATUS_QUEUED).update(
                status=WhatsAppMessage.STATUS_FAILED,
                error="Reconnect this business WhatsApp number before sending the queued reply.",
            )
            continue
        if not _commerce_channel_enabled(message.business):
            WhatsAppMessage.raw_objects.filter(pk=message.pk, status=WhatsAppMessage.STATUS_QUEUED).update(
                status=WhatsAppMessage.STATUS_FAILED,
                error="Commerce or the hosted storefront is no longer enabled for this business.",
            )
            continue
        claim_until = timezone.now() + timedelta(minutes=10)
        claimed = WhatsAppMessage.raw_objects.filter(
            pk=message.pk, status=WhatsAppMessage.STATUS_QUEUED
        ).update(
            status=WhatsAppMessage.STATUS_SENDING,
            attempts=F("attempts") + 1,
            next_attempt_at=claim_until,
        )
        if not claimed:
            continue
        attempt = message.attempts + 1
        connection = message.conversation.connection
        last_inbound = message.conversation.last_customer_message_at
        if not last_inbound or last_inbound < timezone.now() - timedelta(hours=24):
            WhatsAppMessage.raw_objects.filter(pk=message.pk).update(
                status=WhatsAppMessage.STATUS_FAILED, attempts=attempt,
                error="Customer service window expired; an approved template is required.",
            )
            continue
        try:
            result = send_text(connection, message.conversation.customer_wa_id, message.body)
            provider_id = str(((result.get("messages") or [{}])[0]).get("id") or "")
            if not provider_id:
                WhatsAppMessage.raw_objects.filter(pk=message.pk).update(
                    status=WhatsAppMessage.STATUS_FAILED,
                    attempts=attempt,
                    error="Meta accepted the send request without returning a message ID; check provider delivery history.",
                )
                continue
            WhatsAppMessage.raw_objects.filter(pk=message.pk).update(
                provider_message_id=provider_id, status=WhatsAppMessage.STATUS_SENT,
                sent_at=timezone.now(), attempts=attempt, error="",
            )
            WhatsAppUsageEvent.objects.create(
                business=message.business, direction="out", category="service", conversation=message.conversation
            )
            WhatsAppConversation.raw_objects.filter(pk=message.conversation_id).update(last_message_at=timezone.now())
            sent += 1
        except MetaWhatsAppError as exc:
            attempts = attempt
            WhatsAppMessage.raw_objects.filter(pk=message.pk).update(
                status=WhatsAppMessage.STATUS_QUEUED if attempts < 4 else WhatsAppMessage.STATUS_FAILED,
                attempts=attempts, error=str(exc)[:500],
                next_attempt_at=timezone.now() + timedelta(minutes=2 ** attempts),
            )
            if attempts >= 4:
                WhatsAppConnection.raw_objects.filter(pk=connection.pk).update(
                    status=WhatsAppConnection.STATUS_ERROR, last_error=str(exc)[:500]
                )
        except Exception as exc:
            logger.exception("WhatsApp outbound send failed (message_id=%s)", message.pk)
            WhatsAppMessage.raw_objects.filter(pk=message.pk).update(
                status=WhatsAppMessage.STATUS_FAILED, attempts=attempt, error=str(exc)[:500]
            )
    return sent


def process_whatsapp_queue():
    return process_pending_events() + process_assistant_replies() + process_outbound_messages()
