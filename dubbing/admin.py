from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from .engine.audio import FFmpeg
from .models import AIModel, DubbingSettings, DubJob, JobEvent, Segment, Speaker
from .pipeline import delete_job_files


@admin.register(DubbingSettings)
class DubbingSettingsAdmin(admin.ModelAdmin):
    readonly_fields = ('ffmpeg_status', 'current_routing', 'updated_at')
    fieldsets = (
        ('Engine', {'fields': ('default_engine', 'elevenlabs_dubbing_languages')}),
        ('Pipeline providers', {
            'fields': ('stt_provider', 'translation_provider', 'tts_provider', 'allow_noncommercial_models',
                       'current_routing', 'separation'),
            'description': 'Automatic (recommended) picks, for each language, the best AI model that is turned on, '
                           'covers the language, has a licence that allows commercial use and has its integration set up. '
                           'If one fails, the next is tried. Manage the list in Settings > AI models.',
        }),
        ('Voices', {'fields': ('tts_model_id', 'clone_voices', 'default_voice_id')}),
        ('Quality', {'fields': ('review_by_default', 'max_speedup', 'target_loudness_lufs')}),
        ('Podcasts', {
            'fields': ('max_podcast_minutes', 'podcast_max_speedup', 'podcast_loudness_lufs', 'podcast_bitrate_kbps'),
            'description': 'Audio-only uploads are dubbed as podcasts: natural pacing (no lip-sync), MP3 with tags, SRT and a readable transcript.',
        }),
        ('Limits', {'fields': ('max_duration_minutes', 'max_jobs_per_user_per_day')}),
        ('Server', {'fields': ('ffmpeg_path', 'ffmpeg_status', 'updated_at')}),
    )

    @admin.display(description='Models in use now (Automatic)')
    def current_routing(self, obj):
        from projects.models import Language

        from .routing import overview

        langs = list(Language.objects.filter(enabled=True).order_by('sort_order', 'name').values_list('code', 'name'))
        lines = [f"{r['name']}: hear {r['stt']} · translate {r['translate']} · voice {r['tts']}" for r in overview(langs, obj)]
        return '\n'.join(lines) or 'No languages yet.'

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


@admin.register(AIModel)
class AIModelAdmin(admin.ModelAdmin):
    list_display = ('name', 'task', 'languages', 'licence', 'commercial_use', 'enabled', 'priority', 'status')
    list_filter = ('task', 'enabled', 'commercial_use', 'runner')
    search_fields = ('name', 'key', 'languages', 'licence')
    readonly_fields = ('status', 'updated_at')
    fieldsets = (
        (None, {
            'fields': ('name', 'key', 'task', 'runner', 'model_ref', 'enabled', 'status'),
            'description': 'Models on "Ndimi model service" run on our GPU server: list the same key in its models.json, '
                           'and set the server in Integrations > Ndimi models.',
        }),
        ('Languages', {
            'fields': ('languages', 'source_languages', 'priority'),
            'description': 'Ndimi codes (sw, ki, luo, kln, mas, so, en…). With a step on Automatic, the usable model with '
                           'the lowest priority number handles each language. Add a language to one of our models only '
                           'after it passes its test bar.',
        }),
        ('Licence', {
            'fields': ('licence', 'commercial_use'),
            'description': 'Customer dubs only use models whose licence allows commercial use. Non-commercial models '
                           '(e.g. NLLB-200, MMS voices) run only when "Allow non-commercial models" is on in Dubbing engine.',
        }),
        ('Quality', {'fields': ('results', 'notes', 'docs_url', 'updated_at')}),
    )

    @admin.display(description='Status')
    def status(self, obj):
        from .routing import runner_ready

        if not obj.enabled:
            return 'Off'
        if not obj.language_set():
            return 'On, but no languages listed yet'
        if not runner_ready(obj.runner):
            return f'Waiting: set up {obj.get_runner_display()} in Integrations'
        if not obj.commercial_use:
            return 'Internal tests only (non-commercial licence)'
        return 'In use where it has the best priority'


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
        ('Output', {'fields': ('dubbed_video', 'dubbed_audio', 'subtitles_target', 'subtitles_source', 'transcript_target')}),
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
