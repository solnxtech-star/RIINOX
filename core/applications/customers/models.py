from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

import auto_prefetch
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.applications.customers.queryset import (
    CustomerAddressQuerySet,
    CustomerQuerySet,
)
from core.helper.enums import CustomerStatus, CustomerType
from core.helper.models import TimeBasedModel


class Customer(TimeBasedModel):
    """
    Tenant-scoped customer record (PRD §20).
    Represents retail, wholesale, and corporate buyers with financial governance,
    credit limits, outstanding balances, and multi-tenant adaptability.
    """

    objects = CustomerQuerySet.as_manager()

    organization = auto_prefetch.ForeignKey(
        "users.Organization",
        on_delete=models.CASCADE,
        related_name="customers",
        help_text=_("Tenant organization owning this customer record."),
    )
    customer_type = models.CharField(
        max_length=20,
        choices=CustomerType.choices,
        default=CustomerType.INDIVIDUAL,
        db_index=True,
    )
    name = models.CharField(
        max_length=255,
        help_text=_("Full name of individual customer or primary contact."),
    )
    company_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("Registered legal company name for corporate/B2B clients."),
    )
    email = models.EmailField(
        blank=True,
        default="",
        help_text=_("Primary billing or notification email."),
    )
    phone = models.CharField(
        max_length=35,
        blank=True,
        default="",
        help_text=_("Primary contact or WhatsApp phone number."),
    )
    tax_id = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text=_("Tax Identification Number (TIN/VAT) for formal invoice issuance."),
    )

    # Financial & Credit Governance (PRD §20, §26)
    credit_limit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=_("Maximum outstanding credit allowed. 0.00 prevents purchasing on credit."),
    )
    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        editable=False,
        help_text=_("Current outstanding receivable balance; derived from sales and payment ledgers."),
    )
    payment_terms_days = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("Credit payment due terms in days (e.g. Net 0, Net 15, Net 30)."),
    )

    # Lifecycle & Audit
    status = models.CharField(
        max_length=20,
        choices=CustomerStatus.choices,
        default=CustomerStatus.ACTIVE,
        db_index=True,
    )
    notes = models.TextField(
        blank=True,
        null=True,
        help_text=_("Internal staff notes regarding preferences or credit terms."),
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Extensible industry-specific attributes (student ID, patient ID, etc.)."),
    )
    created_by = auto_prefetch.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("User who registered this customer."),
    )

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Customer")
        verbose_name_plural = _("Customers")
        ordering: ClassVar = ["name"]
        indexes: ClassVar = [
            models.Index(fields=["organization", "status"], name="cust_org_status_idx"),
            models.Index(fields=["organization", "name"], name="cust_org_name_idx"),
        ]
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(credit_limit__gte=Decimal("0.00")),
                name="customer_credit_limit_non_negative",
            ),
            models.UniqueConstraint(
                fields=["organization", "email"],
                condition=~models.Q(email=""),
                name="unique_org_customer_email",
            ),
            models.UniqueConstraint(
                fields=["organization", "phone"],
                condition=~models.Q(phone=""),
                name="unique_org_customer_phone",
            ),
        ]

    def __str__(self) -> str:
        org_name = getattr(self.organization, "name", "No Org")
        return f"{self.name} ({org_name})"

    @property
    def primary_address(self) -> CustomerAddress | None:
        """Returns the default or first registered address for this customer."""
        return self.addresses.filter(is_default=True).first() or self.addresses.first()

    @property
    def formatted_address(self) -> str:
        """Formatted string representation of the primary address."""
        addr = self.primary_address
        return str(addr) if addr else ""


class CustomerAddress(TimeBasedModel):
    """
    Structured delivery and billing addresses attached to a customer.
    """

    class AddressType(models.TextChoices):
        BILLING = "billing", _("Billing")
        SHIPPING = "shipping", _("Shipping")
        BOTH = "both", _("Both")

    objects = CustomerAddressQuerySet.as_manager()

    customer = auto_prefetch.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="addresses",
    )
    address_type = models.CharField(
        max_length=20,
        choices=AddressType.choices,
        default=AddressType.BOTH,
    )
    street = models.CharField(max_length=255)
    city = models.CharField(max_length=100, blank=True, default="")
    state = models.CharField(max_length=100, blank=True, default="")
    postal_code = models.CharField(max_length=20, blank=True, default="")
    country = models.CharField(max_length=100, default="Nigeria")
    is_default = models.BooleanField(default=False)

    class Meta(auto_prefetch.Model.Meta):
        verbose_name = _("Customer Address")
        verbose_name_plural = _("Customer Addresses")
        ordering: ClassVar = ["-is_default", "created_at"]

    def __str__(self) -> str:
        city_suffix = f", {self.city}" if self.city else ""
        return f"{self.street}{city_suffix} ({self.get_address_type_display()})"
