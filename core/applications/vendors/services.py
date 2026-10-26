from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ObjectDoesNotExist, ValidationError

from core.applications.vendors.models import Vendor
from core.helper.enums import PurchaseStatusChoices, VendorStatusChoices
from core.helper.money import quantize_money

if TYPE_CHECKING:
    from core.applications.users.models import Organization

__all__ = [
    "load_vendor_for_organization",
    "vendor_snapshot",
    "get_vendor_balance_summary",
]


def load_vendor_for_organization(
    organization: Organization,
    vendor_id: Any,
    *,
    require_active: bool = True,
    lock: bool = False,
) -> Vendor:
    """
    Loads a vendor belonging to the given organization.
    Raises ValidationError if the vendor does not belong to the organization or is inactive.
    """
    queryset = Vendor.objects.for_organization(organization)
    if lock:
        queryset = queryset.select_for_update()
    try:
        vendor = queryset.get(pk=vendor_id)
    except (ObjectDoesNotExist, ValidationError, ValueError, TypeError) as exc:
        raise ValidationError({"vendor": "Vendor does not belong to this organization."}) from exc

    if require_active and vendor.status != VendorStatusChoices.ACTIVE:
        raise ValidationError({"vendor": "This vendor is inactive and cannot receive new orders."})
    return vendor


def vendor_snapshot(vendor: Vendor) -> dict[str, str]:
    """
    Returns frozen supplier details for snapshotting onto a PurchaseInvoice.
    """
    return {
        "vendor_company_name": vendor.company_name or "",
        "vendor_contact_person": vendor.contact_person or "",
        "vendor_email": vendor.email or "",
        "vendor_phone": vendor.phone or "",
        "vendor_address": vendor.address or "",
    }


def get_vendor_balance_summary(vendor: Vendor) -> dict[str, Decimal]:
    """
    Derives total purchases, amount paid, and outstanding balance from purchase history (PRD §19).
    Only non-cancelled purchases that have received goods contribute to supplier liability.
    """
    purchases = (
        vendor.purchases.filter(visible=True)
        .exclude(status=PurchaseStatusChoices.CANCELLED)
        .prefetch_related("items", "purchase_invoices")
    )

    total_purchases = Decimal("0.00")
    amount_paid = Decimal("0.00")

    for purchase in purchases:
        active_invoice = next(
            (inv for inv in purchase.purchase_invoices.all() if not inv.is_voided),
            None,
        )
        if active_invoice is not None:
            total_purchases += quantize_money(active_invoice.total, field="total")
            amount_paid += quantize_money(active_invoice.amount_paid, field="amount_paid")
        elif any(item.quantity_received > 0 for item in purchase.items.all()):
            total_purchases += quantize_money(purchase.total_value, field="total_value")
            amount_paid += quantize_money(purchase.amount_paid, field="amount_paid")

    outstanding_balance = max(Decimal("0.00"), total_purchases - amount_paid)
    return {
        "total_purchases": quantize_money(total_purchases, field="total_purchases"),
        "amount_paid": quantize_money(amount_paid, field="amount_paid"),
        "outstanding_balance": quantize_money(outstanding_balance, field="outstanding_balance"),
    }

