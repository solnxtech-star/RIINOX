"""
Live notifications over WebSockets, through the real ASGI stack
(origin check -> JWT -> routing -> consumer) with the in-memory channel layer.

TransactionTestCase: the consumer reads the database from another thread, so the
test data must really be committed (TestCase's wrapping transaction would hide it).
"""

from __future__ import annotations

from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.core.cache import cache
from django.test import TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from config.asgi import application
from core.applications.inventory.tests.factories import OrganizationFactory
from core.applications.notification.dispatch import deliver
from core.applications.notification.models import Notification
from core.applications.notification.realtime import push_unread_count
from core.applications.users.models import Membership
from core.applications.users.services import seed_default_roles
from core.applications.users.tests.factories import UserFactory


class LiveNotificationTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.org = OrganizationFactory()
        roles = seed_default_roles(self.org)
        self.manager = UserFactory()
        Membership.objects.create(user=self.manager, organization=self.org, role=roles["manager"])
        self.outsider = UserFactory()

    def _path(self, user=None, token=None):
        token = token if token is not None else (str(AccessToken.for_user(user)) if user else "")
        return f"/ws/organizations/{self.org.pk}/notifications/?token={token}"

    async def test_member_receives_live_notification_and_badge_updates(self):
        communicator = WebsocketCommunicator(application, self._path(self.manager))
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        self.assertEqual(await communicator.receive_json_from(), {"type": "connected", "unread": 0})

        await sync_to_async(deliver)(
            event="refund", organization=self.org, title="Refund", message="A return of N5,000 was processed.",
            location=None, reference=None, link={"resource": "sales/returns", "query": {}}, data={},
            extra_users=(), exclude_user=None, dedupe_key="", dedupe_hours=24,
        )
        message = await communicator.receive_json_from(timeout=3)
        self.assertEqual(message["type"], "notification")
        self.assertEqual(message["unread"], 1)
        self.assertEqual(message["notification"]["event"], "refund")
        self.assertEqual(message["notification"]["message"], "A return of N5,000 was processed.")

        await sync_to_async(Notification.objects.filter(recipient=self.manager).update)(is_read=True)
        await sync_to_async(push_unread_count)(self.org.pk, self.manager.pk, 0)
        self.assertEqual(await communicator.receive_json_from(timeout=3), {"type": "unread_count", "unread": 0})

        await communicator.send_json_to({"action": "ping"})
        self.assertEqual(await communicator.receive_json_from(), {"type": "pong"})
        await communicator.disconnect()

    async def test_rejections(self):
        # No / bad token: accepted then closed with 4401 so the client knows why.
        for path in (self._path(), self._path(token="not-a-jwt")):
            communicator = WebsocketCommunicator(application, path)
            await communicator.connect()
            closed = await communicator.receive_output(timeout=3)
            self.assertEqual(closed, {"type": "websocket.close", "code": 4401})

        # Valid user, but not a member of this organization: 4404.
        communicator = WebsocketCommunicator(application, self._path(self.outsider))
        await communicator.connect()
        self.assertEqual(await communicator.receive_output(timeout=3), {"type": "websocket.close", "code": 4404})

        # A browser page on another site can't open the socket at all.
        communicator = WebsocketCommunicator(
            application, self._path(self.manager), headers=[(b"origin", b"https://evil.example")]
        )
        connected, _ = await communicator.connect()
        self.assertFalse(connected)
