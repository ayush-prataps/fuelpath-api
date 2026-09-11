"""Development settings — not for production use."""

from .base import *  # noqa: F401, F403

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]  # noqa: S104

# Enable browsable API renderer in dev only
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] += [  # noqa: F405
    "rest_framework.renderers.BrowsableAPIRenderer",
]

# django-debug-toolbar (optional; install requirements/dev.txt)
try:
    import debug_toolbar  # noqa: F401

    INSTALLED_APPS += ["debug_toolbar"]  # noqa: F405
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")  # noqa: F405
    INTERNAL_IPS = ["127.0.0.1"]
except ImportError:
    pass

# Simpler email backend for local dev
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# ── Cache: upgrade to Redis in dev if REDIS_URL is set ───────────────────────
import environ as _environ
_env = _environ.Env()
if _redis_url := _env.str("REDIS_URL", default=""):
    try:
        import django_redis  # noqa: F401
        CACHES = {
            "default": {
                "BACKEND": "django_redis.cache.RedisCache",
                "LOCATION": _redis_url,
                "OPTIONS": {
                    "CLIENT_CLASS": "django_redis.client.DefaultClient",
                    "SOCKET_CONNECT_TIMEOUT": 5,
                    "SOCKET_TIMEOUT": 5,
                },
                "KEY_PREFIX": "fuelpath",
            }
        }
    except ImportError:
        pass  # django-redis not installed — stick with LocMemCache
