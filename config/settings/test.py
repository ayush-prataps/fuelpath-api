"""Test settings — used by pytest-django via pytest.ini."""

import os

# Provide a dummy SECRET_KEY so the test suite runs without a .env file.
# This value is intentionally public — it is never used in production.
os.environ.setdefault("SECRET_KEY", "test-only-insecure-secret-key-do-not-use-in-prod")

from .base import *  # noqa: F401, F403

# Use a dedicated test database defined in pytest.ini / conftest
# (pytest-django creates and tears it down automatically)
DEBUG = False

# Faster password hashing in tests
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Use in-memory dummy cache so tests don't depend on Redis
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# Suppress log noise during tests
LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"]},
}
