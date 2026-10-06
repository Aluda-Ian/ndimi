from django.db import migrations, models

import dubbing.models


class Migration(migrations.Migration):

    dependencies = [
        ('dubbing', '0003_workerheartbeat'),
    ]

    operations = [
        migrations.AddField(
            model_name='dubbingsettings',
            name='max_podcast_minutes',
            field=models.PositiveIntegerField(default=180, help_text='Longest podcast episode (audio only) users may submit.'),
        ),
        migrations.AddField(
            model_name='dubbingsettings',
            name='podcast_max_speedup',
            field=models.FloatField(default=1.1, help_text='Podcasts: fastest a line may be sped up. Lines that still run long move the rest of the episode later instead.'),
        ),
        migrations.AddField(
            model_name='dubbingsettings',
            name='podcast_loudness_lufs',
            field=models.FloatField(default=-16.0, help_text='Podcasts: integrated loudness of the episode. -16 is the Apple and Spotify norm.'),
        ),
        migrations.AddField(
            model_name='dubbingsettings',
            name='podcast_bitrate_kbps',
            field=models.PositiveSmallIntegerField(default=128, help_text='Podcasts: MP3 bitrate. 128 kbps stereo is the usual podcast standard.'),
        ),
        migrations.AddField(
            model_name='dubjob',
            name='transcript_target',
            field=models.FileField(blank=True, help_text='Podcasts: readable dubbed transcript with speakers and timestamps.', max_length=300, upload_to=dubbing.models.job_output_path),
        ),
    ]
