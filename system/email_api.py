"""Send email with Resend over HTTPS instead of SMTP.

Railway (Hobby and Trial plans) blocks outbound SMTP, so connecting to a mail server on
port 587/465 times out. Resend accepts mail over HTTPS (port 443), which is never blocked.
Choose it in Settings > Email > "Send through" and paste a Resend API key there.

API: POST https://api.resend.com/emails with "Authorization: Bearer <key>" and JSON
{"from", "to", "subject", "text", "html", "cc", "bcc", "reply_to"}. Docs: https://resend.com/docs/api-reference
The From address must be on a domain verified in Resend (Domains page).
"""
import logging

import requests
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

RESEND_URL = 'https://api.resend.com/emails'


class EmailAPIError(Exception):
    pass


def _html(message):
    for content, mimetype in getattr(message, 'alternatives', None) or []:
        if mimetype == 'text/html':
            return content
    return message.body if getattr(message, 'content_subtype', 'plain') == 'html' else None


def _text(message):
    return None if getattr(message, 'content_subtype', 'plain') == 'html' else message.body


# What Resend's common errors mean, in plain words for the admin.
HINTS = {
    401: 'The API key is wrong or was deleted. Create a new one in Resend > API Keys.',
    403: 'The From address is not on a domain verified in Resend. Verify the domain or change the From address.',
    422: 'Resend rejected the message. Check the From address (e.g. no-reply@ndimi.jeotamedia.co.ke).',
    429: 'Too many emails at once, or the daily limit of your Resend plan was reached.',
}


class ResendBackend(BaseEmailBackend):
    def __init__(self, api_key, timeout=20, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key, self.timeout = api_key, timeout

    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            try:
                self._send(message)
                sent += 1
            except Exception as error:
                logger.warning('Email to %s via Resend failed: %s', message.to, error)
                if not self.fail_silently:
                    raise
        return sent

    def _send(self, message):
        if not [r for r in message.recipients() if r]:
            return
        body = {'from': message.from_email, 'to': message.to, 'subject': message.subject}
        if message.cc:
            body['cc'] = message.cc
        if message.bcc:
            body['bcc'] = message.bcc
        if message.reply_to:
            body['reply_to'] = message.reply_to
        html, text = _html(message), _text(message)
        if html:
            body['html'] = html
        if text:
            body['text'] = text
        response = requests.post(RESEND_URL, json=body, timeout=self.timeout,
                                 headers={'Authorization': f'Bearer {self.api_key}', 'Accept': 'application/json'})
        if response.status_code >= 400:
            hint = HINTS.get(response.status_code, '')
            raise EmailAPIError(f'Resend returned {response.status_code}: {response.text[:200]} {hint}'.strip())
