"""Isolated UI runner settings. Never point this runner at a business database."""
import os
from storetrack.settings import *
DEBUG = True
SECRET_KEY = 'local-ui-fixture-only-not-a-deployment-secret'
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']
SITE_URL = ''
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.environ['INPROFIC_UI_DB']}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}
STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
ROOT_URLCONF = 'scripts.ui.urls'
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
CSRF_TRUSTED_ORIGINS = ['http://127.0.0.1:8765']
PWA_BUILD_VERSION = 'ui-fixture'
MIGRATION_MODULES = {name: None for name in ['admin','auth','contenttypes','sessions','accounts','core','inventory','procurement','production','sales','expenses','commerce','whatsapp_storefront','assistant']}

ASSISTANT_ENABLED = True
