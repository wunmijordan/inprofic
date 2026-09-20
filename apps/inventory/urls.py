from django.urls import path
from . import alert_views, views

urlpatterns = [
    path("alerts/feed/", alert_views.alert_feed, name="inventory_alert_feed"),
    path("alerts/acknowledge/", alert_views.alert_acknowledge, name="inventory_alert_acknowledge"),
    path("alert-settings/", alert_views.alert_settings_view, name="inventory_alert_settings"),
    path("", views.inventory, name="inventory"),
    path("raw-material/add/", views.raw_material_form, name="raw_material_add"),
    path("raw-material/export/pdf/", views.raw_material_inventory_pdf, name="raw_material_inventory_pdf"),
    path("raw-material/<int:pk>/edit/", views.raw_material_form, name="raw_material_edit"),
    path("raw-material/<int:pk>/delete/", views.raw_material_delete, name="raw_material_delete"),
    path("product-categories/", views.product_category_list, name="product_categories"),
    path("product-categories/<int:pk>/edit/", views.product_category_edit, name="product_category_edit"),
    path("product-categories/<int:pk>/toggle/", views.product_category_toggle, name="product_category_toggle"),
    path("product/add/", views.finished_good_form, name="finished_good_add"),
    path("product/<int:pk>/edit/", views.finished_good_form, name="finished_good_edit"),
    path("product/<int:pk>/delete/", views.finished_good_delete, name="finished_good_delete"),
    path("market-stock/", views.market_stock, name="market_stock"),
    path("market-stock/release/", views.market_stock_release, name="market_stock_release"),
    path("market-stock/transfer/", views.market_stock_transfer, name="market_stock_transfer"),
    path("market-stock/return/", views.distribution_return, name="distribution_return"),
    path("market-stock/lots/<int:pk>/expire/", views.market_stock_expire, name="market_stock_expire"),
    path("stock-history/<str:kind>/<int:pk>/", views.stock_history, name="stock_history"),
    path("operational-supply/dispense/", views.operational_supply_dispense, name="operational_supply_dispense"),
]
