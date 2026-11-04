from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from django.db.models.signals import post_init, pre_save
        from .ledger import guard_ledger_date, registered_ledger_models, remember_ledger_date, validate_registry

        validate_registry()
        pre_save.connect(guard_ledger_date, dispatch_uid="core.ledger_date_guard", weak=False)
        for model in registered_ledger_models():
            post_init.connect(
                remember_ledger_date, sender=model, weak=False,
                dispatch_uid=f"core.ledger_remember_{model._meta.label_lower}",
            )
        # Imported here so model modules remain free of cross-app signal
        # imports during Django's app-loading phase.
        from . import signals  # noqa: F401
