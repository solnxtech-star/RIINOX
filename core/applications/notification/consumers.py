"""
Live notifications socket: ws(s)://<api-host>/ws/organizations/<org_id>/notifications/?token=<access>

Server -> client messages (JSON):
  {"type": "connected", "unread": 3}
  {"type": "notification", "notification": {...same shape as the list API...}, "unread": 4, "updated": false}
      "updated": true = a grouped notification already shown changed ("13 products are ...");
      replace the row with the same `id` and move it to the top.
  {"type": "unread_count", "unread": 0}          (after read / read-all, in any tab)
  {"type": "pong"}                               (reply to {"action": "ping"})

Close codes: 4401 not signed in / token expired, 4404 not an active member.
"""

from __future__ import annotations

import time
import uuid

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from core.applications.notification.realtime import group_name

CLOSE_UNAUTHENTICATED = 4401
CLOSE_NOT_MEMBER = 4404


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    group: str | None = None

    async def connect(self):
        user = self.scope.get("user")
        # Accept first so the client receives a meaningful close code (a refused
        # handshake is indistinguishable from a network error in browsers).
        await self.accept()
        if user is None or not user.is_authenticated:
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return
        try:
            self.organization_id = uuid.UUID(self.scope["url_route"]["kwargs"]["organization_id"])
        except ValueError:
            await self.close(code=CLOSE_NOT_MEMBER)
            return
        if not await self._is_active_member(user):
            await self.close(code=CLOSE_NOT_MEMBER)
            return

        self.group = group_name(self.organization_id, user.pk)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.send_json({"type": "connected", "unread": await self._unread(user)})

    async def disconnect(self, code):
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if await self._expired():
            return
        if isinstance(content, dict) and content.get("action") == "ping":
            await self.send_json({"type": "pong"})

    # Group handlers (type "notification.created" -> notification_created) ----- #
    async def notification_created(self, event):
        if not await self._expired():
            await self.send_json({"type": "notification", "notification": event["notification"],
                                  "unread": event["unread"], "updated": event.get("updated", False)})

    async def notification_unread(self, event):
        if not await self._expired():
            await self.send_json({"type": "unread_count", "unread": event["unread"]})

    # ------------------------------------------------------------------ #
    async def _expired(self) -> bool:
        exp = self.scope.get("token_exp")
        if exp is not None and time.time() >= exp:
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return True
        return False

    @database_sync_to_async
    def _is_active_member(self, user) -> bool:
        from core.applications.users.models import Membership

        return Membership.objects.effective().filter(
            user=user, organization_id=self.organization_id, organization__is_active=True
        ).exists()

    @database_sync_to_async
    def _unread(self, user) -> int:
        from core.applications.notification.models import Notification

        return Notification.objects.filter(
            organization_id=self.organization_id, recipient=user, is_read=False
        ).count()
