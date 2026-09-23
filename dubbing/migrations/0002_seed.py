from django.db import migrations


def seed(apps, schema_editor):
    from django.contrib.auth.management import create_permissions

    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None

    DubbingSettings = apps.get_model('dubbing', 'DubbingSettings')
    Integration = apps.get_model('system', 'Integration')
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')

    DubbingSettings.objects.get_or_create(pk=1)

    Integration.objects.get_or_create(
        slug='ndimi-models',
        defaults={
            'name': 'Ndimi models (custom HTTP)',
            'category': 'ai',
            'key_label': 'Bearer token',
            'config': {'timeout_seconds': 300},
            'notes': (
                'Your own speech models, e.g. trained on AfriVoices-KE for Dholuo, Kalenjin and Maa. '
                'The server must accept POST {base_url}/transcribe (multipart "file", "language"), '
                '{base_url}/translate (JSON {"texts", "source", "target"}), and '
                '{base_url}/tts (JSON {"text", "language", "voice"}, returns audio). See dubbing/providers/custom.py.'
            ),
        },
    )

    # Let the starter "Users" group create dubs. Admins can change this in Groups.
    group = Group.objects.filter(name='Users').first()
    if group:
        group.permissions.add(Permission.objects.get(content_type__app_label='dubbing', codename='add_dubjob'))


class Migration(migrations.Migration):

    dependencies = [
        ('dubbing', '0001_initial'),
        ('system', '0002_seed'),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
