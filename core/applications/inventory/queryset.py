from __future__ import annotations

import auto_prefetch
from django.db.models import QuerySet

from core.helper.enums import ApprovalStatusChoices


class InventoryQuerySet(auto_prefetch.QuerySet):
    """Queryset for Inventory stock balances."""

    def for_organization(self, organization):
        return self.filter(warehouse__organization=organization)

    def for_user(self, user):
        from core.applications.users.models import Organization

        return self.filter(
            warehouse__organization__in=Organization.objects.for_user(user)
        )

    def in_stock(self):
        return self.filter(quantity__gt=0)

    def with_details(self):
        return self.select_related("product", "variant", "batch", "warehouse")


class InventoryLedgerEntryQuerySet(auto_prefetch.QuerySet):
    """Queryset for immutable Inventory Ledger entries."""

    def for_organization(self, organization):
        return self.filter(warehouse__organization=organization)

    def for_user(self, user):
        from core.applications.users.models import Organization

        return self.filter(
            warehouse__organization__in=Organization.objects.for_user(user)
        )

    def with_details(self):
        return self.select_related(
            "transaction", "product", "variant", "batch", "warehouse"
        )


class StockAdjustmentRequestQuerySet(auto_prefetch.QuerySet):
    """Queryset for Stock Adjustment Requests."""

    def for_organization(self, organization):
        return self.filter(warehouse__organization=organization)

    def for_user(self, user):
        from core.applications.users.models import Organization

        return self.filter(
            warehouse__organization__in=Organization.objects.for_user(user)
        )

    def pending(self):
        return self.filter(status=ApprovalStatusChoices.PENDING)

    def approved(self):
        return self.filter(status=ApprovalStatusChoices.APPROVED)

    def with_details(self):
        return self.select_related(
            "product", "variant", "batch", "warehouse", "requested_by", "reviewed_by"
        )


class PhysicalStockCountQuerySet(auto_prefetch.QuerySet):
    """Queryset for Physical Stock Counts."""

    def for_organization(self, organization):
        return self.filter(warehouse__organization=organization)

    def for_user(self, user):
        from core.applications.users.models import Organization

        return self.filter(
            warehouse__organization__in=Organization.objects.for_user(user)
        )

    def with_details(self):
        return self.select_related("warehouse", "counted_by").prefetch_related(
            "items__product"
        )


InventoryManager = auto_prefetch.Manager.from_queryset(InventoryQuerySet)
InventoryLedgerEntryManager = auto_prefetch.Manager.from_queryset(
    InventoryLedgerEntryQuerySet
)
StockAdjustmentRequestManager = auto_prefetch.Manager.from_queryset(
    StockAdjustmentRequestQuerySet
)
PhysicalStockCountManager = auto_prefetch.Manager.from_queryset(
    PhysicalStockCountQuerySet
)
