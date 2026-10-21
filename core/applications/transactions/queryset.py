from __future__ import annotations

import auto_prefetch
from core.applications.users.models import Organization


class TransactionQuerySet(auto_prefetch.QuerySet):
    """Slim, chainable queryset for Transactions scoped by organization and user."""

    def for_organization(self, organization: Organization | str) -> TransactionQuerySet:
        if isinstance(organization, Organization):
            return self.filter(organization=organization)
        return self.filter(organization_id=organization)

    def for_user(self, user) -> TransactionQuerySet:
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_superuser", False):
            return self.all()
        return self.filter(organization__in=Organization.objects.for_user(user))

    def with_entries(self) -> TransactionQuerySet:
        return self.select_related("organization", "created_by").prefetch_related("financial_entries")


class FinancialLedgerEntryQuerySet(auto_prefetch.QuerySet):
    """Slim, chainable queryset for FinancialLedgerEntries."""

    def for_organization(self, organization: Organization | str) -> FinancialLedgerEntryQuerySet:
        if isinstance(organization, Organization):
            return self.filter(transaction__organization=organization)
        return self.filter(transaction__organization_id=organization)

    def for_user(self, user) -> FinancialLedgerEntryQuerySet:
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_superuser", False):
            return self.all()
        return self.filter(transaction__organization__in=Organization.objects.for_user(user))

