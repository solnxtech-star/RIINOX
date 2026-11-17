from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view

from core.applications.notification.api.serializers import NotificationSerializer
from core.applications.notification.api.serializers import PreferencesUpdateSerializer
from core.applications.notification.api.serializers import SettingsUpdateSerializer

SETTINGS_TAGS = ["Notification Settings"]
INBOX_TAGS = ["Notifications"]

_ROLE = {"id": "8f0c…", "name": "Inventory Manager", "slug": "inventory-manager"}
SETTINGS_EXAMPLE = {
    "categories": [
        {
            "category": "inventory",
            "label": "Inventory",
            "events": [
                {"event": "low_stock", "label": "Low Stock",
                 "description": "A product's stock at a branch is at or below its minimum stock level.",
                 "enabled": True, "in_app": True, "email": True,
                 "recipient_roles": [_ROLE, {"id": "2b1d…", "name": "Manager", "slug": "manager"}],
                 "options": [], "triggered": True, "customized": False},
                {"event": "batch_expiry", "label": "Batch/Expiry",
                 "description": "Batches with stock on hand are about to expire (checked daily).",
                 "enabled": True, "in_app": True, "email": True, "recipient_roles": [_ROLE],
                 "options": [{"name": "days_before_expiry", "value": 30, "minimum": 1, "maximum": 365,
                              "help": "Warn this many days before expiry."}],
                 "triggered": True, "customized": False},
            ],
        },
        {"category": "sales", "label": "Sales", "events": ["…"]},
        {"category": "security", "label": "Security", "events": ["…"]},
        {"category": "operations", "label": "Operations", "events": ["…"]},
    ],
    "roles": [_ROLE, {"id": "2b1d…", "name": "Manager", "slug": "manager"}],
}

settings_schema = extend_schema_view(
    get=extend_schema(
        tags=SETTINGS_TAGS,
        summary="Notification settings",
        description=(
            "Everything the Settings > Notifications screen needs in one call: the 4 sections (`categories`, in "
            "display order), each event's **Enabled / In-app / Email** switches and **Recipient** roles, and "
            "`roles` (every role in the organization) for the recipient dropdown.\n\n"
            "- `options`: extra settings some events have (Large Sale `threshold`, Batch/Expiry "
            "`days_before_expiry`, Failed Logins `attempts`), with their allowed range.\n"
            "- `triggered: false`: the event can be configured but nothing sends it yet (Failed Transaction, "
            "Incoming Sales Orders). Show a \"coming soon\" hint.\n"
            "- `customized: false`: the organization is on the default for this event.\n\n"
            "**Who receives an alert:** active members holding one of the recipient roles. For branch events "
            "(stock, sales, approvals) only members with access to that branch; security events go to all of "
            "them. The person who caused the event isn't notified; the requester of an approval always is.\n\n"
            "**Permission:** `VIEW_SETTINGS`."
        ),
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT,
                                        examples=[OpenApiExample("Settings", value=SETTINGS_EXAMPLE, response_only=True)])},
    ),
    patch=extend_schema(
        tags=SETTINGS_TAGS,
        summary="Save notification settings",
        description=(
            "Send only what changed, for one event or many: `{\"settings\": [{\"event\": \"low_stock\", "
            "\"email\": false}, ...]}`. Fields left out keep their value. `recipient_role_ids` **replaces** the "
            "list. Returns the full settings (same as GET). Every change is audit-logged.\n\n"
            "Errors: unknown event, a role from another organization, an unknown option or an option out of "
            "range: `400 VALIDATION_ERROR`.\n\n**Permission:** `EDIT_SETTINGS` (Owner / Administrator)."
        ),
        request=SettingsUpdateSerializer,
        examples=[
            OpenApiExample("Toggle email", request_only=True,
                           value={"settings": [{"event": "stock_variance", "email": True}]}),
            OpenApiExample("Recipients and threshold", request_only=True,
                           value={"settings": [{"event": "large_sale", "recipient_role_ids": ["2b1d…"],
                                                "options": {"threshold": 750000}}]}),
        ],
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT, description="The full settings, as GET.")},
    ),
)

PREFERENCES_TAGS = ["Notification Settings"]
_PREF = {
    "event": "low_stock", "category": "inventory", "label": "Low Stock",
    "description": "A product's stock at a branch is at or below its minimum stock level.",
    "receives": True, "organization_in_app": True, "organization_email": True,
    "in_app": True, "email": False, "locked": False,
}

preferences_schema = extend_schema_view(
    get=extend_schema(
        tags=PREFERENCES_TAGS,
        summary="My notification preferences",
        description=(
            "The signed-in member's own choices, per event, for this organization. A person can only switch "
            "channels **off** for themselves; the organization settings decide what is sent at all.\n\n"
            "- `receives`: the organization sends this event to this person's role (show these; grey out or "
            "hide the rest).\n"
            "- `organization_in_app` / `organization_email`: whether the organization uses that channel. If "
            "false, the personal switch has no effect (disable it in the UI).\n"
            "- `in_app` (includes the live pop-up) / `email`: the person's own choice; default on.\n"
            "- `locked`: security alerts that Owners and Administrators can't mute.\n\n"
            "**Permission:** any active member."
        ),
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT, examples=[OpenApiExample(
            "Preferences", response_only=True,
            value={"categories": [{"category": "inventory", "label": "Inventory", "events": [_PREF]}]})])},
    ),
    patch=extend_schema(
        tags=PREFERENCES_TAGS,
        summary="Save my notification preferences",
        description=(
            "`{\"preferences\": [{\"event\": \"low_stock\", \"email\": false}]}`. Fields left out keep their "
            "value. Turning off a `locked` event is a `400`. Returns the full list (same as GET)."
        ),
        request=PreferencesUpdateSerializer,
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT, description="Same as GET.")},
    ),
)

NOTIFICATIONS_GUIDE = """
The caller's own in-app notifications in this organization, newest first, paginated
(`?page_size=5` for the bell dropdown; `?page=2` for "See all"). Filters: `?unread=true`,
`?category=inventory|sales|security|operations`.

### Building the screens
- **Bell badge:** `GET unread-count/` on load, then keep it live with the WebSocket below.
- **Dropdown / page:** this list. Group rows under **Today / Yesterday / Earlier** from `created_at`
  in the user's local time (the server doesn't group by day).
- **Click a row:** `POST {id}/read/`, then navigate using `link`.
- **"Mark all as read":** `POST read-all/`.

### Grouped notifications
Low Stock, Out of Stock and Pending Approvals are **grouped**: while a person has one of them unread,
new occurrences update that same row instead of adding rows (e.g. "Coca-Cola is low at Lekki: 9 left"
becomes "2 products are below their reorder level"). The row keeps its `id`, `data.count` holds the
number of items, `created_at` moves to the latest occurrence (so it rises to the top) and it counts as
**one** unread. After it is read, the next occurrence starts a new row.

### Live updates (WebSocket)
Not shown in Swagger (OpenAPI can't describe sockets), so here is the contract:

**Connect** after login, once per open organization:
`wss://<api-host>/ws/organizations/{organization_id}/notifications/?token=<access token>`
(`ws://localhost:8000/...` locally). Browsers can't send headers on sockets, so the normal JWT access
token goes in `token`; don't log that URL.

**Messages from the server** (JSON):
| `type` | Body | What to do |
|---|---|---|
| `connected` | `{"type": "connected", "unread": 3}` | Set the badge. |
| `notification` | `{"type": "notification", "notification": {...same shape as a list item...}, "unread": 4, "updated": false}` | `updated: false`: add it to the top and show a toast. `updated: true`: replace the row with the same `id` and move it to the top (a grouped notification changed). Set the badge to `unread`. |
| `unread_count` | `{"type": "unread_count", "unread": 0}` | Set the badge (sent after read / read-all from any tab or device). |
| `pong` | `{"type": "pong"}` | Reply to `{"action": "ping"}`; send a ping every ~30 s to keep proxies from closing an idle socket. |

**Close codes:** `4401` not signed in or the token expired (refresh the token, then reconnect);
`4404` not an active member of this organization (don't retry). Any other close (network, deploy):
reconnect with backoff (1 s, 2 s, 5 s, max 30 s), then call `unread-count/` to catch up; nothing is
lost because every notification is saved before it is pushed.

**What arrives live** is exactly what lands in this list: only events the organization sends to the
user's role (and branch), minus anything the user switched off in `notification-preferences/`
(In-app off = no row and no push).
"""

inbox_schema = extend_schema_view(
    list=extend_schema(
        tags=INBOX_TAGS,
        summary="My notifications (bell) and the live WebSocket",
        description=NOTIFICATIONS_GUIDE,
        parameters=[
            OpenApiParameter("unread", OpenApiTypes.BOOL, description="true = only unread."),
            OpenApiParameter("category", OpenApiTypes.STR, description="inventory, sales, security, operations."),
        ],
        responses={200: NotificationSerializer(many=True)},
        examples=[
            OpenApiExample(
                "Grouped low stock",
                response_only=True,
                value={
                    "id": "5b0e…", "event": "low_stock", "category": "inventory", "title": "Low Stock",
                    "message": "12 products are below their reorder level", "location": None,
                    "link": {"resource": "products", "query": {"low_stock": "true"}},
                    "data": {"count": 12}, "is_read": False, "read_at": None,
                    "created_at": "2026-11-17T09:40:00Z",
                },
            ),
            OpenApiExample(
                "Overdue payment",
                response_only=True,
                value={
                    "id": "9c2d…", "event": "overdue_payment", "category": "sales", "title": "Overdue Payment",
                    "message": "Iuliom Bike Food Co owes ₦1,550,000 on INV-000123 (due 01 Nov, 16 days overdue).",
                    "location": {"id": "495e…", "name": "Lekki"},
                    "link": {"resource": "invoices", "query": {"id": "eb23…"}},
                    "data": {"invoice_number": "INV-000123", "balance": "1550000.00", "days_overdue": 16},
                    "is_read": False, "read_at": None, "created_at": "2026-11-17T06:00:00Z",
                },
            ),
        ],
    ),
    unread_count=extend_schema(
        tags=INBOX_TAGS, summary="Unread count (bell badge)",
        description=(
            "How many of my notifications in this organization are unread (a grouped notification counts once). "
            "Call it on load and after reconnecting the WebSocket; while the socket is open the server pushes "
            "changes, so no polling is needed. Without the socket, poll every 30-60 s."
        ),
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT,
                                        examples=[OpenApiExample("Count", value={"unread": 3}, response_only=True)])},
    ),
    read=extend_schema(
        tags=INBOX_TAGS, summary="Mark one as read", request=None,
        description=(
            "No body. Marks it read (idempotent: reading twice is fine) and pushes the new unread count to the "
            "user's other open tabs/devices over the WebSocket."
        ),
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT,
                                        examples=[OpenApiExample("Read", value={"status": "read"}, response_only=True)]),
                   404: OpenApiResponse(description="Not one of your notifications.")},
    ),
    read_all=extend_schema(
        tags=INBOX_TAGS, summary="Mark all as read", request=None,
        description="No body. Marks every unread notification of mine in this organization as read; returns how many.",
        responses={200: OpenApiResponse(response=OpenApiTypes.OBJECT,
                                        examples=[OpenApiExample("Done", value={"marked_read": 5}, response_only=True)])},
    ),
)
