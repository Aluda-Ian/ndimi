import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import dubbing.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DubbingSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('default_engine', models.CharField(choices=[('auto', 'Auto: ElevenLabs Dubbing for its languages, our pipeline for the rest'), ('pipeline', 'Ndimi pipeline (every language, editable)'), ('elevenlabs_dubbing', 'ElevenLabs Dubbing API (one-click)')], default='auto', max_length=20)),
                ('elevenlabs_dubbing_languages', models.CharField(default='sw,ki', help_text='Comma-separated language codes "Auto" sends to the ElevenLabs Dubbing API.', max_length=200)),
                ('stt_provider', models.CharField(choices=[('elevenlabs', 'ElevenLabs Scribe (speakers + word timings)'), ('openai', 'OpenAI Whisper'), ('custom', 'Ndimi models (custom HTTP)')], default='elevenlabs', max_length=12, verbose_name='Speech-to-text')),
                ('translation_provider', models.CharField(choices=[('openai', 'OpenAI (context-aware, timing-aware)'), ('google', 'Google Translate'), ('custom', 'Ndimi models (custom HTTP)'), ('none', 'None: humans translate in review')], default='openai', max_length=12, verbose_name='Translation')),
                ('tts_provider', models.CharField(choices=[('elevenlabs', 'ElevenLabs (voice cloning)'), ('custom', 'Ndimi models (custom HTTP)')], default='elevenlabs', max_length=12, verbose_name='Voice (text-to-speech)')),
                ('separation', models.CharField(choices=[('demucs', 'Demucs, local (best: clean music and effects bed)'), ('elevenlabs', 'ElevenLabs voice isolator (clean voice for cloning; original is ducked)'), ('duck', 'None: duck the original audio under the new voice')], default='elevenlabs', max_length=12, verbose_name='Voice/music separation')),
                ('tts_model_id', models.CharField(default='eleven_v3', help_text='ElevenLabs model. eleven_v3 supports Swahili and Somali.', max_length=60)),
                ('clone_voices', models.BooleanField(default=True, help_text="Clone each speaker's voice. Off: use the default voice.")),
                ('default_voice_id', models.CharField(blank=True, help_text='ElevenLabs voice used when cloning is off or fails.', max_length=60)),
                ('review_by_default', models.BooleanField(default=True, help_text='Pause after translation so a person can check the script before voices are made.')),
                ('max_speedup', models.FloatField(default=1.3, help_text='Fastest a dubbed line may be sped up to fit its slot (1.0 = never).')),
                ('target_loudness_lufs', models.FloatField(default=-16.0, help_text='Integrated loudness of the final mix. -16 for web, -23 for broadcast.')),
                ('max_duration_minutes', models.PositiveIntegerField(default=30, help_text='Longest source video users may submit.')),
                ('max_jobs_per_user_per_day', models.PositiveIntegerField(default=10, help_text='0 = no limit. Superusers are exempt.')),
                ('ffmpeg_path', models.CharField(default='ffmpeg', help_text='Full path if ffmpeg is not on PATH, e.g. C:\\ffmpeg\\bin\\ffmpeg.exe', max_length=255)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'dubbing settings',
                'verbose_name_plural': 'dubbing settings',
            },
        ),
        migrations.CreateModel(
            name='DubJob',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=200)),
                ('source_file', models.FileField(blank=True, max_length=300, upload_to=dubbing.models.job_upload_path)),
                ('source_url', models.URLField(blank=True, max_length=500)),
                ('source_language', models.CharField(blank=True, help_text='Blank = detect automatically.', max_length=12)),
                ('target_language', models.CharField(max_length=12)),
                ('engine', models.CharField(default='pipeline', max_length=20)),
                ('options', models.JSONField(blank=True, default=dict, help_text='review, num_speakers, keep_background, ...')),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('running', 'Running'), ('review', 'Waiting for review'), ('completed', 'Completed'), ('failed', 'Failed'), ('cancelled', 'Cancelled')], db_index=True, default='queued', max_length=12)),
                ('stage', models.CharField(blank=True, max_length=40)),
                ('progress', models.PositiveSmallIntegerField(default=0)),
                ('state', models.JSONField(blank=True, default=dict, help_text='Pipeline bookkeeping: finished stages, provider IDs.')),
                ('error', models.TextField(blank=True)),
                ('cancel_requested', models.BooleanField(default=False)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('locked_by', models.CharField(blank=True, max_length=80)),
                ('locked_at', models.DateTimeField(blank=True, null=True)),
                ('run_after', models.DateTimeField(blank=True, db_index=True, help_text='Worker waits until this time (polling, retries).', null=True)),
                ('duration_seconds', models.FloatField(blank=True, null=True)),
                ('dubbed_video', models.FileField(blank=True, max_length=300, upload_to=dubbing.models.job_output_path)),
                ('dubbed_audio', models.FileField(blank=True, max_length=300, upload_to=dubbing.models.job_output_path)),
                ('subtitles_target', models.FileField(blank=True, max_length=300, upload_to=dubbing.models.job_output_path)),
                ('subtitles_source', models.FileField(blank=True, max_length=300, upload_to=dubbing.models.job_output_path)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dub_jobs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'dub job',
                'ordering': ['-created_at'],
                'permissions': [('view_all_dubjobs', "Can see every user's dub jobs"), ('choose_dub_engine', 'Can choose the dubbing engine per job')],
            },
        ),
        migrations.CreateModel(
            name='Speaker',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('label', models.CharField(help_text='ID from speech-to-text, e.g. speaker_0.', max_length=40)),
                ('name', models.CharField(blank=True, max_length=80)),
                ('voice_id', models.CharField(blank=True, help_text='Voice used for this speaker.', max_length=80)),
                ('cloned', models.BooleanField(default=False, help_text='voice_id is a clone made for this job (deleted with the job).')),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='speakers', to='dubbing.dubjob')),
            ],
            options={
                'ordering': ['label'],
                'unique_together': {('job', 'label')},
            },
        ),
        migrations.CreateModel(
            name='Segment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('index', models.PositiveIntegerField()),
                ('start', models.FloatField(help_text='Seconds')),
                ('end', models.FloatField(help_text='Seconds')),
                ('source_text', models.TextField()),
                ('translated_text', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('ready', 'Voice ready'), ('stale', 'Needs new voice')], default='pending', max_length=8)),
                ('audio', models.FileField(blank=True, max_length=300, upload_to=dubbing.models.segment_audio_path)),
                ('audio_duration', models.FloatField(blank=True, null=True)),
                ('speed', models.FloatField(default=1.0, help_text='Speed-up applied to fit the slot.')),
                ('overflow', models.FloatField(default=0.0, help_text='Seconds the line still runs past its slot. Shorten the text if > 0.')),
                ('edited', models.BooleanField(default=False)),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='segments', to='dubbing.dubjob')),
                ('speaker', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='segments', to='dubbing.speaker')),
            ],
            options={
                'ordering': ['index'],
                'unique_together': {('job', 'index')},
            },
        ),
        migrations.CreateModel(
            name='JobEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('at', models.DateTimeField(default=django.utils.timezone.now)),
                ('level', models.CharField(default='info', max_length=8)),
                ('stage', models.CharField(blank=True, max_length=40)),
                ('message', models.TextField()),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='dubbing.dubjob')),
            ],
            options={
                'ordering': ['at', 'id'],
            },
        ),
    ]
