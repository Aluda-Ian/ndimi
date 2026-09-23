"""Account emails. Failures are logged and reported, never crash the request."""
import logging

from django.core import signing
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger(__name__)

VERIFY_SALT = 'accounts.verify-email'
VERIFY_MAX_AGE = 60 * 60 * 24 * 3  # 3 days


def _site_name():
    from django.db import DatabaseError

    from system.models import SystemSettings

    try:
        return SystemSettings.load().site_name
    except DatabaseError:
        return 'Ndimi'


def _send(subject, template, context, to):
    context = {'site_name': _site_name(), **context}
    try:
        send_mail(subject, render_to_string(template, context), None, [to])
        return True
    except Exception:  # SMTP/network problems
        logger.exception('Could not send "%s" email to %s', subject, to)
        return False


def make_verify_token(user):
    return signing.dumps({'u': user.pk, 'e': user.email.lower()}, salt=VERIFY_SALT)


def read_verify_token(token):
    """Return (user_id, email) or None if invalid/expired."""
    try:
        data = signing.loads(token, salt=VERIFY_SALT, max_age=VERIFY_MAX_AGE)
        return data['u'], data['e']
    except (signing.BadSignature, KeyError, TypeError):
        return None


def send_verification_email(request, user):
    if not user.email:
        return False
    link = request.build_absolute_uri(reverse('verify_email', args=[make_verify_token(user)]))
    return _send(
        f'Confirm your email for {_site_name()}',
        'accounts/emails/verify_email.txt',
        {'user': user, 'link': link},
        user.email,
    )


def send_password_changed_email(request, user):
    if not user.email:
        return False
    return _send(
        'Your password was changed',
        'accounts/emails/password_changed.txt',
        {'user': user, 'reset_link': request.build_absolute_uri(reverse('password_reset'))},
        user.email,
    )
