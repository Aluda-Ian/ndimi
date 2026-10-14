"""Run with:  python manage.py test system"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase

from .admin import EmailSettingsForm
from .models import EmailSettings


class ResendEmailTests(TestCase):
    """Railway blocks SMTP, so Settings > Email can send through Resend over HTTPS instead."""

    def setUp(self):
        cfg = EmailSettings.load()
        cfg.enabled, cfg.provider = True, 'resend'
        cfg.from_email, cfg.from_name = 'no-reply@ndimi.example', 'Ndimi'
        cfg.resend_api_key = 're_test'
        cfg.save()

    def test_password_reset_style_email_goes_through_resend(self):
        with mock.patch('system.email_api.requests.post') as post:
            post.return_value.status_code = 200
            sent = mail.send_mail('Reset your password', 'Click the link', None, ['user@example.com'])
        self.assertEqual(sent, 1)
        url, kwargs = post.call_args[0][0], post.call_args[1]
        self.assertEqual(url, 'https://api.resend.com/emails')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer re_test')
        self.assertEqual(kwargs['json']['from'], 'Ndimi <no-reply@ndimi.example>')
        self.assertEqual((kwargs['json']['to'], kwargs['json']['text']), (['user@example.com'], 'Click the link'))

    def test_resend_error_explains_itself(self):
        with mock.patch('system.email_api.requests.post') as post:
            post.return_value.status_code = 403
            post.return_value.text = 'The domain is not verified'
            with self.assertRaisesMessage(Exception, 'not on a domain verified in Resend'):
                mail.send_mail('Hi', 'Body', None, ['user@example.com'])

    def test_smtp_settings_are_kept_and_used_when_chosen(self):
        cfg = EmailSettings.load()
        cfg.provider, cfg.host = 'smtp', 'mail.example.com'
        cfg.save()
        self.assertEqual(cfg.connection().__class__.__name__, 'EmailBackend')  # Django's SMTP backend
        self.assertTrue(cfg.ready)

    def test_form_requires_key_and_from_address_for_resend(self):
        cfg = EmailSettings.load()
        cfg.resend_api_key = ''
        cfg.save()
        form = EmailSettingsForm(instance=cfg, data={
            'enabled': 'on', 'provider': 'resend', 'port': 587, 'security': 'tls', 'timeout': 20, 'from_name': 'Ndimi',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('resend_api_key', form.errors)
        self.assertIn('from_email', form.errors)
        form = EmailSettingsForm(instance=cfg, data={
            'enabled': 'on', 'provider': 'resend', 'resend_api_key': 're_new', 'port': 587, 'security': 'tls',
            'timeout': 20, 'from_name': 'Ndimi', 'from_email': 'no-reply@ndimi.example',
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().resend_api_key, 're_new')

    def test_admin_test_button_reports_resend(self):
        from django.contrib import admin
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from .admin import EmailSettingsAdmin

        admin_user = get_user_model().objects.create_superuser('boss', 'boss@example.com', 'pass-12345!')
        request = RequestFactory().post('/')
        request.user, request.session = admin_user, {}
        request._messages = FallbackStorage(request)
        with mock.patch('system.email_api.requests.post') as post:
            post.return_value.status_code = 200
            EmailSettingsAdmin(EmailSettings, admin.site)._send_test(request, EmailSettings.load())
        self.assertIn('via Resend', EmailSettings.load().last_test_result)
