from django.urls import path
from . import views
from . import spending_views

urlpatterns = [
    path("summary.png", spending_views.spending_snapshot, name="spending_snapshot"),
    path("spending.csv", spending_views.spending_export, {'file_format': 'csv'}, name="spending_csv"),
    path("spending.xlsx", spending_views.spending_export, {'file_format': 'xlsx'}, name="spending_xlsx"),
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
