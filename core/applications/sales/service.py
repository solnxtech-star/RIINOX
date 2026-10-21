from decimal import Decimal
import uuid
from django.db import transaction
from django.utils import timezone

from core.applications.invoice.models import Client, Invoice, InvoiceItem
from core.applications.invoice.services.documents import allocate_document_number
from core.applications.notification.audit.action import AuditAction
from core.applications.notification.audit.services import record as audit_record
from core.applications.payment.models import InvoicePayment, Payment, Refund
from core.applications.sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem
from core.helper.enums import (
    CustomerStatusChoices,
    InvoiceStatusChoices,
    PaymentMethod,
    PaymentStatus,
    SalePaymentStatusChoices,
    TEMPLATE_TYPES,
)
from core.applications.inventory.services import (
    process_sale_inventory_and_ledger,
    process_return_inventory_and_ledger,
)
from core.applications.subscriptions.services.entitlements import (
    ensure_subscription_active,
)


def get_or_create_client_for_customer(customer) -> Client:
    """
    Resolves or provisions an Invoice Client for the corresponding transactions Customer (PRD §20, §21).
    Ensures multi-tenant scoping and unique_together (organization, email) rules are preserved.
    """
    client = None
    if customer.email:
        client = Client.objects.filter(
            organization=customer.organization,
            email=customer.email,
        ).first()

    if not client and customer.name:
        client = Client.objects.filter(
            organization=customer.organization,
            name=customer.name,
        ).first()

    if not client:
        fallback_email = customer.email or f"customer-{str(customer.id)[:12]}@riinox.internal"
        client, _ = Client.objects.get_or_create(
            organization=customer.organization,
            email=fallback_email,
            defaults={
                "name": customer.name or "Walk-in Customer",
                "phone": customer.phone or "",
                "address": customer.formatted_address,
                "status": CustomerStatusChoices.ACTIVE,
                "is_active": True,
            },
        )
    return client


def create_invoice_for_sale(sale: Sale) -> Invoice:
    """
    Creates an Invoice and its line items for a confirmed Sale (PRD §21, §22, §24).
    Snapshots prices, links the customer client, and dispatches invoice audit events.
    """
    client = get_or_create_client_for_customer(sale.customer)

    # Determine invoice status based on sale payment status
    if sale.payment_status == SalePaymentStatusChoices.PAID:
        inv_status = InvoiceStatusChoices.PAID
    elif sale.payment_status == SalePaymentStatusChoices.PARTIALLY_PAID:
        inv_status = InvoiceStatusChoices.PARTIALLY_PAID
    else:
        inv_status = InvoiceStatusChoices.CONFIRMED

    # Allocate a document number using DocumentSequence or fallback to formatted sale ID
    try:
        doc_num = allocate_document_number(sale.organization, TEMPLATE_TYPES.INVOICE)
        invoice_number = doc_num.formatted
    except Exception:
        invoice_number = f"INV-{sale.sale_id}"

    if Invoice.objects.filter(invoice_number=invoice_number).exists():
        invoice_number = f"INV-{sale.sale_id}-{uuid.uuid4().hex[:4].upper()}"

    due_date = sale.due_date or timezone.localdate()

    invoice = Invoice.objects.create(
        organization=sale.organization,
        client=client,
        warehouse=sale.location.warehouse,
        sales_rep=sale.sales_rep,
        confirmed_by=sale.sales_rep,
        invoice_number=invoice_number,
        due_date=due_date,
        status=inv_status,
        subtotal=sale.subtotal,
        discount_amount=sale.discount_amount,
        tax_amount=sale.tax_amount,
        total=sale.total,
        notes=sale.notes,
        extra_data={"sale_id": str(sale.id), "sale_number": sale.sale_id},
    )

    invoice_items = [
        InvoiceItem(
            invoice=invoice,
            product=item.product,
            description=item.product_name or item.product.name,
            quantity=item.quantity,
            unit_price=item.unit_price,
        )
        for item in sale.items.all()
    ]
    if invoice_items:
        InvoiceItem.objects.bulk_create(invoice_items)

    audit_record(
        action=AuditAction.INVOICE_CREATED,
        organization=sale.organization,
        actor=sale.sales_rep,
        resource=invoice,
        metadata={
            "sale_id": str(sale.id),
            "invoice_number": invoice.invoice_number,
            "total": str(invoice.total),
        },
    )

    return invoice


def record_payment_for_sale(sale: Sale, invoice: Invoice | None = None) -> Payment | None:
    """
    Records a Payment for the sale if payment has been tendered (PRD §21, §26).
    Returns None if the sale remains unpaid.
    """
    if sale.payment_status not in [SalePaymentStatusChoices.PAID, SalePaymentStatusChoices.PARTIALLY_PAID]:
        return None

    payment = Payment.objects.create(
        user=sale.sales_rep,
        amount=sale.total,
        currency=sale.currency or "USD",
        method=PaymentMethod.BANK_TRANSFER,
        status=PaymentStatus.SUCCESS,
        reference=f"PAY-{sale.sale_id}-{uuid.uuid4().hex[:6].upper()}",
        description=f"Payment for Sale {sale.sale_id}",
    )
    return payment


def create_receipt_for_sale(
    sale: Sale, invoice: Invoice | None = None, payment: Payment | None = None
) -> InvoicePayment | None:
    """
    Generates a Receipt (InvoicePayment) linking the Invoice and Payment (PRD §21, §25).
    """
    if not invoice or not payment:
        return None

    balance_after = max(Decimal("0.00"), invoice.total - payment.amount)
    receipt = InvoicePayment.objects.create(
        invoice=invoice,
        payment=payment,
        sales_rep=sale.sales_rep,
        amount=payment.amount,
        balance_after=balance_after,
    )
    return receipt


def record_sale_audit_event(sale: Sale):
    """
    Dispatches immutable audit log event for sale order creation and fulfillment (PRD §31).
    """
    audit_record(
        action=AuditAction.SALES_ORDER_CREATED,
        organization=sale.organization,
        actor=sale.sales_rep,
        resource=sale,
        metadata={
            "sale_id": sale.sale_id,
            "total": str(sale.total),
            "customer_id": str(sale.customer_id),
            "payment_status": sale.payment_status,
            "fulfillment_status": sale.fulfillment_status,
            "items_count": sale.items.count(),
        },
    )


def record_sale_return_audit_event(sale_return: SaleReturn):
    """
    Dispatches immutable audit log event for sale return creation (PRD §23, §31).
    """
    audit_record(
        action=AuditAction.SALES_RETURN_CREATED,
        organization=sale_return.organization,
        actor=sale_return.original_sale.sales_rep,
        resource=sale_return,
        metadata={
            "sale_return_id": str(sale_return.id),
            "original_sale_id": str(sale_return.original_sale_id),
            "refund_amount": str(sale_return.refund_amount),
            "refund_method": sale_return.refund_method,
            "return_reason": sale_return.return_reason,
            "items_count": sale_return.items.count(),
        },
    )


def record_refund_for_return(sale_return: SaleReturn) -> Refund | None:
    """
    Records a Refund for returned items against the original sale payment if one exists (PRD §23, §26).
    """
    invoice_payment = InvoicePayment.objects.filter(
        invoice__extra_data__sale_id=str(sale_return.original_sale.id)
    ).first()

    if invoice_payment and invoice_payment.payment:
        refund = Refund.objects.create(
            payment=invoice_payment.payment,
            amount=sale_return.refund_amount,
            reason=sale_return.return_reason[:255] if sale_return.return_reason else "Sale Return",
            reference=f"REF-{str(sale_return.id)[:8]}-{uuid.uuid4().hex[:6].upper()}",
            status=PaymentStatus.SUCCESS,
            approved_by=sale_return.original_sale.sales_rep if getattr(sale_return.original_sale.sales_rep, "is_staff", False) else None,
            approved_at=timezone.now(),
        )
        return refund
    return None


# ---------------------------------------------------------------------------
# Backwards Compatibility Aliases
# ---------------------------------------------------------------------------
mock_create_invoice = create_invoice_for_sale
mock_create_payment = record_payment_for_sale
mock_create_receipt = create_receipt_for_sale
mock_create_audit_event = record_sale_audit_event


def create_sale(data: dict) -> Sale:
    """Create a Sale and its items, coordinating external side-effects."""
    if "organization" in data:
        ensure_subscription_active(data["organization"])

    if not data.get("sale_id"):
        data["sale_id"] = f"SALE-{uuid.uuid4().hex[:8].upper()}"

    items_data = data.pop("items", [])

    with transaction.atomic():
        sale = Sale.objects.create(**data)

        for item_data in items_data:
            product = item_data["product"]
            unit_name = item_data.get("unit_name")

            if unit_name and unit_name != product.unit_of_measurement:
                conversion = product.unit_conversions.filter(unit_name=unit_name).first()
                if conversion:
                    item_data["unit_multiplier"] = conversion.multiplier
                else:
                    item_data["unit_multiplier"] = 1.0
            else:
                item_data["unit_multiplier"] = 1.0

            SaleItem.objects.create(sale=sale, **item_data)

        # Calculate subtotal and total if not set
        if not sale.subtotal or not sale.total:
            computed_subtotal = sum(item.quantity * item.unit_price for item in sale.items.all())
            sale.subtotal = sale.subtotal or computed_subtotal
            computed_total = max(
                Decimal("0.00"),
                sale.subtotal - (sale.discount_amount or Decimal("0.00")) + (sale.tax_amount or Decimal("0.00")),
            )
            sale.total = sale.total or computed_total
            sale.save(update_fields=["subtotal", "total"])

        # Execute external operations
        process_sale_inventory_and_ledger(sale)
        invoice = create_invoice_for_sale(sale)
        payment = record_payment_for_sale(sale, invoice=invoice)
        if invoice and payment:
            create_receipt_for_sale(sale, invoice=invoice, payment=payment)
        record_sale_audit_event(sale)

    return sale


def create_sale_return(data: dict) -> SaleReturn:
    """Create a SaleReturn and its items."""
    if "organization" in data:
        ensure_subscription_active(data["organization"])

    items_data = data.pop("items", [])

    with transaction.atomic():
        sale_return = SaleReturn.objects.create(**data)

        for item_data in items_data:
            SaleReturnItem.objects.create(sale_return=sale_return, **item_data)

        process_return_inventory_and_ledger(sale_return)
        record_refund_for_return(sale_return)
        record_sale_return_audit_event(sale_return)

    return sale_return

