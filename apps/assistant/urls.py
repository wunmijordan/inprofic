from django.urls import path

from . import views

urlpatterns = [
    path("assistant/", views.assistant_page, name="assistant"),
    path("assistant/ask/", views.assistant_ask, name="assistant_ask"),
    path("assistant/settings/", views.assistant_settings, name="assistant_settings"),
]
