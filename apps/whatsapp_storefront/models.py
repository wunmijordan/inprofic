from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import BusinessOwnedModel


class WhatsAppConnection(BusinessOwnedModel):
    STATUS_CONNECTED = "connected"
    STATUS_DISCONNECTED = "disconnected"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_CONNECTED, "Connected"),
        (STATUS_DISCONNECTED, "Disconnected"),
        (STATUS_ERROR, "Needs attention"),
    ]

    waba_id = models.CharField(max_length=80)
    phone_number_id = models.CharField(max_length=80, unique=True)
    display_phone_number = models.CharField(max_length=40, blank=True, default="")
    encrypted_access_token = models.TextField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_CONNECTED)
    assistant_enabled = models.BooleanField(default=False)
    ai_enabled = models.BooleanField(default=False)
    welcome_message = models.CharField(max_length=500, blank=True, default="")
    assistant_instructions = models.TextField(blank=True, default="")
    handoff_message = models.CharField(
        max_length=500,
        default="I’ll pass this to the business team. A person will reply as soon as possible.",
    )
    connected_at = models.DateTimeField(default=timezone.now)
    last_error = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["business"], name="wa_connection_business_uniq")]
        indexes = [models.Index(fields=["business", "status"], name="wa_conn_biz_status_idx")]

    def __str__(self):
        return f"WhatsApp · {self.business_id} · {self.display_phone_number or self.phone_number_id}"


class WhatsAppConversation(BusinessOwnedModel):
    STATUS_OPEN = "open"
    STATUS_HANDOFF = "handoff"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Assistant active"),
        (STATUS_HANDOFF, "Human takeover"),
        (STATUS_CLOSED, "Closed"),
    ]

    connection = models.ForeignKey(WhatsAppConnection, on_delete=models.CASCADE, related_name="conversations")
    customer_wa_id = models.CharField(max_length=40)
    customer_name = models.CharField(max_length=160, blank=True, default="")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_OPEN)
    state = models.JSONField(default=dict, blank=True)
    last_customer_message_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="whatsapp_conversations",
    )

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["connection", "customer_wa_id"], name="wa_conversation_contact_uniq"
        )]
        indexes = [models.Index(fields=["business", "status", "last_message_at"], name="wa_conv_biz_status_idx")]
        ordering = ["-last_message_at", "-id"]


class WhatsAppMessage(BusinessOwnedModel):
    DIRECTION_IN = "in"
    DIRECTION_OUT = "out"
    STATUS_QUEUED = "queued"
    STATUS_SENDING = "sending"
    STATUS_SENT = "sent"
    STATUS_DELIVERED = "delivered"
    STATUS_READ = "read"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"), (STATUS_SENDING, "Sending"), (STATUS_SENT, "Sent"),
        (STATUS_DELIVERED, "Delivered"), (STATUS_READ, "Read"), (STATUS_FAILED, "Failed"),
    ]

    conversation = models.ForeignKey(WhatsAppConversation, on_delete=models.CASCADE, related_name="messages")
    in_reply_to = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replies"
    )
    direction = models.CharField(max_length=3, choices=[(DIRECTION_IN, "Inbound"), (DIRECTION_OUT, "Outbound")])
    provider_message_id = models.CharField(max_length=160, blank=True, default=None, unique=True, null=True)
    body = models.TextField(blank=True, default="")
    message_type = models.CharField(max_length=24, default="text")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_SENT)
    error = models.CharField(max_length=500, blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    provider_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["business", "direction", "status"], name="wa_msg_biz_status_idx")]
        ordering = ["created_at", "id"]


class WhatsAppWebhookEvent(models.Model):
    event_key = models.CharField(max_length=160, unique=True)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["processed_at", "received_at"], name="wa_event_pending_idx")]


class WhatsAppUsageEvent(models.Model):
    business = models.ForeignKey("core.Business", on_delete=models.CASCADE, related_name="whatsapp_usage")
    direction = models.CharField(max_length=3, choices=[("in", "Inbound"), ("out", "Outbound")])
    category = models.CharField(max_length=24, blank=True, default="service")
    occurred_at = models.DateTimeField(default=timezone.now)
    conversation = models.ForeignKey(WhatsAppConversation, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["business", "occurred_at"], name="wa_usage_biz_date_idx")]
