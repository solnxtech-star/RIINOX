from decimal import ROUND_HALF_UP
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import PermissionDenied

from core.applications.invoice.exceptions import InvoiceValidationError
from core.applications.invoice.services import sales_adapters as sales_adapter
from core.applications.invoice.services.ports import UnitResolution
from core.applications.products.models import ProductUnitConversion
from core.applications.users import services as user_services
from core.helper.enums import UnitOfMeasureChoices

_CENT = Decimal("0.01")


def _dev_only(name: str) -> None:
    """Let a temporary stand-in run in development and refuse to run in production."""
    if not settings.DEBUG:
        msg = f"InvoicePorts.{name} is a development stand-in and cannot run in production."
        raise ImproperlyConfigured(msg)


def _normalize_unit(raw: str) -> str | None:
    """Map what a client sent ('Carton', 'carton ') to a stored enum value."""
    wanted = raw.strip().lower()
    for value, label in UnitOfMeasureChoices.choices:
        if wanted in (str(value).lower(), str(label).lower()):
            return value
    return None


class DefaultInvoicePorts:
    # --- sales app ---
    def get_sale_snapshot(self, sale):
        return sales_adapter.build_sale_snapshot(sale)

    def cancel_sale(self, invoice, *, actor, reason):
        return sales_adapter.cancel_sale_for_invoice(invoice, actor=actor, reason=reason)

    def has_payments(self, invoice):
        return sales_adapter.invoice_has_payments(invoice)

    def refresh_customer_balance(self, customer):
        return sales_adapter.refresh_customer_balance(customer)

    # --- products app ---
    def resolve_unit(self, *, organization, product, variant, unit_name, base_price):
        unit = _normalize_unit(unit_name)

        conversion = None
        if unit is not None:
            # The product's own base unit needs no conversion row.
            if unit == product.unit_of_measurement:
                return UnitResolution(unit_name=unit, multiplier=Decimal(1), unit_price=base_price)

            conversion = ProductUnitConversion.objects.filter(
                product=product,
                product__organization=organization,
                unit_name=unit,
            ).first()

        if conversion is None:
            msg = f"The unit '{unit_name}' is not available for this product."
            raise InvoiceValidationError(
                msg,
                detail={"unit_name": unit_name, "product_id": str(product.pk)},
            )

        price = (
            conversion.price_override
            if conversion.price_override is not None
            else base_price * conversion.multiplier
        )
        return UnitResolution(
            unit_name=conversion.unit_name,
            multiplier=conversion.multiplier,
            unit_price=price.quantize(_CENT, rounding=ROUND_HALF_UP),
        )

    # --- real now ---
    def authorize(self, actor, organization, permission):
        if not user_services.has_org_permission(actor, organization, permission):
            msg = "You do not have permission to perform this action."
            raise PermissionDenied(msg)

    def publish(self, event, invoice):
        # No PDFs, emails or notifications yet. Enqueue them here later (PRD §51).
        return

    # --- development stand-ins: run only when DEBUG is True ---
    def calculate_tax(self, *, organization, lines, taxable_amount):
        _dev_only("calculate_tax")
        return Decimal("0.00")

    def validate_discount(self, *, actor, organization, subtotal, discount, approval_id=None):
        _dev_only("validate_discount")

    def require_approval(self, *, actor, organization, action, resource, approval_id):
        _dev_only("require_approval")

    # --- must never be silent ---
    def deduct_stock(self, invoice, lines):
        msg = "InvoicePorts.deduct_stock is not implemented: confirming would change no stock."
        raise NotImplementedError(msg)

    def restore_stock(self, invoice, lines):
        msg = "InvoicePorts.restore_stock is not implemented: voiding would not reverse stock."
        raise NotImplementedError(msg)
