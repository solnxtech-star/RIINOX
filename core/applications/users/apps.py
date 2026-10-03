import contextlib

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

def _sync_catalog(sender, using=None, **kwargs):
    from core.applications.users.permissions import sync_permission_catalog
    sync_permission_catalog(using=using)

class UsersConfig(AppConfig):
    name = "core.applications.users"
    verbose_name = _("Users")

    def ready(self):
        with contextlib.suppress(ImportError):
            import core.applications.users.signals  # noqa: F401, PLC0415
