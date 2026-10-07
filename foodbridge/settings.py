"""
Django settings for the FoodBridge project.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-js+nx5hp42r8*9b_=hfx@9jn4q3mr87-mti3_-tgi==8-uxwr4",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"

ALLOWED_HOSTS = os.environ.get(
    "DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,2026s2e.winproject.com.au"
).split(",")

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'accounts',
    'donations',
    'inbox',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'accounts.middleware.ApprovalGateMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'foodbridge.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'inbox.context_processors.unread_counts',
                'accounts.context_processors.portal',
            ],
        },
    },
]

WSGI_APPLICATION = 'foodbridge.wsgi.application'

AUTH_USER_MODEL = 'accounts.User'

# Database
# Uses the same college MySQL server as the old static script, via PyMySQL
# (no mysqlclient / system packages needed). Falls back to SQLite for local
# dev if DB_* env vars aren't set, so `manage.py runserver` works out of the box.
import pymysql
pymysql.install_as_MySQLdb()

DB_NAME = os.environ.get("DB_NAME")
if DB_NAME:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': DB_NAME,
            'USER': os.environ.get("DB_USER", ""),
            'PASSWORD': os.environ.get("DB_PASSWORD", ""),
            'HOST': os.environ.get("DB_HOST", "localhost"),
            'PORT': os.environ.get("DB_PORT", "3306"),
            'OPTIONS': {'charset': 'utf8mb4'},
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Australia/Sydney'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'accounts:dashboard'
LOGOUT_REDIRECT_URL = 'home'

# ---- Session timeout / "Keep me logged in" -----------------------------------
# A normal login (checkbox left unticked) is idle-timed-out after this many
# seconds of inactivity. SESSION_SAVE_EVERY_REQUEST makes this a *sliding*
# timeout - every page view resets the clock - rather than a hard cutoff.
SESSION_COOKIE_AGE = 30 * 60          # 30 minutes
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
# Ticking "Keep me logged in" on the login page extends the session to this
# many seconds instead (see accounts.views.RoleAwareLoginView.form_valid).
REMEMBER_ME_SESSION_AGE = 14 * 24 * 60 * 60   # 14 days

CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in ("127.0.0.1", "localhost")]

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# ---- FoodBridge matching & reporting -----------------------------------------
# How long a matched pantry / driver has to accept an offer before it moves on.
OFFER_TIMEOUT_MINUTES = int(os.environ.get("OFFER_TIMEOUT_MINUTES", "30"))
OFFER_WAVE_SIZE = 2          # nearest N candidates are offered at a time
OFFER_MAX_WAVES = 3          # after this many waves the listing opens to everyone eligible
ALERT_RADIUS_KM = 50         # "new listing near you" alerts go to drivers within this distance
OFFER_PROCESS_THROTTLE_SECONDS = 15   # how often page loads sweep for expired offers/listings
MEALS_PER_KG = 2             # rule of thumb used for "meals" estimates (OzHarvest's own figures work out ~2)
CO2E_KG_PER_KG_FOOD = 1.0    # est. kg CO2-equivalent avoided per kg of food rescued

# ---- Email -------------------------------------------------------------------
# Until SMTP details are set (env vars), emails are printed to the server console
# so nothing breaks. To send real email set EMAIL_HOST, EMAIL_HOST_USER and
# EMAIL_HOST_PASSWORD (and optionally EMAIL_PORT / EMAIL_USE_TLS / DEFAULT_FROM_EMAIL).
if os.environ.get("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ["EMAIL_HOST"]
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "FoodBridge <no-reply@2026s2e.winproject.com.au>")
SITE_URL = os.environ.get("SITE_URL", "https://2026s2e.winproject.com.au")
# Which notification kinds are also emailed (users can opt out in their profile).
EMAIL_NOTIFICATION_KINDS = {"approval", "offer", "released", "cancelled", "expired", "expiring", "opened", "issue"}
