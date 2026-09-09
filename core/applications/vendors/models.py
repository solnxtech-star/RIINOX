import auto_prefetch
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.helper.enums import VendorStatusChoices
from core.helper.models import TimeBasedModel


class Vendor(TimeBasedModel):

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="vendors",
        help_text=_("Organization this vendor supplies."),
    )
    company_name = models.CharField(
        _("Company Name"),
        max_length=255,
        help_text=_("Registered/trading name of the vendor."),
    )
    contact_person = models.CharField(
        _("Contact Person"),
        max_length=150,
        blank=True,
        null=True,
        help_text=_("Primary contact at the vendor for order/delivery queries."),
    )
    phone = models.CharField(
        _("Phone"),
        max_length=20,
        blank=True,
        null=True,
        help_text=_("Vendor contact phone number."),
    )
    email = models.EmailField(
        _("Email"),
        blank=True,
        null=True,
        help_text=_("Vendor contact email."),
    )
    address = models.TextField(
        blank=True,
        null=True,
        help_text=_("Vendor's registered or warehouse address."),
    )
    products_supplied = models.ManyToManyField(
        "products.Product",
        related_name="vendors",
        blank=True,
        help_text=_("Products this vendor is known to supply."),
    )
    status = models.CharField(
        max_length=20,
        choices=VendorStatusChoices.choices,
        default=VendorStatusChoices.ACTIVE,
        help_text=_("Inactive vendors are hidden from new-purchase pickers."),
    )
    notes = models.TextField(
        blank=True,
        null=True,
        help_text=_("Internal notes about this vendor (terms, reliability, etc.)."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Vendor")
        verbose_name_plural = _("Vendors")
        ordering = ["company_name"]

    def __str__(self):
        return self.company_name