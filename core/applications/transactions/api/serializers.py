from __future__ import annotations

from rest_framework import serializers

from core.applications.transactions.models import FinancialLedgerEntry
from core.applications.transactions.models import Transaction


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
        read_only_fields = fields


class TransactionSerializer(serializers.ModelSerializer):
    """Read-only: transactions are written by the services that perform the operation."""

    financial_entries = FinancialLedgerEntrySerializer(many=True, read_only=True)

    class Meta:
        model = Transaction
        fields = [
            "id",
            "transaction_id",
            "organization",
            "transaction_type",
            "status",
            "location",
            "content_type",
            "object_id",
            "created_by",
            "notes",
            "financial_entries",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
