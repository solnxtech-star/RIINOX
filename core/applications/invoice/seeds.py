from collections import Counter

from core.applications.invoice.models import DocumentTemplate
from core.applications.invoice.seeding import seed_default_templates
from core.seeding.registry import SeedResult
from core.seeding.registry import register


@register("document_templates", external=True)
def seed_document_templates() -> SeedResult:
    """Shared default invoice and receipt templates (uploads the bundled files to storage)."""
    counts = Counter(seed_default_templates(DocumentTemplate).values())
    return SeedResult(created=counts["created"], updated=counts["refreshed"], unchanged=counts["skipped"])
