from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CustomersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core.applications.customers"
    verbose_name = _("Customers")
