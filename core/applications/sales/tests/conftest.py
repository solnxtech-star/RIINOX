from decimal import Decimal
import pytest

from core.applications.invoice.services.ports import (
    SaleLineSnapshot,
    SaleSnapshot,
    register_invoice_ports,
)


class TestInvoicePorts:
    def authorize(self, actor, organization, permission) -> None:
        pass

    def validate_discount(self, *, actor, organization, subtotal, discount, approval_id=None) -> None:
        pass

    def require_approval(self, *, actor, organization, action, resource, approval_id) -> None:
        pass

    def calculate_tax(self, *, organization, lines, taxable_amount) -> Decimal:
        return Decimal("0.00")

    def deduct_stock(self, invoice, lines) -> None:
        pass

    def restore_stock(self, invoice, lines) -> None:
        pass

    def get_sale_snapshot(self, sale) -> SaleSnapshot:
        items = sale.items.all().order_by("pk")
        return SaleSnapshot(
            sale_id=sale.pk,
            organization_id=sale.organization_id,
            customer_id=sale.customer_id,
            warehouse_id=(
                sale.warehouse_id
                or (sale.location.warehouse_id if sale.location else None)
            ),
            sales_rep_id=sale.sales_rep_id,
            currency=sale.currency,
            due_date=sale.due_date,
            notes=sale.notes,
            is_cancelled=getattr(sale, "cancelled_at", None) is not None,
            subtotal=sale.subtotal,
            discount_amount=sale.discount_amount,
            tax_amount=sale.tax_amount,
            total=sale.total,
            lines=tuple(
                SaleLineSnapshot(
                    product_id=item.product_id,
                    variant_id=item.variant_id,
                    description=item.product_name,
                    quantity=item.quantity,
                    unit_name=item.unit_name,
                    unit_multiplier=item.unit_multiplier,
                    unit_price=item.unit_price,
                    discount=item.discount,
                    tax=item.tax,
                    tax_rate=item.tax_rate,
                )
                for item in items
            ),
            billing_address=sale.billing_address or "",
            shipping_address=sale.shipping_address or "",
        )

    def cancel_sale(self, invoice, *, actor, reason) -> None:
        pass

    def has_payments(self, invoice) -> bool:
        return False

    def refresh_customer_balance(self, customer) -> None:
        pass

    def publish(self, event, invoice) -> None:
        pass


@pytest.fixture(autouse=True, scope="session")
def setup_test_invoice_ports():
    register_invoice_ports(TestInvoicePorts())
