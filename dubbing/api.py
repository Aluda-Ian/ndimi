import json
from datetime import timedelta
from pathlib import Path

from django.http import FileResponse, Http404, JsonResponse
from django.utils import timezone

from projects.models import Language
from projects.views import USE_TOOL, api_permission_required
from system.models import SystemSettings

from .models import DubbingSettings, DubJob
from .pipeline import delete_job_files, stages_for

VIDEO_EXTS = {'mp4', 'mov', 'm4v', 'webm', 'mkv', 'avi', 'mp3', 'wav', 'm4a', 'aac', 'ogg'}


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
        'created_at': job.created_at.isoformat(),
        'finished_at': job.finished_at.isoformat() if job.finished_at else None,
        'editable': job.engine == 'pipeline' and job.status in ('review', 'completed', 'failed'),
        'outputs': {
            'video': _url(job, 'video') if job.dubbed_video else None,
            'audio': _url(job, 'audio') if job.dubbed_audio else None,
            'subtitles': _url(job, 'subtitles') if job.subtitles_target else None,
            'source_subtitles': _url(job, 'source-subtitles') if job.subtitles_source else None,
            'source': _url(job, 'source') if job.source_file else None,
        },
    }
    if detail:
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
    source_url = post.get('source_url', '').strip()
    if not upload and not source_url:
        return JsonResponse({'error': 'Upload a video or paste a link.'}, status=400)
    if upload:
        ext = Path(upload.name).suffix.lower().lstrip('.')
        if ext not in VIDEO_EXTS:
            return JsonResponse({'error': f'Unsupported file type .{ext}. Use MP4, MOV, WEBM, MP3, WAV or M4A.'}, status=400)
        max_mb = SystemSettings.load().max_video_upload_mb
        if upload.size > max_mb * 1024 * 1024:
            return JsonResponse({'error': f'The file is larger than {max_mb} MB.'}, status=400)
    elif not source_url.startswith(('http://', 'https://')):
        return JsonResponse({'error': 'The link must start with https://'}, status=400)

    target = post.get('target_language', '').strip()
    language = Language.objects.filter(code=target, enabled=True).first()
    if language is None:
        return JsonResponse({'error': 'Choose a target language.'}, status=400)
    if not language.available and not user.has_perm('dubbing.choose_dub_engine'):
        return JsonResponse({'error': f'{language.name} is coming soon.'}, status=400)

    requested = post.get('engine') if user.has_perm('dubbing.choose_dub_engine') else None
    engine = settings.resolve_engine(requested, target)
    if engine == 'pipeline' and source_url and not upload and ('youtu' in source_url or 'tiktok' in source_url):
        return JsonResponse({'error': 'For YouTube or TikTok links, upload the video file instead.'}, status=400)

    try:
        glossary = json.loads(post.get('glossary') or '{}')
        assert isinstance(glossary, dict)
    except (json.JSONDecodeError, AssertionError):
        return JsonResponse({'error': 'glossary must be a JSON object like {"Jeota Media": "Jeota Media"}.'}, status=400)

    options = {
        'review': engine == 'pipeline' and _bool(post.get('review'), settings.review_by_default),
        'clone_voices': _bool(post.get('clone_voices'), settings.clone_voices),
        'keep_background': _bool(post.get('keep_background'), True),
        'glossary': glossary,
    }
    if post.get('num_speakers', '').isdigit():
        options['num_speakers'] = max(0, min(32, int(post['num_speakers'])))

    name = (post.get('name') or (upload.name if upload else source_url)).strip()[:200] or 'Untitled dub'
    job = DubJob(owner=user, name=name, source_url=source_url if not upload else '',
                 source_language=post.get('source_language', '').strip()[:12],
                 target_language=target, engine=engine, options=options)
    if upload:
        job.source_file.save(Path(upload.name).name, upload, save=False)
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
def cancel(request, job):
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
    if not _can_modify(request.user, job) or job.engine != 'pipeline':
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
    }.get(kind)
    if not field:
        raise Http404('File not ready.')
    as_attachment = request.GET.get('download') == '1'
    return FileResponse(field.open('rb'), as_attachment=as_attachment, filename=Path(field.name).name)


@api_permission_required(USE_TOOL)
def segment_audio(request, job_id, index):
    job = _get_job(request, job_id)
    seg = job.segments.filter(index=index).first()
    if seg is None or not seg.audio:
        raise Http404('No audio for this line yet.')
    return FileResponse(seg.audio.open('rb'), content_type='audio/mpeg')
