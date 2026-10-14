"""Send email over HTTPS instead of SMTP.

Railway (Hobby and Trial plans) blocks outbound SMTP, so connecting to a mail server on
port 587/465 times out. Transactional email services accept mail over HTTPS (port 443),
which is never blocked. Turn one on in Settings > Integrations & API keys:

- Resend  (slug "resend"):  POST https://api.resend.com/emails       Authorization: Bearer <key>
- Brevo   (slug "brevo"):   POST https://api.brevo.com/v3/smtp/email  api-key: <key>

The From address (Settings > Email) must be on a domain you have verified with that service.
"""
import logging
from email.utils import parseaddr

import requests
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

PROVIDERS = ('resend', 'brevo')
NAMES = {'resend': 'Resend', 'brevo': 'Brevo'}


def active_provider():
    """(slug, credentials) of the first email API integration that is turned on, or (None, None)."""
    from .models import Integration

    for slug in PROVIDERS:
        try:
            creds = Integration.credentials(slug)
        except Exception:  # missing table during migrations
            return None, None
        if creds and creds.get('api_key'):
            return slug, creds
    return None, None


def _html(message):
    for content, mimetype in getattr(message, 'alternatives', None) or []:
        if mimetype == 'text/html':
            return content
    return message.body if getattr(message, 'content_subtype', 'plain') == 'html' else None


def _text(message):
    return None if getattr(message, 'content_subtype', 'plain') == 'html' else message.body


class EmailAPIError(Exception):
    pass


class HTTPSEmailBackend(BaseEmailBackend):
    def __init__(self, provider, creds, timeout=20, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.provider, self.creds, self.timeout = provider, creds, timeout

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            try:
                self._send(message)
                sent += 1
            except Exception as error:
                logger.warning('Email to %s via %s failed: %s', message.to, self.provider, error)
                if not self.fail_silently:
                    raise
        return sent

    def _send(self, message):
        recipients = [r for r in message.recipients() if r]
        if not recipients:
            return
        base = (self.creds.get('base_url') or '').rstrip('/')
        key = self.creds['api_key']
        html, text = _html(message), _text(message)
        if self.provider == 'resend':
            body = {'from': message.from_email, 'to': message.to, 'subject': message.subject}
            if message.cc:
                body['cc'] = message.cc
            if message.bcc:
                body['bcc'] = message.bcc
            if message.reply_to:
                body['reply_to'] = message.reply_to
            if html:
                body['html'] = html
            if text:
                body['text'] = text
            url, headers = f'{base or "https://api.resend.com"}/emails', {'Authorization': f'Bearer {key}'}
        else:  # brevo
            name, address = parseaddr(message.from_email)
            sender = {'email': address, **({'name': name} if name else {})}
            body = {'sender': sender, 'to': [{'email': a} for a in message.to], 'subject': message.subject}
            if message.cc:
                body['cc'] = [{'email': a} for a in message.cc]
            if message.bcc:
                body['bcc'] = [{'email': a} for a in message.bcc]
            if message.reply_to:
                body['replyTo'] = {'email': parseaddr(message.reply_to[0])[1]}
            if html:
                body['htmlContent'] = html
            if text:
                body['textContent'] = text
            url, headers = f'{base or "https://api.brevo.com/v3"}/smtp/email', {'api-key': key}
        response = requests.post(url, json=body, headers={**headers, 'Accept': 'application/json'}, timeout=self.timeout)
        if response.status_code >= 400:
            raise EmailAPIError(f'{NAMES[self.provider]} returned {response.status_code}: {response.text[:300]}')
