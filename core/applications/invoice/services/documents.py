from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import PurePath
from types import MappingProxyType
from typing import TYPE_CHECKING
from typing import Final

from django.db import IntegrityError
from django.db import transaction

from core.applications.invoice.exceptions import DefaultTemplateMissingError
from core.applications.invoice.exceptions import DuplicateTemplateNameError
from core.applications.invoice.exceptions import FeatureNotAvailableError
from core.applications.invoice.exceptions import InvalidDocumentTypeError
from core.applications.invoice.exceptions import InvalidSequenceSettingError
from core.applications.invoice.exceptions import InvalidTemplateFileError
from core.applications.invoice.exceptions import TemplateNotAvailableError
from core.applications.invoice.models import DocumentSequence
from core.applications.invoice.models import DocumentTemplate
from core.applications.subscriptions.services import plan_has_feature
from core.helper.enums import TEMPLATE_TYPES

if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.core.files.uploadedfile import UploadedFile
    from django.db.models import QuerySet

    from core.applications.users.models import Organization

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Subscription Feature.code values (PRD §10, §25).
FEATURE_CUSTOM_TEMPLATES: Final = "CUSTOM_TEMPLATES"
FEATURE_CUSTOM_NUMBERING: Final = "CUSTOM_DOCUMENT_NUMBERING"

# Prefix only. The separator and zero padding are applied by
# `format_document_number`, giving INV-000123.
DEFAULT_SEQUENCE_PREFIXES: Final[Mapping[str, str]] = MappingProxyType(
    {
        TEMPLATE_TYPES.INVOICE: "INV",
        TEMPLATE_TYPES.RECEIPT: "RCT",
        TEMPLATE_TYPES.QUOTE: "QUO",
        TEMPLATE_TYPES.CREDIT_NOTE: "CN",
    }
)

# Attribute on Organization that points at the template in use, per type.
TEMPLATE_FIELD_BY_TYPE: Final[Mapping[str, str]] = MappingProxyType(
    {
        TEMPLATE_TYPES.INVOICE: "invoice_template",
        TEMPLATE_TYPES.RECEIPT: "receipt_template",
        TEMPLATE_TYPES.QUOTE: "quote_template",
        TEMPLATE_TYPES.CREDIT_NOTE: "credit_note_template",
    }
)

# Types every deployment must have a shared default for before organizations
# can be provisioned.
REQUIRED_DEFAULT_TEMPLATE_TYPES: Final = frozenset({TEMPLATE_TYPES.INVOICE, TEMPLATE_TYPES.RECEIPT})

ALLOWED_TEMPLATE_EXTENSIONS: Final = frozenset({".html", ".docx"})
MAX_TEMPLATE_FILE_BYTES: Final = 2 * 1024 * 1024

MIN_NUMBER_PADDING: Final = 3
MAX_NUMBER_PADDING: Final = 10
MAX_NUMBER_VALUE: Final = 2_147_483_647  # PositiveIntegerField ceiling
_PREFIX_PATTERN: Final = re.compile(r"^[A-Z0-9][A-Z0-9/\-]{0,19}$")


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _validate_document_type(document_type: str) -> str:
    if document_type not in TEMPLATE_TYPES.values:
        raise InvalidDocumentTypeError(detail={"document_type": document_type})
    return document_type


def _template_field(template_type: str) -> str:
    return TEMPLATE_FIELD_BY_TYPE[_validate_document_type(template_type)]


def _require_feature(organization: Organization, feature_code: str) -> None:
    if not plan_has_feature(organization, feature_code):
        raise FeatureNotAvailableError(detail={"feature": feature_code})


# --------------------------------------------------------------------------- #
# Plan gating
# --------------------------------------------------------------------------- #


def can_use_custom_templates(organization: Organization) -> bool:
    return plan_has_feature(organization, FEATURE_CUSTOM_TEMPLATES)


def can_customize_numbering(organization: Organization) -> bool:
    return plan_has_feature(organization, FEATURE_CUSTOM_NUMBERING)


# --------------------------------------------------------------------------- #
# Shared default templates
# --------------------------------------------------------------------------- #


def get_default_templates() -> dict[str, DocumentTemplate]:
    """
    Shared default templates keyed by type, in a single query.

    Raises DefaultTemplateMissingError if a required type is not seeded, so a
    misconfigured deployment fails loudly instead of creating broken tenants.
    """
    templates = {t.template_type: t for t in DocumentTemplate.objects.active().defaults()}
    missing = sorted(REQUIRED_DEFAULT_TEMPLATE_TYPES - templates.keys())
    if missing:
        raise DefaultTemplateMissingError(
            f"No default template for {missing}. Run `manage.py seed_default_templates`.",
            detail={"missing": missing},
        )
    return templates


def get_default_template(template_type: str) -> DocumentTemplate:
    """The shared default for one type (single indexed query)."""
    _validate_document_type(template_type)
    template = DocumentTemplate.objects.active().defaults().of_type(template_type).first()
    if template is None:
        raise DefaultTemplateMissingError(
            f"No default template for ['{template_type}'].",
            detail={"missing": [template_type]},
        )
    return template


def templates_available_to(organization: Organization | None, template_type: str) -> QuerySet[DocumentTemplate]:
    """Active templates an organization may assign for a type: shared plus its own."""
    return DocumentTemplate.objects.active().of_type(template_type).available_to(organization)


# --------------------------------------------------------------------------- #
# Provisioning
# --------------------------------------------------------------------------- #


@transaction.atomic
def provision_organization_documents(organization: Organization) -> None:
    """
    Numbering sequences plus shared default templates for a new organization.

    Atomic and idempotent: safe to call from the organization-creation
    transaction and safe to re-run for repair.
    """
    seed_document_sequences(organization)
    assign_default_templates(organization)
    logger.info("documents.provisioned", extra={"organization_id": str(organization.pk)})


def seed_document_sequences(organization: Organization) -> None:
    """Create any missing sequence rows. Existing rows are never touched."""
    DocumentSequence.objects.bulk_create(
        [
            DocumentSequence(
                organization=organization,
                document_type=document_type,
                prefix=DEFAULT_SEQUENCE_PREFIXES[document_type],
            )
            for document_type in TEMPLATE_TYPES.values
        ],
        ignore_conflicts=True,
    )


def assign_default_templates(organization: Organization) -> None:
    """
    Point the organization at the shared defaults, only where nothing is set.

    Nothing is copied: Free-plan tenants share one row per type. Existing
    assignments, including Custom templates, are never overwritten.
    """
    defaults = get_default_templates()
    changed: list[str] = []
    for template_type, field in TEMPLATE_FIELD_BY_TYPE.items():
        default = defaults.get(template_type)
        if default is not None and getattr(organization, f"{field}_id") is None:
            setattr(organization, field, default)
            changed.append(field)
    if changed:
        organization.save(update_fields=[*changed, "updated_at"])


# --------------------------------------------------------------------------- #
# Template resolution and assignment
# --------------------------------------------------------------------------- #


def resolve_template(organization: Organization, template_type: str) -> DocumentTemplate:
    """
    The template to render for this organization and type.

    Uses the assigned template when it is active and permitted; otherwise falls
    back to the shared default. A Custom template stops being used the moment
    the plan no longer includes custom templates (for example after a downgrade),
    without deleting the organization's data.
    """
    assigned = getattr(organization, _template_field(template_type))
    if assigned is not None and assigned.is_active:
        if assigned.organization_id is None:
            return assigned
        if assigned.organization_id == organization.pk and can_use_custom_templates(organization):
            return assigned
    return get_default_template(template_type)


@transaction.atomic
def assign_template(organization: Organization, template: DocumentTemplate) -> DocumentTemplate:
    """Make `template` the one the organization uses for its type."""
    field = _template_field(template.template_type)
    if not template.is_active or template.organization_id not in (None, organization.pk):
        raise TemplateNotAvailableError(detail={"template_id": str(template.pk)})
    if template.organization_id is not None:
        _require_feature(organization, FEATURE_CUSTOM_TEMPLATES)

    setattr(organization, field, template)
    organization.save(update_fields=[field, "updated_at"])
    logger.info(
        "documents.template_assigned",
        extra={"organization_id": str(organization.pk), "template_id": str(template.pk)},
    )
    return template


# --------------------------------------------------------------------------- #
# Custom templates
# --------------------------------------------------------------------------- #


def _validate_template_file(file: UploadedFile) -> None:
    extension = PurePath(file.name or "").suffix.lower()
    if extension not in ALLOWED_TEMPLATE_EXTENSIONS:
        raise InvalidTemplateFileError(
            f"Unsupported file type '{extension}'.",
            detail={"allowed": sorted(ALLOWED_TEMPLATE_EXTENSIONS)},
        )
    if file.size is None or file.size <= 0:
        raise InvalidTemplateFileError("The template file is empty.")
    if file.size > MAX_TEMPLATE_FILE_BYTES:
        raise InvalidTemplateFileError(
            "The template file is too large.",
            detail={"max_bytes": MAX_TEMPLATE_FILE_BYTES},
        )


def create_custom_template(
    organization: Organization,
    *,
    name: str,
    template_type: str,
    file: UploadedFile,
    description: str = "",
    assign: bool = False,
) -> DocumentTemplate:
    """
    Create an organization-owned template. Requires the Custom templates feature.

    Template files are user-supplied markup: render them only with a sandboxed
    engine (never plain Django/Jinja templates with full context access).
    """
    _require_feature(organization, FEATURE_CUSTOM_TEMPLATES)
    _validate_document_type(template_type)
    _validate_template_file(file)

    name = name.strip()
    if not name:
        raise InvalidTemplateFileError("A template name is required.")

    with transaction.atomic():
        try:
            with transaction.atomic():
                template = DocumentTemplate.objects.create(
                    organization=organization,
                    name=name,
                    description=description or None,
                    template_type=template_type,
                    file=file,
                    is_default=False,
                )
        except IntegrityError as exc:
            raise DuplicateTemplateNameError(detail={"name": name}) from exc

        if assign:
            assign_template(organization, template)

    logger.info(
        "documents.custom_template_created",
        extra={"organization_id": str(organization.pk), "template_id": str(template.pk)},
    )
    return template


@transaction.atomic
def archive_custom_template(organization: Organization, template: DocumentTemplate) -> DocumentTemplate:
    """
    Retire a Custom template without deleting it (PRD §44).

    If it is the one in use, the organization falls back to the shared default
    in the same transaction, so there is never a moment with a dead assignment.
    """
    if template.organization_id != organization.pk:
        raise TemplateNotAvailableError(detail={"template_id": str(template.pk)})

    field = _template_field(template.template_type)
    if getattr(organization, f"{field}_id") == template.pk:
        setattr(organization, field, get_default_template(template.template_type))
        organization.save(update_fields=[field, "updated_at"])

    template.is_active = False
    template.save(update_fields=["is_active", "updated_at"])
    logger.info(
        "documents.custom_template_archived",
        extra={"organization_id": str(organization.pk), "template_id": str(template.pk)},
    )
    return template


# --------------------------------------------------------------------------- #
# Document numbering
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AllocatedNumber:
    """A reserved document number: the raw counter value and its display form."""

    sequence: int
    formatted: str


def format_document_number(prefix: str, number: int, padding: int) -> str:
    """('INV', 123, 6) -> 'INV-000123'. An empty prefix gives just the padded number."""
    padded = f"{number:0{padding}d}"
    return f"{prefix}-{padded}" if prefix else padded


def _lock_sequence(organization: Organization, document_type: str) -> DocumentSequence:
    """Fetch the sequence row with a row lock, creating it first for legacy tenants."""
    locked = DocumentSequence.objects.locked()
    try:
        return locked.get(organization=organization, document_type=document_type)
    except DocumentSequence.DoesNotExist:
        DocumentSequence.objects.bulk_create(
            [
                DocumentSequence(
                    organization=organization,
                    document_type=document_type,
                    prefix=DEFAULT_SEQUENCE_PREFIXES[document_type],
                )
            ],
            ignore_conflicts=True,
        )
        return locked.get(organization=organization, document_type=document_type)


def allocate_document_number(organization: Organization, document_type: str) -> AllocatedNumber:
    """
    Reserve the next number for a document type.

    MUST be called inside the same transaction that creates the document
    (Django raises TransactionManagementError otherwise, because of the row
    lock). That is what makes numbering gapless: if the document fails, the
    increment rolls back with it.
    """
    _validate_document_type(document_type)
    sequence = _lock_sequence(organization, document_type)
    if sequence.next_number >= MAX_NUMBER_VALUE:
        raise InvalidSequenceSettingError("The document number range is exhausted.")

    number = sequence.next_number
    sequence.next_number = number + 1
    sequence.save(update_fields=["next_number", "updated_at"])
    return AllocatedNumber(
        sequence=number,
        formatted=format_document_number(sequence.prefix, number, sequence.padding),
    )


@transaction.atomic
def update_sequence_settings(
    organization: Organization,
    document_type: str,
    *,
    prefix: str | None = None,
    padding: int | None = None,
    next_number: int | None = None,
) -> DocumentSequence:
    """
    Customize numbering. Plan-gated; changes apply only to future documents.

    The counter can move forward but never backward, so a number is never
    issued twice and existing documents are never contradicted.
    """
    _validate_document_type(document_type)
    _require_feature(organization, FEATURE_CUSTOM_NUMBERING)
    sequence = _lock_sequence(organization, document_type)
    changed: list[str] = []

    if prefix is not None:
        prefix = prefix.strip().upper()
        if prefix and not _PREFIX_PATTERN.match(prefix):
            raise InvalidSequenceSettingError(
                "Prefix may contain A-Z, 0-9, '-' and '/' (max 20 characters).",
                detail={"prefix": prefix},
            )
        sequence.prefix = prefix
        changed.append("prefix")

    if padding is not None:
        if not MIN_NUMBER_PADDING <= padding <= MAX_NUMBER_PADDING:
            raise InvalidSequenceSettingError(
                f"Padding must be between {MIN_NUMBER_PADDING} and {MAX_NUMBER_PADDING}.",
                detail={"padding": padding},
            )
        sequence.padding = padding
        changed.append("padding")

    if next_number is not None:
        if next_number < sequence.next_number:
            raise InvalidSequenceSettingError(
                "The next number cannot be lower than the current counter.",
                detail={"current": sequence.next_number, "requested": next_number},
            )
        if next_number > MAX_NUMBER_VALUE:
            raise InvalidSequenceSettingError("The next number is too large.")
        sequence.next_number = next_number
        changed.append("next_number")

    if changed:
        sequence.save(update_fields=[*changed, "updated_at"])
        logger.info(
            "documents.sequence_updated",
            extra={"organization_id": str(organization.pk), "document_type": document_type, "fields": changed},
        )
    return sequence
