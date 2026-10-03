from pathlib import Path

from django.conf import settings
from django.core.files import File

from core.helper.storage import RawCloudinaryStorage

def seed_default_templates(document_template_model, *, refresh: bool = False):
    ...
    for template_type, (name, filename) in BUNDLED.items():
        template = manager.filter(
            organization__isnull=True, template_type=template_type, is_default=True
        ).first()
        if template is not None and not refresh:
            results[template_type] = "skipped"
            continue

        with (base / filename).open("rb") as handle:
            if template is None:
                template = document_template_model(
                    name=name,
                    template_type=template_type,
                    description=f"Shared default {template_type} template.",
                    is_default=True,
                    is_active=True,
                )
                results[template_type] = "created"
            else:
                results[template_type] = "refreshed"

            template.file.storage = RawCloudinaryStorage()  # override historical storage
            template.file.save(filename, File(handle), save=False)
            template.save()