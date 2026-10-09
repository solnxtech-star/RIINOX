from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import transaction

from core.applications.products.models import (
    Product,
    ProductBatch,
    ProductBulkDiscount,
    ProductUnitConversion,
)
from core.applications.subscriptions.services import (
    check_plan_limit,
    ensure_subscription_active,
)
from core.helper.enums import ProductStatusChoices

if TYPE_CHECKING:
    from core.applications.users.models import Organization, User
    from core.applications.warehouse.models import Warehouse

__all__ = [
    "create_product",
    "ensure_product_limit",
]


def ensure_product_limit(organization: Organization) -> None:
    """
    Asserts that adding a new product does not exceed the organization's plan limit.
    Archived products are excluded so tenants can archive discontinued items to free up slots.
    """
    active_count = (
        Product.objects.filter(organization=organization)
        .exclude(status=ProductStatusChoices.ARCHIVED)
        .count()
    )
    check_plan_limit(organization, "max_products", active_count)


@transaction.atomic
def create_product(
    *,
    organization: Organization,
    created_by: User,
    quantity_in_stock: int = 0,
    warehouse: Warehouse | None = None,
    batches: list[dict[str, Any]] | None = None,
    bulk_discounts: list[dict[str, Any]] | None = None,
    unit_conversions: list[dict[str, Any]] | None = None,
    **product_fields: Any,
) -> Product:
    """
    Creates a new Product and coordinates associated nested items and opening stock.
    Enforces active subscription standing and the max_products plan limit server-side.
    """
    ensure_subscription_active(organization)
    ensure_product_limit(organization)

    product = Product.objects.create(
        organization=organization,
        created_by=created_by,
        **product_fields,
    )

    if batches:
        for batch_data in batches:
            ProductBatch.objects.create(product=product, **batch_data)

    if bulk_discounts:
        for discount_data in bulk_discounts:
            ProductBulkDiscount.objects.create(product=product, **discount_data)

    if unit_conversions:
        for unit_data in unit_conversions:
            ProductUnitConversion.objects.create(product=product, **unit_data)

    if quantity_in_stock > 0 and warehouse:
        from core.applications.inventory.services import create_opening_stock

        create_opening_stock(
            product=product,
            variant=None,
            batch=None,
            warehouse=warehouse,
            quantity=quantity_in_stock,
            user=created_by,
        )

    return product
