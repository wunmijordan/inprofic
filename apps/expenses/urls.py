from django.urls import path
from . import views, requests

urlpatterns = [
    path("requests/add/", requests.request_form, name="expense_request_add"),
    path("requests/<int:pk>/", requests.request_form, name="expense_request_detail"),
    path("requests/<int:pk>/snapshot.png", requests.request_image, name="expense_request_image"),
    path("", views.expenses_list, name="expenses_list"),
    path("add/", views.expense_form, name="expense_add"),
    path("<int:pk>/edit/", views.expense_form, name="expense_edit"),
    path("<int:pk>/delete/", views.expense_delete, name="expense_delete"),
    path("<int:pk>/invoice/", views.expense_invoice, name="expense_invoice"),
    path("payments/add/", views.expense_payment_form, name="expense_payment_add"),
]
