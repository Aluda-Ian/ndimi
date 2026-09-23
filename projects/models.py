import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models

VIDEO_EXTENSIONS = ['mp4', 'webm', 'mov', 'm4v']

_YOUTUBE_ID = re.compile(
    r'(?:youtube(?:-nocookie)?\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/|v/)|youtu\.be/)([A-Za-z0-9_-]{11})'
)


def youtube_id(url):
    """The 11-character video id from any common YouTube link, or '' if it isn't one."""
    match = _YOUTUBE_ID.search(url or '')
    return match.group(1) if match else ''


class Language(models.Model):
    """A language users can pick in the dubbing tool. Managed by admins."""

    code = models.SlugField(max_length=12, unique=True, help_text='Short code, e.g. sw, ki, luo.')
    name = models.CharField(max_length=80)
    note = models.CharField(max_length=120, blank=True, help_text='Small label under the name, e.g. "Available in preview".')
    available = models.BooleanField(
        default=False,
        help_text='On: users can run the full dub flow. Off: users see a "coming soon" screen.',
    )
    enabled = models.BooleanField(default=True, help_text='Off hides the language from users entirely.')
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name

    def as_dict(self):
        return {'id': self.code, 'name': self.name, 'note': self.note, 'ok': self.available}


class DemoSetup(models.Model):
    """The single demo configuration (videos + script) every user sees."""

    original_video = models.FileField(
        upload_to='demo/', blank=True,
        validators=[FileExtensionValidator(VIDEO_EXTENSIONS)],
        help_text='Original English video.',
    )
    dubbed_video = models.FileField(
        upload_to='demo/', blank=True,
        validators=[FileExtensionValidator(VIDEO_EXTENSIONS)],
        help_text='Dubbed Kiswahili video.',
    )
    original_url = models.URLField(
        'Original video link', blank=True,
        help_text='YouTube link to the original video. Used instead of the uploaded file when set.',
    )
    dubbed_url = models.URLField(
        'Dubbed video link', blank=True,
        help_text='YouTube link to the dubbed video. Used instead of the uploaded file when set.',
    )
    script = models.TextField(
        blank=True,
        help_text='One line per segment: 0:00 | Narrator | English line | Kiswahili line',
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )

    class Meta:
        verbose_name = 'demo setup'
        verbose_name_plural = 'demo setup'

    def __str__(self):
        return 'Demo setup'

    def clean(self):
        for field in ('original_url', 'dubbed_url'):
            value = getattr(self, field)
            if value and not youtube_id(value):
                raise ValidationError({field: 'Paste a YouTube link, like https://youtu.be/abc123XYZ00.'})

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
