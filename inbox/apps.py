from django.apps import AppConfig


class InboxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inbox"
    verbose_name = "Inbox (messages & notifications)"

    def ready(self):
        # Registers the donation signal handlers that create notifications.
        from . import signals  # noqa: F401
