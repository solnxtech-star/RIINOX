import auto_prefetch
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.applications.warehouse.queryset import StockLocationQuerySet
from core.applications.warehouse.queryset import WarehouseQuerySet
from core.helper.enums import LocationType
from core.helper.models import TimeBasedModel


class Warehouse(TimeBasedModel):
    """
    An organization's location / branch: a store, warehouse, office, etc. (PRD §35).
    Every organization has exactly one default location (`is_default`).
    """

    objects = WarehouseQuerySet.as_manager()

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="warehouses",
        help_text=_("Organization this warehouse belongs to."),
    )
    name = models.CharField(
        _("Warehouse Name"),
        max_length=150,
        help_text=_("Display name, e.g. 'Port Harcourt Main Warehouse'."),
    )
    code = models.CharField(
        _("Warehouse Code"),
        max_length=30,
        db_index=True,
        help_text=_("Short unique code used in references and reports, e.g. 'PH-01'."),
    )
    address = models.TextField(
        blank=True,
        null=True,
        help_text=_("Physical address of the warehouse."),
    )
    manager = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_warehouses",
        help_text=_("User responsible for this warehouse's day-to-day operations."),
    )
    location_type = models.CharField(
        max_length=20,
        choices=LocationType.choices,
        default=LocationType.BRANCH,
    )
    is_default = models.BooleanField(
        default=False,
        help_text=_("The organization's primary location. Exactly one per organization."),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("Inactive warehouses are hidden from new-purchase/invoice pickers."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Warehouse")
        verbose_name_plural = _("Warehouses")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="unique_location_code_per_org"),
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(is_default=True),
                name="unique_default_location_per_org",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"


class StockLocation(TimeBasedModel):
    """
    Optional finer-grained location within a warehouse — a shelf, rack,
    or bin (PRD §35: Location → Storage Area → Inventory).

    Reached from a location as `warehouse.stock_locations`. Not to be confused with
    `membership.locations` / `invitation.locations`, which are Warehouse rows.
    """

    objects = StockLocationQuerySet.as_manager()

    warehouse = auto_prefetch.ForeignKey(
        "warehouse.Warehouse",
        on_delete=models.CASCADE,
        related_name="stock_locations",
        help_text=_("Warehouse this shelf/rack/bin is inside."),
    )
    name = models.CharField(
        _("Location Name"),
        max_length=100,
        help_text=_("e.g. 'Aisle 3, Shelf B'."),
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text=_("Optional notes on what's stored here or how to find it."),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("Inactive locations are hidden from stock-placement pickers."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Stock Location")
        verbose_name_plural = _("Stock Locations")
        unique_together = ("warehouse", "name")
        ordering = ["name"]

    def __str__(self):
        return f"{self.warehouse.code} / {self.name}"
