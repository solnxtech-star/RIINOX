from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view

from core.applications.payment.api.serializers import PaymentCreateSerializer
from core.applications.payment.api.serializers import PaymentSerializer
from core.applications.payment.api.serializers import RecordedPaymentSerializer
from core.applications.warehouse.api.schemas import LOCATION_ACCESS_NOTE
from core.applications.warehouse.api.schemas import LOCATION_FILTER_PARAM
from core.helper.enums import PaymentMethod

TAGS = ["Payments"]
_INVOICE_ID = "8d2f6c1a-7b3e-4f9a-a1c2-5e6d7f8a9b0c"

_EXAMPLE = {
    "id": "3b9e7a52-1c4d-4e8f-9a6b-2d7c5e1f0a34",
    "receipt_number": "R-2026-0042",
    "amount": "20000.00",
    "currency": "NGN",
    "method": "bank_transfer",
    "reference": "TRF-558201",
    "note": "Second instalment",
    "balance_after": "15000.00",
    "invoice": {
        "id": _INVOICE_ID,
        "invoice_number": "INV-000118",
        "status": "partially_paid",
        "total": "50000.00",
        "amount_paid": "35000.00",
        "balance_due": "15000.00",
        "due_date": "2026-11-30",
    },
    "customer": {"id": "c41a0e6b-9d2f-4b7a-8e3c-1f5a6b7c8d9e", "name": "Adaeze Stores"},
    "location": {"id": "495e06a8-d874-489e-96c9-9fc9c10a450e", "name": "Lekki"},
    "received_by": {"id": 7, "name": "Emeka Obi"},
    "received_on": "2026-11-20T10:15:00Z",
    "is_voided": False,
}

_FILTERS = [
    LOCATION_FILTER_PARAM,
    OpenApiParameter("invoice", OpenApiTypes.UUID, description="Payments of one invoice (its payment history)."),
    OpenApiParameter("customer", OpenApiTypes.UUID, description="Payments from one customer."),
    OpenApiParameter("method", OpenApiTypes.STR, description=f"One of: {', '.join(PaymentMethod.values)}."),
    OpenApiParameter("date_from", OpenApiTypes.DATE, description="Received on or after (YYYY-MM-DD)."),
    OpenApiParameter("date_to", OpenApiTypes.DATE, description="Received on or before (YYYY-MM-DD)."),
    OpenApiParameter(
        "search", OpenApiTypes.STR, description="Receipt number, payment reference, invoice number or customer name."
    ),
    OpenApiParameter("ordering", OpenApiTypes.STR, description="`received_on` or `amount` (prefix `-` for desc)."),
]

_READ_PERMISSION = "**Permission:** `VIEW_PAYMENTS` or `VIEW_RECEIPTS`.\n\n"

payment_schema = extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        summary="List customer payments (receipts)",
        description=(
            "Money received from customers against invoices, newest first, paginated. Every payment is also "
            "the customer's receipt (`receipt_number`).\n\n"
            "Includes payments taken at the till with `POST /sales/` and payments recorded later with "
            "`POST /payments/`. Supplier payments are separate: `GET /purchases/{id}/payments/`.\n\n"
            "`invoice` shows the invoice **as it is now** (status, amount paid, balance). `balance_after` is the "
            "balance right after this payment.\n\n"
            "For one invoice's payment history use `?invoice=<id>`.\n\n" + _READ_PERMISSION + LOCATION_ACCESS_NOTE
        ),
        parameters=_FILTERS,
        responses={200: PaymentSerializer(many=True)},
        examples=[OpenApiExample("Payment", value=_EXAMPLE, response_only=True)],
    ),
    retrieve=extend_schema(
        tags=TAGS,
        summary="Payment (receipt) detail",
        description=_READ_PERMISSION + "Another organization's payment, or one outside your locations, is `404`.",
        responses={200: PaymentSerializer},
    ),
    create=extend_schema(
        tags=TAGS,
        summary="Record a customer payment against an invoice",
        description=(
            "Records money a customer paid towards an invoice: a part payment, the rest of a balance, or a "
            "credit customer settling up later (PRD §52). An invoice can take any number of payments.\n\n"
            "In one atomic step it creates the payment and its receipt number, updates the invoice "
            "(`amount_paid`, and status `partially_paid` or `paid`), the sale's payment status, the customer's "
            "balance and the ledger (Dr Cash/Bank, Cr Accounts Receivable), and writes an audit event.\n\n"
            "**Rules**\n"
            "* The invoice must be `confirmed`, `partially_paid` or `overdue`. Draft, void or fully paid "
            "invoices are `409 INVOICE_NOT_PAYABLE`.\n"
            "* `amount` left out = pay the whole balance.\n"
            "* **Cash** above the balance is accepted: the balance is recorded and the extra is returned as "
            "`change_due`. **Any other method** above the balance is `409 PAYMENT_EXCEEDS_BALANCE`.\n"
            "* **Safe retries:** `reference` identifies the payment. Resending the same `reference` for the same "
            "invoice and method returns the original payment with `200` and `replayed: true` instead of paying "
            "twice. A reference already used for a different payment is `409 DUPLICATE_PAYMENT_REFERENCE`. "
            "Tip: for cash, send a random id (e.g. a UUID) as the reference so a double-tap or network retry "
            "can't record the payment twice.\n"
            "* The invoice must be in one of your locations, else `404 INVOICE_NOT_FOUND`.\n\n"
            f"**`method`:** {', '.join(f'`{value}`' for value in PaymentMethod.values)}.\n\n"
            "Payments can't be edited or deleted. A returned sale is refunded through `POST /sales/returns/`.\n\n"
            "**Permission:** `RECORD_PAYMENT`."
        ),
        request=PaymentCreateSerializer,
        examples=[
            OpenApiExample(
                "Part payment by transfer",
                request_only=True,
                value={
                    "invoice_id": _INVOICE_ID,
                    "method": "bank_transfer",
                    "amount": "20000.00",
                    "reference": "TRF-558201",
                    "note": "Second instalment",
                },
            ),
            OpenApiExample(
                "Pay the rest in cash",
                request_only=True,
                value={"invoice_id": _INVOICE_ID, "method": "cash", "amount": "20000.00"},
            ),
            OpenApiExample(
                "Recorded",
                value={**_EXAMPLE, "change_due": "0.00", "replayed": False},
                response_only=True,
                status_codes=["201"],
            ),
        ],
        responses={
            201: RecordedPaymentSerializer,
            200: OpenApiResponse(RecordedPaymentSerializer, description="Retry of an already recorded payment."),
            400: OpenApiResponse(description="`VALIDATION_ERROR`, e.g. a bad amount or method."),
            404: OpenApiResponse(description="`INVOICE_NOT_FOUND`."),
            409: OpenApiResponse(
                description="`INVOICE_NOT_PAYABLE`, `PAYMENT_EXCEEDS_BALANCE` or `DUPLICATE_PAYMENT_REFERENCE`."
            ),
        },
    ),
)
