from __future__ import annotations

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from core.applications.transactions.api.permissions import TransactionPermission
from core.applications.transactions.api.schemas import transaction_schema
from core.applications.transactions.api.serializers import TransactionSerializer
from core.applications.transactions.models import Transaction
from core.applications.users.permissions import OrganizationScopedMixin


@transaction_schema
class TransactionViewSet(OrganizationScopedMixin, viewsets.ReadOnlyModelViewSet):
    """
    The organization's business events (sales, purchases, stock movements,
    payments) with their double-entry financial lines.

    Read-only by design (PRD §15, §31, §44): transactions are written only by
    the services that perform the business operation, so they always carry the
    plan's usage accounting, a location and balanced ledger lines. They are
    never edited or deleted; corrections are new, reversing transactions.
    """

    queryset = Transaction.objects.none()
    permission_classes = [IsAuthenticated, TransactionPermission]
    serializer_class = TransactionSerializer

    def get_queryset(self):
        user = self.request.user
        org = self.organization
        if not org:
            return Transaction.objects.none()
        queryset = Transaction.objects.for_organization(org).for_user(user).with_entries()
        # Only the caller's locations (PRD §35); organization-level events without
        # a location are visible to members with all locations only.
        return self.scope_to_locations(queryset, field="location")
