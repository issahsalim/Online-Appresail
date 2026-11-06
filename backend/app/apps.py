from django.apps import AppConfig


class AppConfig(AppConfig):
    name = 'app'

    def ready(self):
        from .audit import register_audit_signals

        register_audit_signals()
