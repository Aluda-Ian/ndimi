"""Run with:  python manage.py test system"""
from unittest import mock

from django.core import mail
from django.test import TestCase

from .models import EmailSettings, Integration


class EmailOverHTTPSTests(TestCase):
    """Hosts like Railway block SMTP, so an email API (Resend/Brevo) is used when it is turned on."""

    def setUp(self):
        cfg = EmailSettings.load()
        cfg.enabled, cfg.from_email, cfg.from_name = True, 'no-reply@ndimi.example', 'Ndimi'
        cfg.save()

    def _turn_on(self, slug):
        integration = Integration.objects.get(slug=slug)
        integration.enabled = True
        integration.api_key = 'test-key'
        integration.save()

    def test_resend_is_used_instead_of_smtp(self):
        self._turn_on('resend')
        with mock.patch('system.email_api.requests.post') as post:
            post.return_value.status_code = 200
            sent = mail.send_mail('Reset your password', 'Click the link', None, ['user@example.com'])
        self.assertEqual(sent, 1)
        url, kwargs = post.call_args[0][0], post.call_args[1]
        self.assertEqual(url, 'https://api.resend.com/emails')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer test-key')
        self.assertEqual(kwargs['json']['from'], 'Ndimi <no-reply@ndimi.example>')
        self.assertEqual(kwargs['json']['to'], ['user@example.com'])

    def test_brevo_payload(self):
        self._turn_on('brevo')
        with mock.patch('system.email_api.requests.post') as post:
            post.return_value.status_code = 201
            mail.send_mail('Hi', 'Body', None, ['user@example.com'])
        body = post.call_args[1]['json']
        self.assertEqual(body['sender'], {'email': 'no-reply@ndimi.example', 'name': 'Ndimi'})
        self.assertEqual(body['to'], [{'email': 'user@example.com'}])
        self.assertEqual(body['textContent'], 'Body')

    def test_api_error_is_reported(self):
        self._turn_on('resend')
        with mock.patch('system.email_api.requests.post') as post:
            post.return_value.status_code = 403
            post.return_value.text = 'domain not verified'
            with self.assertRaisesMessage(Exception, 'domain not verified'):
                mail.send_mail('Hi', 'Body', None, ['user@example.com'])
