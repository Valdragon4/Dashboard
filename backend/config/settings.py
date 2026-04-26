import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret")
DEBUG = os.getenv("DJANGO_DEBUG", "True") == "True"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "finance",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "finance" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "finances"),
        "USER": os.getenv("POSTGRES_USER", "finances"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "finances"),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": int(os.getenv("POSTGRES_PORT", "5432")),
    }
}

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Celery
CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TIMEZONE = "Europe/Paris"

from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    # Synchronisation bancaire tous les jours à 00h00
    "sync-all-bank-accounts-daily": {
        "task": "finance.tasks.sync_all_bank_accounts",
        "schedule": crontab(hour=0, minute=0),
    },
    # Nettoyage des anciens logs de sync tous les jours à 01h00
    "cleanup-sync-logs-daily": {
        "task": "finance.tasks.cleanup_old_sync_logs",
        "schedule": crontab(hour=1, minute=0),
    },
}

CORS_ALLOW_ALL_ORIGINS = True

# Auth
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# CSRF / Proxy SSL
# Exemple d'env: DJANGO_CSRF_TRUSTED_ORIGINS=https://finance.valentin-marot.fr
_csrf_env = os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").strip()
CSRF_TRUSTED_ORIGINS = [o for o in _csrf_env.split(",") if o] or []
# Si vous êtes derrière Nginx en HTTPS, activez l'en-tête suivant:
if os.getenv("DJANGO_USE_XFORWARDED_PROTO", "true").lower() in {"1", "true", "yes"}:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Logging configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'finance': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# Trade Republic : tentative de login via Chromium (Playwright) avant le fallback HTTP,
# pour réduire les 403 sur api.traderepublic.com lorsque app.traderepublic.com est OK dans le navigateur.
# Désactiver : TRADEPUBLIC_USE_PLAYWRIGHT_INITIATE=0
TRADEPUBLIC_USE_PLAYWRIGHT_INITIATE = os.getenv("TRADEPUBLIC_USE_PLAYWRIGHT_INITIATE", "1") == "1"

# BoursoBank scraper:
# - timeout Playwright global pour les waits/navigation
# - fenêtre d'attente supplémentaire quand BoursoBank demande une sécurisation
# - proxy SOCKS5/HTTP pour contourner le blocage des IP de datacenter par BoursoBank
#   (BoursoBank bloque les IP d'hébergeurs ; router via une IP résidentielle est requis
#    pour la phase de sécurisation/2FA)
#   Exemple : BOURSOBANK_PROXY=socks5://localhost:1080
#   Mettre en place le tunnel SSH côté serveur :
#     ssh -D 1080 -N -f user@ma-machine-perso
#   Puis exposer le port dans docker-compose si le worker tourne dans Docker.
BOURSOBANK_TIMEOUT_MS = int(os.getenv("BOURSOBANK_TIMEOUT_MS", "60000"))
BOURSOBANK_AUTH_TIMEOUT_MS = int(os.getenv("BOURSOBANK_AUTH_TIMEOUT_MS", "30000"))
BOURSOBANK_TX_TIMEOUT_MS = int(os.getenv("BOURSOBANK_TX_TIMEOUT_MS", "30000"))
BOURSOBANK_BALANCE_TIMEOUT_MS = int(os.getenv("BOURSOBANK_BALANCE_TIMEOUT_MS", "10000"))
BOURSOBANK_HEADLESS = os.getenv("BOURSOBANK_HEADLESS", "1") == "1"
BOURSOBANK_DATA_DIR = os.getenv("BOURSOBANK_DATA_DIR", str(BASE_DIR / "boursobank-data"))
BOURSOBANK_PROXY = os.getenv("BOURSOBANK_PROXY", "")
BOURSOBANK_ACCOUNT_NUMBER = os.getenv("BOURSOBANK_ACCOUNT_NUMBER", "")


