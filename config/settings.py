"""
Blinkit EDI Microservice - Django Settings
"""
import os
from pathlib import Path
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "change-me-in-production")
DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "corsheaders",
    # Local
    "blinkit_edi.edi",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# =============================================================================
# DATABASE - PostgreSQL (Supabase compatible)
# =============================================================================
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "blinkit_edi"),
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "OPTIONS": {
            "connect_timeout": 5,
        },
    }
}

# =============================================================================
# DRF
# =============================================================================
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "EXCEPTION_HANDLER": "blinkit_edi.core.exceptions.custom_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "blinkit_webhook": "500/hour",
    },
}

# =============================================================================
# BLINKIT EDI CONFIG
# =============================================================================
BLINKIT_EDI = {
    # Blinkit's API key (they provide this, we validate incoming requests)
    "INBOUND_API_KEY": os.environ.get("BLINKIT_INBOUND_API_KEY", ""),
    
    # Our API key (we send this in headers when pushing ASN/Ack to Blinkit)
    "OUTBOUND_API_KEY": os.environ.get("BLINKIT_OUTBOUND_API_KEY", ""),
    
    # Blinkit endpoints
    "ASN_ENDPOINT_PROD": "https://api.partnersbiz.com/webhook/public/v1/asn",
    "ASN_ENDPOINT_PREPROD": "https://dev.partnersbiz.com/webhook/public/v1/asn",
    
    "PO_ACK_ENDPOINT_PROD": "https://api.partnersbiz.com/webhook/public/v1/po/acknowledgement",
    "PO_ACK_ENDPOINT_PREPROD": "https://dev.partnersbiz.com/webhook/public/v1/po/acknowledgement",
    
    "PO_AMENDMENT_ENDPOINT_PROD": "https://api.partnersbiz.com/webhook/public/v1/po/amendment",
    "PO_AMENDMENT_ENDPOINT_PREPROD": "https://dev.partnersbiz.com/webhook/public/v1/po/amendment",
    
    # Environment toggle
    "USE_PROD": os.environ.get("BLINKIT_USE_PROD", "False").lower() == "true",
    
    # Retry config for outbound calls
    "MAX_RETRIES": 3,
    "RETRY_BACKOFF": 60,  # seconds
    
    # Supported tenants
    "TENANTS": ["BLINKIT", "HYPERPURE"],
    
    # JSON format (Blinkit supports JSON and XML, we'll use JSON)
    "CONTENT_TYPE": "application/json",
}

# =============================================================================
# LOGGING
# =============================================================================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.environ.get("LOG_FILE", str(BASE_DIR / "logs" / "app.log")),
            "maxBytes": 10 * 1024 * 1024,  # 10 MB
            "backupCount": 5,
            "formatter": "verbose",
        },
    },
    "loggers": {
        "blinkit_edi": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
        },
    },
}

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

STATIC_URL = "/static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "Asia/Kolkata"
