import uuid

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from core.applications.products.catalog import backfill_starter_catalogs
from core.applications.users.models import Organization


class Command(BaseCommand):
    help = (
        "Give organizations the starter product categories and brands of business types "
        "they have not received yet. This also runs automatically after `migrate` (the "
        "'product_catalog' seed); use it to run it on demand. Each organization/business "
        "type pair is applied once, so it never re-adds what an organization deleted."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--organization", nargs="+", metavar="ID",
            help="Only these organization id(s). Default: every active organization.",
        )

    def handle(self, *args, **options):
        organization_ids = None
        if options["organization"]:
            try:
                organization_ids = {uuid.UUID(str(value)) for value in options["organization"]}
            except ValueError as exc:
                msg = f"Not a valid organization id: {exc}"
                raise CommandError(msg) from exc
            found = {uuid.UUID(str(pk)) for pk in Organization.objects.filter(pk__in=organization_ids)
                     .values_list("pk", flat=True)}
            if missing := organization_ids - found:
                msg = f"Organization(s) not found: {', '.join(sorted(map(str, missing)))}"
                raise CommandError(msg)

        result = backfill_starter_catalogs(organization_ids=organization_ids)
        if result.failed:
            self.stderr.write(self.style.WARNING(f"{result.failed} organization(s) failed; see the logs."))
        self.stdout.write(self.style.SUCCESS(f"Applied {result.applied} organization/business type set(s)."))
