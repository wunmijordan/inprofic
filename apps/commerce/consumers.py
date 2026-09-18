from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from accounts.services import business_has_module, user_has_permission
from core.models import Business

from .models import DeliveryAssignment, DeliverySettings
from .realtime import business_notification_group, public_delivery_group, user_notification_group


class CommerceNotificationConsumer(AsyncJsonWebsocketConsumer):
    """Push tenant activity and delivery refresh signals to authorized staff."""

    async def connect(self):
        user = self.scope.get("user")
        session = self.scope.get("session")
        if not getattr(user, "is_authenticated", False) or session is None:
            await self.close(code=4403)
            return
        access = await database_sync_to_async(self._resolve_access)(
            user, session.get("active_business_id")
        )
        if access is None:
            await self.close(code=4403)
            return

        self.business_id, self.user_id, include_business_group = access
        groups = [user_notification_group(self.business_id, self.user_id)]
        if include_business_group:
            groups.insert(0, business_notification_group(self.business_id))
        self.notification_groups = tuple(groups)
        for group in self.notification_groups:
            await self.channel_layer.group_add(group, self.channel_name)
        await self.accept()
        await self.send_json({"type": "notifications.ready"})

    async def disconnect(self, close_code):
        for group in getattr(self, "notification_groups", ()):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def notifications_changed(self, event):
        await self.send_json({
            "type": "notifications.changed",
            "reason": event.get("reason", "changed"),
        })

    async def delivery_changed(self, event):
        await self.send_json({
            "type": "delivery.changed",
            "delivery_id": event.get("delivery_id"),
            "reason": event.get("reason", "status"),
        })

    @staticmethod
    def _resolve_access(user, business_id):
        if not business_id:
            return None
        business = Business.objects.filter(pk=business_id).first()
        if business is None:
            return None
        broad_delivery_access = any((
            user_has_permission(user, business, "commerce", "view"),
            user_has_permission(user, business, "delivery", "view"),
        ))
        rider_access = user_has_permission(user, business, "delivery_rider", "view")
        if not broad_delivery_access and not rider_access:
            return None
        # Rider-only users intentionally stay off the tenant-wide group. They
        # receive only their direct notification/update channel.
        return business.pk, user.pk, broad_delivery_access


class StorefrontDeliveryConsumer(AsyncJsonWebsocketConsumer):
    """Customer-safe wake-up channel for one unguessable public delivery URL.

    No order/customer details are sent over the socket. The hosted or headless
    client receives only a change signal, then fetches the current tracking
    snapshot over HTTP. Access therefore mirrors the existing public tracking
    URL: knowledge of the UUID is the capability.
    """

    async def connect(self):
        kwargs = self.scope.get("url_route", {}).get("kwargs", {})
        access = await database_sync_to_async(self._resolve_delivery)(
            kwargs.get("business_slug"), kwargs.get("public_id")
        )
        if access is None:
            await self.close(code=4404)
            return
        business_id, public_id = access
        self.delivery_group = public_delivery_group(business_id, public_id)
        await self.channel_layer.group_add(self.delivery_group, self.channel_name)
        await self.accept()
        await self.send_json({"type": "delivery.ready", "delivery_id": str(public_id)})

    async def disconnect(self, close_code):
        group = getattr(self, "delivery_group", None)
        if group:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def delivery_changed(self, event):
        await self.send_json({
            "type": "delivery.changed",
            "delivery_id": event.get("delivery_id"),
            "reason": event.get("reason", "status"),
        })

    @staticmethod
    def _resolve_delivery(business_slug, public_id):
        business = Business.objects.filter(slug=business_slug).first()
        if business is None or not business_has_module(business, "delivery"):
            return None
        if not DeliverySettings.raw_objects.filter(
            business=business, enabled=True, customer_tracking_enabled=True
        ).exists():
            return None
        assignment = DeliveryAssignment.raw_objects.filter(
            business=business, public_id=public_id
        ).only("public_id").first()
        if assignment is None:
            return None
        return business.pk, assignment.public_id
