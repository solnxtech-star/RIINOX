from __future__ import annotations

from rest_framework import serializers

from core.applications.vendors.models import Vendor


class VendorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vendor
        fields = [
            "id",
            "organization",
            "company_name",
            "contact_person",
            "phone",
            "email",
            "address",
            "status",
            "notes",
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
        return super().validate(attrs)

