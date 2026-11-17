from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework import mixins
from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.applications.notification.api.schemas import inbox_schema
from core.applications.notification.api.schemas import preferences_schema
from core.applications.notification.api.schemas import settings_schema
from core.applications.notification.api.serializers import NotificationSerializer
from core.applications.notification.api.serializers import PreferencesUpdateSerializer
from core.applications.notification.api.serializers import SettingsUpdateSerializer
from core.applications.notification.audit.context import AuditContext
from core.applications.notification.events import CATEGORY_LABELS
from core.applications.notification.events import EVENTS
from core.applications.notification.models import Notification
from core.applications.notification.preferences import effective_settings
from core.applications.notification.preferences import personal_preferences
from core.applications.notification.preferences import update_personal_preferences
from core.applications.notification.preferences import update_settings
from core.applications.notification.realtime import push_unread_count
from core.applications.users.models import Role
from core.applications.users.permissions import HasOrgPermission
from core.applications.users.permissions import OrganizationScopedMixin
from core.applications.users.permissions import resolve_active_membership
from core.helper.enums import PermissionCode


class IsOrganizationMember(BasePermission):
    """Any active member. Non-members get 404 so organizations aren't revealed."""

    def has_permission(self, request, view):
        if resolve_active_membership(request, view) is None:
            raise NotFound("Organization not found.")
        return True


def _settings_payload(organization) -> dict:
    current = effective_settings(organization)
    roles = Role.objects.filter(organization=organization).order_by("name")
    categories = []
    for category, label in CATEGORY_LABELS.items():
        events = []
        for code, spec in EVENTS.items():
            if spec.category != category:
                continue
            setting = current[code]
            events.append(
                {
                    "event": code,
                    "label": spec.label,
                    "description": spec.description,
                    "enabled": setting.enabled,
                    "in_app": setting.in_app,
                    "email": setting.email,
                    "recipient_roles": [{"id": str(r.pk), "name": r.name, "slug": r.slug} for r in setting.roles],
                    "options": [
                        {"name": name, "value": setting.options[name], "minimum": low, "maximum": high, "help": help_}
                        for name, (_kind, _default, low, high, help_) in spec.options.items()
                    ],
                    "triggered": spec.triggered,
                    "customized": setting.customized,
                }
            )
        categories.append({"category": category, "label": label, "events": events})
    return {
        "categories": categories,
        "roles": [{"id": str(r.pk), "name": r.name, "slug": r.slug} for r in roles],
    }


@settings_schema
class NotificationSettingsView(OrganizationScopedMixin, APIView):
    """The Notifications settings screen: one GET to render it, one PATCH to save."""

    def get_permissions(self):
        code = PermissionCode.VIEW_SETTINGS if self.request.method == "GET" else PermissionCode.EDIT_SETTINGS
        return [IsAuthenticated(), HasOrgPermission(code)()]

    def get(self, request, organization_id=None):
        return Response(_settings_payload(self.organization))

    def patch(self, request, organization_id=None):
        serializer = SettingsUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        update_settings(
            self.organization,
            actor=request.user,
            changes=serializer.validated_data["settings"],
            audit_context=AuditContext.from_request(request),
        )
        return Response(_settings_payload(self.organization))


@preferences_schema
class MyNotificationPreferencesView(OrganizationScopedMixin, APIView):
    """'My notifications': each member switches channels off for themselves."""

    permission_classes = [IsAuthenticated, IsOrganizationMember]

    def get(self, request, organization_id=None):
        return Response(_preferences_payload(self.membership))

    def patch(self, request, organization_id=None):
        serializer = PreferencesUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        update_personal_preferences(self.membership, serializer.validated_data["preferences"])
        return Response(_preferences_payload(self.membership))


def _preferences_payload(membership) -> dict:
    rows = personal_preferences(membership)
    return {
        "categories": [
            {"category": category, "label": label, "events": [row for row in rows if row["category"] == category]}
            for category, label in CATEGORY_LABELS.items()
        ]
    }


@inbox_schema
class NotificationViewSet(OrganizationScopedMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """The bell: the caller's own notifications in this organization."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember]
    queryset = Notification.objects.none()  # schema generation only; get_queryset() scopes real requests

    def get_queryset(self):
        queryset = Notification.objects.filter(
            organization=self.organization, recipient=self.request.user
        ).select_related("location").order_by("-created_at")
        unread = self.request.query_params.get("unread")
        if unread not in (None, ""):
            if unread.lower() not in ("true", "false", "1", "0"):
                raise ValidationError({"unread": ["Use true or false."]})
            queryset = queryset.filter(is_read=unread.lower() in ("false", "0"))
        category = self.request.query_params.get("category")
        if category:
            if category not in CATEGORY_LABELS:
                raise ValidationError({"category": [f"Must be one of: {', '.join(CATEGORY_LABELS)}."]})
            queryset = queryset.filter(notification_type__in=[c for c, s in EVENTS.items() if s.category == category])
        return queryset

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request, organization_id=None):
        count = Notification.objects.filter(
            organization=self.organization, recipient=request.user, is_read=False
        ).count()
        return Response({"unread": count})

    @action(detail=True, methods=["post"])
    def read(self, request, organization_id=None, pk=None):
        updated = Notification.objects.filter(
            pk=pk, organization=self.organization, recipient=request.user, is_read=False
        ).update(is_read=True, read_at=timezone.now())
        if not updated and not Notification.objects.filter(
            pk=pk, organization=self.organization, recipient=request.user
        ).exists():
            raise NotFound("Notification not found.")
        if updated:
            self._push_unread()
        return Response({"status": "read"})

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request, organization_id=None):
        updated = Notification.objects.filter(
            organization=self.organization, recipient=request.user, is_read=False
        ).update(is_read=True, read_at=timezone.now())
        if updated:
            self._push_unread()
        return Response({"marked_read": updated}, status=status.HTTP_200_OK)

    def _push_unread(self):
        """Every open tab of this user updates its badge (after the request commits)."""
        organization_id, user_id = self.organization.pk, self.request.user.pk

        def push():
            unread = Notification.objects.filter(
                organization_id=organization_id, recipient_id=user_id, is_read=False
            ).count()
            push_unread_count(organization_id, user_id, unread)

        transaction.on_commit(push)
