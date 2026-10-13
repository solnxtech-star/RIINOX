from __future__ import annotations

import auto_prefetch
from django.db.models import QuerySet

from core.helper.enums import ProductStatusChoices


class ProductQuerySet(auto_prefetch.QuerySet):
    """Slim, organization-scoped queryset for Product."""

    def for_organization(self, organization):
        return self.filter(organization=organization)

    def for_user(self, user):
        from core.applications.users.models import Organization

        return self.filter(
            organization__in=Organization.objects.for_user(user)
        )

    def active(self):
        return self.filter(status=ProductStatusChoices.ACTIVE)

    def with_details(self):
        return self.select_related("category", "brand").prefetch_related(
            "batches", "bulk_discounts", "unit_conversions", "images"
        )


class CategoryQuerySet(auto_prefetch.QuerySet):
    """Slim, organization-scoped queryset for Category."""

    def for_organization(self, organization):
        return self.filter(organization=organization)

    def for_user(self, user):
        from core.applications.users.models import Organization

        return self.filter(
            organization__in=Organization.objects.for_user(user)
        )

    def active(self):
        return self.filter(is_active=True)


class BrandQuerySet(auto_prefetch.QuerySet):
    """Slim, organization-scoped queryset for Brand."""

    def for_organization(self, organization):
        return self.filter(organization=organization)

    def for_user(self, user):
        from core.applications.users.models import Organization

        return self.filter(
            organization__in=Organization.objects.for_user(user)
        )

    def active(self):
        return self.filter(is_active=True)


class ProductVariantQuerySet(auto_prefetch.QuerySet):
    """Slim, organization-scoped queryset for ProductVariant."""

    def for_organization(self, organization):
        return self.filter(product__organization=organization)

    def for_user(self, user):
        from core.applications.users.models import Organization

        return self.filter(
            product__organization__in=Organization.objects.for_user(user)
        )

    def active(self):
        return self.filter(is_active=True)


ProductManager = auto_prefetch.Manager.from_queryset(ProductQuerySet)
CategoryManager = auto_prefetch.Manager.from_queryset(CategoryQuerySet)
BrandManager = auto_prefetch.Manager.from_queryset(BrandQuerySet)
ProductVariantManager = auto_prefetch.Manager.from_queryset(ProductVariantQuerySet)
