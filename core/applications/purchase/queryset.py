from __future__ import annotations

import auto_prefetch
from django.db.models import QuerySet

from core.applications.users.models import Organization
from core.helper.enums import PurchaseStatusChoices


class PurchaseQuerySet(auto_prefetch.QuerySet):
    """Slim, chainable queryset for Purchases scoped by organization and user."""

    def for_organization(self, organization: Organization | str) -> PurchaseQuerySet:
        if isinstance(organization, Organization):
            return self.filter(organization=organization)
        return self.filter(organization_id=organization)

    def for_user(self, user) -> PurchaseQuerySet:
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_superuser", False):
            return self.all()
        return self.filter(organization__in=Organization.objects.for_user(user))

    def draft(self) -> PurchaseQuerySet:
        return self.filter(status=PurchaseStatusChoices.DRAFT)

    def received(self) -> PurchaseQuerySet:
        return self.filter(status=PurchaseStatusChoices.RECEIVED)

    def with_details(self) -> PurchaseQuerySet:
        return self.select_related(
            "organization", "vendor", "warehouse", "ordered_by", "received_by"
        ).prefetch_related("items__product", "items__variant", "items__unit")


class PurchaseItemQuerySet(auto_prefetch.QuerySet):
    """Slim, chainable queryset for PurchaseItems."""

    def for_organization(self, organization: Organization | str) -> PurchaseItemQuerySet:
        if isinstance(organization, Organization):
            return self.filter(purchase__organization=organization)
        return self.filter(purchase__organization_id=organization)

    def for_user(self, user) -> PurchaseItemQuerySet:
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_superuser", False):
            return self.all()
        return self.filter(purchase__organization__in=Organization.objects.for_user(user))

    def with_details(self) -> PurchaseItemQuerySet:
        return self.select_related("purchase", "product", "variant", "unit")
