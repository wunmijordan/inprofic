from django.urls import path
from . import views, offline, imports

urlpatterns = [
    path(
        "retail/cashier/<int:pk>/", views.cashier_detail, name="retail_cashier_detail"
    ),
    path(
        "retail/branches/<int:pk>/settings/",
        views.branch_settings,
        name="retail_branch_settings",
    ),
    path(
        "retail/branches/<int:pk>/enable/",
        views.branch_workspace_enable,
        name="retail_branch_enable",
    ),
    path("retail/baskets/", views.parked_baskets, name="retail_baskets"),
    path(
        "retail/products/<int:pk>/barcode/",
        views.barcode_label,
        name="retail_barcode_label",
    ),
    path(
        "retail/products/import/", imports.import_products, name="retail_product_import"
    ),
    path("retail/offline/snapshot/", offline.snapshot, name="retail_offline_snapshot"),
    path("retail/offline/sync/", offline.sync, name="retail_offline_sync"),
    path("retail/", views.workspace, name="retail_workspace"),
    path("retail/branches/add/", views.branch_add, name="retail_branch_add"),
    path("retail/branches/switch/", views.branch_switch, name="retail_branch_switch"),
    path("retail/branches/staff/", views.branch_staff, name="retail_branch_staff"),
    path("retail/transfers/add/", views.transfer_add, name="retail_transfer_add"),
    path(
        "retail/transfers/<int:pk>/receive/",
        views.transfer_receive,
        name="retail_transfer_receive",
    ),
    path("retail/profile/", views.profile, name="retail_profile"),
    path("retail/devices/", views.devices, name="retail_devices"),
    path(
        "retail/devices/register/", views.register_device, name="retail_device_register"
    ),
    path(
        "retail/devices/<int:pk>/review/",
        views.device_review,
        name="retail_device_review",
    ),
    path("retail/cashier/open/", views.cashier_open, name="retail_cashier_open"),
    path(
        "retail/cashier/<int:pk>/close/",
        views.cashier_close,
        name="retail_cashier_close",
    ),
    path(
        "retail/cashier/<int:pk>/review/",
        views.cashier_review,
        name="retail_cashier_review",
    ),
    path("retail/promotions/", views.promotions, name="retail_promotions"),
    path("retail/promotions/add/", views.promotion_form, name="retail_promotion_add"),
    path(
        "retail/promotions/<int:pk>/edit/",
        views.promotion_form,
        name="retail_promotion_edit",
    ),
    path("retail/quotations/", views.quotations, name="retail_quotations"),
    path("retail/quotations/add/", views.quotation_add, name="retail_quotation_add"),
    path(
        "retail/quotations/<int:pk>/",
        views.quotation_detail,
        name="retail_quotation_detail",
    ),
    path(
        "retail/quotations/<int:pk>/convert/",
        views.quotation_convert,
        name="retail_quotation_convert",
    ),
    path(
        "shared/quotations/<uuid:public_id>/",
        views.quotation_public,
        name="retail_quotation_public",
    ),
    path("retail/expiration/", views.expiration, name="retail_expiration"),
    path(
        "retail/expiration/<int:pk>/flag/",
        views.expiration_flag,
        name="retail_expiration_flag",
    ),
]
