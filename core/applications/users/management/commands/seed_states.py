
import pycountry
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from core.applications.users.models import State


class Command(BaseCommand):
    """
    Seed the State table for a given country from pycountry's ISO 3166-2
    subdivision data.

    Usage:
        python manage.py seed_states NG
        python manage.py seed_states NG --dry-run
    """

    help = "Seed states/provinces for a country from pycountry."

    def add_arguments(self, parser):
        parser.add_argument(
            "country_code",
            type=str,
            help="ISO 3166-1 alpha-2 country code, e.g. NG",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be created without writing to the DB.",
        )

    def handle(self, *args, **options):
        code = options["country_code"].upper()
        dry_run = options["dry_run"]

        if not pycountry.countries.get(alpha_2=code):
            raise CommandError(f"'{code}' is not a valid ISO 3166-1 alpha-2 code.")

        subdivisions = list(pycountry.subdivisions.get(country_code=code))
        if not subdivisions:
            self.stdout.write(self.style.WARNING(f"No subdivisions found for {code}."))
            return

        created, skipped = 0, 0
        for sub in subdivisions:
            if dry_run:
                self.stdout.write(f"[dry-run] {sub.name} ({sub.code}) — type: {sub.type}")
                continue

            _, was_created = State.objects.get_or_create(
                country=code,
                name=sub.name,
                defaults={"code": sub.code},
            )
            created += 1 if was_created else 0
            skipped += 0 if was_created else 1

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"{len(subdivisions)} subdivisions previewed for {code}."))
        else:
            self.stdout.write(
                self.style.SUCCESS(f"{code}: {created} states created, {skipped} already existed.")
            )
