from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from core.seeding.registry import UnknownSeedError
from core.seeding.registry import ordered_seeds
from core.seeding.registry import run_seeds


class Command(BaseCommand):
    help = (
        "Create or refresh platform defaults (idempotent). Runs automatically after "
        "`migrate`; use this to re-run on demand."
    )

    def add_arguments(self, parser):
        parser.add_argument("--only", nargs="+", metavar="SEED", default=[])
        parser.add_argument("--skip", nargs="+", metavar="SEED", default=[])
        parser.add_argument(
            "--list", action="store_true",
            help="Show the seeds and their order, then exit."
        )

    def handle(self, *args, **options):
        try:
            if options["list"]:
                for seed in ordered_seeds(only=options["only"], skip=options["skip"]):
                    flags = " [external]" if seed.external else ""
                    deps = f" (after: {', '.join(seed.depends_on)})" if seed.depends_on else ""
                    self.stdout.write(f"{seed.name}{flags}{deps}: {seed.description}")
                return
            results = run_seeds(only=options["only"], skip=options["skip"])
        except UnknownSeedError as exc:
            raise CommandError(str(exc)) from exc

        for name, r in results.items():
            self.stdout.write(f"  {name:<22} +{r.created} created  ~{r.updated} updated  ={r.unchanged} unchanged")
        self.stdout.write(self.style.SUCCESS("Defaults are in place."))
