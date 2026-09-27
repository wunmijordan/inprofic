from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        # Imported here so model modules remain free of cross-app signal
        # imports during Django's app-loading phase.
        from . import signals  # noqa: F401
