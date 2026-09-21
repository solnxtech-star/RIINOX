from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.files import File

# template_type -> (display name, bundled file)
BUNDLED = {
    "invoice": ("Default Invoice", "default_invoice.html"),
    "receipt": ("Default Receipt", "default_receipt.html"),
}


def bundled_templates_dir() -> Path:
    return Path(settings.APPS_DIR) / "templates" / "documents"


def seed_default_templates(document_template_model, *, refresh: bool = False) -> dict[str, str]:
    """
    Create the shared (organization=NULL, is_default=True) template of each bundled
    type that doesn't exist yet. Returns {template_type: "created" | "skipped" | "refreshed"}.
    Every bundled file is checked BEFORE anything is written, so a missing file
    fails cleanly instead of leaving a half-seeded set.
    """
    base = bundled_templates_dir()
    missing = [str(base / filename) for _, filename in BUNDLED.values() if not (base / filename).exists()]
    if missing:
        raise FileNotFoundError(f"Bundled template file(s) missing: {', '.join(missing)}")

    manager = document_template_model.objects
    results: dict[str, str] = {}

    for template_type, (name, filename) in BUNDLED.items():
        template = manager.filter(organization__isnull=True, template_type=template_type, is_default=True).first()
        if template is not None and not refresh:
            results[template_type] = "skipped"
            continue

        with (base / filename).open("rb") as handle:
            if template is None:
                manager.create(
                    name=name,
                    template_type=template_type,
                    description=f"Shared default {template_type} template.",
                    file=File(handle, name=filename),
                    is_default=True,
                    is_active=True,
                )
                results[template_type] = "created"
            else:
                template.file.save(filename, File(handle), save=True)
                results[template_type] = "refreshed"

    return results
