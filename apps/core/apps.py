from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from django.db.models.signals import pre_save
        from .ledger import guard_ledger_date

        pre_save.connect(guard_ledger_date, dispatch_uid="core.ledger_date_guard", weak=False)
        # Imported here so model modules remain free of cross-app signal
        # imports during Django's app-loading phase.
        from . import signals  # noqa: F401
