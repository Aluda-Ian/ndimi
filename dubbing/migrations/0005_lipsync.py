from django.db import migrations, models

import dubbing.models


def add_sync_integration(apps, schema_editor):
    Integration = apps.get_model('system', 'Integration')
    Integration.objects.get_or_create(
        slug='sync',
        defaults={
            'name': 'Sync (lip sync)',
            'category': 'ai',
            'base_url': 'https://api.sync.so/v2',
            'docs_url': 'https://sync.so/docs',
            'key_label': 'API key',
            'config': {'model': 'lipsync-2', 'sync_mode': 'cut_off', 'price_per_second': 0.05},
            'notes': (
                'Re-animates mouths in the original video to match the dubbed voice. '
                'Create a key at sync.so > Settings > API keys. Needs R2 storage (Sync downloads the files). '
                'model: lipsync-2 (fast), lipsync-2-pro (sharper faces) or sync-3. '
                'price_per_second is only used for the cost estimate shown before a lip sync.'
            ),
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('dubbing', '0004_podcast_mode'),
        ('system', '0002_seed'),
    ]

    operations = [
        migrations.AddField(
            model_name='dubjob',
            name='lipsync_video',
            field=models.FileField(blank=True, help_text='Dubbed video with the mouths re-animated to match the new voice (Sync).',
                                   max_length=300, upload_to=dubbing.models.job_output_path),
        ),
        migrations.RunPython(add_sync_integration, migrations.RunPython.noop),
    ]
