from django.contrib import admin
from .models import (
    CommerceCheckoutItem,
    CommerceCheckoutSession,
    CommerceGatewayEvent,
    CommerceIntegration,
    CommerceIntake,
    CommerceIntakeItem,
    CommerceNotification,
    CommerceNotificationRead,
    CommercePayment,
    CommercePaymentAllocation,
    CommercePaymentClaim,
    CommercePaymentConfiguration,
    CommercePaymentReceipt,
    CommerceSettings,
    StorefrontProduct,
)

admin.site.register(CommerceSettings)
admin.site.register(CommerceIntegration)
admin.site.register(StorefrontProduct)
admin.site.register(CommerceIntake)
admin.site.register(CommerceIntakeItem)


@admin.register(CommercePaymentConfiguration)
class CommercePaymentConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        "business", "currency", "paystack_enabled", "monnify_enabled",
        "bank_transfer_enabled", "cash_enabled",
    )
    exclude = ("paystack_secret_key", "monnify_api_key", "monnify_secret_key")


class ImmutablePaymentAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(CommerceCheckoutSession, ImmutablePaymentAdmin)
admin.site.register(CommerceCheckoutItem, ImmutablePaymentAdmin)
admin.site.register(CommercePayment, ImmutablePaymentAdmin)
admin.site.register(CommercePaymentClaim, ImmutablePaymentAdmin)
admin.site.register(CommercePaymentReceipt, ImmutablePaymentAdmin)
admin.site.register(CommercePaymentAllocation, ImmutablePaymentAdmin)
admin.site.register(CommerceGatewayEvent, ImmutablePaymentAdmin)
admin.site.register(CommerceNotification, ImmutablePaymentAdmin)
admin.site.register(CommerceNotificationRead, ImmutablePaymentAdmin)

from .models import DeliveryProviderAccount
admin.site.register(DeliveryProviderAccount)
