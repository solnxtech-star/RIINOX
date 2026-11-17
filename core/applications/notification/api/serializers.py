from __future__ import annotations

from rest_framework import serializers

from core.applications.notification.events import EVENTS
from core.applications.notification.models import Notification


class SettingChangeSerializer(serializers.Serializer):
    event = serializers.ChoiceField(choices=sorted(EVENTS), help_text="Event code, e.g. `low_stock`.")
    enabled = serializers.BooleanField(required=False)
    in_app = serializers.BooleanField(required=False)
    email = serializers.BooleanField(required=False)
    recipient_role_ids = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        max_length=50,
        help_text="Role ids from `roles` in the GET response. Replaces the current list; [] = nobody.",
    )
    options = serializers.DictField(
        required=False,
        help_text="Only the event's own options (see `options` in the GET response), e.g. {\"threshold\": 750000}.",
    )


class SettingsUpdateSerializer(serializers.Serializer):
    settings = SettingChangeSerializer(many=True, allow_empty=False, max_length=len(EVENTS))

    def validate_settings(self, value):
        codes = [item["event"] for item in value]
        if len(codes) != len(set(codes)):
            raise serializers.ValidationError("Each event can appear only once.")
        return value


class PreferenceChangeSerializer(serializers.Serializer):
    event = serializers.ChoiceField(choices=sorted(EVENTS))
    in_app = serializers.BooleanField(required=False, help_text="In-app, including the live pop-up.")
    email = serializers.BooleanField(required=False)


class PreferencesUpdateSerializer(serializers.Serializer):
    preferences = PreferenceChangeSerializer(many=True, allow_empty=False, max_length=len(EVENTS))

    def validate_preferences(self, value):
        codes = [item["event"] for item in value]
        if len(codes) != len(set(codes)):
            raise serializers.ValidationError("Each event can appear only once.")
        return value


class NotificationSerializer(serializers.ModelSerializer):
    event = serializers.CharField(
        source="notification_type", read_only=True,
        help_text="Event code, e.g. `low_stock`, `overdue_payment` (see GET notification-settings). Use it for the icon.",
    )
    category = serializers.SerializerMethodField(
        help_text="`inventory`, `sales`, `security` or `operations`.",
    )
    location = serializers.SerializerMethodField(
        help_text="The branch `{id, name}` it happened at; null for organization-wide events or groups spanning branches.",
    )
    title = serializers.CharField(read_only=True, help_text="Short heading, e.g. \"Low Stock\".")
    message = serializers.CharField(
        read_only=True, help_text="Ready-to-show sentence, e.g. \"Coca-Cola is low at Lekki: 9 left (minimum 50).\"",
    )
    link = serializers.JSONField(
        read_only=True,
        help_text=(
            "Where clicking it should go: `{\"resource\": \"products\", \"query\": {\"low_stock\": \"true\"}}`. "
            "`resource` is the frontend route (products, invoices, inventory, inventory/adjustments, sales, "
            "sales/returns, team); `query` holds the list filters or an `id`. May be null."
        ),
    )
    data = serializers.JSONField(
        read_only=True,
        help_text=(
            "Extra facts for the UI. Grouped notifications carry `count` (how many items the message covers); "
            "others carry ids and figures such as `stock`, `balance`, `days_overdue`."
        ),
    )
    is_read = serializers.BooleanField(read_only=True, help_text="false = show the unread dot.")
    created_at = serializers.DateTimeField(
        read_only=True,
        help_text="When it happened (for grouped ones: the latest occurrence). Use for \"20 mins ago\" and the "
                  "Today / Yesterday / Earlier headings.",
    )

    class Meta:
        model = Notification
        fields = ["id", "event", "category", "title", "message", "location", "link", "data",
                  "is_read", "read_at", "created_at"]
        read_only_fields = fields

    def get_category(self, obj) -> str | None:
        spec = EVENTS.get(obj.notification_type)
        return spec.category if spec else None

    def get_location(self, obj) -> dict | None:
        return {"id": str(obj.location_id), "name": obj.location.name} if obj.location_id else None
