"""OpenAPI documentation for the billing API, written for the frontend team."""

from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from drf_spectacular.utils import OpenApiResponse
from drf_spectacular.utils import extend_schema
from drf_spectacular.utils import extend_schema_view

from core.applications.subscriptions.api.serializers import BillingSummarySerializer
from core.applications.subscriptions.api.serializers import CheckoutRequestSerializer
from core.applications.subscriptions.api.serializers import CheckoutResponseSerializer
from core.applications.subscriptions.api.serializers import DefaultPaymentMethodSerializer
from core.applications.subscriptions.api.serializers import EntitlementsSerializer
from core.applications.subscriptions.api.serializers import PaymentMethodSerializer
from core.applications.subscriptions.api.serializers import BillingPlanSerializer

TAGS = ["Billing"]
ORGANIZATION_PATH_PARAM = OpenApiParameter(
    name="organization_id", type=OpenApiTypes.UUID, location=OpenApiParameter.PATH,
    description="UUID of the organization.",
)
MANAGE = "**Owner & Admin only** (`MANAGE_BILLING`, PRD §55). Other members get 403; non-members 404."

_FLOW = """
**Upgrade flow**

1. `GET /api/billing/plans/?currency=<organization currency>` to show the plan cards.
2. `POST .../billing/checkout/` with the chosen `plan` and `interval`.
3. Redirect the browser to the returned `authorization_url` (the provider's hosted page;
   card details never touch our API).
4. The provider sends the user back to your `callback_url`. **Don't treat the redirect as
   proof of payment**: the plan changes when the provider's webhook is verified. Poll
   `GET .../billing/` (or `.../entitlements/`) until `plan.code` changes; usually seconds.
"""

plan_catalogue_schema = extend_schema(
    tags=TAGS,
    summary="List plans",
    description=(
        "The public plan catalogue, ordered for display. `limits` use `null` for unlimited. "
        "`features` are codes (e.g. `CUSTOM_TEMPLATES`, `API_ACCESS`). Pass `?currency=` to only "
        "get prices in that currency (use the organization's currency).\n\n" + _FLOW
    ),
    parameters=[
        OpenApiParameter("currency", OpenApiTypes.STR, OpenApiParameter.QUERY, required=False,
                         description="ISO 4217 code, e.g. NGN."),
    ],
    responses=BillingPlanSerializer(many=True),
)

billing_schema = extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        summary="Billing overview",
        description=(
            "Everything the billing page shows: the current plan, its status, the billing period, "
            "pending cancellation, the default payment method and usage against every limit "
            "(`usage.<metric>.used` / `.limit`, `null` = unlimited).\n\n" + MANAGE
        ),
        parameters=[ORGANIZATION_PATH_PARAM],
        responses=BillingSummarySerializer,
    ),
    entitlements=extend_schema(
        tags=TAGS,
        summary="What the plan allows (for every member)",
        description=(
            "Call once after switching organization and cache it client-side; refresh after a plan "
            "change. Use it to **hide modules the plan doesn't include** (PRD §37) and to show limit "
            "warnings. It is advisory: the server enforces everything itself.\n\n"
            "* `access = read_only`: the plan lapsed. Show a banner and disable create/edit actions; "
            "writes are refused with `SUBSCRIPTION_READ_ONLY` / `SUBSCRIPTION_REQUIRED`.\n"
            "* `features`: feature codes the plan includes.\n"
            "* `limits`: per metric, `null` = unlimited.\n\n"
            "Any active member may call it."
        ),
        parameters=[ORGANIZATION_PATH_PARAM],
        responses=EntitlementsSerializer,
    ),
    payment_methods=extend_schema(
        tags=TAGS,
        summary="Saved payment methods",
        description="Cards saved during checkout (brand, last 4 digits, expiry). " + MANAGE,
        parameters=[ORGANIZATION_PATH_PARAM],
        responses=PaymentMethodSerializer(many=True),
    ),
    checkout=extend_schema(
        tags=TAGS,
        summary="Start an upgrade / plan change",
        description=(
            "Creates a hosted checkout and returns where to send the user.\n\n"
            "* Changing from a paid plan credits the unused part of the current period.\n"
            "* Send an `idempotency_key` (or `Idempotency-Key` header) per attempt: repeating it "
            "returns the same checkout instead of charging twice.\n"
            "* `callback_url` must be an allowed HTTPS host (`400 INVALID_CALLBACK_URL`).\n\n"
            "Errors: `PLAN_NOT_AVAILABLE`, `PLAN_IS_FREE`, `PLAN_PRICE_NOT_FOUND`, "
            "`UNSUPPORTED_CURRENCY`, `PLAN_CHANGE_NOT_ALLOWED` (e.g. already on it), "
            "`DUPLICATE_REQUEST`, `CHECKOUT_FAILED` (provider down: retry later). "
            "Rate limited (10/min).\n\n" + MANAGE + "\n\n" + _FLOW
        ),
        parameters=[ORGANIZATION_PATH_PARAM],
        request=CheckoutRequestSerializer,
        responses={201: CheckoutResponseSerializer},
    ),
    cancel=extend_schema(
        tags=TAGS,
        summary="Cancel at period end",
        description=(
            "Stops renewal: the paid plan stays active until `current_period_end`, then the "
            "organization drops to read-only unless it moves to Free. Undo with `resume`. "
            "Optional body `{\"reason\": \"...\"}` is kept in the subscription history. "
            "`409 SUBSCRIPTION_INVALID_STATE` when there is no active paid plan.\n\n" + MANAGE
        ),
        parameters=[ORGANIZATION_PATH_PARAM],
        request=None,
        responses=BillingSummarySerializer,
    ),
    resume=extend_schema(
        tags=TAGS,
        summary="Undo a pending cancellation",
        description="Only before the period ends. `409 SUBSCRIPTION_INVALID_STATE` otherwise.\n\n" + MANAGE,
        parameters=[ORGANIZATION_PATH_PARAM],
        request=None,
        responses=BillingSummarySerializer,
    ),
    switch_to_free=extend_schema(
        tags=TAGS,
        summary="Move a lapsed organization to Free",
        description=(
            "Only for a lapsed (read-only) subscription, and only if current usage fits the Free "
            "limits (`409 USAGE_EXCEEDS_PLAN` lists what to reduce).\n\n" + MANAGE
        ),
        parameters=[ORGANIZATION_PATH_PARAM],
        request=None,
        responses=BillingSummarySerializer,
    ),
    default_payment_method=extend_schema(
        tags=TAGS,
        summary="Choose the card renewals charge",
        description="`payment_method_id` from `payment-methods`. `404 PAYMENT_METHOD_NOT_FOUND` otherwise.\n\n" + MANAGE,
        parameters=[ORGANIZATION_PATH_PARAM],
        request=DefaultPaymentMethodSerializer,
        responses=BillingSummarySerializer,
    ),
)

webhook_schema = extend_schema(
    tags=TAGS,
    summary="Payment provider webhook (server to server)",
    description=(
        "**Not for the frontend.** Configure this URL in the provider's dashboard, e.g. "
        "`https://<api-host>/api/billing/webhooks/paystack/`. Authenticated by the provider's "
        "signature: invalid signature 401, unknown provider 404, otherwise 200."
    ),
    request=OpenApiTypes.OBJECT,
    responses={200: OpenApiResponse(description="Accepted, duplicate or ignored."), 401: None, 404: None},
)
