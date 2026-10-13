from __future__ import annotations

import uuid
from rest_framework import serializers

from core.applications.transactions.models import (
    Transaction,
    FinancialLedgerEntry,
    Customer,
)
from core.applications.transactions.services import (
    create_transaction,
    TransactionData,
)


class FinancialLedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = FinancialLedgerEntry
        fields = [
            "id",
            "transaction",
            "account_name",
            "debit",
            "credit",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class TransactionSerializer(serializers.ModelSerializer):
    financial_entries = FinancialLedgerEntrySerializer(many=True, read_only=True)

    class Meta:
        model = Transaction
        fields = [
            "id",
            "transaction_id",
            "organization",
            "transaction_type",
            "status",
            "content_type",
            "object_id",
            "created_by",
            "notes",
            "financial_entries",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_at", "updated_at"]
        extra_kwargs = {
            "organization": {"required": False},
            "transaction_id": {"required": False},
        }

    def to_internal_value(self, data):
        data = data.copy() if hasattr(data, "copy") else dict(data)
        if "organization" not in data or not data["organization"]:
            view = self.context.get("view")
            url_org = getattr(view, "organization", None) if view else None
            if url_org:
                data["organization"] = str(url_org.id)
        if "transaction_id" not in data or not data["transaction_id"]:
            data["transaction_id"] = f"TRX-{uuid.uuid4().hex[:8].upper()}"
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

    def create(self, validated_data):
        if "created_by" not in validated_data and "request" in self.context:
            validated_data["created_by"] = self.context["request"].user
        return Transaction.objects.create(**validated_data)


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = [
            "id",
            "organization",
            "linked_user",
            "customer_type",
            "name",
            "email",
            "phone",
            "address",
            "credit_limit",
            "balance",
            "status",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "balance", "created_at", "updated_at"]
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
