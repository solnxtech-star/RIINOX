import uuid

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction

from core.applications.products.catalog import seed_starter_catalog
from core.applications.users.models import Organization
from core.applications.users.models import OrganizationBusinessType


class Command(BaseCommand):
    help = (
        "Give existing organizations the starter product categories and brands of their "
        "business types. Adds missing names only; never renames, reactivates or deletes. "
        "New organizations get them automatically on creation."
    )

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--organization", nargs="+", metavar="ID", help="Organization id(s).")
        target.add_argument("--all", action="store_true", help="Every active organization.")

    def handle(self, *args, **options):
        organizations = Organization.objects.filter(is_active=True)
        if options["organization"]:
            try:
                wanted = {uuid.UUID(str(value)) for value in options["organization"]}
            except ValueError as exc:
                raise CommandError(f"Not a valid organization id: {exc}") from exc
            organizations = Organization.objects.filter(pk__in=wanted)
            missing = wanted - {uuid.UUID(str(pk)) for pk in organizations.values_list("pk", flat=True)}
            if missing:
                raise CommandError(f"Organization(s) not found: {', '.join(sorted(map(str, missing)))}")

        totals = [0, 0]
        for organization in organizations.order_by("name"):
            codes = list(
                OrganizationBusinessType.objects.filter(organization=organization)
                .order_by("-is_primary")
                .values_list("business_type__code", flat=True)
            )
            with transaction.atomic():
                result = seed_starter_catalog(organization, codes)
            totals[0] += result.categories
            totals[1] += result.brands
            self.stdout.write(
                f"  {organization.name:<32} +{result.categories} categories  +{result.brands} brands"
                f"  ({', '.join(codes) or 'no business type'})"
            )
        self.stdout.write(self.style.SUCCESS(f"Done: +{totals[0]} categories, +{totals[1]} brands."))
