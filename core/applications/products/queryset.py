from __future__ import annotations

import auto_prefetch
from django.db.models import IntegerField
from django.db.models import OuterRef
from django.db.models import QuerySet
from django.db.models import Subquery
from django.db.models import Sum
from django.db.models import Value
from django.db.models.functions import Coalesce

from core.applications.users.models import Organization
from core.helper.enums import ProductStatusChoices


class ProductQuerySet(auto_prefetch.QuerySet):
    """
    Slim, organization-scoped queryset for Product.
    Enforces multi-tenant isolation and eager relation loading.
    """

    def for_organization(self, organization):
        """Scope records strictly to the specified tenant organization."""
        return self.filter(organization=organization)

    def with_stock(self, scope, location_id=None):
        """
        Annotate `stock_on_hand`: the product's on-hand quantity summed over the
        locations in `scope` (a warehouse.access.LocationScope), or only at
        `location_id` when given (PRD §35). Products are organization-wide; stock
        is per location.

        One correlated subquery served by the Inventory (product, ...) index, so a
        page of N products costs one query, not N + 1.
        """
        from core.applications.inventory.models import Inventory  # inventory imports products

        rows = scope.apply(Inventory.objects.filter(product=OuterRef("pk")), "warehouse")
        if location_id is not None:
            rows = scope.narrow(rows, location_id, "warehouse")
        total = rows.order_by().values("product").annotate(total=Sum("quantity")).values("total")[:1]
        return self.annotate(
            stock_on_hand=Coalesce(Subquery(total, output_field=IntegerField()), Value(0)),
        )

    def for_user(self, user):
        """Scope records to organizations the given user is an active member of."""
        return self.filter(
            organization__in=Organization.objects.for_user(user),
        )

    def active(self):
        """Filter to products in ACTIVE status, excluding archived or draft items."""
        return self.filter(status=ProductStatusChoices.ACTIVE)

    def with_details(self):
        """
        Eagerly load related foreign keys and reverse relations (batches, discounts,
        unit conversions, variants, images, inventory_records) to eliminate N+1 queries during serialization.
        """
        return self.select_related("category", "brand").prefetch_related(
            "batches",
            "bulk_discounts",
            "unit_conversions",
            "variants",
            "images",
            "inventory_records",
        )


class CategoryQuerySet(auto_prefetch.QuerySet):
    """Slim, organization-scoped queryset for Category."""

    def for_organization(self, organization):
        return self.filter(organization=organization)

    def for_user(self, user):
        return self.filter(
            organization__in=Organization.objects.for_user(user),
        )

    def active(self):
        return self.filter(is_active=True)


class BrandQuerySet(auto_prefetch.QuerySet):
    """Slim, organization-scoped queryset for Brand."""

    def for_organization(self, organization):
        return self.filter(organization=organization)

    def for_user(self, user):
        return self.filter(
            organization__in=Organization.objects.for_user(user),
        )

    def active(self):
        return self.filter(is_active=True)


class ProductVariantQuerySet(auto_prefetch.QuerySet):
    """Slim, organization-scoped queryset for ProductVariant."""

    def for_organization(self, organization):
        return self.filter(product__organization=organization)

    def for_user(self, user):
        return self.filter(
            product__organization__in=Organization.objects.for_user(user),
        )

    def active(self):
        return self.filter(is_active=True)


ProductManager = auto_prefetch.Manager.from_queryset(ProductQuerySet)
CategoryManager = auto_prefetch.Manager.from_queryset(CategoryQuerySet)
BrandManager = auto_prefetch.Manager.from_queryset(BrandQuerySet)
ProductVariantManager = auto_prefetch.Manager.from_queryset(ProductVariantQuerySet)
