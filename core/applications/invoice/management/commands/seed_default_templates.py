from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction

from core.applications.invoice.models import DocumentTemplate

# template_type -> (display name, bundled file). Add quote / credit_note here
# once you ship those files.
BUNDLED = {
    "invoice": ("Default Invoice", "default_invoice.html"),
    "receipt": ("Default Receipt", "default_receipt.html"),
}


class Command(BaseCommand):
    help = "Create the shared default document templates."

    def add_arguments(self, parser):
        parser.add_argument("--refresh", action="store_true", help="Replace the file of existing defaults.")

    @transaction.atomic
    def handle(self, *args, refresh=False, **options):
        base = Path(settings.APPS_DIR) / "templates" / "documents"

        for template_type, (name, filename) in BUNDLED.items():
            path = base / filename
            if not path.exists():
                raise CommandError(f"Bundled template missing: {path}")

            template = DocumentTemplate.objects.filter(
                organization__isnull=True, template_type=template_type, is_default=True
            ).first()

            if template is not None and not refresh:
                self.stdout.write(f"{template_type}: exists, skipped")
                continue

            with path.open("rb") as fh:
                if template is None:
                    DocumentTemplate.objects.create(
                        name=name,
                        template_type=template_type,
                        description=f"Shared default {template_type} template.",
                        file=File(fh, name=filename),
                        is_default=True,
                        is_active=True,
                    )
                    self.stdout.write(self.style.SUCCESS(f"{template_type}: created"))
                else:
                    template.file.save(filename, File(fh), save=True)
                    self.stdout.write(self.style.SUCCESS(f"{template_type}: file refreshed"))
