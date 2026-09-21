"""
core/applications/invoice/querysets.py

    from .querysets import DocumentTemplateManager
    class DocumentTemplate(TimeBasedModel):
        objects = DocumentTemplateManager()
"""

from __future__ import annotations

import auto_prefetch
from django.db.models import Q


class DocumentTemplateQuerySet(auto_prefetch.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def shared(self):
        """Default/System templates (no owning organization)."""
        return self.filter(organization__isnull=True)

    def defaults(self):
        """THE shared default per template type."""
        return self.shared().filter(is_default=True)

    def of_type(self, template_type: str):
        return self.filter(template_type=template_type)

    def available_to(self, organization):
        """Shared templates plus the organization's own. Never another tenant's."""
        scope = Q(organization__isnull=True)
        if organization is not None:
            scope |= Q(organization=organization)
        return self.filter(scope)


DocumentTemplateManager = auto_prefetch.Manager.from_queryset(DocumentTemplateQuerySet)
