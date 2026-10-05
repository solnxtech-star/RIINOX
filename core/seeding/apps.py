from django.apps import AppConfig
from django.conf import settings
from django.db import DEFAULT_DB_ALIAS
from django.db.models.signals import post_migrate
from django.utils.module_loading import autodiscover_modules


def seed_after_migrate(sender, using=DEFAULT_DB_ALIAS, plan=None, **kwargs):
    from core.seeding.registry import run_seeds

    if using != DEFAULT_DB_ALIAS:
        return
    if not getattr(settings, "SEED_ON_MIGRATE", True):
        return
    # `migrate app zero` and other rollbacks: the tables may not match the models.
    if any(backwards for _migration, backwards in (plan or ())):
        return
    run_seeds(skip=getattr(settings, "SEEDING_SKIP", ()))


class SeedingConfig(AppConfig):
    name = "core.seeding"
    label = "seeding"
    verbose_name = "Platform seeding"

    def ready(self):
        autodiscover_modules("seeds")  # imports <app>.seeds for every installed app that has one
        post_migrate.connect(
            seed_after_migrate,
            sender=self,
            dispatch_uid="core.seeding.seed_after_migrate",
        )
