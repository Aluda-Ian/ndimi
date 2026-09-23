from django.conf import settings
from django.db import migrations

INTEGRATIONS = [
    # slug, name, category, base_url, docs_url, key_label, secret_label, config, notes
    ('openai', 'OpenAI', 'ai', 'https://api.openai.com/v1', 'https://platform.openai.com/docs',
     'API key', '', {'organization': '', 'project': ''},
     'Speech-to-text (Whisper) and translation. Create a key at platform.openai.com > API keys.'),
    ('elevenlabs', 'ElevenLabs', 'ai', 'https://api.elevenlabs.io/v1', 'https://elevenlabs.io/docs',
     'API key', '', {'default_voice_id': '', 'model_id': ''},
     'Voice generation and voice cloning for dubbed tracks.'),
    ('google-cloud', 'Google Cloud (Speech, Translate, TTS)', 'ai', 'https://googleapis.com', 'https://cloud.google.com/docs',
     'API key', '', {'project_id': '', 'location': 'global'},
     'Use an API key, or paste a service account into Extra secrets as {"service_account": {...}}.'),
    ('afrivoices-ke', 'AfriVoices-KE dataset', 'data', '', '',
     'Access token', '', {'dataset_url': '', 'languages': []},
     'Kenyan-language speech dataset for training. Access details come from the research consortium.'),
    ('cloudflare-r2', 'Cloudflare R2', 'storage', '', 'https://developers.cloudflare.com/r2/',
     'Access key ID', 'Secret access key', {'account_id': '', 'bucket': '', 'public_url': ''},
     'Base URL: https://<account_id>.r2.cloudflarestorage.com'),
    ('aws-s3', 'AWS S3', 'storage', '', 'https://docs.aws.amazon.com/s3/',
     'Access key ID', 'Secret access key', {'region': 'af-south-1', 'bucket': ''},
     'af-south-1 is Cape Town, the closest AWS region to Kenya.'),
    ('mpesa-daraja', 'M-Pesa (Daraja)', 'payments', 'https://sandbox.safaricom.co.ke', 'https://developer.safaricom.co.ke',
     'Consumer key', 'Consumer secret', {'shortcode': '', 'callback_url': ''},
     'Put the Lipa na M-Pesa passkey in Extra secrets as {"passkey": "..."}. '
     'Change the base URL to https://api.safaricom.co.ke when you go live.'),
    ('stripe', 'Stripe', 'payments', 'https://api.stripe.com', 'https://docs.stripe.com',
     'Publishable key', 'Secret key', {},
     'Put the webhook signing secret in Extra secrets as {"webhook_secret": "whsec_..."}.'),
    ('africastalking', "Africa's Talking (SMS)", 'messaging', 'https://api.sandbox.africastalking.com/version1',
     'https://developers.africastalking.com', 'API key', '', {'username': 'sandbox', 'sender_id': ''},
     'Use username "sandbox" while testing. For live, set your app username and base URL '
     'https://api.africastalking.com/version1.'),
]


def seed(apps, schema_editor):
    from django.contrib.auth.management import create_permissions

    # Permissions are normally created after migrations finish; create them now so we can assign them.
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None

    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    SystemSettings = apps.get_model('system', 'SystemSettings')
    EmailSettings = apps.get_model('system', 'EmailSettings')
    Integration = apps.get_model('system', 'Integration')

    # Starter group for sign-ups: dubbing tool only. Admins can edit it or build others.
    users_group, _ = Group.objects.get_or_create(name='Users')
    users_group.permissions.add(Permission.objects.get(content_type__app_label='system', codename='use_dubbing_tool'))
    for user in User.objects.filter(is_superuser=False):
        user.groups.add(users_group)

    SystemSettings.objects.get_or_create(
        pk=1,
        defaults={
            'default_group': users_group,
            'max_video_upload_mb': getattr(settings, 'MAX_VIDEO_UPLOAD_MB', 500),
        },
    )
    EmailSettings.objects.get_or_create(pk=1)

    for slug, name, category, base_url, docs_url, key_label, secret_label, config, notes in INTEGRATIONS:
        Integration.objects.get_or_create(
            slug=slug,
            defaults={
                'name': name, 'category': category, 'base_url': base_url, 'docs_url': docs_url,
                'key_label': key_label, 'secret_label': secret_label, 'config': config, 'notes': notes,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('system', '0001_initial'),
        ('projects', '0002_default_languages'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
