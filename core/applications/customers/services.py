from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError
from rest_framework.exceptions import PermissionDenied

from core.applications.customers.models import Customer, CustomerAddress
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.services import record as audit_record
from core.applications.subscriptions.services.entitlements import ensure_subscription_active
from core.applications.users.services import has_org_permission
from core.helper.enums import CustomerStatus, PermissionCode

if TYPE_CHECKING:
    from core.applications.users.models import Organization, User


__all__ = [
    "create_customer",
    "update_customer",
    "delete_customer",
    "adjust_customer_credit",
]


@transaction.atomic
def create_customer(
    *,
    organization: Organization,
    user: User,
    name: str,
    customer_type: str = "individual",
    company_name: str = "",
    email: str = "",
    phone: str = "",
    tax_id: str = "",
    credit_limit: Decimal | float | int = Decimal("0.00"),
    payment_terms_days: int = 0,
    status: str = CustomerStatus.ACTIVE,
    notes: str = "",
    metadata: dict[str, Any] | None = None,
    addresses: list[dict[str, Any]] | None = None,
) -> Customer:
    """
    Provisions a new customer for the tenant organization (PRD §20).
    Enforces active subscription standing, role permissions, and credit governance.
    """
    ensure_subscription_active(organization)

    if not has_org_permission(user, organization, PermissionCode.MANAGE_CUSTOMERS):
        raise PermissionDenied("You do not have permission to create customers in this organization.")

    credit_limit_dec = Decimal(str(credit_limit)) if credit_limit is not None else Decimal("0.00")
    if credit_limit_dec < Decimal("0.00"):
        raise ValidationError({"credit_limit": "Credit limit cannot be negative."})

    # Credit governance (PRD §26): Granting credit requires explicit permission
    if credit_limit_dec > Decimal("0.00") or payment_terms_days > 0:
        if not has_org_permission(user, organization, PermissionCode.MANAGE_CUSTOMER_CREDIT):
            raise PermissionDenied("You do not have permission to grant customer credit limits or credit terms.")

    # Tenant-scoped uniqueness validation
    email_clean = email.strip() if email else ""
    phone_clean = phone.strip() if phone else ""

    if email_clean and Customer.objects.filter(organization=organization, email__iexact=email_clean).exists():
        raise ValidationError({"email": "A customer with this email already exists in your organization."})

    if phone_clean and Customer.objects.filter(organization=organization, phone=phone_clean).exists():
        raise ValidationError({"phone": "A customer with this phone number already exists in your organization."})

    customer = Customer.objects.create(
        organization=organization,
        created_by=user,
        customer_type=customer_type,
        name=name.strip(),
        company_name=company_name.strip() if company_name else "",
        email=email_clean,
        phone=phone_clean,
        tax_id=tax_id.strip() if tax_id else "",
        credit_limit=credit_limit_dec,
        balance=Decimal("0.00"),
        payment_terms_days=payment_terms_days,
        status=status,
        notes=notes.strip() if notes else None,
        metadata=metadata or {},
    )

    if addresses:
        for addr_data in addresses:
            CustomerAddress.objects.create(customer=customer, **addr_data)

    audit_record(
        action=AuditAction.CUSTOMER_CREATED,
        organization=organization,
        actor=user,
        resource=customer,
        metadata={"customer_id": str(customer.id), "name": customer.name, "credit_limit": str(customer.credit_limit)},
    )
    return customer


@transaction.atomic
def update_customer(
    *,
    customer: Customer,
    user: User,
    addresses: list[dict[str, Any]] | None = None,
    **customer_fields: Any,
) -> Customer:
    """
    Updates an existing customer record and synchronizes associated addresses.
    Protects credit limit adjustments behind MANAGE_CUSTOMER_CREDIT.
    """
    organization = customer.organization

    if not has_org_permission(user, organization, PermissionCode.MANAGE_CUSTOMERS):
        raise PermissionDenied("You do not have permission to update customers in this organization.")

    # Credit modification protection
    new_credit_limit = customer_fields.get("credit_limit")
    new_terms = customer_fields.get("payment_terms_days")

    if new_credit_limit is not None and Decimal(str(new_credit_limit)) != customer.credit_limit:
        if not has_org_permission(user, organization, PermissionCode.MANAGE_CUSTOMER_CREDIT):
            raise PermissionDenied("You do not have permission to modify customer credit limits.")
        if Decimal(str(new_credit_limit)) < Decimal("0.00"):
            raise ValidationError({"credit_limit": "Credit limit cannot be negative."})
        customer_fields["credit_limit"] = Decimal(str(new_credit_limit))

    if new_terms is not None and new_terms != customer.payment_terms_days:
        if not has_org_permission(user, organization, PermissionCode.MANAGE_CUSTOMER_CREDIT):
            raise PermissionDenied("You do not have permission to modify customer credit payment terms.")

    # Email and phone uniqueness validation (excluding current instance)
    if "email" in customer_fields:
        email_clean = (customer_fields["email"] or "").strip()
        if email_clean and Customer.objects.filter(
            organization=organization, email__iexact=email_clean
        ).exclude(pk=customer.pk).exists():
            raise ValidationError({"email": "A customer with this email already exists in your organization."})
        customer_fields["email"] = email_clean

    if "phone" in customer_fields:
        phone_clean = (customer_fields["phone"] or "").strip()
        if phone_clean and Customer.objects.filter(
            organization=organization, phone=phone_clean
        ).exclude(pk=customer.pk).exists():
            raise ValidationError({"phone": "A customer with this phone number already exists in your organization."})
        customer_fields["phone"] = phone_clean

    # Snapshot old data for audit
    previous_values = {
        "name": customer.name,
        "credit_limit": str(customer.credit_limit),
        "status": customer.status,
    }

    for attr, val in customer_fields.items():
        if attr != "balance":  # balance is read-only / ledger derived
            setattr(customer, attr, val)

    customer.clean()
    customer.save()

    if addresses is not None:
        customer.addresses.all().delete()
        for addr_data in addresses:
            CustomerAddress.objects.create(customer=customer, **addr_data)

    new_values = {
        "name": customer.name,
        "credit_limit": str(customer.credit_limit),
        "status": customer.status,
    }

    audit_record(
        action=AuditAction.CUSTOMER_UPDATED,
        organization=organization,
        actor=user,
        resource=customer,
        previous_values=previous_values,
        new_values=new_values,
    )
    return customer


@transaction.atomic
def delete_customer(*, customer: Customer, user: User) -> tuple[bool, Customer]:
    """
    Deletes an unreferenced customer, or gracefully soft-deactivates if referenced (PRD §44).
    Returns (True, customer) if hard-deleted, or (False, customer) if soft-deactivated.
    """
    organization = customer.organization

    if not has_org_permission(user, organization, PermissionCode.MANAGE_CUSTOMERS):
        raise PermissionDenied("You do not have permission to delete customers in this organization.")

    try:
        customer.delete()
        audit_record(
            action=AuditAction.CUSTOMER_DELETED,
            organization=organization,
            actor=user,
            resource=customer,
            reason="Customer hard-deleted.",
        )
        return True, customer
    except ProtectedError:
        customer.status = CustomerStatus.INACTIVE
        customer.save(update_fields=["status", "updated_at"])
        audit_record(
            action=AuditAction.CUSTOMER_DEACTIVATED,
            organization=organization,
            actor=user,
            resource=customer,
            reason="Customer referenced by sales/invoices; soft-deactivated instead of deleted.",
        )
        return False, customer


@transaction.atomic
def adjust_customer_credit(
    *,
    customer: Customer,
    user: User,
    credit_limit: Decimal | float | int,
    payment_terms_days: int | None = None,
    reason: str = "",
) -> Customer:
    """
    Dedicated credit policy governance service (PRD §26).
    Requires MANAGE_CUSTOMER_CREDIT and records an immutable audit log.
    """
    organization = customer.organization

    if not has_org_permission(user, organization, PermissionCode.MANAGE_CUSTOMER_CREDIT):
        raise PermissionDenied("You do not have permission to adjust customer credit limits or terms.")

    credit_limit_dec = Decimal(str(credit_limit))
    if credit_limit_dec < Decimal("0.00"):
        raise ValidationError({"credit_limit": "Credit limit cannot be negative."})

    previous_values = {
        "credit_limit": str(customer.credit_limit),
        "payment_terms_days": customer.payment_terms_days,
    }

    customer.credit_limit = credit_limit_dec
    if payment_terms_days is not None:
        customer.payment_terms_days = payment_terms_days

    customer.save(update_fields=["credit_limit", "payment_terms_days", "updated_at"])

    new_values = {
        "credit_limit": str(customer.credit_limit),
        "payment_terms_days": customer.payment_terms_days,
    }

    audit_record(
        action=AuditAction.CUSTOMER_CREDIT_ADJUSTED,
        organization=organization,
        actor=user,
        resource=customer,
        previous_values=previous_values,
        new_values=new_values,
        reason=reason or "Customer credit adjusted.",
    )
    return customer
