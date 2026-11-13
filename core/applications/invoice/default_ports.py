from decimal import ROUND_HALF_UP
from decimal import Decimal

from rest_framework.exceptions import PermissionDenied

from core.applications.invoice.exceptions import ApprovalRequiredError
from core.applications.invoice.exceptions import InvoiceValidationError
from core.applications.invoice.services import inventory_adapter
from core.applications.invoice.services import sales_adapters as sales_adapter
from core.applications.invoice.services.ports import APPROVAL_CREDIT_OVERRIDE
from core.applications.invoice.services.ports import APPROVAL_INVOICE_VOID
from core.applications.invoice.services.ports import UnitResolution
from core.applications.products.models import Product
from core.applications.products.models import ProductUnitConversion
from core.applications.products.tax import TaxPolicy
from core.applications.products.tax import calculate as calculate_tax
from core.applications.users import services as user_services
from core.applications.warehouse.access import LocationScope
from core.helper.enums import PermissionCode
from core.helper.enums import UnitOfMeasureChoices
from core.helper.money import allocate_amount

_CENT = Decimal("0.01")


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

    # --- inventory app (standalone invoices only) ---
    def deduct_stock(self, invoice, lines):
        inventory_adapter.deduct_stock_for_invoice(invoice, lines)

    def restore_stock(self, invoice, lines):
        # The original ledger entries decide what comes back (same products,
        # variants and batches), so `lines` is not used here.
        inventory_adapter.restore_stock_for_invoice(invoice)

    # --- users app ---
    def authorize(self, actor, organization, permission):
        if not user_services.has_org_permission(actor, organization, permission):
            msg = "You do not have permission to perform this action."
            raise PermissionDenied(msg)

    def location_scope(self, actor, organization):
        return LocationScope.for_user(actor, organization)

    # --- background work (later) ---
    def publish(self, event, invoice):
        # No PDFs, emails or notifications yet. Enqueue them here later (PRD §51).
        return

    # --- tax (PRD §13, §39): the calculator sales use too ---
    def calculate_tax(self, *, organization, lines, taxable_amount):
        """
        Tax for a standalone invoice, from the products' tax settings and the
        organization's default rate (products.tax). The invoice discount is spread
        over the lines in proportion to their value, so each line is taxed on what
        is actually charged for it. One query loads the products.
        """
        if not lines:
            return Decimal("0.00")
        product_ids = {line.product_id for line in lines}
        products = {
            str(product.pk): product
            for product in Product.objects.filter(organization=organization, pk__in=product_ids).only(
                "pk", "tax_config", "tax_rate"
            )
        }
        weights = [line.unit_price * line.quantity for line in lines]
        amounts = allocate_amount(Decimal(taxable_amount), weights) if sum(weights) else [Decimal(0)] * len(lines)
        try:
            result = calculate_tax(
                [(products.get(str(line.product_id)), amount) for line, amount in zip(lines, amounts, strict=True)],
                TaxPolicy.for_organization(organization),
            )
        except ValueError as exc:
            raise InvoiceValidationError(str(exc), detail={"tax": "invalid_rate"}) from exc
        return result.tax

    # --- Phase 2 approval rules (PRD §30, §56). The approval engine (pending /
    # approved / rejected requests) is Phase 3; these rules plug into the same
    # ports, so nothing that calls them changes when it lands. ---
    def validate_discount(self, *, actor, organization, subtotal, discount, approval_id=None):
        """Phase 2: a discount needs APPLY_DISCOUNT. (Phase 3: a "large discount" threshold.)"""
        if not user_services.has_org_permission(actor, organization, PermissionCode.APPLY_DISCOUNT):
            msg = "You do not have permission to apply discounts."
            raise PermissionDenied(msg)

    def require_approval(self, *, actor, organization, action, resource, approval_id):
        """
        Phase 2 rules:
          * invoice.void: allowed; the endpoint already requires the void permission,
            and the void is audited with its reason.
          * invoice.credit_override (a sale beyond the customer's credit limit):
            only members holding APPROVE_REQUESTS may push it through.
          * anything else: refused, so an unknown action can never slip past.
        An `approval_id` can't be honoured until the approval engine exists.
        """
        if action == APPROVAL_INVOICE_VOID:
            return
        if action == APPROVAL_CREDIT_OVERRIDE and user_services.has_org_permission(
            actor, organization, PermissionCode.APPROVE_REQUESTS
        ):
            return
        raise ApprovalRequiredError(detail={"action": action})
