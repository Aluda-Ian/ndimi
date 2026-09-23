"""Encrypt secrets (SMTP password, API keys) before they are stored in the database.

The key comes from FIELD_ENCRYPTION_KEY in .env. If that is not set, a key is
derived from DJANGO_SECRET_KEY. Changing whichever key is in use makes saved
secrets unreadable, and they must be entered again.
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


def _fernet():
    key = getattr(settings, 'FIELD_ENCRYPTION_KEY', '')
    if key:
        return Fernet(key.encode())
    digest = hashlib.sha256(('ndimi-field-encryption:' + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value):
    if not value:
        return ''
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token):
    """Return the plain value, '' if empty, or None if it can't be decrypted."""
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        logger.warning('A stored secret could not be decrypted. Was the encryption key changed?')
        return None


def mask(value):
    if value is None:
        return 'Saved, but unreadable with the current encryption key. Enter it again.'
    if not value:
        return 'Not set.'
    return f'Saved (ends in …{value[-4:]}).' if len(value) >= 12 else 'Saved.'
