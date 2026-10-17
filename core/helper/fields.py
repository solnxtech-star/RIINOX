import json

from cryptography.fernet import Fernet
from cryptography.fernet import MultiFernet
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


def _fernet() -> MultiFernet:
    # First key encrypts; all keys decrypt, so keys rotate without downtime.
    return MultiFernet([Fernet(k) for k in settings.FIELD_ENCRYPTION_KEYS])


def _encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def _decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


class EncryptedTextField(models.TextField):
    """Fernet-encrypted at rest. Cannot be filtered or ordered on."""

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return _encrypt(value) if value else value

    def from_db_value(self, value, expression, connection):
        return _decrypt(value) if value else value


class EncryptedJSONField(models.TextField):
    """JSON stored as Fernet ciphertext. Not queryable; for payloads that may carry secrets."""

    def get_prep_value(self, value):
        if value is None:
            return None
        return _encrypt(json.dumps(value, cls=DjangoJSONEncoder))

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        return json.loads(_decrypt(value))

    def to_python(self, value):
        return value  # TextField.to_python would str() a dict
