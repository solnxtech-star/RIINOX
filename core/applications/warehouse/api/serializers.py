from __future__ import annotations

from rest_framework import serializers

from core.applications.warehouse.models import Warehouse, StockLocation
from core.applications.warehouse.services import create_warehouse


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = [
            "id",
            "organization",
            "name",
            "code",
            "address",
            "manager",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {
            "organization": {"required": False},
        }

    def to_internal_value(self, data):
        data = data.copy() if hasattr(data, "copy") else dict(data)
        if "organization" not in data or not data["organization"]:
            view = self.context.get("view")
            url_org = getattr(view, "organization", None) if view else None
            if url_org:
                data["organization"] = str(url_org.id)
        return super().to_internal_value(data)

    def validate(self, attrs):
        view = self.context.get("view")
        url_org = getattr(view, "organization", None) if view else None
        if url_org:
            body_org = attrs.get("organization")
            if body_org and body_org != url_org:
                raise serializers.ValidationError(
                    {"organization": "Organization in payload does not match URL path tenant."}
                )
            attrs["organization"] = url_org

            manager = attrs.get("manager")
            if manager:
                # Validate manager has membership in organization
                if not manager.memberships.filter(organization=url_org, is_active=True).exists():
                    raise serializers.ValidationError(
                        {"manager": "Assigned manager is not an active member of this organization."}
                    )
        return super().validate(attrs)

    def create(self, validated_data):
        return create_warehouse(**validated_data)


class StockLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockLocation
        fields = [
            "id",
            "warehouse",
            "name",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        view = self.context.get("view")
        url_org = getattr(view, "organization", None) if view else None
        warehouse = attrs.get("warehouse")
        if url_org and warehouse:
            if warehouse.organization_id != url_org.id:
                raise serializers.ValidationError(
                    {"warehouse": "Warehouse does not belong to this organization."}
                )
        return super().validate(attrs)
