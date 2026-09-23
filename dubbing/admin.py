from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from .engine.audio import FFmpeg
from .models import DubbingSettings, DubJob, JobEvent, Segment, Speaker
from .pipeline import delete_job_files


@admin.register(DubbingSettings)
class DubbingSettingsAdmin(admin.ModelAdmin):
    readonly_fields = ('ffmpeg_status', 'updated_at')
    fieldsets = (
        ('Engine', {'fields': ('default_engine', 'elevenlabs_dubbing_languages')}),
        ('Pipeline providers', {
            'fields': ('stt_provider', 'translation_provider', 'tts_provider', 'separation'),
            'description': 'Each provider needs its integration switched on with a key in System management > Integrations.',
        }),
        ('Voices', {'fields': ('tts_model_id', 'clone_voices', 'default_voice_id')}),
        ('Quality', {'fields': ('review_by_default', 'max_speedup', 'target_loudness_lufs')}),
        ('Limits', {'fields': ('max_duration_minutes', 'max_jobs_per_user_per_day')}),
        ('Server', {'fields': ('ffmpeg_path', 'ffmpeg_status', 'updated_at')}),
    )

    @admin.display(description='ffmpeg check')
    def ffmpeg_status(self, obj):
        version = FFmpeg(obj.ffmpeg_path).check() if obj.pk else None
        return version or 'Not found. Install ffmpeg (winget install ffmpeg) or set the full path above.'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = DubbingSettings.load()
        return redirect(reverse('admin:dubbing_dubbingsettings_change', args=[obj.pk]))


class SpeakerInline(admin.TabularInline):
    model = Speaker
    extra = 0
    fields = ('label', 'name', 'voice_id', 'cloned')
    readonly_fields = ('label', 'cloned')


class SegmentInline(admin.TabularInline):
    model = Segment
    extra = 0
    fields = ('index', 'speaker', 'start', 'end', 'source_text', 'translated_text', 'status', 'speed', 'overflow')
    readonly_fields = ('index', 'start', 'end', 'source_text', 'status', 'speed', 'overflow')
    show_change_link = False


class EventInline(admin.TabularInline):
    model = JobEvent
    extra = 0
    fields = ('at', 'level', 'stage', 'message')
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(DubJob)
class DubJobAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'target_language', 'engine', 'status_badge', 'progress', 'stage', 'created_at')
    list_filter = ('status', 'engine', 'target_language')
    search_fields = ('name', 'owner__username', 'owner__email')
    readonly_fields = ('id', 'owner', 'status', 'stage', 'progress', 'error', 'attempts', 'locked_by', 'locked_at',
                       'run_after', 'duration_seconds', 'created_at', 'started_at', 'finished_at', 'state')
    fieldsets = (
        (None, {'fields': ('id', 'name', 'owner', 'status', 'stage', 'progress', 'error')}),
        ('Source', {'fields': ('source_file', 'source_url', 'source_language', 'target_language', 'engine', 'options')}),
        ('Output', {'fields': ('dubbed_video', 'dubbed_audio', 'subtitles_target', 'subtitles_source')}),
        ('Worker', {'fields': ('attempts', 'locked_by', 'locked_at', 'run_after', 'duration_seconds',
                               'created_at', 'started_at', 'finished_at', 'state'), 'classes': ('collapse',)}),
    )
    inlines = [SpeakerInline, SegmentInline, EventInline]
    actions = ['retry', 'cancel']

    @admin.display(description='Status')
    def status_badge(self, obj):
        colours = {'completed': '#0B6E66', 'failed': '#B42318', 'review': '#B7791F', 'running': '#2B6CB0'}
        return format_html('<b style="color:{}">{}</b>', colours.get(obj.status, '#555'), obj.get_status_display())

    @admin.action(description='Retry selected jobs', permissions=['change'])
    def retry(self, request, queryset):
        count = 0
        for job in queryset.filter(status__in=['failed', 'cancelled']):
            job.attempts = 0
            job.requeue()
            count += 1
        self.message_user(request, f'Requeued {count} job(s).')

    @admin.action(description='Cancel selected jobs', permissions=['change'])
    def cancel(self, request, queryset):
        count = queryset.filter(status__in=['queued', 'running', 'review']).update(cancel_requested=True)
        queryset.filter(status='review').update(status='cancelled')
        self.message_user(request, f'Cancelling {count} job(s).', messages.WARNING)

    def delete_model(self, request, obj):
        delete_job_files(obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for job in queryset:
            delete_job_files(job)
        super().delete_queryset(request, queryset)
