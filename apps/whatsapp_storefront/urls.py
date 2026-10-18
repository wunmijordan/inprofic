from django.urls import path

from . import views

urlpatterns = [
    path("commerce/whatsapp/", views.settings_page, name="whatsapp_settings"),
    path("commerce/whatsapp/connect/", views.embedded_signup_complete, name="whatsapp_embedded_signup_complete"),
    path("commerce/whatsapp/disconnect/", views.disconnect, name="whatsapp_disconnect"),
    path("commerce/whatsapp/inbox/", views.inbox, name="whatsapp_inbox"),
    path("commerce/whatsapp/inbox/<int:conversation_id>/", views.inbox, name="whatsapp_conversation"),
    path("commerce/whatsapp/inbox/<int:conversation_id>/action/", views.conversation_action, name="whatsapp_conversation_action"),
    path("api/v1/whatsapp/webhook/", views.webhook, name="whatsapp_webhook"),
]
