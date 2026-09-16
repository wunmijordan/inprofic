from django.contrib import admin
from .models import Business, CashAccount, FinancialTransaction, AuditLog


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "vertical", "currency_symbol", "accent_color", "background_color")
    list_filter = ("vertical",)
    search_fields = ("name", "slug")
    ordering = ("name",)


admin.site.register(CashAccount)
@admin.register(FinancialTransaction)
class FinancialTransactionAdmin(admin.ModelAdmin): list_display=('date','transaction_type','amount','category','description','payment_method','account','reversed'); list_filter=('transaction_type','category','payment_method')
@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin): list_display=('created_at','action','model_name','object_id','created_by','description'); readonly_fields=('created_at','updated_at')

from .models import AuditQuery
admin.site.register(AuditQuery)
