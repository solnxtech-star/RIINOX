"""
Billing API (PRD §10 SaaS subscription, §37 module visibility, §55 billing is
Owner & Admin). A thin HTTP layer over the subscription services: every rule
(prices, plan-change credit, idempotency, state transitions) lives there.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.permissions import BasePermission
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.applications.notification.audit.context import get_client_ip
from core.applications.subscriptions.api.schemas import billing_schema
from core.applications.subscriptions.api.schemas import plan_catalogue_schema
from core.applications.subscriptions.api.schemas import webhook_schema
from core.applications.subscriptions.api.serializers import BillingSummarySerializer
from core.applications.subscriptions.api.serializers import CheckoutRequestSerializer
from core.applications.subscriptions.api.serializers import CheckoutResponseSerializer
from core.applications.subscriptions.api.serializers import DefaultPaymentMethodSerializer
from core.applications.subscriptions.api.serializers import EntitlementsSerializer
from core.applications.subscriptions.api.serializers import PaymentMethodSerializer
from core.applications.subscriptions.api.serializers import BillingPlanSerializer
from core.applications.subscriptions.errors import SubscriptionErrorCode as Err
from core.applications.subscriptions.errors import fail
from core.applications.subscriptions.models import BillingPaymentMethod
from core.applications.subscriptions.models import Plan
from core.applications.subscriptions.models import Subscription
from core.applications.subscriptions.providers.errors import InvalidWebhookSignatureError
from core.applications.subscriptions.providers.registry import is_known_provider
from core.applications.subscriptions.services import lifecycle
from core.applications.subscriptions.services.audit import AuditContext
from core.applications.subscriptions.services.checkout import initiate_plan_checkout
from core.applications.subscriptions.services.entitlements import effective_status
from core.applications.subscriptions.services.entitlements import require_entitlements
from core.applications.subscriptions.services.usage import usage_summary
from core.applications.subscriptions.services.webhooks import receive_webhook
from core.applications.users.permissions import HasOrgPermission
from core.applications.users.permissions import OrganizationScopedMixin
from core.applications.users.permissions import resolve_active_membership
from core.helper.enums import BillingPaymentMethodStatus
from core.helper.enums import PermissionCode


def _plans():
    """The public catalogue, prices and features in two extra queries total."""
    return (
        Plan.objects.filter(is_active=True, is_public=True)
        .prefetch_related("prices", "plan_features__feature")
        .order_by("sort_order", "name")
    )


def _audit_context(request, reason: str = "") -> AuditContext:
    return AuditContext(
        actor=request.user,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:1000],
        reason=reason,
    )


class IsOrganizationMember(BasePermission):
    """Any active member of the organization in the URL; non-members get a 404."""

    def has_permission(self, request, view):
        if resolve_active_membership(request, view) is None:
            raise NotFound("Organization not found.")
        return True


# --------------------------------------------------------------------------- #
# Public plan catalogue
# --------------------------------------------------------------------------- #
@plan_catalogue_schema
class PlanCatalogueView(APIView):
    """GET /api/billing/plans/: the plans anyone signed in can choose from."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        currency = (request.query_params.get("currency") or "").strip().upper() or None
        serializer = BillingPlanSerializer(_plans(), many=True, context={"currency": currency})
        return Response(serializer.data)


# --------------------------------------------------------------------------- #
# Organization billing
# --------------------------------------------------------------------------- #
@billing_schema
class OrganizationBillingViewSet(OrganizationScopedMixin, viewsets.ViewSet):
    """/api/organizations/{organization_id}/billing/..."""

    manage = HasOrgPermission(PermissionCode.MANAGE_BILLING)
    action_permissions = {
        "entitlements": IsOrganizationMember,
        "list": manage,
        "payment_methods": manage,
        "checkout": manage,
        "cancel": manage,
        "resume": manage,
        "switch_to_free": manage,
        "default_payment_method": manage,
    }

    def get_permissions(self):
        permission = self.action_permissions.get(self.action, self.manage)
        return [IsAuthenticated(), permission()]

    def get_throttles(self):
        if self.action == "checkout":
            self.throttle_scope = "billing_checkout"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    # -- reads ------------------------------------------------------------- #
    def list(self, request, *args, **kwargs):
        """GET .../billing/: the billing page."""
        return Response(self._summary())

    @action(detail=False, methods=["get"])
    def entitlements(self, request, *args, **kwargs):
        """GET .../billing/entitlements/: plan, features, limits and access, for every member."""
        snapshot = require_entitlements(self.organization)
        data = {
            "plan_code": snapshot.plan_code,
            "is_free": snapshot.is_free,
            "status": snapshot.status,
            "access": snapshot.access,
            "features": sorted(snapshot.features),
            "limits": snapshot.limits,
            "period_end": snapshot.period_end,
        }
        return Response(EntitlementsSerializer(data).data)

    @action(detail=False, methods=["get"], url_path="payment-methods")
    def payment_methods(self, request, *args, **kwargs):
        subscription = self._subscription()
        methods = BillingPaymentMethod.objects.filter(
            organization=self.organization, status=BillingPaymentMethodStatus.ACTIVE
        ).order_by("-created_at")
        context = {"default_method_id": getattr(subscription, "default_payment_method_id", None)}
        return Response(PaymentMethodSerializer(methods, many=True, context=context).data)

    # -- commands ---------------------------------------------------------- #
    @action(detail=False, methods=["post"])
    def checkout(self, request, *args, **kwargs):
        body = CheckoutRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        plan = Plan.objects.filter(code=data["plan"]).first()
        if plan is None:
            raise fail(Err.PLAN_NOT_AVAILABLE, plan=data["plan"])
        session = initiate_plan_checkout(
            organization=self.organization,
            plan=plan,
            interval=data["interval"],
            user=request.user,
            callback_url=data["callback_url"],
            idempotency_key=data.get("idempotency_key") or request.headers.get("Idempotency-Key", ""),
        )
        payload = {"authorization_url": session.authorization_url, "reference": session.reference}
        return Response(CheckoutResponseSerializer(payload).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def cancel(self, request, *args, **kwargs):
        lifecycle.cancel_subscription(
            organization=self.organization,
            context=_audit_context(request, reason=str(request.data.get("reason", ""))[:500]),
        )
        return Response(self._summary())

    @action(detail=False, methods=["post"])
    def resume(self, request, *args, **kwargs):
        lifecycle.resume_subscription(organization=self.organization, context=_audit_context(request))
        return Response(self._summary())

    @action(detail=False, methods=["post"], url_path="switch-to-free")
    def switch_to_free(self, request, *args, **kwargs):
        lifecycle.switch_to_free(organization=self.organization, context=_audit_context(request))
        return Response(self._summary())

    @action(detail=False, methods=["put"], url_path="default-payment-method")
    def default_payment_method(self, request, *args, **kwargs):
        body = DefaultPaymentMethodSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        lifecycle.set_default_payment_method(
            organization=self.organization,
            method_id=body.validated_data["payment_method_id"],
            context=_audit_context(request),
        )
        return Response(self._summary())

    # -- helpers ----------------------------------------------------------- #
    def _subscription(self) -> Subscription:
        # A fresh read: billing state must never come from a cached reverse relation.
        subscription = Subscription.objects.with_billing().filter(organization=self.organization).first()
        if subscription is None:
            raise fail(Err.SUBSCRIPTION_REQUIRED)
        return subscription

    def _summary(self) -> dict:
        subscription = self._subscription()
        plan = _plans().filter(pk=subscription.plan_id).first() or (
            Plan.objects.prefetch_related("prices", "plan_features__feature").get(pk=subscription.plan_id)
        )
        snapshot = require_entitlements(self.organization)
        data = {
            "plan": plan,
            "status": effective_status(subscription),
            "access": snapshot.access,
            "interval": getattr(subscription.plan_price, "interval", None),
            "trial_ends_at": subscription.trial_ends_at,
            "current_period_start": subscription.current_period_start,
            "current_period_end": subscription.current_period_end,
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "default_payment_method": subscription.default_payment_method,
            "usage": usage_summary(self.organization),
        }
        context = {
            "currency": (self.organization.currency or "").upper() or None,
            "default_method_id": subscription.default_payment_method_id,
        }
        return BillingSummarySerializer(data, context=context).data


# --------------------------------------------------------------------------- #
# Provider webhooks
# --------------------------------------------------------------------------- #
@webhook_schema
class BillingWebhookView(APIView):
    """
    POST /api/billing/webhooks/{provider}/: payment notifications from the provider.

    No user authentication: the provider's signature over the raw body is the
    authentication (verified in services.webhooks). Invalid signature -> 401 and
    nothing is stored. Everything else -> 200, so the provider stops retrying;
    processing failures are recorded on the stored event and retried by the
    billing.process_webhook_backlog task.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "billing_webhook"

    def post(self, request, provider: str):
        if not is_known_provider(provider):
            raise NotFound("Unknown billing provider.")
        # A known but misconfigured provider (e.g. no secret key) raises
        # ImproperlyConfigured here: a loud 500 for operators, never a silent 404.
        try:
            receipt = receive_webhook(provider, body=request.body, headers=request.headers)
        except InvalidWebhookSignatureError:
            return Response({"detail": "Invalid signature."}, status=status.HTTP_401_UNAUTHORIZED)
        return Response({"status": str(receipt)}, status=status.HTTP_200_OK)
