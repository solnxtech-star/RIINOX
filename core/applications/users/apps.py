# core/applications/users/apps.py
from importlib.util import find_spec

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class UsersConfig(AppConfig):
    name = "core.applications.users"
    verbose_name = _("Users")

    def ready(self):
        # Import signals only if the module exists, so a real ImportError inside
        # it is no longer swallowed (the old `suppress(ImportError)` hid those).
        if find_spec("core.applications.users.signals"):
            import core.applications.users.signals  # noqa: F401, PLC0415
