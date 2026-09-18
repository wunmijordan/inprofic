from django.urls import path

from .consumers import CommerceNotificationConsumer, StorefrontDeliveryConsumer


websocket_urlpatterns = [
    path(
        "ws/commerce/notifications/",
        CommerceNotificationConsumer.as_asgi(),
        name="commerce_notification_socket",
    ),
    path(
        "ws/storefront/<slug:business_slug>/deliveries/<uuid:public_id>/",
        StorefrontDeliveryConsumer.as_asgi(),
        name="storefront_delivery_socket",
    ),
]
