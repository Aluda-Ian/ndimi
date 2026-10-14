import json
from datetime import timedelta
from pathlib import Path

from django.core.files.storage import default_storage
from django.http import Http404, JsonResponse
from django.utils import timezone

from projects.models import Language
from projects.views import USE_TOOL, api_permission_required
from system.models import SystemSettings

from .models import DubbingSettings, DubJob
from .pipeline import convert_to_pipeline, delete_job_files, stages_for
from .providers import sync as sync_provider
from .storage import is_remote, new_upload_key, presigned_put, serve, upload_key_belongs_to

AUDIO_EXTS = {'mp3', 'wav', 'm4a', 'aac', 'ogg', 'opus', 'flac'}
VIDEO_EXTS = {'mp4', 'mov', 'm4v', 'webm', 'mkv', 'avi'} | AUDIO_EXTS
KINDS = ('auto', 'video', 'podcast')


# ---------- helpers ----------

def _json(request):
    try:
        return json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return None


def _bool(value, default=False):
    if value is None or value == '':
        return default
    return str(value).lower() in ('1', 'true', 'yes', 'on')


def _visible_jobs(user):
    qs = DubJob.objects.all()
    return qs if user.has_perm('dubbing.view_all_dubjobs') else qs.filter(owner=user)


def _get_job(request, job_id):
    job = _visible_jobs(request.user).filter(pk=job_id).first()
    if job is None:
        raise Http404('Dub not found.')
    return job


def _can_modify(user, job):
    return job.owner_id == user.pk or user.has_perm('dubbing.change_dubjob')


def _url(job, kind):
    return f'/api/dubs/{job.pk}/download/{kind}/'


def _stages(job):
    done = set(job.state.get('done', []))
    out = []
    for key, label, _ in stages_for(job):
        if key == 'review' and not job.options.get('review'):
            continue
        if key in done:
            state = 'done'
        elif key == job.stage and job.status in ('running', 'queued', 'review'):
            state = 'active'
        else:
            state = 'pending'
        out.append({'key': key, 'label': label, 'state': state})
    return out


def job_dict(job, detail=False):
    data = {
        'id': str(job.pk),
        'name': job.name,
        'owner': job.owner.get_username(),
        'status': job.status,
        'stage': job.stage,
        'progress': job.progress,
        'error': job.error,
        'engine': job.engine,
        'source_language': job.source_language,
        'target_language': job.target_language,
        'duration': job.duration_seconds,
        'kind': 'podcast' if job.is_podcast else 'video',
        'pacing': job.pacing,
        'created_at': job.created_at.isoformat(),
        'finished_at': job.finished_at.isoformat() if job.finished_at else None,
        'editable': (job.engine == 'pipeline' and job.status in ('review', 'completed', 'failed'))
                    or (job.engine == 'elevenlabs_dubbing' and job.status == 'completed'),
        'redub_needed': job.engine == 'elevenlabs_dubbing' and job.segments.filter(edited=True).exists(),
        'outputs': {
            'video': _url(job, 'video') if job.dubbed_video else None,
            'audio': _url(job, 'audio') if job.dubbed_audio else None,
            'subtitles': _url(job, 'subtitles') if job.subtitles_target else None,
            'source_subtitles': _url(job, 'source-subtitles') if job.subtitles_source else None,
            'source': _url(job, 'source') if job.source_file else None,
            'transcript': _url(job, 'transcript') if job.transcript_target else None,
            'lipsync_video': _url(job, 'lipsync') if job.lipsync_video else None,
        },
    }
    if job.is_podcast:
        data['podcast'] = {
            'output_duration': job.state.get('output_duration'),
            'show_notes': job.state.get('show_notes') or {},
        }
    if detail:
        info = job.state.get('lipsync') or {}
        data['lipsync'] = {
            'status': info.get('status', 'done' if job.lipsync_video else 'none'),
            'error': info.get('error', ''),
            # Possible when there is a dubbed video and the original video to re-animate.
            'available': bool(job.dubbed_video and job.source_file and is_remote()),
            'configured': sync_provider.settings() is not None,
            'estimate_usd': sync_provider.estimate_usd(job.duration_seconds),
        }
        data['stages'] = _stages(job)
        data['speakers'] = [
            {'id': s.pk, 'label': s.label, 'name': s.name, 'voice_id': s.voice_id, 'cloned': s.cloned}
            for s in job.speakers.all()
        ]
        data['segments'] = [
            {
                'index': s.index, 'speaker_id': s.speaker_id, 'start': s.start, 'end': s.end,
                'source_text': s.source_text, 'translated_text': s.translated_text, 'status': s.status,
                'speed': s.speed, 'overflow': s.overflow, 'edited': s.edited,
                'audio': f'/api/dubs/{job.pk}/segments/{s.index}/audio/' if s.audio else None,
            }
            for s in job.segments.all()
        ]
        data['events'] = [
            {'at': e.at.isoformat(), 'level': e.level, 'stage': e.stage, 'message': e.message}
            for e in job.events.order_by('-at', '-id')[:30]
        ][::-1]
    return data


# ---------- list / create ----------

def _check_upload(filename, size):
    ext = Path(filename).suffix.lower().lstrip('.')
    if ext not in VIDEO_EXTS:
        return f'Unsupported file type .{ext}. Use MP4, MOV, WEBM, MP3, WAV, M4A or FLAC.'
    max_mb = SystemSettings.load().max_video_upload_mb
    if size > max_mb * 1024 * 1024:
        return f'The file is larger than {max_mb} MB.'
    return None


@api_permission_required(USE_TOOL)
def upload_url(request):
    """Step 1 of a direct upload: a signed R2 URL the browser PUTs the file to.

    Body: {"filename": "...", "size": bytes, "content_type": "video/mp4"}.
    Returns {"mode": "direct", "key", "url", "headers"}; then POST /api/dubs/ with upload_key=key.
    Without R2 (local development) returns {"mode": "form"}: send the file in the normal form instead.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    if not request.user.has_perm('dubbing.add_dubjob'):
        return JsonResponse({'error': "Your account can't create dubs yet. Ask an admin."}, status=403)
    if not is_remote():
        return JsonResponse({'mode': 'form'})
    body = _json(request) or {}
    filename = str(body.get('filename') or '').strip()
    try:
        size = int(body.get('size') or 0)
    except (TypeError, ValueError):
        size = 0
    if not filename or size <= 0:
        return JsonResponse({'error': 'Send filename and size.'}, status=400)
    problem = _check_upload(filename, size)
    if problem:
        return JsonResponse({'error': problem}, status=400)
    content_type = str(body.get('content_type') or 'application/octet-stream')[:100]
    key = new_upload_key(request.user, filename)
    return JsonResponse({'mode': 'direct', 'key': key, 'url': presigned_put(key, content_type),
                         'headers': {'Content-Type': content_type}})

@api_permission_required(USE_TOOL)
def dubs(request):
    if request.method == 'GET':
        jobs = _visible_jobs(request.user).select_related('owner')[:100]
        return JsonResponse({'dubs': [job_dict(j) for j in jobs]})
    if request.method == 'POST':
        return _create(request)
    return JsonResponse({'error': 'Method not allowed.'}, status=405)


def _create(request):
    user = request.user
    if not user.has_perm('dubbing.add_dubjob'):
        return JsonResponse({'error': "Your account can't create dubs yet. Ask an admin."}, status=403)
    settings = DubbingSettings.load()

    if settings.max_jobs_per_user_per_day and not user.is_superuser:
        today = DubJob.objects.filter(owner=user, created_at__gte=timezone.now() - timedelta(days=1)).count()
        if today >= settings.max_jobs_per_user_per_day:
            return JsonResponse({'error': f'Daily limit reached ({settings.max_jobs_per_user_per_day} dubs per day).'}, status=429)

    post = request.POST
    upload = request.FILES.get('file')
    upload_key = post.get('upload_key', '').strip()
    source_url = post.get('source_url', '').strip()
    if upload_key:
        # The file was already uploaded straight to storage (see upload_url).
        if not upload_key_belongs_to(user, upload_key) or not default_storage.exists(upload_key):
            return JsonResponse({'error': 'The upload was not found. Upload the file again.'}, status=400)
        problem = _check_upload(upload_key, default_storage.size(upload_key))
        if problem:
            default_storage.delete(upload_key)
            return JsonResponse({'error': problem}, status=400)
        upload = None
    upload_name = upload.name if upload else (Path(upload_key).name if upload_key else '')
    has_file = bool(upload or upload_key)
    if not has_file and not source_url:
        return JsonResponse({'error': 'Upload a video or podcast episode, or paste a link.'}, status=400)
    if upload:
        problem = _check_upload(upload.name, upload.size)
        if problem:
            return JsonResponse({'error': problem}, status=400)
    elif not has_file and not source_url.startswith(('http://', 'https://')):
        return JsonResponse({'error': 'The link must start with https://'}, status=400)

    target = post.get('target_language', '').strip()
    language = Language.objects.filter(code=target, enabled=True).first()
    if language is None:
        return JsonResponse({'error': 'Choose a target language.'}, status=400)
    if not language.available and not user.has_perm('dubbing.choose_dub_engine'):
        return JsonResponse({'error': f'{language.name} is coming soon.'}, status=400)

    requested = post.get('engine') if user.has_perm('dubbing.choose_dub_engine') else None
    engine = settings.resolve_engine(requested, target)
    if engine == 'elevenlabs_dubbing' and not requested and str(post.get('review', '')).lower() == 'true':
        engine = 'pipeline'  # ElevenLabs Dubbing can't pause for script review; the Ndimi pipeline can
    if engine == 'pipeline' and source_url and not has_file and ('youtu' in source_url or 'tiktok' in source_url):
        return JsonResponse({'error': 'For YouTube or TikTok links, upload the video file instead.'}, status=400)

    try:
        glossary = json.loads(post.get('glossary') or '{}')
        assert isinstance(glossary, dict)
    except (json.JSONDecodeError, AssertionError):
        return JsonResponse({'error': 'glossary must be a JSON object like {"Jeota Media": "Jeota Media"}.'}, status=400)

    kind = post.get('kind', 'auto').strip().lower() or 'auto'
    if kind not in KINDS:
        return JsonResponse({'error': 'kind must be auto, video or podcast.'}, status=400)
    if kind == 'auto' and has_file and Path(upload_name).suffix.lower().lstrip('.') in AUDIO_EXTS:
        kind = 'podcast'
    pacing = post.get('pacing', '').strip().lower()
    if pacing and pacing not in ('natural', 'sync'):
        return JsonResponse({'error': 'pacing must be natural or sync.'}, status=400)

    options = {
        'kind': kind,
        'review': engine == 'pipeline' and _bool(post.get('review'), settings.review_by_default),
        'clone_voices': _bool(post.get('clone_voices'), settings.clone_voices),
        'keep_background': _bool(post.get('keep_background'), True),
        'glossary': glossary,
    }
    if pacing:
        options['pacing'] = pacing
    show_notes = {key: post.get(key, '').strip()[:limit]
                  for key, limit in (('description', 4000), ('show', 200), ('author', 200))}
    if any(show_notes.values()):
        options['show_notes'] = show_notes
    if post.get('num_speakers', '').isdigit():
        options['num_speakers'] = max(0, min(32, int(post['num_speakers'])))

    name = (post.get('name') or upload_name or source_url).strip()[:200] or 'Untitled dub'
    job = DubJob(owner=user, name=name, source_url=source_url if not has_file else '',
                 source_language=post.get('source_language', '').strip()[:12],
                 target_language=target, engine=engine, options=options)
    if upload:
        job.source_file.save(Path(upload.name).name, upload, save=False)
    elif upload_key:
        job.source_file.name = upload_key
    job.save()
    job.log(f'Queued ({"ElevenLabs Dubbing" if engine == "elevenlabs_dubbing" else "Ndimi pipeline"}, {language.name}).', stage='queued')
    return JsonResponse({'dub': job_dict(job, detail=True)}, status=201)


# ---------- one job ----------

@api_permission_required(USE_TOOL)
def dub_detail(request, job_id):
    job = _get_job(request, job_id)
    if request.method == 'GET':
        return JsonResponse({'dub': job_dict(job, detail=True)})
    if request.method == 'DELETE':
        if not _can_modify(request.user, job):
            return JsonResponse({'error': "You can't delete this dub."}, status=403)
        if job.status == 'running':
            return JsonResponse({'error': 'Cancel the dub first, then delete it.'}, status=409)
        delete_job_files(job)
        job.delete()
        return JsonResponse({'deleted': True})
    return JsonResponse({'error': 'Method not allowed.'}, status=405)


def _post_action(view):
    def wrapper(request, job_id, *args, **kwargs):
        if request.method != 'POST':
            return JsonResponse({'error': 'Method not allowed.'}, status=405)
        job = _get_job(request, job_id)
        if not _can_modify(request.user, job):
            return JsonResponse({'error': "You can't change this dub."}, status=403)
        return view(request, job, *args, **kwargs)
    return api_permission_required(USE_TOOL)(wrapper)


@_post_action
def approve(request, job):
    if job.status != 'review':
        return JsonResponse({'error': 'This dub is not waiting for review.'}, status=409)
    empty = job.segments.filter(translated_text='').count()
    if empty:
        return JsonResponse({'error': f'{empty} line(s) have no translation yet.'}, status=400)
    job.state['approved'] = True
    job.mark_stage_done('review')
    job.requeue()
    job.log(f'Script approved by {request.user.get_username()}.', stage='review')
    return JsonResponse({'dub': job_dict(job, detail=True)})


@_post_action
def regenerate(request, job):
    """Re-voice edited lines (or the given line indexes) and re-render."""
    if job.engine != 'pipeline':
        return JsonResponse({'error': 'Only Ndimi pipeline dubs can be edited line by line.'}, status=400)
    if job.status in DubJob.ACTIVE:
        return JsonResponse({'error': 'This dub is still processing.'}, status=409)
    body = _json(request) or {}
    indexes = body.get('segments')
    if indexes:
        job.segments.filter(index__in=indexes).update(status='stale')
    if not job.segments.filter(status__in=['stale', 'pending']).exclude(translated_text='').exists():
        return JsonResponse({'error': 'No changed lines to regenerate.'}, status=400)
    job.state['approved'] = True
    job.reset_stages('synthesize', 'mix', 'render')
    if not job.stage_done('review'):
        job.mark_stage_done('review')
    job.requeue()
    job.log(f'Regenerating changed lines (requested by {request.user.get_username()}).', stage='synthesize')
    return JsonResponse({'dub': job_dict(job, detail=True)})


@_post_action
def redub(request, job):
    """Voice an ElevenLabs Dubbing job again from its edited script (cloned voices, ElevenLabs TTS)."""
    if job.engine != 'elevenlabs_dubbing':
        return JsonResponse({'error': 'Use Regenerate: this dub already re-voices only the lines you change.'}, status=400)
    if job.status != 'completed':
        return JsonResponse({'error': 'Wait until the dub has finished.'}, status=409)
    if not job.source_file:
        return JsonResponse({'error': 'Redubbing needs the original file. This dub was made from a link: '
                                      'upload the video file and dub it again.'}, status=400)
    if not job.segments.exclude(translated_text='').exists():
        return JsonResponse({'error': 'This dub has no script to voice.'}, status=400)
    convert_to_pipeline(job)
    job.requeue()
    edited = job.segments.filter(edited=True).count()
    job.log(f'Redub with the edited script requested by {request.user.get_username()} ({edited} edited line(s)).',
            stage='prepare')
    return JsonResponse({'dub': job_dict(job, detail=True)})


@_post_action
def lipsync(request, job):
    """Re-animate the mouths in the finished dub to match the new voice (Sync). Charged per second by Sync."""
    if job.status in DubJob.ACTIVE:
        return JsonResponse({'error': 'This dub is still processing.'}, status=409)
    if job.status != 'completed' or not job.dubbed_video:
        return JsonResponse({'error': 'Lip sync works on a finished dubbed video.'}, status=400)
    if not job.source_file:
        return JsonResponse({'error': 'Lip sync needs the original video file. Dub an uploaded video, not a link.'}, status=400)
    if not is_remote():
        return JsonResponse({'error': 'Lip sync needs cloud storage (R2) so the lip sync service can fetch the video.'}, status=400)
    if sync_provider.settings() is None:
        return JsonResponse({'error': 'Turn on "Sync (lip sync)" in Settings > Integrations and add its API key first.'}, status=400)
    previous = job.state.get('lipsync') or {}
    job.state['lipsync'] = {'status': 'queued', 'round': int(previous.get('round', 0)) + 1}
    job.options['lip_sync'] = True
    job.attempts = 0
    job.save(update_fields=['state', 'options', 'attempts', 'updated_at'])
    job.reset_stages('ls_submit', 'ls_wait', 'ls_download')
    job.progress, job.stage = 88, 'ls_submit'
    job.requeue()
    job.log(f'Lip sync requested by {request.user.get_username()} '
            f'(about ${sync_provider.estimate_usd(job.duration_seconds)}).', stage='ls_submit')
    return JsonResponse({'dub': job_dict(job, detail=True)})


@_post_action
def cancel(request, job):
    if job.status == 'queued' and str(job.stage).startswith('ls_') and job.dubbed_video:
        # Stopping a lip sync keeps the finished dub.
        job.state.setdefault('lipsync', {}).update(status='failed', error='Cancelled.')
        job.state['done'] = list(set(job.state.get('done', [])) | {'ls_submit', 'ls_wait', 'ls_download'})
        job.status, job.progress, job.run_after = 'completed', 100, None
        job.finished_at = timezone.now()
        job.save()
        job.log(f'Lip sync cancelled by {request.user.get_username()}.', level='warning')
        return JsonResponse({'dub': job_dict(job, detail=True)})
    if job.status == 'review' or job.status == 'queued':
        job.status = 'cancelled'
        job.finished_at = timezone.now()
        job.save()
    elif job.status == 'running':
        job.cancel_requested = True
        job.save(update_fields=['cancel_requested'])
    else:
        return JsonResponse({'error': 'This dub has already finished.'}, status=409)
    return JsonResponse({'dub': job_dict(job, detail=True)})


@_post_action
def retry(request, job):
    if job.status not in ('failed', 'cancelled'):
        return JsonResponse({'error': 'Only failed or cancelled dubs can be retried.'}, status=409)
    job.attempts = 0
    job.requeue()
    job.log('Retry requested.', stage=job.stage)
    return JsonResponse({'dub': job_dict(job, detail=True)})


@api_permission_required(USE_TOOL)
def segment_detail(request, job_id, index):
    if request.method != 'PATCH':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    job = _get_job(request, job_id)
    if not _can_modify(request.user, job) or not job_dict(job)['editable']:
        return JsonResponse({'error': "You can't edit this dub."}, status=403)
    if job.status in DubJob.ACTIVE:
        return JsonResponse({'error': 'Wait until processing stops, then edit.'}, status=409)
    body = _json(request)
    if body is None:
        return JsonResponse({'error': 'Send JSON.'}, status=400)
    seg = job.segments.filter(index=index).first()
    if seg is None:
        raise Http404('Line not found.')
    changed = False
    if 'translated_text' in body:
        text = str(body['translated_text']).strip()
        if text != seg.translated_text:
            seg.translated_text, changed = text, True
    if 'speaker_id' in body:
        speaker = job.speakers.filter(pk=body['speaker_id']).first()
        if speaker and speaker.pk != seg.speaker_id:
            seg.speaker, changed = speaker, True
    for field in ('start', 'end'):
        if field in body:
            try:
                value = round(float(body[field]), 3)
            except (TypeError, ValueError):
                return JsonResponse({'error': f'{field} must be a number of seconds.'}, status=400)
            if value != getattr(seg, field):
                setattr(seg, field, value)
                changed = True
    if seg.end <= seg.start:
        return JsonResponse({'error': 'The line must end after it starts.'}, status=400)
    if changed:
        seg.edited = True
        if seg.status == 'ready':
            seg.status = 'stale'
        seg.save()
    return JsonResponse({'segment': {'index': seg.index, 'translated_text': seg.translated_text,
                                     'status': seg.status, 'speaker_id': seg.speaker_id, 'edited': seg.edited}})


@api_permission_required(USE_TOOL)
def speaker_detail(request, job_id, speaker_id):
    if request.method != 'PATCH':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    job = _get_job(request, job_id)
    if not _can_modify(request.user, job):
        return JsonResponse({'error': "You can't edit this dub."}, status=403)
    speaker = job.speakers.filter(pk=speaker_id).first()
    if speaker is None:
        raise Http404('Speaker not found.')
    body = _json(request) or {}
    if 'name' in body:
        speaker.name = str(body['name'])[:80]
    if 'voice_id' in body and body['voice_id'] != speaker.voice_id:
        speaker.voice_id = str(body['voice_id'])[:80]
        speaker.cloned = False
        speaker.segments.filter(status='ready').update(status='stale')
    speaker.save()
    return JsonResponse({'speaker': {'id': speaker.pk, 'name': speaker.name, 'voice_id': speaker.voice_id}})


# ---------- files ----------

@api_permission_required(USE_TOOL)
def download(request, job_id, kind):
    job = _get_job(request, job_id)
    field = {
        'video': job.dubbed_video, 'audio': job.dubbed_audio, 'subtitles': job.subtitles_target,
        'source-subtitles': job.subtitles_source, 'source': job.source_file,
        'transcript': job.transcript_target, 'lipsync': job.lipsync_video,
    }.get(kind)
    if not field:
        raise Http404('File not ready.')
    as_attachment = request.GET.get('download') == '1'
    return serve(field, as_attachment=as_attachment)


@api_permission_required(USE_TOOL)
def segment_audio(request, job_id, index):
    job = _get_job(request, job_id)
    seg = job.segments.filter(index=index).first()
    if seg is None or not seg.audio:
        raise Http404('No audio for this line yet.')
    return serve(seg.audio, content_type='audio/mpeg')
