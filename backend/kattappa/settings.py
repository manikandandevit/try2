"""
Django settings for SynQuot project.
"""

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
# Frontend directory (parent of backend)
FRONTEND_DIR = BASE_DIR.parent / 'frontend'

# Load environment variables from .env file if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / '.env')
except ImportError:
    pass  # python-dotenv not installed, use environment variables directly


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-synquot-ai-quotation-maker-dev-key-change-in-production'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['*']

# CSRF Trusted Origins - Allow requests from frontend
CSRF_TRUSTED_ORIGINS = [
    'http://localhost:5173',
    'http://127.0.0.1:5173',
]


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'quotations',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'kattappa.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [FRONTEND_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'kattappa.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases

# Database Configuration - SQLite
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Authentication Backends
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',  # Default Django authentication
]

# Password validation
# https://docs.djangoproject.com/en/5.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.0/ref/settings/#i18n

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/

# STATIC_URL = 'static/'
# STATIC_ROOT = BASE_DIR / 'staticfiles'

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
# Only include static directory if it exists
STATICFILES_DIRS = []
static_dir = FRONTEND_DIR / 'static'
if static_dir.exists():
    STATICFILES_DIRS.append(static_dir)

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
# https://docs.djangoproject.com/en/5.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# OpenRouter API Configuration
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
# Default to a free model that's known to work. User can override in .env
# The system will automatically fallback to other available models if this one fails
OPENROUTER_MODEL = os.environ.get('OPENROUTER_MODEL', 'google/gemini-flash-1.5:free')
OPENROUTER_API_URL = 'https://openrouter.ai/api/v1/chat/completions'

# Valid free models on OpenRouter (system will try these in order if default fails):
# - google/gemini-flash-1.5:free (recommended - most reliable)
# - meta-llama/llama-3.1-8b-instruct:free
# - microsoft/phi-3-mini-128k-instruct:free
# Note: meta-llama/llama-3.1-70b-instruct:free may not always be available

# Session Configuration
SESSION_COOKIE_AGE = 86400  # 24 hours
SESSION_SAVE_EVERY_REQUEST = True

# JWT Configuration - Production Banking Level
JWT_ISSUER = os.environ.get('JWT_ISSUER', 'kattappa-api')
JWT_AUDIENCE = os.environ.get('JWT_AUDIENCE', 'kattappa-client')
ENABLE_TOKEN_ROTATION = os.environ.get('ENABLE_TOKEN_ROTATION', 'True').lower() == 'true'

# JWT RSA Keys (in production, store these in environment variables or secrets manager)
# For development, keys will be auto-generated. For production, set these:
# JWT_PRIVATE_KEY = os.environ.get('JWT_PRIVATE_KEY', '')
# JWT_PUBLIC_KEY = os.environ.get('JWT_PUBLIC_KEY', '')

# Cache Configuration (for chatbot response caching)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'synquot-cache',
        'OPTIONS': {
            'MAX_ENTRIES': 1000,
            'CULL_FREQUENCY': 3,
        }
    }
}

# CORS configuration for frontend (Vite React app on port 5173)
# CORS_ALLOWED_ORIGINS = [
#     "http://localhost:5174",
#     "http://127.0.0.1:5174",
# ]

# Allow all origins in development (more permissive)
# CORS_ALLOW_ALL_ORIGINS = True  # For development only

# CORS_ALLOWED_ORIGINS = [
#     "https://synquote-frontend.vercel.app",
# ]
# CSRF_TRUSTED_ORIGINS = [
#     "https://synquote-frontend.vercel.app",
#     "https://lovably-landed-payton.ngrok-free.dev",
# ]


CORS_ALLOW_CREDENTIALS = True



# Allow cookies/authorization headers to be sent from the frontend if needed
CORS_ALLOW_CREDENTIALS = True

# CORS headers
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]

SESSION_COOKIE_SAMESITE = "None"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = "None"
CSRF_COOKIE_SECURE = True
