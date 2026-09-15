from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from accounts.services import user_has_permission
from core.models import Business

from .realtime import business_notification_group, user_notification_group


class CommerceNotificationConsumer(AsyncJsonWebsocketConsumer):
    """Push refresh signals only to authorized members of the active tenant."""

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

        self.business_id, self.user_id = access
        self.notification_groups = (
            business_notification_group(self.business_id),
            user_notification_group(self.business_id, self.user_id),
        )
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

    @staticmethod
    def _resolve_access(user, business_id):
        if not business_id:
            return None
        business = Business.objects.filter(pk=business_id).first()
        if business is None:
            return None
        if not any((
            user_has_permission(user, business, "commerce", "view"),
            user_has_permission(user, business, "delivery", "view"),
            user_has_permission(user, business, "delivery_rider", "view"),
        )):
            return None
        return business.pk, user.pk
