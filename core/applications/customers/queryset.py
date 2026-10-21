from __future__ import annotations

from typing import TYPE_CHECKING
import auto_prefetch
from django.db import models

from core.helper.enums import CustomerStatus

if TYPE_CHECKING:
    from core.applications.users.models import Organization


class CustomerQuerySet(auto_prefetch.QuerySet):
    """
    Tenant-scoped, chainable QuerySet for Customer records.
    """

    def for_organization(self, organization: Organization | str) -> CustomerQuerySet:
        org_id = getattr(organization, "id", organization)
        return self.filter(organization_id=org_id)

    def active(self) -> CustomerQuerySet:
        return self.filter(status=CustomerStatus.ACTIVE)

    def search(self, query: str) -> CustomerQuerySet:
        if not query or not query.strip():
            return self
        q = query.strip()
        return self.filter(
            models.Q(name__icontains=q)
            | models.Q(company_name__icontains=q)
            | models.Q(email__icontains=q)
            | models.Q(phone__icontains=q)
            | models.Q(tax_id__icontains=q)
        )

    def with_details(self) -> CustomerQuerySet:
        return self.prefetch_related("addresses").select_related("organization", "created_by")


class CustomerAddressQuerySet(auto_prefetch.QuerySet):
    """
    QuerySet for CustomerAddress records.
    """

    def default(self) -> CustomerAddressQuerySet:
        return self.filter(is_default=True)
