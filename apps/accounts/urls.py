from django.urls import path
from . import views

urlpatterns = [
    path("", views.users_list, name="users_list"),
    path("add/", views.user_form, name="user_add"),
    path("<int:pk>/edit/", views.user_form, name="user_edit"),
    path("<int:pk>/permissions/", views.user_permissions, name="users_permissions"),
    path("roles/", views.roles_list, name="roles_list"),
    path("roles/add/", views.role_form, name="role_add"),
    path("roles/<int:pk>/edit/", views.role_form, name="role_edit"),
    path("roles/<int:pk>/permissions/", views.role_permissions, name="role_permissions"),
    path("plans/", views.subscription_plans, name="subscription_plans"),
    path("plans/payment/", views.subscription_payment, name="subscription_payment"),
    path("plans/payment/<str:plan_code>/", views.subscription_payment, name="subscription_payment_plan"),
    path("plans/payment/callback/<str:provider>/", views.subscription_payment_callback, name="subscription_payment_callback"),
    path("plans/payment/webhook/<str:provider>/", views.subscription_payment_webhook, name="subscription_payment_webhook"),
    path("plans/services/add/", views.subscription_add_service, name="subscription_add_service"),
    path("founder/subscriptions/", views.founder_subscriptions, name="founder_subscriptions"),
    path("founder/platform/business/<int:pk>/", views.founder_platform_business, name="founder_platform_business"),
    path("founder/platform/business/<int:pk>/delete/", views.founder_platform_business_delete, name="founder_platform_business_delete"),
    path("founder/platform/user/<int:pk>/", views.founder_platform_user, name="founder_platform_user"),
    path("founder/platform/user/<int:pk>/delete/", views.founder_platform_user_delete, name="founder_platform_user_delete"),
]
