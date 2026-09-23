from django.conf import settings
from django.db import models


class EmailVerification(models.Model):
    """Records which email address a user has confirmed.

    A user counts as verified only while their current email matches
    verified_email, so changing email requires verifying again.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, primary_key=True, related_name='email_verification',
    )
    verified_email = models.EmailField(blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.user} ({self.verified_email or "unverified"})'


def is_email_verified(user):
    if not user.is_authenticated or not user.email:
        return False
    record = EmailVerification.objects.filter(user=user).first()
    return bool(record and record.verified_at and record.verified_email.lower() == user.email.lower())


def mark_email_verified(user):
    from django.utils import timezone

    EmailVerification.objects.update_or_create(
        user=user, defaults={'verified_email': user.email, 'verified_at': timezone.now()},
    )


def needs_email_verification(user):
    """True when the site requires verified emails and this user hasn't verified theirs."""
    from django.db import DatabaseError

    from system.models import SystemSettings

    if not user.is_authenticated or user.is_superuser:
        return False
    try:
        required = SystemSettings.load().require_email_verification
    except DatabaseError:
        return False
    return required and not is_email_verified(user)
