from __future__ import annotations

import auto_prefetch
from django.db.models import QuerySet

from core.applications.users.models import Organization


class SaleQuerySet(auto_prefetch.QuerySet):
    """Slim, chainable queryset for Sales scoped by organization and user."""

    def for_organization(self, organization: Organization | str) -> SaleQuerySet:
        if isinstance(organization, Organization):
            return self.filter(organization=organization)
        return self.filter(organization_id=organization)

    def for_user(self, user) -> SaleQuerySet:
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_superuser", False):
            return self.all()
        return self.filter(organization__in=Organization.objects.for_user(user))

    def with_details(self) -> SaleQuerySet:
        return self.select_related(
            "organization", "customer", "warehouse", "location", "sales_rep"
        ).prefetch_related("items__product", "items__variant")


class SaleItemQuerySet(auto_prefetch.QuerySet):
    """Slim, chainable queryset for SaleItems."""

    def for_organization(self, organization: Organization | str) -> SaleItemQuerySet:
        if isinstance(organization, Organization):
            return self.filter(sale__organization=organization)
        return self.filter(sale__organization_id=organization)

    def for_user(self, user) -> SaleItemQuerySet:
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_superuser", False):
            return self.all()
        return self.filter(sale__organization__in=Organization.objects.for_user(user))

    def with_details(self) -> SaleItemQuerySet:
        return self.select_related("sale", "product", "variant")


class SaleReturnQuerySet(auto_prefetch.QuerySet):
    """Slim, chainable queryset for SaleReturns."""

    def for_organization(self, organization: Organization | str) -> SaleReturnQuerySet:
        if isinstance(organization, Organization):
            return self.filter(organization=organization)
        return self.filter(organization_id=organization)

    def for_user(self, user) -> SaleReturnQuerySet:
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_superuser", False):
            return self.all()
        return self.filter(organization__in=Organization.objects.for_user(user))

    def with_details(self) -> SaleReturnQuerySet:
        return self.select_related(
            "original_sale", "organization"
        ).prefetch_related("items__sale_item")


class SaleReturnItemQuerySet(auto_prefetch.QuerySet):
    """Slim, chainable queryset for SaleReturnItems."""

    def for_organization(self, organization: Organization | str) -> SaleReturnItemQuerySet:
        if isinstance(organization, Organization):
            return self.filter(sale_return__organization=organization)
        return self.filter(sale_return__organization_id=organization)

    def for_user(self, user) -> SaleReturnItemQuerySet:
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_superuser", False):
            return self.all()
        return self.filter(sale_return__organization__in=Organization.objects.for_user(user))
