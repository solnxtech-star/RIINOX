from __future__ import annotations

import auto_prefetch
from django.apps import apps


class ReportQuerySet(auto_prefetch.QuerySet):
    """Organization-scoped queryset for stored Report records."""

    def for_user(self, user):
        """Restrict reports to organizations where the user has an active membership."""
        Organization = apps.get_model("users", "Organization")
        return self.filter(organization__in=Organization.objects.for_user(user))

    def for_organization(self, organization_id):
        """Restrict reports to a single organization."""
        return self.filter(organization_id=organization_id)
