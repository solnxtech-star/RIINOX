from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from rest_framework import serializers

from core.applications.customers.models import Customer, CustomerAddress
from core.applications.customers.services import create_customer, update_customer
from core.applications.users.services import has_org_permission
from core.helper.enums import CustomerStatus, CustomerType, PermissionCode


class CustomerAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerAddress
        fields = [
            "id",
            "address_type",
            "street",
            "city",
            "state",
            "postal_code",
            "country",
            "is_default",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class CustomerSerializer(serializers.ModelSerializer):
    """
    Standard read/detail representation for customer records.
    """
    addresses = CustomerAddressSerializer(many=True, read_only=True)

    class Meta:
        model = Customer
        fields = [
            "id",
            "organization",
            "customer_type",
            "name",
            "company_name",
            "email",
            "phone",
            "tax_id",
            "credit_limit",
            "balance",
            "payment_terms_days",
            "status",
            "notes",
            "metadata",
            "addresses",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "organization", "balance", "created_at", "updated_at"]


class CustomerCreateSerializer(serializers.ModelSerializer):
    """
    Write-side serializer for creating and updating customer records.
    Guards credit limits, balances, and tenant boundaries.
    """
    addresses = CustomerAddressSerializer(many=True, required=False)
    credit_limit = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        default=Decimal("0.00"),
        min_value=Decimal("0.00"),
    )
    payment_terms_days = serializers.IntegerField(
        required=False,
        default=0,
        min_value=0,
    )

    class Meta:
        model = Customer
        fields = [
            "id",
            "customer_type",
            "name",
            "company_name",
            "email",
            "phone",
            "tax_id",
            "credit_limit",
            "payment_terms_days",
            "status",
            "notes",
            "metadata",
            "addresses",
        ]
        read_only_fields = ["id"]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = super().validate(attrs)
        view = self.context.get("view")
        url_org = getattr(view, "organization", None) if view else None
        user = self.context["request"].user if "request" in self.context else None

        # Cross-tenant payload tampering check
        if url_org and hasattr(self, "initial_data") and isinstance(self.initial_data, dict):
            raw_org = self.initial_data.get("organization")
            if raw_org:
                try:
                    if uuid.UUID(str(raw_org)) != uuid.UUID(str(url_org.id)):
                        raise serializers.ValidationError({"organization": "Organization does not match URL path."})
                except (ValueError, AttributeError):
                    raise serializers.ValidationError({"organization": "Invalid organization UUID."})

        org = url_org or (self.instance.organization if self.instance else None)

        if org and user and user.is_authenticated:
            # Credit governance checks (PRD §26)
            new_credit = attrs.get("credit_limit")
            new_terms = attrs.get("payment_terms_days")

            if self.instance:
                # Update flow
                if new_credit is not None and new_credit != self.instance.credit_limit:
                    if not has_org_permission(user, org, PermissionCode.MANAGE_CUSTOMER_CREDIT):
                        raise serializers.ValidationError(
                            {"credit_limit": "You do not have permission to modify customer credit limits."}
                        )
                if new_terms is not None and new_terms != self.instance.payment_terms_days:
                    if not has_org_permission(user, org, PermissionCode.MANAGE_CUSTOMER_CREDIT):
                        raise serializers.ValidationError(
                            {"payment_terms_days": "You do not have permission to modify credit payment terms."}
                        )
            else:
                # Creation flow
                if (new_credit is not None and new_credit > Decimal("0.00")) or (new_terms is not None and new_terms > 0):
                    if not has_org_permission(user, org, PermissionCode.MANAGE_CUSTOMER_CREDIT):
                        raise serializers.ValidationError(
                            {"credit_limit": "You do not have permission to grant customer credit limits or terms."}
                        )

            # Tenant uniqueness validation
            email_val = (attrs.get("email") or "").strip()
            if email_val:
                qs = Customer.objects.filter(organization=org, email__iexact=email_val)
                if self.instance:
                    qs = qs.exclude(pk=self.instance.pk)
                if qs.exists():
                    raise serializers.ValidationError({"email": "A customer with this email already exists in your organization."})

            phone_val = (attrs.get("phone") or "").strip()
            if phone_val:
                qs = Customer.objects.filter(organization=org, phone=phone_val)
                if self.instance:
                    qs = qs.exclude(pk=self.instance.pk)
                if qs.exists():
                    raise serializers.ValidationError({"phone": "A customer with this phone already exists in your organization."})

        return attrs

    def create(self, validated_data: dict[str, Any]) -> Customer:
        view = self.context.get("view")
        organization = getattr(view, "organization", None) if view else None
        user = self.context["request"].user
        addresses_data = validated_data.pop("addresses", None)

        return create_customer(
            organization=organization,
            user=user,
            addresses=addresses_data,
            **validated_data,
        )

    def update(self, instance: Customer, validated_data: dict[str, Any]) -> Customer:
        user = self.context["request"].user
        addresses_data = validated_data.pop("addresses", None)

        return update_customer(
            customer=instance,
            user=user,
            addresses=addresses_data,
            **validated_data,
        )


class CustomerCreditAdjustmentSerializer(serializers.Serializer):
    """
    Dedicated serializer for adjusting credit limits and terms.
    """
    credit_limit = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=True,
        min_value=Decimal("0.00"),
        help_text="New credit limit amount.",
    )
    payment_terms_days = serializers.IntegerField(
        required=False,
        min_value=0,
        help_text="Updated payment terms in days.",
    )
    reason = serializers.CharField(
        max_length=255,
        required=False,
        default="",
        help_text="Audit justification for adjusting credit terms.",
    )
