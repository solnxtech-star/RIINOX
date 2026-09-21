from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import ImproperlyConfigured

from core.applications.invoice.models import DocumentSequence
from core.applications.invoice.models import DocumentTemplate
from core.applications.subscriptions.services import plan_has_feature

if TYPE_CHECKING:
    from core.applications.users.models import Organization

# Feature.code that unlocks organization-owned (Custom) templates.
FEATURE_CUSTOM_TEMPLATES = "CUSTOM_TEMPLATES"

# One DocumentSequence per type, created with the organization. The separator
# and zero-padding come from your number formatter (INV-000123), so no trailing
# dash here; adjust if your formatter doesn't add one.
DOCUMENT_SEQUENCE_PREFIXES = {
    "invoice": "INV",
    "receipt": "RCT",
    "quote": "QUO",
    "credit_note": "CN",
}

REQUIRED_DEFAULT_TEMPLATES = ("invoice", "receipt")


def get_default_templates() -> dict[str, DocumentTemplate]:
    """Shared default templates keyed by type: a single query, however many types exist."""
    templates = {t.template_type: t for t in DocumentTemplate.objects.active().defaults()}
    missing = [t for t in REQUIRED_DEFAULT_TEMPLATES if t not in templates]
    if missing:
        raise ImproperlyConfigured(
            f"No default template for {missing}. Run `manage.py seed_default_templates`."
        )
    return templates


def provision_organization_documents(organization: Organization) -> None:
    """Numbering sequences + shared default templates for a new organization."""
    seed_document_sequences(organization)
    assign_default_templates(organization)


def seed_document_sequences(organization: Organization) -> None:
    DocumentSequence.objects.bulk_create(
        [
            DocumentSequence(organization=organization, document_type=document_type, prefix=prefix)
            for document_type, prefix in DOCUMENT_SEQUENCE_PREFIXES.items()
        ]
    )


def assign_default_templates(organization: Organization) -> None:
    """
    Point the organization at the shared defaults. Nothing is copied: Free-plan
    tenants share one row per type; a Custom template (organization set) is
    created later, and only when the plan allows it.
    """
    templates = get_default_templates()
    organization.invoice_template = templates["invoice"]
    organization.receipt_template = templates["receipt"]
    organization.quote_template = templates.get("quote")
    organization.credit_note_template = templates.get("credit_note")
    organization.save(
        update_fields=["invoice_template", "receipt_template", "quote_template", "credit_note_template", "updated_at"]
    )


def templates_available_to(organization: Organization | None, template_type: str):
    """Templates an organization may assign for a type."""
    return DocumentTemplate.objects.active().of_type(template_type).available_to(organization)


def can_use_custom_templates(organization: Organization) -> bool:
    return plan_has_feature(organization, FEATURE_CUSTOM_TEMPLATES)
