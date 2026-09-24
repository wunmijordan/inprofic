from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .realtime import FOUNDER_SIGNUP_GROUP


class FounderSignupConsumer(AsyncJsonWebsocketConsumer):
    """Superuser-only wake-up channel for Founder Console signup refreshes."""

    async def connect(self):
        user = self.scope.get("user")
        if not getattr(user, "is_authenticated", False) or not getattr(user, "is_superuser", False):
            await self.close(code=4403)
            return
        self.group_name = FOUNDER_SIGNUP_GROUP
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json({"type": "founder.signups.ready"})

    async def disconnect(self, close_code):
        group = getattr(self, "group_name", None)
        if group:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def founder_signup_changed(self, event):
        await self.send_json({
            "type": "founder.signup.changed",
            "event_id": int(event.get("event_id") or 0),
        })
