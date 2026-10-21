from __future__ import annotations

from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
    extend_schema_view,
)

from core.applications.customers.api.serializers import (
    CustomerCreditAdjustmentSerializer,
    CustomerSerializer,
)

CUSTOMER_ORGANIZATION_PARAM = OpenApiParameter(
    "organization_id",
    OpenApiTypes.UUID,
    OpenApiParameter.PATH,
    description="UUID of the tenant organization owning the customer records.",
)

CUSTOMER_CREATE_EXAMPLE = {
    "customer_type": "business",
    "name": "Acme Global Solutions",
    "company_name": "Acme Global Solutions Ltd",
    "email": "purchasing@acmeglobal.com",
    "phone": "+2348012345678",
    "tax_id": "TIN-9876543210",
    "address": "Plot 12, Commercial Avenue, Victoria Island, Lagos",
    "credit_limit": "50000.00",
    "payment_terms_days": 30,
    "status": "active",
    "notes": "Preferred wholesale client with Net-30 credit terms.",
    "metadata": {
        "tier": "wholesale_gold",
        "industry": "logistics",
    },
    "addresses": [
        {
            "address_type": "shipping",
            "street": "Warehouse 4, Ikeja Industrial Estate",
            "city": "Ikeja",
            "state": "Lagos",
            "postal_code": "100001",
            "country": "Nigeria",
            "is_default": True,
        }
    ],
}

CUSTOMER_CREDIT_ADJUSTMENT_EXAMPLE = {
    "credit_limit": "75000.00",
    "payment_terms_days": 45,
    "reason": "Approved higher credit limit based on consistent on-time payment track record.",
}

customer_schema = extend_schema_view(
    list=extend_schema(
        tags=["Customers"],
        summary="List all Customers",
        description="""
        Retrieves a paginated list of customers scoped strictly to the tenant organization.
        Supports full-text search across name, company, email, and phone, as well as filtering by status and customer type.
        """,
        parameters=[
            CUSTOMER_ORGANIZATION_PARAM,
            OpenApiParameter(
                "status",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                description="Filter by lifecycle status (active, inactive, blocked).",
            ),
            OpenApiParameter(
                "customer_type",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                description="Filter by customer classification (individual, business).",
            ),
            OpenApiParameter(
                "search",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                description="Search query matching customer name, company name, email, or phone number.",
            ),
        ],
        responses={200: CustomerSerializer(many=True)},
    ),
    retrieve=extend_schema(
        tags=["Customers"],
        summary="Retrieve Customer Details",
        description="Retrieves complete profile details for a specific customer, including current balance, credit limits, and attached addresses.",
        parameters=[CUSTOMER_ORGANIZATION_PARAM],
        responses={
            200: CustomerSerializer,
            404: OpenApiResponse(description="Customer or organization not found."),
        },
    ),
    create=extend_schema(
        tags=["Customers"],
        summary="Create a Customer",
        description="""
        Provisions a new customer record under the tenant organization (PRD §20).
        - Enforces active subscription standing (SUBSCRIPTION_REQUIRED write gate).
        - Granting non-zero credit limits or payment terms requires MANAGE_CUSTOMER_CREDIT permission.
        - Enforces tenant-scoped email and phone uniqueness.
        """,
        parameters=[CUSTOMER_ORGANIZATION_PARAM],
        examples=[
            OpenApiExample(
                "Corporate Customer Example",
                value=CUSTOMER_CREATE_EXAMPLE,
                request_only=True,
            )
        ],
        responses={
            201: CustomerSerializer,
            400: OpenApiResponse(description="Validation error (e.g. duplicate email/phone, negative credit limit)."),
            403: OpenApiResponse(description="Forbidden (lacks MANAGE_CUSTOMERS or subscription expired)."),
        },
    ),
    update=extend_schema(
        tags=["Customers"],
        summary="Update a Customer",
        description="""
        Updates an existing customer record.
        Modifying credit_limit or payment_terms_days requires MANAGE_CUSTOMER_CREDIT permission.
        Customer balance is read-only and derived from the financial transaction ledger.
        """,
        parameters=[CUSTOMER_ORGANIZATION_PARAM],
        responses={
            200: CustomerSerializer,
            400: OpenApiResponse(description="Validation error."),
            403: OpenApiResponse(description="Forbidden (insufficient permissions)."),
            404: OpenApiResponse(description="Customer not found."),
        },
    ),
    partial_update=extend_schema(
        tags=["Customers"],
        summary="Partial Update Customer",
        description="Partially updates customer attributes while protecting financial fields and multi-tenant scoping.",
        parameters=[CUSTOMER_ORGANIZATION_PARAM],
        responses={
            200: CustomerSerializer,
            400: OpenApiResponse(description="Validation error."),
            403: OpenApiResponse(description="Forbidden."),
            404: OpenApiResponse(description="Customer not found."),
        },
    ),
    destroy=extend_schema(
        tags=["Customers"],
        summary="Delete or Soft-Deactivate Customer",
        description="""
        Deletes a customer record (PRD §44).
        If the customer has no associated sales or invoice ledgers, the record is permanently deleted (204 No Content).
        If the customer is referenced by existing transactions, the system catches the database constraint and gracefully soft-deactivates the customer (200 OK with status set to inactive).
        """,
        parameters=[CUSTOMER_ORGANIZATION_PARAM],
        responses={
            204: OpenApiResponse(description="Customer permanently deleted."),
            200: OpenApiResponse(description="Customer has existing records and was soft-deactivated instead."),
            403: OpenApiResponse(description="Forbidden."),
            404: OpenApiResponse(description="Customer not found."),
        },
    ),
    sales=extend_schema(
        tags=["Customers"],
        summary="Customer Sales History",
        description="Retrieves paginated historical sales orders placed by this customer within the organization (PRD §20).",
        parameters=[CUSTOMER_ORGANIZATION_PARAM],
        responses={
            200: OpenApiResponse(description="List of sales associated with this customer."),
            404: OpenApiResponse(description="Customer not found."),
        },
    ),
    adjust_credit=extend_schema(
        tags=["Customers"],
        summary="Adjust Customer Credit Policy",
        description="""
        Dedicated credit policy governance endpoint (PRD §26).
        Allows authorized financial managers to adjust credit limits or payment terms with an audit justification.
        Requires MANAGE_CUSTOMER_CREDIT permission.
        """,
        parameters=[CUSTOMER_ORGANIZATION_PARAM],
        request=CustomerCreditAdjustmentSerializer,
        examples=[
            OpenApiExample(
                "Credit Adjustment Example",
                value=CUSTOMER_CREDIT_ADJUSTMENT_EXAMPLE,
                request_only=True,
            )
        ],
        responses={
            200: CustomerSerializer,
            400: OpenApiResponse(description="Validation error (negative credit limit)."),
            403: OpenApiResponse(description="Forbidden (requires MANAGE_CUSTOMER_CREDIT)."),
            404: OpenApiResponse(description="Customer not found."),
        },
    ),
)
