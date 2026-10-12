import os

os.environ.setdefault("DATABASE_URL", "sqlite:///test_db.sqlite3")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from .base import *  # noqa: F403
from .base import TEMPLATES
from .base import env

CLOUDINARY_STORAGE = {
    "CLOUD_NAME": "test_cloud",
    "API_KEY": "test_key",
    "API_SECRET": "test_secret",
}

SEEDING_SKIP = ("document_templates",)

# GENERAL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#secret-key
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="CtK9CYd6nEkC2kMCVtJSXt82O7J7rX7Alzhdo50RSiQRcQ6p2TUODRpKqwzMmhWE",
)
# https://docs.djangoproject.com/en/dev/ref/settings/#test-runner
TEST_RUNNER = "django.test.runner.DiscoverRunner"

# PASSWORDS
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#password-hashers
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# EMAIL
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#email-backend
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# DEBUGGING FOR TEMPLATES
# ------------------------------------------------------------------------------
TEMPLATES[0]["OPTIONS"]["debug"] = True  # type: ignore[index]

# MEDIA
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#media-url
MEDIA_URL = "http://media.testserver/"
# Your stuff...
# ------------------------------------------------------------------------------
