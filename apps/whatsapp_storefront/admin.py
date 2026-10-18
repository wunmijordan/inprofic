from django.contrib import admin

from .models import WhatsAppConnection, WhatsAppConversation, WhatsAppMessage, WhatsAppUsageEvent, WhatsAppWebhookEvent


class PlatformAdminOnly:
    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_superuser)

    def has_change_permission(self, request, obj=None):
        return bool(request.user.is_superuser)

    def has_add_permission(self, request):
        return bool(request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return bool(request.user.is_superuser)


@admin.register(WhatsAppConnection)
class WhatsAppConnectionAdmin(PlatformAdminOnly, admin.ModelAdmin):
    list_display = ("business", "display_phone_number", "status", "assistant_enabled", "connected_at")
    list_filter = ("status", "assistant_enabled", "ai_enabled")
    search_fields = ("business__name", "business__slug", "display_phone_number", "waba_id", "phone_number_id")
    exclude = ("encrypted_access_token",)
    readonly_fields = ("business", "waba_id", "phone_number_id", "connected_at", "last_error")


@admin.register(WhatsAppConversation)
class WhatsAppConversationAdmin(PlatformAdminOnly, admin.ModelAdmin):
    list_display = ("business", "customer_name", "customer_wa_id", "status", "last_message_at", "assigned_to")
    list_filter = ("status",)
    search_fields = ("business__name", "customer_name", "customer_wa_id")
    readonly_fields = ("business", "connection", "customer_wa_id", "state", "last_customer_message_at", "last_message_at")


@admin.register(WhatsAppMessage)
class WhatsAppMessageAdmin(PlatformAdminOnly, admin.ModelAdmin):
    list_display = ("business", "conversation", "direction", "status", "created_at")
    list_filter = ("direction", "status")
    search_fields = ("business__name", "body", "provider_message_id")
    readonly_fields = ("business", "conversation", "direction", "body", "provider_message_id", "provider_payload")


@admin.register(WhatsAppWebhookEvent)
class WhatsAppWebhookEventAdmin(PlatformAdminOnly, admin.ModelAdmin):
    list_display = ("event_key", "received_at", "processed_at", "attempts")
    readonly_fields = ("event_key", "payload", "received_at", "processed_at", "attempts", "last_error")


@admin.register(WhatsAppUsageEvent)
class WhatsAppUsageEventAdmin(PlatformAdminOnly, admin.ModelAdmin):
    list_display = ("business", "direction", "category", "occurred_at")
    list_filter = ("direction", "category")
    readonly_fields = ("business", "direction", "category", "occurred_at", "conversation")
