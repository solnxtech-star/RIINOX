from __future__ import annotations

import auto_prefetch
from core.applications.users.models import Organization


class WarehouseQuerySet(auto_prefetch.QuerySet):
    """Slim, chainable queryset for Warehouses scoped by organization and user."""

    def for_organization(self, organization: Organization | str) -> WarehouseQuerySet:
        if isinstance(organization, Organization):
            return self.filter(organization=organization)
        return self.filter(organization_id=organization)

    def for_user(self, user) -> WarehouseQuerySet:
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_superuser", False):
            return self.all()
        return self.filter(organization__in=Organization.objects.for_user(user))

    def active(self) -> WarehouseQuerySet:
        return self.filter(is_active=True)


class StockLocationQuerySet(auto_prefetch.QuerySet):
    """Slim, chainable queryset for StockLocations scoped by organization and user."""

    def for_organization(self, organization: Organization | str) -> StockLocationQuerySet:
        if isinstance(organization, Organization):
            return self.filter(warehouse__organization=organization)
        return self.filter(warehouse__organization_id=organization)

    def for_user(self, user) -> StockLocationQuerySet:
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_superuser", False):
            return self.all()
        return self.filter(warehouse__organization__in=Organization.objects.for_user(user))

    def active(self) -> StockLocationQuerySet:
        return self.filter(is_active=True)
