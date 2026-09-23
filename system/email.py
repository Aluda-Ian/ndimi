import logging

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.console import EmailBackend as ConsoleBackend
from django.db import DatabaseError

logger = logging.getLogger(__name__)


class DatabaseEmailBackend(BaseEmailBackend):
    """Sends mail with the SMTP settings admins enter in the admin panel.

    When email is turned off (or not set up yet), messages are printed to the
    server console so nothing fails silently during development.
    """

    def send_messages(self, email_messages):
        from .models import EmailSettings

        try:
            cfg = EmailSettings.load()
        except DatabaseError:
            cfg = None

        if cfg is None or not cfg.enabled or not cfg.host:
            return ConsoleBackend(fail_silently=self.fail_silently).send_messages(email_messages)

        if cfg.formatted_from:
            for message in email_messages:
                if not message.from_email or message.from_email == settings.DEFAULT_FROM_EMAIL:
                    message.from_email = cfg.formatted_from

        return cfg.smtp_connection(fail_silently=self.fail_silently).send_messages(email_messages)
