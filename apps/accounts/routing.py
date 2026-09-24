from django.urls import path

from .consumers import FounderSignupConsumer


websocket_urlpatterns = [
    path("ws/founder/signups/", FounderSignupConsumer.as_asgi(), name="founder_signup_socket"),
]
