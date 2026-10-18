"""
Last Modified: 2026-10-18
Module: core.applications.products.services
Description:
    Core domain services and business orchestration for the Products application.
    Enforces multi-tenant scoping, subscription limits (PRD §44), permission-based
    cost protection, strict opening stock validation (PRD §15), atomic nested entity
    synchronization (variants, batches, discounts, conversions), and immutable audit logs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework.exceptions import PermissionDenied

from core.applications.inventory.services import create_opening_stock
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.services import record as audit_record
from core.applications.products.models import (
    Product,
    ProductBatch,
    ProductBulkDiscount,
    ProductUnitConversion,
    ProductVariant,
)
from core.applications.subscriptions.services import (
    check_plan_limit,
    ensure_subscription_active,
)
from core.applications.users.services import has_org_permission
from core.helper.enums import PermissionCode, ProductStatusChoices

if TYPE_CHECKING:
    from core.applications.users.models import Organization, User
    from core.applications.warehouse.models import Warehouse

__all__ = [
    "create_product",
    "ensure_product_limit",
    "update_product",
    "archive_product",
    "restore_product",
]


def ensure_product_limit(organization: Organization) -> None:
    """
    Asserts that adding or restoring a product does not exceed the organization's plan limit.
    
    Strategic Note (PRD §44):
    Archived products are excluded from the active count so that organizations reaching
    their plan quota can archive obsolete or discontinued items to free up slots without
    losing historical transaction records.
    """
    active_count = (
        Product.objects.filter(organization=organization)
        .exclude(status=ProductStatusChoices.ARCHIVED)
        .count()
    )
    # Delegates to the centralized subscription limit service (raises PlanLimitExceeded if reached)
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
    variants: list[dict[str, Any]] | None = None,
    **product_fields: Any,
) -> Product:
    """
    Creates a new Product and coordinates associated nested items and opening stock.
    Enforces active subscription standing and the max_products plan limit server-side.
    """
    # Verify tenant subscription standing and quota
    ensure_subscription_active(organization)
    ensure_product_limit(organization)

    # Verify basic product creation capability
    if not has_org_permission(created_by, organization, PermissionCode.CREATE_PRODUCT):
        raise PermissionDenied("You do not have permission to create products in this organization.")

    # Enforce granular cost editing permissions (PRD role-based access control)
    # Users without EDIT_PRODUCT_COSTS (e.g. Sales Reps or basic Managers) cannot define purchase costs.
    if any(product_fields.get(f) for f in ("purchase_cost", "vendor_sale_cost")):
        if not has_org_permission(created_by, organization, PermissionCode.EDIT_PRODUCT_COSTS):
            raise PermissionDenied("You do not have permission to set product costs.")

    if batches and any(b.get("cost_price") is not None for b in batches):
        if not has_org_permission(created_by, organization, PermissionCode.EDIT_PRODUCT_COSTS):
            raise PermissionDenied("You do not have permission to set batch costs.")

    if variants and any(v.get("purchase_cost") is not None for v in variants):
        if not has_org_permission(created_by, organization, PermissionCode.EDIT_PRODUCT_COSTS):
            raise PermissionDenied("You do not have permission to set variant purchase costs.")

    # Multi-tenant validation: ensure foreign relations belong to the same tenant organization
    category = product_fields.get("category")
    if category and getattr(category, "organization_id", None) != organization.id:
        raise ValidationError({"category": "Selected category belongs to a different organization."})

    brand = product_fields.get("brand")
    if brand and getattr(brand, "organization_id", None) != organization.id:
        raise ValidationError({"brand": "Selected brand belongs to a different organization."})

    # Inventory compliance (PRD §15): opening stock strictly requires a designated warehouse
    # to ensure that initial inventory is immediately logged to an audited location.
    if quantity_in_stock > 0 and not warehouse:
        raise ValidationError({"warehouse": "Warehouse is required when opening stock is specified."})

    if warehouse and getattr(warehouse, "organization_id", None) != organization.id:
        raise ValidationError({"warehouse": "Selected warehouse belongs to a different organization."})

    # Persist primary product instance
    product = Product(
        organization=organization,
        created_by=created_by,
        **product_fields,
    )
    product.clean()
    product.save()

    # Provision nested child entities (atomic within transaction)
    if batches:
        for batch_data in batches:
            ProductBatch.objects.create(product=product, **batch_data)

    if bulk_discounts:
        for discount_data in bulk_discounts:
            ProductBulkDiscount.objects.create(product=product, **discount_data)

    if unit_conversions:
        for unit_data in unit_conversions:
            ProductUnitConversion.objects.create(product=product, **unit_data)

    if variants:
        for var_data in variants:
            ProductVariant.objects.create(product=product, **var_data)

    # Real side-effect: dispatch opening stock movement and ledger entries
    if quantity_in_stock > 0 and warehouse:
        create_opening_stock(
            product=product,
            variant=None,
            batch=None,
            warehouse=warehouse,
            quantity=quantity_in_stock,
            user=created_by,
        )

    return product


@transaction.atomic
def update_product(
    *,
    product: Product,
    user: User,
    batches: list[dict[str, Any]] | None = None,
    bulk_discounts: list[dict[str, Any]] | None = None,
    unit_conversions: list[dict[str, Any]] | None = None,
    variants: list[dict[str, Any]] | None = None,
    **product_fields: Any,
) -> Product:
    """
    Updates a Product and synchronizes nested relations (batches, discounts, conversions, variants).
    Enforces server-side permission checks for costs and selling prices.
    """
    organization = product.organization

    # Permission checks: verify basic edit capability
    if not has_org_permission(user, organization, PermissionCode.EDIT_PRODUCT):
        raise PermissionDenied("You do not have permission to edit products in this organization.")

    # Granular permission checks: protect purchasing cost modifications
    cost_fields = ("purchase_cost", "vendor_sale_cost")
    if any(product_fields.get(f) is not None and product_fields.get(f) != getattr(product, f) for f in cost_fields):
        if not has_org_permission(user, organization, PermissionCode.EDIT_PRODUCT_COSTS):
            raise PermissionDenied("You do not have permission to edit product costs.")

    # Granular permission checks: protect customer selling price modifications
    if "customer_sale_price" in product_fields and product_fields["customer_sale_price"] != product.customer_sale_price:
        if not has_org_permission(user, organization, PermissionCode.EDIT_SELLING_PRICE):
            raise PermissionDenied("You do not have permission to edit selling prices in this organization.")

    # Granular permission checks: verify nested batch and variant cost permissions
    if batches and any(b.get("cost_price") is not None for b in batches):
        if not has_org_permission(user, organization, PermissionCode.EDIT_PRODUCT_COSTS):
            raise PermissionDenied("You do not have permission to edit batch costs.")

    if variants and any(v.get("purchase_cost") is not None for v in variants):
        if not has_org_permission(user, organization, PermissionCode.EDIT_PRODUCT_COSTS):
            raise PermissionDenied("You do not have permission to edit variant purchase costs.")

    # Multi-tenant integrity: prevent cross-tenant foreign key assignment
    category = product_fields.get("category")
    if category and getattr(category, "organization_id", None) != organization.id:
        raise ValidationError({"category": "Selected category belongs to a different organization."})

    brand = product_fields.get("brand")
    if brand and getattr(brand, "organization_id", None) != organization.id:
        raise ValidationError({"brand": "Selected brand belongs to a different organization."})

    # Apply scalar product updates
    for attr, value in product_fields.items():
        setattr(product, attr, value)
    product.clean()
    product.save()

    # Upsert nested batches (differentiates by batch_number)
    if batches is not None:
        for batch_data in batches:
            batch_number = batch_data.get("batch_number")
            if batch_number:
                ProductBatch.objects.update_or_create(
                    product=product,
                    batch_number=batch_number,
                    defaults=batch_data,
                )
            else:
                ProductBatch.objects.create(product=product, **batch_data)

    # Synchronize bulk discounts (replace active tiers)
    if bulk_discounts is not None:
        product.bulk_discounts.all().delete()
        for discount_data in bulk_discounts:
            ProductBulkDiscount.objects.create(product=product, **discount_data)

    # Synchronize unit conversions (replace packaging options)
    if unit_conversions is not None:
        product.unit_conversions.all().delete()
        for unit_data in unit_conversions:
            ProductUnitConversion.objects.create(product=product, **unit_data)

    # Upsert variants (differentiates by SKU when provided)
    if variants is not None:
        for var_data in variants:
            var_sku = var_data.get("sku")
            if var_sku:
                ProductVariant.objects.update_or_create(
                    product=product,
                    sku=var_sku,
                    defaults=var_data,
                )
            else:
                ProductVariant.objects.create(product=product, **var_data)

    return product


@transaction.atomic
def archive_product(*, product: Product, user: User, reason: str = "") -> Product:
    """
    Soft-archives a product and records an immutable audit event (PRD §44).
    Invoked when items are referenced by transaction history (invoices, movements)
    and cannot be safely deleted via hard delete.
    """
    if not has_org_permission(user, product.organization, PermissionCode.ARCHIVE_PRODUCT):
        raise PermissionDenied("You do not have permission to archive products in this organization.")

    product.status = ProductStatusChoices.ARCHIVED
    product.save(update_fields=["status", "updated_at"])
    
    # Audit trail: Record the archival action with the actor and optional justification reason
    audit_record(
        action=AuditAction.PRODUCT_ARCHIVED,
        organization=product.organization,
        actor=user,
        resource=product,
        reason=reason or "Product archived.",
    )
    return product


@transaction.atomic
def restore_product(*, product: Product, user: User) -> Product:
    """
    Restores an archived product to active status, enforcing plan limits and recording audit event (PRD §44).
    """
    if not has_org_permission(user, product.organization, PermissionCode.EDIT_PRODUCT):
        raise PermissionDenied("You do not have permission to restore products in this organization.")

    # Enforce that restoring this product does not breach active subscription plan limits
    ensure_subscription_active(product.organization)
    ensure_product_limit(product.organization)

    product.status = ProductStatusChoices.ACTIVE
    product.save(update_fields=["status", "updated_at"])

    # Audit trail: Record the restoration action
    audit_record(
        action=AuditAction.PRODUCT_RESTORED,
        organization=product.organization,
        actor=user,
        resource=product,
    )
    return product
