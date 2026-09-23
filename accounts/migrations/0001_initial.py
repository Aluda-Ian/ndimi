import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def mark_existing_verified(apps, schema_editor):
    """Existing accounts are trusted: treat their current emails as verified."""
    from django.utils import timezone

    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    EmailVerification = apps.get_model('accounts', 'EmailVerification')
    now = timezone.now()
    for user in User.objects.exclude(email=''):
        EmailVerification.objects.get_or_create(user=user, defaults={'verified_email': user.email, 'verified_at': now})


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EmailVerification',
            fields=[
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, related_name='email_verification', serialize=False, to=settings.AUTH_USER_MODEL)),
                ('verified_email', models.EmailField(blank=True, max_length=254)),
                ('verified_at', models.DateTimeField(blank=True, null=True)),
            ],
        ),
        migrations.RunPython(mark_existing_verified, migrations.RunPython.noop),
    ]
