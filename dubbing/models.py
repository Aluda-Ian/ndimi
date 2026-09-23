import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone

from system.models import SingletonModel


def job_upload_path(instance, filename):
    return f'dubbing/jobs/{instance.pk}/source/{Path(filename).name}'


def job_output_path(instance, filename):
    return f'dubbing/jobs/{instance.pk}/output/{Path(filename).name}'


def segment_audio_path(instance, filename):
    return f'dubbing/jobs/{instance.job_id}/segments/{Path(filename).name}'


class DubbingSettings(SingletonModel):
    """How the dubbing engine runs. Admins change this in /admin/ > Dubbing."""

    ENGINE_CHOICES = [
        ('auto', 'Auto: ElevenLabs Dubbing for its languages, our pipeline for the rest'),
        ('pipeline', 'Ndimi pipeline (every language, editable)'),
        ('elevenlabs_dubbing', 'ElevenLabs Dubbing API (one-click)'),
    ]
    STT_CHOICES = [('elevenlabs', 'ElevenLabs Scribe (speakers + word timings)'), ('openai', 'OpenAI Whisper'), ('custom', 'Ndimi models (custom HTTP)')]
    MT_CHOICES = [('openai', 'OpenAI (context-aware, timing-aware)'), ('google', 'Google Translate'), ('custom', 'Ndimi models (custom HTTP)'), ('none', 'None: humans translate in review')]
    TTS_CHOICES = [('elevenlabs', 'ElevenLabs (voice cloning)'), ('custom', 'Ndimi models (custom HTTP)')]
    SEPARATION_CHOICES = [
        ('demucs', 'Demucs, local (best: clean music and effects bed)'),
        ('elevenlabs', 'ElevenLabs voice isolator (clean voice for cloning; original is ducked)'),
        ('duck', 'None: duck the original audio under the new voice'),
    ]

    default_engine = models.CharField(max_length=20, choices=ENGINE_CHOICES, default='auto')
    elevenlabs_dubbing_languages = models.CharField(
        max_length=200, default='sw,ki',
        help_text='Comma-separated language codes "Auto" sends to the ElevenLabs Dubbing API.',
    )
    stt_provider = models.CharField('Speech-to-text', max_length=12, choices=STT_CHOICES, default='elevenlabs')
    translation_provider = models.CharField('Translation', max_length=12, choices=MT_CHOICES, default='openai')
    tts_provider = models.CharField('Voice (text-to-speech)', max_length=12, choices=TTS_CHOICES, default='elevenlabs')
    separation = models.CharField('Voice/music separation', max_length=12, choices=SEPARATION_CHOICES, default='elevenlabs')
    tts_model_id = models.CharField(max_length=60, default='eleven_v3', help_text='ElevenLabs model. eleven_v3 supports Swahili and Somali.')
    clone_voices = models.BooleanField(default=True, help_text="Clone each speaker's voice. Off: use the default voice.")
    default_voice_id = models.CharField(max_length=60, blank=True, help_text='ElevenLabs voice used when cloning is off or fails.')
    review_by_default = models.BooleanField(
        default=True, help_text='Pause after translation so a person can check the script before voices are made.',
    )
    max_speedup = models.FloatField(default=1.3, help_text='Fastest a dubbed line may be sped up to fit its slot (1.0 = never).')
    target_loudness_lufs = models.FloatField(default=-16.0, help_text='Integrated loudness of the final mix. -16 for web, -23 for broadcast.')
    max_duration_minutes = models.PositiveIntegerField(default=30, help_text='Longest source video users may submit.')
    max_jobs_per_user_per_day = models.PositiveIntegerField(default=10, help_text='0 = no limit. Superusers are exempt.')
    ffmpeg_path = models.CharField(max_length=255, default='ffmpeg', help_text='Full path if ffmpeg is not on PATH, e.g. C:\\ffmpeg\\bin\\ffmpeg.exe')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'dubbing settings'
        verbose_name_plural = 'dubbing settings'

    def __str__(self):
        return 'Dubbing settings'

    def elevenlabs_languages(self):
        return {c.strip().lower() for c in self.elevenlabs_dubbing_languages.split(',') if c.strip()}

    def resolve_engine(self, requested, target_language):
        engine = requested or self.default_engine
        if engine == 'auto':
            return 'elevenlabs_dubbing' if target_language.lower() in self.elevenlabs_languages() else 'pipeline'
        return engine


class DubJob(models.Model):
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('running', 'Running'),
        ('review', 'Waiting for review'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]
    ACTIVE = ('queued', 'running')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='dub_jobs')
    name = models.CharField(max_length=200)
    source_file = models.FileField(upload_to=job_upload_path, max_length=300, blank=True)
    source_url = models.URLField(max_length=500, blank=True)
    source_language = models.CharField(max_length=12, blank=True, help_text='Blank = detect automatically.')
    target_language = models.CharField(max_length=12)
    engine = models.CharField(max_length=20, default='pipeline')
    options = models.JSONField(default=dict, blank=True, help_text='review, num_speakers, keep_background, ...')

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='queued', db_index=True)
    stage = models.CharField(max_length=40, blank=True)
    progress = models.PositiveSmallIntegerField(default=0)
    state = models.JSONField(default=dict, blank=True, help_text='Pipeline bookkeeping: finished stages, provider IDs.')
    error = models.TextField(blank=True)
    cancel_requested = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    locked_by = models.CharField(max_length=80, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    run_after = models.DateTimeField(null=True, blank=True, db_index=True, help_text='Worker waits until this time (polling, retries).')
    duration_seconds = models.FloatField(null=True, blank=True)

    dubbed_video = models.FileField(upload_to=job_output_path, max_length=300, blank=True)
    dubbed_audio = models.FileField(upload_to=job_output_path, max_length=300, blank=True)
    subtitles_target = models.FileField(upload_to=job_output_path, max_length=300, blank=True)
    subtitles_source = models.FileField(upload_to=job_output_path, max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'dub job'
        permissions = [
            ('view_all_dubjobs', "Can see every user's dub jobs"),
            ('choose_dub_engine', 'Can choose the dubbing engine per job'),
        ]

    def __str__(self):
        return self.name

    @property
    def workdir(self):
        path = Path(settings.MEDIA_ROOT) / 'dubbing' / 'jobs' / str(self.pk) / 'work'
        path.mkdir(parents=True, exist_ok=True)
        return path

    def stage_done(self, stage):
        return stage in self.state.get('done', [])

    def mark_stage_done(self, stage):
        done = self.state.setdefault('done', [])
        if stage not in done:
            done.append(stage)
        self.save(update_fields=['state', 'updated_at'])

    def reset_stages(self, *stages):
        self.state['done'] = [s for s in self.state.get('done', []) if s not in stages]
        self.save(update_fields=['state', 'updated_at'])

    def log(self, message, level='info', stage=None):
        JobEvent.objects.create(job=self, level=level, stage=stage or self.stage, message=message[:1000])

    def set_stage(self, stage, progress=None):
        self.stage = stage
        if progress is not None:
            self.progress = max(0, min(100, int(progress)))
        self.save(update_fields=['stage', 'progress', 'updated_at'])

    def requeue(self):
        self.status = 'queued'
        self.error = ''
        self.cancel_requested = False
        self.locked_by = ''
        self.locked_at = None
        self.finished_at = None
        self.save()


class Speaker(models.Model):
    job = models.ForeignKey(DubJob, on_delete=models.CASCADE, related_name='speakers')
    label = models.CharField(max_length=40, help_text='ID from speech-to-text, e.g. speaker_0.')
    name = models.CharField(max_length=80, blank=True)
    voice_id = models.CharField(max_length=80, blank=True, help_text='Voice used for this speaker.')
    cloned = models.BooleanField(default=False, help_text='voice_id is a clone made for this job (deleted with the job).')

    class Meta:
        ordering = ['label']
        unique_together = [('job', 'label')]

    def __str__(self):
        return self.name or self.label


class Segment(models.Model):
    STATUS_CHOICES = [('pending', 'Pending'), ('ready', 'Voice ready'), ('stale', 'Needs new voice')]

    job = models.ForeignKey(DubJob, on_delete=models.CASCADE, related_name='segments')
    index = models.PositiveIntegerField()
    speaker = models.ForeignKey(Speaker, null=True, blank=True, on_delete=models.SET_NULL, related_name='segments')
    start = models.FloatField(help_text='Seconds')
    end = models.FloatField(help_text='Seconds')
    source_text = models.TextField()
    translated_text = models.TextField(blank=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default='pending')
    audio = models.FileField(upload_to=segment_audio_path, max_length=300, blank=True)
    audio_duration = models.FloatField(null=True, blank=True)
    speed = models.FloatField(default=1.0, help_text='Speed-up applied to fit the slot.')
    overflow = models.FloatField(default=0.0, help_text='Seconds the line still runs past its slot. Shorten the text if > 0.')
    edited = models.BooleanField(default=False)

    class Meta:
        ordering = ['index']
        unique_together = [('job', 'index')]

    def __str__(self):
        return f'#{self.index} {self.source_text[:40]}'


class JobEvent(models.Model):
    job = models.ForeignKey(DubJob, on_delete=models.CASCADE, related_name='events')
    at = models.DateTimeField(default=timezone.now)
    level = models.CharField(max_length=8, default='info')
    stage = models.CharField(max_length=40, blank=True)
    message = models.TextField()

    class Meta:
        ordering = ['at', 'id']
