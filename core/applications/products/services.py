from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework.exceptions import PermissionDenied

from core.applications.inventory.services import StockTarget
from core.applications.inventory.services import create_opening_stock
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.services import record as audit_record
from core.applications.products.models import Brand
from core.applications.products.models import Category
from core.applications.products.models import Product
from core.applications.products.models import ProductBatch
from core.applications.products.models import ProductBulkDiscount
from core.applications.products.models import ProductUnitConversion
from core.applications.products.models import ProductVariant
from core.applications.subscriptions.services.entitlements import (
    ensure_subscription_active,
)
from core.applications.subscriptions.services.usage import consume_usage
from core.applications.subscriptions.services.usage import release_usage
from core.applications.users.services import has_org_permission
from core.helper.enums import PermissionCode
from core.helper.enums import ProductStatusChoices
from core.helper.enums import UsageMetric

if TYPE_CHECKING:
    from core.applications.users.models import Organization
    from core.applications.users.models import User
    from core.applications.warehouse.models import Warehouse

__all__ = [
    "ProductRelationsInput",
    "archive_product",
    "create_brand",
    "create_category",
    "create_product",
    "delete_product",
    "get_unit_multiplier",
    "resolve_or_create_product_batch",
    "restore_product",
    "update_product",
]


@dataclass(frozen=True, slots=True)
class ProductRelationsInput:
    """Parameter object carrying nested product relations and opening stock details."""

    quantity_in_stock: int = 0
    warehouse: Warehouse | None = None
    batches: list[dict[str, Any]] | None = None
    bulk_discounts: list[dict[str, Any]] | None = None
    unit_conversions: list[dict[str, Any]] | None = None
    variants: list[dict[str, Any]] | None = None


def _validate_relation_cost_permissions(
    user: User,
    organization: Organization,
    relations: ProductRelationsInput,
) -> None:
    if relations.batches and any(
        b.get("cost_price") is not None for b in relations.batches
    ):
        if not has_org_permission(
            user, organization, PermissionCode.EDIT_PRODUCT_COSTS
        ):
            raise PermissionDenied(
                "You do not have permission to set or edit batch costs."
            )

    if relations.variants and any(
        v.get("purchase_cost") is not None for v in relations.variants
    ):
        if not has_org_permission(
            user, organization, PermissionCode.EDIT_PRODUCT_COSTS
        ):
            raise PermissionDenied(
                "You do not have permission to set or edit variant purchase costs."
            )


def _validate_product_permissions(
    *,
    user: User,
    organization: Organization,
    relations: ProductRelationsInput,
    product_fields: dict[str, Any],
    existing_product: Product | None = None,
) -> None:
    if existing_product is None:
        if not has_org_permission(user, organization, PermissionCode.CREATE_PRODUCT):
            raise PermissionDenied(
                "You do not have permission to create products in this organization."
            )
        if any(product_fields.get(f) for f in ("purchase_cost", "vendor_sale_cost")):
            if not has_org_permission(
                user, organization, PermissionCode.EDIT_PRODUCT_COSTS
            ):
                raise PermissionDenied(
                    "You do not have permission to set product costs."
                )
    else:
        if not has_org_permission(user, organization, PermissionCode.EDIT_PRODUCT):
            raise PermissionDenied(
                "You do not have permission to edit products in this organization."
            )
        cost_fields = ("purchase_cost", "vendor_sale_cost")
        if any(
            product_fields.get(f) is not None
            and product_fields.get(f) != getattr(existing_product, f)
            for f in cost_fields
        ):
            if not has_org_permission(
                user, organization, PermissionCode.EDIT_PRODUCT_COSTS
            ):
                raise PermissionDenied(
                    "You do not have permission to edit product costs."
                )
        if (
            "customer_sale_price" in product_fields
            and product_fields["customer_sale_price"]
            != existing_product.customer_sale_price
        ):
            if not has_org_permission(
                user, organization, PermissionCode.EDIT_SELLING_PRICE
            ):
                raise PermissionDenied(
                    "You do not have permission to edit selling prices in this organization."
                )

    _validate_relation_cost_permissions(user, organization, relations)


def _validate_product_tenant_references(
    organization: Organization,
    product_fields: dict[str, Any],
) -> None:
    category = product_fields.get("category")
    if category and getattr(category, "organization_id", None) != organization.id:
        raise ValidationError(
            {"category": "Selected category belongs to a different organization."}
        )

    brand = product_fields.get("brand")
    if brand and getattr(brand, "organization_id", None) != organization.id:
        raise ValidationError(
            {"brand": "Selected brand belongs to a different organization."}
        )


def _sync_batches_and_variants(
    product: Product, relations: ProductRelationsInput, *, is_create: bool
) -> None:
    if relations.batches is not None:
        for batch_data in relations.batches:
            batch_number = batch_data.get("batch_number")
            if not is_create and batch_number:
                ProductBatch.objects.update_or_create(
                    product=product,
                    batch_number=batch_number,
                    defaults=batch_data,
                )
            else:
                ProductBatch.objects.create(product=product, **batch_data)

    if relations.variants is not None:
        for var_data in relations.variants:
            var_sku = var_data.get("sku")
            if not is_create and var_sku:
                ProductVariant.objects.update_or_create(
                    product=product,
                    sku=var_sku,
                    defaults=var_data,
                )
            else:
                ProductVariant.objects.create(product=product, **var_data)


def _sync_discounts_and_units(
    product: Product, relations: ProductRelationsInput, *, is_create: bool
) -> None:
    if relations.bulk_discounts is not None:
        if not is_create:
            product.bulk_discounts.all().delete()
        for discount_data in relations.bulk_discounts:
            ProductBulkDiscount.objects.create(product=product, **discount_data)

    if relations.unit_conversions is not None:
        if not is_create:
            product.unit_conversions.all().delete()
        for unit_data in relations.unit_conversions:
            ProductUnitConversion.objects.create(product=product, **unit_data)


def _sync_product_nested_relations(
    product: Product, relations: ProductRelationsInput, *, is_create: bool
) -> None:
    _sync_batches_and_variants(product, relations, is_create=is_create)
    _sync_discounts_and_units(product, relations, is_create=is_create)


@transaction.atomic
def create_product(
    *,
    organization: Organization,
    created_by: User,
    relations: ProductRelationsInput | None = None,
    **product_fields: Any,
) -> Product:
    """
    Creates a new Product and coordinates associated nested items and opening stock.
    Enforces active subscription standing and reserves product quota server-side.
    """
    ensure_subscription_active(organization)

    resolved_relations = relations or ProductRelationsInput(
        quantity_in_stock=product_fields.pop("quantity_in_stock", 0),
        warehouse=product_fields.pop("warehouse", None),
        batches=product_fields.pop("batches", None),
        bulk_discounts=product_fields.pop("bulk_discounts", None),
        unit_conversions=product_fields.pop("unit_conversions", None),
        variants=product_fields.pop("variants", None),
    )

    _validate_product_permissions(
        user=created_by,
        organization=organization,
        relations=resolved_relations,
        product_fields=product_fields,
    )
    _validate_product_tenant_references(organization, product_fields)

    if resolved_relations.quantity_in_stock > 0 and not resolved_relations.warehouse:
        raise ValidationError(
            {"warehouse": "Warehouse is required when opening stock is specified."}
        )

    if (
        resolved_relations.warehouse
        and getattr(resolved_relations.warehouse, "organization_id", None)
        != organization.id
    ):
        raise ValidationError(
            {"warehouse": "Selected warehouse belongs to a different organization."}
        )

    product = Product(
        organization=organization,
        created_by=created_by,
        **product_fields,
    )
    product.clean()
    product.save()

    _sync_product_nested_relations(product, resolved_relations, is_create=True)

    if resolved_relations.quantity_in_stock > 0 and resolved_relations.warehouse:
        create_opening_stock(
            StockTarget(product=product),
            warehouse=resolved_relations.warehouse,
            quantity=resolved_relations.quantity_in_stock,
            user=created_by,
        )

    consume_usage(organization, UsageMetric.PRODUCTS)
    return product


@transaction.atomic
def update_product(
    *,
    product: Product,
    user: User,
    relations: ProductRelationsInput | None = None,
    **product_fields: Any,
) -> Product:
    """
    Updates a Product and synchronizes nested relations (batches, discounts, conversions, variants).
    Enforces server-side permission checks for costs and selling prices.
    """
    organization = product.organization

    resolved_relations = relations or ProductRelationsInput(
        batches=product_fields.pop("batches", None),
        bulk_discounts=product_fields.pop("bulk_discounts", None),
        unit_conversions=product_fields.pop("unit_conversions", None),
        variants=product_fields.pop("variants", None),
    )

    _validate_product_permissions(
        user=user,
        organization=organization,
        relations=resolved_relations,
        product_fields=product_fields,
        existing_product=product,
    )
    _validate_product_tenant_references(organization, product_fields)

    for attr, value in product_fields.items():
        setattr(product, attr, value)
    product.clean()
    product.save()

    _sync_product_nested_relations(product, resolved_relations, is_create=False)

    return product


@transaction.atomic
def archive_product(
    product: Product | None = None,
    user: User | None = None,
    *,
    reason: str = "",
    **kwargs: Any,
) -> Product:
    """
    Soft-archives a product, releases its quota slot, and records an audit event (PRD §44).
    Retains inventory and transaction history while retiring it from active sale.
    """
    if product is None:
        product = kwargs.get("product")
    if user is None:
        user = kwargs.get("user")
    if not reason and "reason" in kwargs:
        reason = kwargs["reason"]

    if user and not has_org_permission(
        user, product.organization, PermissionCode.ARCHIVE_PRODUCT
    ):
        raise PermissionDenied(
            "You do not have permission to archive products in this organization."
        )

    was_active = product.status != ProductStatusChoices.ARCHIVED
    product.status = ProductStatusChoices.ARCHIVED
    product.save(update_fields=["status", "updated_at"])

    if was_active:
        release_usage(product.organization, UsageMetric.PRODUCTS)

    if user:
        audit_record(
            action=AuditAction.PRODUCT_ARCHIVED,
            organization=product.organization,
            actor=user,
            resource=product,
            reason=reason or "Product archived.",
        )
    return product


@transaction.atomic
def restore_product(
    product: Product | None = None,
    user: User | None = None,
    **kwargs: Any,
) -> Product:
    """
    Restores an archived product to active status, enforcing plan limits and recording audit event (PRD §44).
    """
    if product is None:
        product = kwargs.get("product")
    if user is None:
        user = kwargs.get("user")

    if user and not has_org_permission(
        user, product.organization, PermissionCode.EDIT_PRODUCT
    ):
        raise PermissionDenied(
            "You do not have permission to restore products in this organization."
        )

    ensure_subscription_active(product.organization)

    was_archived = product.status == ProductStatusChoices.ARCHIVED
    if was_archived:
        consume_usage(product.organization, UsageMetric.PRODUCTS)

    product.status = ProductStatusChoices.ACTIVE
    product.save(update_fields=["status", "updated_at"])

    if user:
        audit_record(
            action=AuditAction.PRODUCT_RESTORED,
            organization=product.organization,
            actor=user,
            resource=product,
        )
    return product


@transaction.atomic
def delete_product(*, product: Product, user: User) -> None:
    """
    Hard-deletes a product, ensuring permissions and releasing its slot if active.
    """
    if not has_org_permission(
        user, product.organization, PermissionCode.DELETE_PRODUCT
    ):
        raise PermissionDenied(
            "You do not have permission to delete products in this organization."
        )

    was_active = product.status != ProductStatusChoices.ARCHIVED
    product.delete()
    if was_active:
        release_usage(product.organization, UsageMetric.PRODUCTS)


def create_category(
    *, organization: Organization, name: str, description: str = ""
) -> Category:
    """
    Creates a new category for the organization, write-gated by active subscription.
    """
    ensure_subscription_active(organization)
    return Category.objects.create(
        organization=organization, name=name, description=description
    )


def create_brand(*, organization: Organization, name: str) -> Brand:
    """
    Creates a new brand for the organization, write-gated by active subscription.
    """
    ensure_subscription_active(organization)
    return Brand.objects.create(organization=organization, name=name)


def get_unit_multiplier(product: Any, unit_name: str) -> Decimal:
    """
    Calculate how many base units one `unit_name` represents.
    If unit_name matches the product's base unit_of_measurement, returns 1.000.
    Otherwise queries product.unit_conversions.
    Raises ValueError if no conversion exists for the specified unit.
    """
    if unit_name == getattr(product, "unit_of_measurement", None):
        return Decimal("1.000")
    conversion = product.unit_conversions.filter(unit_name=unit_name).first()
    if conversion is None:
        raise ValueError(
            f"Product '{getattr(product, 'name', product)}' has no unit conversion for '{unit_name}'.",
        )
    return Decimal(str(conversion.multiplier))


def resolve_or_create_product_batch(
    *,
    product: Any,
    batch_id: Any = None,
    batch_number: str | None = None,
    expiry_date: Any = None,
    cost_price: Decimal | None = None,
) -> ProductBatch | None:
    """
    Resolves an existing ProductBatch by ID or creates/updates one by batch_number
    for goods receipt (UI Page 19: Batch No. & Expiry Date).
    """
    if batch_id:
        batch = ProductBatch.objects.filter(pk=batch_id, product=product).first()
        if batch is None:
            raise ValidationError(
                {
                    "batch": f"Batch '{batch_id}' does not belong to product '{product.name}'."
                }
            )
        if expiry_date and batch.expiry_date != expiry_date:
            batch.expiry_date = expiry_date
            batch.save(update_fields=["expiry_date", "updated_at"])
        return batch

    cleaned_number = (batch_number or "").strip()
    if cleaned_number:
        defaults: dict[str, Any] = {}
        if expiry_date is not None:
            defaults["expiry_date"] = expiry_date
        if cost_price is not None:
            defaults["cost_price"] = cost_price
        batch, _ = ProductBatch.objects.update_or_create(
            product=product,
            batch_number=cleaned_number,
            defaults=defaults,
        )
        return batch

    return None
