"""The dubbing pipeline. Each stage is resumable: finished stages are skipped on retry.

Ndimi pipeline:
  prepare -> separate -> transcribe -> translate -> [review] -> voices -> synthesize -> mix -> render
ElevenLabs Dubbing API:
  el_submit -> el_wait (non-blocking polling) -> el_download
"""
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import requests
from django.core.files import File
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from .engine.audio import SR, FFmpeg, FFmpegError
from .engine.segmenter import build_lines
from .engine.subtitles import parse_srt, to_srt
from .engine.timing import fit
from .models import DubbingSettings, DubJob, Segment, Speaker
from .providers import ProviderError, get_separator, get_stt, get_translator, get_tts
from .providers.elevenlabs import ElevenLabsDubbing
from .providers.local import NoSeparator

logger = logging.getLogger(__name__)

PIPELINE_STAGES = [
    ('prepare', 'Preparing the video', 3),
    ('separate', 'Separating voices from music and sound', 10),
    ('transcribe', 'Transcribing speech and finding speakers', 25),
    ('translate', 'Translating the script', 40),
    ('review', 'Waiting for script review', 50),
    ('voices', "Matching each speaker's voice", 55),
    ('synthesize', 'Generating the new voices', 60),
    ('mix', 'Syncing and mixing the soundtrack', 90),
    ('render', 'Rendering the final video', 96),
]
ELEVENLABS_STAGES = [
    ('el_submit', 'Uploading to ElevenLabs', 5),
    ('el_wait', 'ElevenLabs is dubbing', 10),
    ('el_download', 'Downloading the dub', 92),
]
ISO3 = {'eng': 'en', 'swa': 'sw', 'kik': 'ki', 'som': 'so', 'fra': 'fr', 'ara': 'ar', 'luo': 'luo', 'kln': 'kln', 'mas': 'mas'}
MAX_ATTEMPTS = 3


class Cancelled(Exception):
    pass


class Paused(Exception):
    """Stop without failing (waiting for review, or polling later)."""

    def __init__(self, status='queued', retry_in=None):
        self.status, self.retry_in = status, retry_in


def stages_for(job):
    return ELEVENLABS_STAGES if job.engine == 'elevenlabs_dubbing' else PIPELINE_STAGES


# ---------- runner ----------

def run_job(job):
    """Run (or resume) a claimed job. Called by the worker."""
    settings = DubbingSettings.load()
    ctx = {'settings': settings, 'ff': FFmpeg(settings.ffmpeg_path)}
    if not job.started_at:
        job.started_at = timezone.now()
        job.save(update_fields=['started_at'])
    stage_funcs = {**PIPELINE_FUNCS, **ELEVENLABS_FUNCS}
    try:
        for name, label, progress in stages_for(job):
            job.refresh_from_db(fields=['cancel_requested', 'state', 'options'])
            if job.cancel_requested:
                raise Cancelled()
            if job.stage_done(name):
                continue
            job.set_stage(name, max(job.progress, progress))
            if name != 'el_wait':
                job.log(f'{label}…', stage=name)
            stage_funcs[name](job, ctx)
            job.mark_stage_done(name)
        _finish(job, 'completed')
        job.log('Dub ready.', stage='render')
    except Paused as pause:
        job.status = pause.status
        job.locked_by, job.locked_at = '', None
        job.run_after = timezone.now() + timedelta(seconds=pause.retry_in) if pause.retry_in else None
        job.save()
    except Cancelled:
        _finish(job, 'cancelled')
        job.log('Cancelled.', level='warning')
    except (ProviderError, FFmpegError, requests.RequestException, OSError, ValueError) as error:
        _fail(job, error)
    except Exception as error:  # unexpected: keep the worker alive, record it
        logger.exception('Dub job %s crashed', job.pk)
        _fail(job, error, retryable=False)


def _finish(job, status):
    job.status = status
    job.locked_by, job.locked_at, job.run_after = '', None, None
    job.finished_at = timezone.now()
    if status == 'completed':
        job.progress = 100
        job.error = ''
    job.save()


def _fail(job, error, retryable=None):
    if retryable is None:
        retryable = getattr(error, 'retryable', False) or isinstance(error, requests.RequestException)
    message = str(error)[:2000]
    job.attempts += 1
    if retryable and job.attempts < MAX_ATTEMPTS:
        wait = 60 * job.attempts
        job.log(f'Temporary problem, retrying in {wait}s: {message}', level='warning')
        job.status = 'queued'
        job.locked_by, job.locked_at = '', None
        job.run_after = timezone.now() + timedelta(seconds=wait)
        job.save()
        return
    job.log(message, level='error')
    job.error = message
    _finish(job, 'failed')


def _files(job):
    return job.state.setdefault('files', {})


def _save_state(job):
    job.save(update_fields=['state', 'updated_at'])


# ---------- source ----------

def _source_path(job):
    if job.source_file:
        return job.source_file.path
    if not job.source_url:
        raise ValueError('This job has no source file or link.')
    # Download direct media links (YouTube pages are only supported by the ElevenLabs engine).
    target = job.workdir / 'download'
    if target.exists():
        return str(target)
    with requests.get(job.source_url, stream=True, timeout=60) as response:
        response.raise_for_status()
        kind = response.headers.get('Content-Type', '')
        if not kind.startswith(('video/', 'audio/', 'application/octet-stream')):
            raise ValueError('That link is a web page, not a video file. Upload the file, or use the ElevenLabs engine for YouTube links.')
        with open(target, 'wb') as fh:
            for chunk in response.iter_content(1024 * 1024):
                fh.write(chunk)
    return str(target)


# ---------- Ndimi pipeline stages ----------

def stage_prepare(job, ctx):
    ff, settings = ctx['ff'], ctx['settings']
    src = _source_path(job)
    info = ff.info(src)
    if not info['has_audio']:
        raise ValueError('This file has no audio track to dub.')
    if info['duration'] > settings.max_duration_minutes * 60:
        raise ValueError(f'The video is longer than {settings.max_duration_minutes} minutes.')
    work = job.workdir
    files = _files(job)
    files['source'] = src
    files['has_video'] = info['has_video']
    files['audio'] = ff.extract_audio(src, str(work / 'audio.wav'))
    files['speech'] = ff.to_speech_mp3(files['audio'], str(work / 'speech.mp3'))
    job.duration_seconds = info['duration']
    job.save(update_fields=['duration_seconds', 'state', 'updated_at'])


def stage_separate(job, ctx):
    ff, settings = ctx['ff'], ctx['settings']
    files = _files(job)
    separator = get_separator(job.options.get('separation') or settings.separation)
    source = files['audio']
    if separator.__class__.__name__ == 'VoiceIsolator':
        source = str(job.workdir / 'audio_upload.mp3')
        ff.run(['-i', files['audio'], '-c:a', 'libmp3lame', '-b:a', '160k', source])
    try:
        result = separator.separate(source, str(job.workdir), ff)
    except ProviderError as error:
        job.log(f'Separation unavailable ({error}). Continuing: the original will be ducked under the new voice.', level='warning')
        result = NoSeparator().separate(files['audio'], str(job.workdir), ff)
    files['vocals'] = result['vocals']
    files['background'] = result['background']
    _save_state(job)


def stage_transcribe(job, ctx):
    settings = ctx['settings']
    files = _files(job)
    stt = get_stt(job.options.get('stt') or settings.stt_provider)
    transcript = stt.transcribe(files['speech'], language=job.source_language or None,
                                num_speakers=job.options.get('num_speakers') or None)
    lines = build_lines(transcript)
    if not lines:
        raise ValueError('No speech was found in this video.')

    detected = ISO3.get(transcript.language, transcript.language)
    with transaction.atomic():
        job.segments.all().delete()
        job.speakers.all().delete()
        labels = sorted({l.speaker for l in lines}, key=lambda s: next(i for i, l in enumerate(lines) if l.speaker == s))
        speakers = {
            label: Speaker.objects.create(job=job, label=label, name=f'Speaker {n}')
            for n, label in enumerate(labels, 1)
        }
        Segment.objects.bulk_create([
            Segment(job=job, index=i, speaker=speakers[l.speaker], start=round(l.start, 3), end=round(l.end, 3), source_text=l.text)
            for i, l in enumerate(lines)
        ])
        if not job.source_language and detected:
            job.source_language = detected[:12]
            job.save(update_fields=['source_language'])
    srt = to_srt((l.start, l.end, l.text) for l in lines)
    job.subtitles_source.save('source.srt', ContentFile(srt.encode('utf-8')), save=True)
    job.log(f'Found {len(lines)} lines from {len(speakers)} speaker(s). Source language: {job.source_language or "unknown"}.')


def stage_translate(job, ctx):
    settings = ctx['settings']
    translator = get_translator(job.options.get('translation') or settings.translation_provider)
    todo = list(job.segments.filter(edited=False).select_related('speaker'))
    if translator is None:
        job.log('No translation provider: add the translations in review.')
        job.options['review'] = True
        job.save(update_fields=['options'])
        return
    lines = [{'i': s.index, 'text': s.source_text, 'duration': s.end - s.start, 'speaker': str(s.speaker or '')} for s in todo]
    results = translator.translate(lines, job.source_language, job.target_language, glossary=job.options.get('glossary'))
    for seg in todo:
        seg.translated_text = results.get(seg.index, seg.translated_text)
        seg.status = 'pending'
    Segment.objects.bulk_update(todo, ['translated_text', 'status'])


def stage_review(job, ctx):
    if job.options.get('review') and not job.state.get('approved'):
        job.log('Script ready for review. Check the translation, then approve to generate voices.')
        raise Paused(status='review')


def stage_voices(job, ctx):
    settings, ff = ctx['settings'], ctx['ff']
    tts = get_tts(job.options.get('tts') or settings.tts_provider)
    files = _files(job)
    clone = job.options.get('clone_voices', settings.clone_voices)
    for speaker in job.speakers.all():
        if speaker.voice_id:
            continue
        if clone:
            spans = list(speaker.segments.values_list('start', 'end'))
            sample = ff.speaker_sample(files['vocals'], spans, str(job.workdir / f'{speaker.label}_sample.mp3'))
            if sample:
                try:
                    speaker.voice_id = tts.clone_voice(f'Ndimi {str(job.pk)[:8]} {speaker}', [sample], language=job.target_language)
                    speaker.cloned = True
                    job.log(f'Cloned the voice of {speaker}.')
                except ProviderError as error:
                    job.log(f'Could not clone {speaker} ({error}). Using the default voice.', level='warning')
            else:
                job.log(f'{speaker} speaks too little to clone. Using the default voice.', level='warning')
        if not speaker.voice_id:
            speaker.voice_id = job.options.get('default_voice_id') or settings.default_voice_id
        if not speaker.voice_id:
            raise ValueError('No voice for this speaker. Set a default voice ID in Admin > Dubbing settings.')
        speaker.save()


def _synthesize_one(tts, seg, voice_id, path, language, model_id, prev_text, next_text):
    try:
        tts.synthesize(seg.translated_text, voice_id, path, language=language, model_id=model_id,
                       previous_text=prev_text, next_text=next_text)
    except ProviderError as error:
        if language and not error.retryable and 'language' in str(error).lower():
            tts.synthesize(seg.translated_text, voice_id, path, language=None, model_id=model_id)
        else:
            raise
    return path


def stage_synthesize(job, ctx):
    settings, ff = ctx['settings'], ctx['ff']
    tts = get_tts(job.options.get('tts') or settings.tts_provider)
    segments = list(job.segments.select_related('speaker').order_by('index'))
    todo = [s for s in segments if s.status != 'ready' and s.translated_text.strip()]
    if not todo:
        return
    by_index = {s.index: s for s in segments}
    out_dir = job.workdir / 'tts'
    out_dir.mkdir(exist_ok=True)
    language = job.target_language if len(job.target_language) == 2 else None
    workers = int(job.options.get('tts_concurrency', 3))

    def work(seg):
        prev_seg, next_seg = by_index.get(seg.index - 1), by_index.get(seg.index + 1)
        path = str(out_dir / f'{seg.index:05d}_{int(timezone.now().timestamp())}.mp3')
        return seg, _synthesize_one(
            tts, seg, seg.speaker.voice_id, path, language, settings.tts_model_id,
            prev_seg.translated_text if prev_seg else None, next_seg.translated_text if next_seg else None,
        )

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for seg, path in pool.map(work, todo):
            samples = ff.load_speech(path)
            duration = len(samples) / SR
            nxt = by_index.get(seg.index + 1)
            speed, overflow = fit(duration, seg.start, seg.end, nxt.start if nxt else None, settings.max_speedup)
            with open(path, 'rb') as fh:
                if seg.audio:
                    seg.audio.delete(save=False)
                seg.audio.save(f'{seg.index:05d}.mp3', File(fh), save=False)
            seg.audio_duration, seg.speed, seg.overflow, seg.status = round(duration, 3), speed, overflow, 'ready'
            seg.save(update_fields=['audio', 'audio_duration', 'speed', 'overflow', 'status'])
            Path(path).unlink(missing_ok=True)
            done += 1
            job.refresh_from_db(fields=['cancel_requested'])
            if job.cancel_requested:
                raise Cancelled()
            job.set_stage('synthesize', 60 + int(28 * done / len(todo)))
    long_lines = [s.index + 1 for s in job.segments.filter(overflow__gt=0.3)]
    if long_lines:
        job.log(f'Lines {", ".join(map(str, long_lines[:15]))} run past their slot. Shorten them for tighter sync.', level='warning')


def stage_mix(job, ctx):
    settings, ff = ctx['settings'], ctx['ff']
    files = _files(job)
    placements = []
    for seg in job.segments.filter(status='ready').exclude(audio=''):
        samples = ff.change_speed(ff.load_speech(seg.audio.path), seg.speed)
        placements.append((samples, seg.start))
    voice = ff.build_voice_track(placements, job.duration_seconds, str(job.workdir / 'voice.wav'))
    keep = job.options.get('keep_background', True)
    files['mix'] = ff.mix(
        voice, str(job.workdir / 'mix.wav'),
        background=files.get('background') if keep else None,
        original=files['audio'] if keep and not files.get('background') else None,
        loudness=settings.target_loudness_lufs,
    )
    _save_state(job)


def stage_render(job, ctx):
    ff = ctx['ff']
    files = _files(job)
    work = job.workdir
    audio_out = ff.encode_audio(files['mix'], str(work / 'dub.m4a'))
    _replace(job.dubbed_audio, f'{_slug(job)}-{job.target_language}.m4a', audio_out)
    if files.get('has_video'):
        video_out = ff.mux(files['source'], files['mix'], str(work / 'dub.mp4'))
        _replace(job.dubbed_video, f'{_slug(job)}-{job.target_language}.mp4', video_out)
    srt = to_srt((s.start, s.end, s.translated_text) for s in job.segments.all())
    if job.subtitles_target:
        job.subtitles_target.delete(save=False)
    job.subtitles_target.save(f'{job.target_language}.srt', ContentFile(srt.encode('utf-8')), save=False)
    job.save()


def _replace(field, name, path):
    if field:
        field.delete(save=False)
    with open(path, 'rb') as fh:
        field.save(name, File(fh), save=False)
    Path(path).unlink(missing_ok=True)


def _slug(job):
    from django.utils.text import slugify
    return slugify(job.name)[:50] or 'dub'


PIPELINE_FUNCS = {
    'prepare': stage_prepare, 'separate': stage_separate, 'transcribe': stage_transcribe,
    'translate': stage_translate, 'review': stage_review, 'voices': stage_voices,
    'synthesize': stage_synthesize, 'mix': stage_mix, 'render': stage_render,
}


# ---------- ElevenLabs Dubbing API stages ----------

def stage_el_submit(job, ctx):
    api = ElevenLabsDubbing()
    source_path = None if (job.source_url and not job.source_file) else job.source_file.path
    dubbing_id, expected = api.start(
        source_path=source_path, source_url=job.source_url or None, source_lang=job.source_language or None,
        target_lang=job.target_language, num_speakers=job.options.get('num_speakers') or 0, name=job.name,
    )
    job.state['el_dubbing_id'] = dubbing_id
    job.state['el_expected'] = expected or 120
    job.state['el_started'] = timezone.now().isoformat()
    _save_state(job)
    job.log(f'ElevenLabs accepted the job (expected about {int(expected or 0)}s).')


def stage_el_wait(job, ctx):
    from datetime import datetime

    api = ElevenLabsDubbing()
    status, error = api.status(job.state['el_dubbing_id'])
    if status == 'dubbed':
        return
    if status == 'failed':
        raise ProviderError(f'ElevenLabs could not dub this video: {error or "unknown error"}')
    started = datetime.fromisoformat(job.state['el_started'])
    elapsed = (timezone.now() - started).total_seconds()
    if elapsed > 4 * 3600:
        raise ProviderError('ElevenLabs took more than 4 hours. Try again later.')
    expected = max(float(job.state.get('el_expected') or 120), 30)
    job.set_stage('el_wait', 10 + int(80 * min(elapsed / expected, 0.97)))
    raise Paused(status='queued', retry_in=15)


def stage_el_download(job, ctx):
    ff = ctx['ff']
    api = ElevenLabsDubbing()
    dubbing_id, lang = job.state['el_dubbing_id'], job.target_language
    tmp = job.workdir / 'el_dub'
    path, content_type = api.download(dubbing_id, lang, str(tmp))
    info = ff.info(path)
    job.duration_seconds = info['duration']
    if info['has_video']:
        _replace(job.dubbed_video, f'{_slug(job)}-{lang}.mp4', path)
    else:
        _replace(job.dubbed_audio, f'{_slug(job)}-{lang}.mp3', path)

    target_srt = api.transcript(dubbing_id, lang)
    source_srt = api.transcript(dubbing_id, job.source_language) if job.source_language else None
    if target_srt:
        job.subtitles_target.save(f'{lang}.srt', ContentFile(target_srt.encode('utf-8')), save=False)
        target_items = parse_srt(target_srt)
        source_items = parse_srt(source_srt) if source_srt else []
        job.segments.all().delete()
        Segment.objects.bulk_create([
            Segment(job=job, index=i, start=s, end=e, translated_text=t, status='ready',
                    source_text=source_items[i][2] if i < len(source_items) else '')
            for i, (s, e, t) in enumerate(target_items)
        ])
    if source_srt:
        job.subtitles_source.save('source.srt', ContentFile(source_srt.encode('utf-8')), save=False)
    job.save()


ELEVENLABS_FUNCS = {'el_submit': stage_el_submit, 'el_wait': stage_el_wait, 'el_download': stage_el_download}


# ---------- cleanup ----------

def delete_job_files(job):
    """Delete cloned voices at the provider and all files for this job."""
    settings = DubbingSettings.load()
    for speaker in job.speakers.filter(cloned=True).exclude(voice_id=''):
        try:
            get_tts(job.options.get('tts') or settings.tts_provider).delete_voice(speaker.voice_id)
        except Exception as error:  # best effort
            logger.warning('Could not delete cloned voice %s: %s', speaker.voice_id, error)
    from django.conf import settings as django_settings
    shutil.rmtree(Path(django_settings.MEDIA_ROOT) / 'dubbing' / 'jobs' / str(job.pk), ignore_errors=True)
