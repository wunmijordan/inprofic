from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import (BusinessFeatureAccess, BusinessModuleAccess, BusinessSubscription, CustomUser, MarketingPromoCampaign, PaidPlanTrialClaim, Role, RoleModulePermission, SubscriptionPayment, SubscriptionPaymentSettings, SubscriptionPolicySettings, FounderTrialGrant, PlatformIntegrationSettings, SubscriptionPlan, SubscriptionPlanModule, SubscriptionPromotion, SubscriptionService, UserBusiness, UserModulePermission)

admin.site.site_header = "INPROFIC Founder Administration"
admin.site.site_title = "INPROFIC Admin"
admin.site.index_title = "Platform management"

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    ordering = ("fullname",)
    list_display = ("fullname", "username", "email", "phone", "is_active", "is_superuser")
    readonly_fields = ("last_login", "date_joined")
    fieldsets = ((None, {"fields": ("username", "password")}),
                 ("Personal", {"fields": ("fullname", "email", "phone")}),
                 ("Access", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
                 ("Dates", {"fields": ("last_login", "date_joined")}))
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("fullname", "username", "email", "phone", "password1", "password2", "is_active", "is_staff", "is_superuser")}),)
    search_fields = ("fullname", "username", "email", "phone")

@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "key", "is_system", "visible_to_admin")
    list_filter = ("is_system", "visible_to_admin")
    search_fields = ("name", "key", "business__name")


admin.site.register(RoleModulePermission)


@admin.register(UserBusiness)
class UserBusinessAdmin(admin.ModelAdmin):
    list_display = ("user", "business", "role", "active")
    list_filter = ("active", "role")
    search_fields = ("user__username", "user__email", "business__name")
    autocomplete_fields = ("user", "business", "role")


admin.site.register(UserModulePermission)
admin.site.register(BusinessModuleAccess)

@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "monthly_price", "user_limit", "additional_service_limit", "trial_days", "active")
    list_filter = ("active",)
    search_fields = ("name", "code")

admin.site.register(SubscriptionPlanModule)


@admin.register(SubscriptionPromotion)
class SubscriptionPromotionAdmin(admin.ModelAdmin):
    list_display = ("reason", "plan", "billing_cycle", "discount_type", "discount_value", "starts_at", "ends_at", "active")
    list_filter = ("active", "billing_cycle", "discount_type", "plan")
    search_fields = ("reason", "plan__name")
    date_hierarchy = "starts_at"


@admin.register(MarketingPromoCampaign)
class MarketingPromoCampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "promotion", "theme", "animation_style", "priority", "active")
    list_filter = ("active", "theme", "animation_style")
    search_fields = ("name", "promotion__reason")


@admin.register(BusinessSubscription)
class BusinessSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("primary_business", "plan", "status", "trial_ends_at", "paid_until", "founder_lifetime")
    list_filter = ("status", "plan", "founder_lifetime")
    search_fields = ("primary_business__name", "primary_business__slug")
    autocomplete_fields = ("primary_business", "plan", "founder_granted_by")

admin.site.register(SubscriptionService)
admin.site.register(BusinessFeatureAccess)
@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ("reference", "subscription", "plan", "amount", "provider", "status", "created_at")
    list_filter = ("status", "provider", "billing_cycle", "plan")
    search_fields = ("reference", "subscription__primary_business__name")
    readonly_fields = ("reference", "created_at", "paid_at")

admin.site.register(SubscriptionPaymentSettings)
admin.site.register(SubscriptionPolicySettings)
admin.site.register(FounderTrialGrant)
admin.site.register(PlatformIntegrationSettings)

@admin.register(PaidPlanTrialClaim)
class PaidPlanTrialClaimAdmin(admin.ModelAdmin):
    list_display = ("credential_kind", "masked_fingerprint", "user", "subscription", "plan", "claimed_at")
    list_filter = ("credential_kind", "plan")
    search_fields = ("user__username", "user__email", "subscription__primary_business__name")
    readonly_fields = ("credential_kind", "credential_fingerprint", "user", "subscription", "plan", "claimed_at")

    @admin.display(description="Credential fingerprint")
    def masked_fingerprint(self, obj):
        return f"{obj.credential_fingerprint[:10]}…"

    def has_add_permission(self, request):
        return False
