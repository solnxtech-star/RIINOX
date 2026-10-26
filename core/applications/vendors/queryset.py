from __future__ import annotations

import auto_prefetch
from core.applications.users.models import Organization
from core.helper.enums import VendorStatusChoices


class VendorQuerySet(auto_prefetch.QuerySet):
    """Chainable queryset for Vendors scoped by organization and user."""

    def for_organization(self, organization: Organization | str) -> VendorQuerySet:
        if isinstance(organization, Organization):
            return self.filter(organization=organization)
        return self.filter(organization_id=organization)

    def for_user(self, user) -> VendorQuerySet:
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_superuser", False):
            return self.all()
        return self.filter(organization__in=Organization.objects.for_user(user))

    def active(self) -> VendorQuerySet:
        return self.filter(status=VendorStatusChoices.ACTIVE, visible=True)

