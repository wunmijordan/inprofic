"""
ASGI config for storetrack project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'storetrack.settings')

django_asgi_application = get_asgi_application()

from accounts.routing import websocket_urlpatterns as account_websocket_urlpatterns  # noqa: E402
from commerce.routing import websocket_urlpatterns as commerce_websocket_urlpatterns  # noqa: E402

websocket_urlpatterns = [*account_websocket_urlpatterns, *commerce_websocket_urlpatterns]

application = ProtocolTypeRouter({
    "http": django_asgi_application,
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
    ),
})
