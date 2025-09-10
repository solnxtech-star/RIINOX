
from typing import ClassVar

from django.contrib.auth.models import AbstractUser
from django.db.models import CharField
from django.db.models import EmailField, TextField, BooleanField
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from core.helper.enums import UsersRole

from .managers import UserManager


class User(AbstractUser):
    """
    Custom user model for Invoice Management System.
    Email is used as the unique identifier instead of username.
    """

    first_name = None  # type: ignore
    last_name = None  # type: ignore
    username = None  # type: ignore

    name = CharField(_("Full Name"), max_length=255, blank=True)
    email = EmailField(_("Email Address"), unique=True, db_index=True)
    phone = CharField(_("Phone Number"), max_length=20, blank=True, null=True)
    address = TextField(_("Address"), blank=True, null=True)
    role = CharField(
        _("Role"),
        max_length=20,
        choices=UsersRole.choices,
        default=UsersRole.CLIENT,
    )
    is_active = BooleanField(_("Active"), default=True)
    is_verified = BooleanField(_("Verified"), default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects: ClassVar[UserManager] = UserManager()

    class Meta:
        verbose_name = _("User")
        verbose_name_plural = _("Users")
        ordering = ["-date_joined"]

    def __str__(self):
        return f"{self.name or self.email}"

    def get_absolute_url(self):
        return reverse("users:detail", kwargs={"pk": self.pk})
