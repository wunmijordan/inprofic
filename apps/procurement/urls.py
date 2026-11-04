from django.urls import path
from . import views

urlpatterns = [
    path("", views.procurement_list, name="procurement_list"),
    path("suppliers/add/", views.supplier_add, name="supplier_add"),
    path("suppliers/<int:pk>/edit/", views.supplier_edit, name="supplier_edit"),
    path("add/", views.po_form, name="po_add"),
    path("<int:pk>/edit/", views.po_form, name="po_edit"),
    path("<int:pk>/approve/", views.po_approve, name="po_approve"),
    path("<int:pk>/share.png", views.po_share_image, name="po_share_image"),
    path("<int:pk>/receive/", views.po_receive, name="po_receive"),
    path("<int:pk>/invoice/", views.po_invoice, name="po_invoice"),
    path("<int:pk>/delete/", views.po_delete, name="po_delete"),
]
