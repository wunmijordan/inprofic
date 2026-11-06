from django.urls import path
from . import views
from .repricing_views import customer_reprice

urlpatterns = [
    path("customers/<int:pk>/reprice/", customer_reprice, name="customer_reprice"),
    path("customers/", views.customer_list, name="customer_list"),
    path("customers/<int:pk>/activity/", views.customer_activity, name="customer_activity"),
    path("customers/add/", views.customer_form, name="customer_add"),
    path("customers/<int:pk>/edit/", views.customer_form, name="customer_edit"),
    path("customers/<int:pk>/toggle-active/", views.customer_toggle_active, name="customer_toggle_active"),
    path("", views.sales_list, name="sales_list"),
    path("add/", views.sale_form, name="sale_add"),
    path("<int:pk>/delete/", views.sale_delete, name="sale_delete"),
    path("<int:pk>/invoice/", views.sale_invoice, name="sale_invoice"),
]
