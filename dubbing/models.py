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
    AUTO = ('auto', 'Automatic: best allowed model for each language (see AI models)')
    STT_CHOICES = [AUTO, ('elevenlabs', 'ElevenLabs Scribe (speakers + word timings)'), ('openai', 'OpenAI Whisper'), ('custom', 'Ndimi models (custom HTTP)')]
    MT_CHOICES = [AUTO, ('openai', 'OpenAI (context-aware, timing-aware)'), ('google', 'Google Translate'), ('custom', 'Ndimi models (custom HTTP)'), ('none', 'None: humans translate in review')]
    TTS_CHOICES = [AUTO, ('elevenlabs', 'ElevenLabs (voice cloning)'), ('custom', 'Ndimi models (custom HTTP)')]
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
    stt_provider = models.CharField('Speech-to-text', max_length=12, choices=STT_CHOICES, default='auto')
    translation_provider = models.CharField('Translation', max_length=12, choices=MT_CHOICES, default='auto')
    tts_provider = models.CharField('Voice (text-to-speech)', max_length=12, choices=TTS_CHOICES, default='auto')
    allow_noncommercial_models = models.BooleanField(
        'Allow non-commercial models', default=False,
        help_text='Off for real customers. On only for internal tests: lets Automatic pick models whose licence '
                  'forbids commercial use (e.g. NLLB-200, MMS voices).',
    )
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
    max_podcast_minutes = models.PositiveIntegerField(default=180, help_text='Longest podcast episode (audio only) users may submit.')
    podcast_max_speedup = models.FloatField(
        default=1.1, help_text='Podcasts: fastest a line may be sped up. Lines that still run long move the rest of the episode later instead.',
    )
    podcast_loudness_lufs = models.FloatField(default=-16.0, help_text='Podcasts: integrated loudness of the episode. -16 is the Apple and Spotify norm.')
    podcast_bitrate_kbps = models.PositiveSmallIntegerField(default=128, help_text='Podcasts: MP3 bitrate. 128 kbps stereo is the usual podcast standard.')
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


class AIModel(models.Model):
    """One model Ndimi can use for a step, with its licence and languages.

    With a step set to "Automatic" in Dubbing settings, Ndimi picks, per language, the usable model
    with the lowest priority number (see dubbing/routing.py), and falls back to the next if it fails.
    """
    TASKS = [('stt', 'Speech-to-text'), ('translate', 'Translation'), ('tts', 'Voice (text-to-speech)')]
    RUNNERS = [
        ('elevenlabs', 'ElevenLabs API'),
        ('openai', 'OpenAI API'),
        ('google', 'Google Cloud API'),
        ('ndimi', 'Ndimi model service (our GPU server)'),
    ]

    key = models.SlugField(max_length=60, unique=True, help_text='Code name sent to the model service, e.g. madlad-400.')
    name = models.CharField(max_length=100)
    task = models.CharField(max_length=10, choices=TASKS)
    runner = models.CharField('Runs on', max_length=12, choices=RUNNERS)
    model_ref = models.CharField('Model', max_length=200, blank=True,
                                 help_text='What runs: API model ID or Hugging Face repo, e.g. google/madlad400-3b-mt.')
    languages = models.CharField(max_length=300, blank=True,
                                 help_text='Ndimi codes it produces or understands, comma-separated (e.g. luo,ki). * = any language.')
    source_languages = models.CharField(max_length=300, blank=True, default='*',
                                        help_text='Translation only: languages it translates from. * = any.')
    licence = models.CharField(max_length=120, blank=True)
    commercial_use = models.BooleanField('Commercial use allowed', default=True,
                                         help_text='Off: never used for customer dubs unless "Allow non-commercial models" is on.')
    enabled = models.BooleanField(default=False)
    priority = models.PositiveSmallIntegerField(default=50, help_text='Lower wins. Our trained models: 5; open models: 10–20; APIs: 30+.')
    results = models.JSONField(default=dict, blank=True,
                               help_text='Test scores per language, e.g. {"luo": {"chrF": 41.2, "accepted": 0.83}}.')
    notes = models.TextField(blank=True)
    docs_url = models.URLField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['task', 'priority', 'name']
        verbose_name = 'AI model'

    def __str__(self):
        return self.name

    def language_set(self, field='languages'):
        return {c.strip().lower() for c in (getattr(self, field) or '').split(',') if c.strip()}

    def covers(self, language, source=None):
        langs = self.language_set()
        if not ('*' in langs or (language and language.lower() in langs)):
            return False
        if self.task == 'translate' and source:
            sources = self.language_set('source_languages') or {'*'}
            return '*' in sources or source.lower() in sources
        return True


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
    transcript_target = models.FileField(upload_to=job_output_path, max_length=300, blank=True,
                                         help_text='Podcasts: readable dubbed transcript with speakers and timestamps.')
    archived = models.BooleanField(default=False, db_index=True, help_text='Hidden from the projects list; files are kept.')
    lipsync_video = models.FileField(upload_to=job_output_path, max_length=300, blank=True,
                                     help_text='Dubbed video with the mouths re-animated to match the new voice (Sync).')

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
        # Local scratch space for the worker (DUB_WORK_ROOT). Finished files go to storage (disk or R2).
        path = Path(settings.DUB_WORK_ROOT) / str(self.pk) / 'work'
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def is_podcast(self):
        """Audio-only episode: natural pacing, MP3 + transcript output (no lip-sync needed)."""
        kind = self.options.get('kind', 'auto')
        if kind in ('podcast', 'video'):
            return kind == 'podcast'
        return self.state.get('files', {}).get('has_video') is False

    @property
    def pacing(self):
        """'natural': lines take the time they need (podcasts). 'sync': lines fit the original slots (video)."""
        if not self.is_podcast:
            return 'sync'  # picture: the voice has to stay on the speaker's lips
        return 'sync' if self.options.get('pacing') == 'sync' else 'natural'

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


class WorkerHeartbeat(models.Model):
    name = models.CharField(max_length=320, primary_key=True)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    is_running = models.BooleanField(default=True)
    ffmpeg_version = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-last_seen_at']
        verbose_name = 'dub worker'
        verbose_name_plural = 'dub workers'

    @property
    def ffmpeg_available(self):
        return bool(self.ffmpeg_version)


class JobEvent(models.Model):
    job = models.ForeignKey(DubJob, on_delete=models.CASCADE, related_name='events')
    at = models.DateTimeField(default=timezone.now)
    level = models.CharField(max_length=8, default='info')
    stage = models.CharField(max_length=40, blank=True)
    message = models.TextField()

    class Meta:
        ordering = ['at', 'id']
