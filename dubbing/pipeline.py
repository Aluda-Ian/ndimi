"""The dubbing pipeline. Each stage is resumable: finished stages are skipped on retry.

Ndimi pipeline:
  prepare -> separate -> transcribe -> translate -> [review] -> voices -> synthesize -> mix -> render
Podcasts (audio-only jobs) run the same stages with natural pacing (engine/podcast.py)
and render a tagged MP3, SRT and a readable transcript.
ElevenLabs Dubbing API:
  el_submit -> el_wait (non-blocking polling) -> el_download   (Dubbing v2 projects API)
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

from .engine import podcast
from .engine.audio import SR, FFmpeg, FFmpegError
from .engine.segmenter import build_lines
from .engine.subtitles import parse_srt, to_srt
from .engine.timing import fit
from .engine.types import Transcript, Word
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


PODCAST_LABELS = {
    'prepare': 'Preparing the episode',
    'separate': 'Separating voices from music',
    'mix': 'Pacing and mixing the episode',
    'render': 'Rendering the MP3 and transcript',
}
MEDIA_SUFFIXES = {'.mp3', '.wav', '.m4a', '.aac', '.ogg', '.opus', '.flac', '.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi'}
WHISPER_MAX_BYTES = 24 * 1024 * 1024  # OpenAI's upload limit is 25 MB
WHISPER_CHUNK_SECONDS = 20 * 60


def stages_for(job):
    if job.engine == 'elevenlabs_dubbing':
        return ELEVENLABS_STAGES
    if job.is_podcast:
        return [(key, PODCAST_LABELS.get(key, label), progress) for key, label, progress in PIPELINE_STAGES]
    return PIPELINE_STAGES


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
    work = job.workdir
    files = _files(job)
    files['source'] = src
    files['has_video'] = info['has_video']
    if job.is_podcast:
        if info['duration'] > settings.max_podcast_minutes * 60:
            raise ValueError(f'The episode is longer than {settings.max_podcast_minutes} minutes.')
    elif info['duration'] > settings.max_duration_minutes * 60:
        raise ValueError(f'The video is longer than {settings.max_duration_minutes} minutes.')
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
    provider = job.options.get('stt') or settings.stt_provider
    stt = get_stt(provider)
    transcript = _transcribe(job, ctx, stt, provider, files['speech'])
    lines = build_lines(transcript)
    if not lines:
        raise ValueError(f'No speech was found in this {"episode" if job.is_podcast else "video"}.')

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


def _transcribe(job, ctx, stt, provider, path):
    """Long podcasts are sent to Whisper in 20-minute parts (its upload limit is 25 MB)."""
    language, speakers = job.source_language or None, job.options.get('num_speakers') or None
    if provider != 'openai' or Path(path).stat().st_size <= WHISPER_MAX_BYTES:
        return stt.transcribe(path, language=language, num_speakers=speakers)
    ff, words, detected, offset = ctx['ff'], [], '', 0.0
    total = job.duration_seconds or ff.info(path)['duration']
    while offset < total:
        part = str(job.workdir / f'speech_part_{int(offset):06d}.mp3')
        ff.run(['-ss', f'{offset:.3f}', '-t', WHISPER_CHUNK_SECONDS, '-i', path, '-c', 'copy', part])
        result = stt.transcribe(part, language=language or detected or None, num_speakers=speakers)
        detected = detected or result.language
        words += [Word(w.start + offset, w.end + offset, w.text, w.speaker) for w in result.words]
        Path(part).unlink(missing_ok=True)
        offset += WHISPER_CHUNK_SECONDS
        job.log(f'Transcribed {podcast.timestamp(min(offset, total))} of {podcast.timestamp(total)}.')
    return Transcript(language=detected, words=words, phrase_level=True)


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
    if job.is_podcast:
        _translate_show_notes(job, translator)


def _translate_show_notes(job, translator):
    """Episode title and description, for the MP3 tags and the transcript. Never fails the job."""
    notes = job.options.get('show_notes') or {}
    title = '' if Path(job.name).suffix.lower() in MEDIA_SUFFIXES else job.name  # a file name is not a title
    items = [(-1, title), (-2, notes.get('description', ''))]
    lines = [{'i': i, 'text': text, 'duration': 120.0, 'speaker': ''} for i, text in items if text.strip()]
    if not lines:
        return
    try:
        results = translator.translate(lines, job.source_language, job.target_language, glossary=job.options.get('glossary'))
    except (ProviderError, requests.RequestException) as error:
        job.log(f'Could not translate the show notes ({error}). The original title is used.', level='warning')
        return
    job.state['show_notes'] = {'title': results.get(-1, ''), 'description': results.get(-2, '')}
    _save_state(job)


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
    natural = job.pacing == 'natural'
    max_speedup = settings.podcast_max_speedup if natural else settings.max_speedup

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
            speed, overflow = fit(duration, seg.start, seg.end, nxt.start if nxt else None, max_speedup)
            if natural:
                overflow = 0.0  # the episode makes room for the line (see stage_mix)
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
    if job.pacing == 'natural':
        return _mix_natural(job, ctx)
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


def _mix_natural(job, ctx):
    """Podcast mix: each line gets the time it needs; the bed (music) is re-timed around it."""
    settings, ff = ctx['settings'], ctx['ff']
    files, work = _files(job), job.workdir
    segments = list(job.segments.order_by('index'))
    ready = {s.index: s for s in segments if s.status == 'ready' and s.audio}

    def clip(index):
        seg = ready.get(index)
        return ff.change_speed(ff.load_speech(seg.audio.path), seg.speed) if seg else None

    durations = {i: (s.audio_duration or 0.0) / max(s.speed, 0.01) for i, s in ready.items()}
    keep = job.options.get('keep_background', True)
    bed_source = files.get('background') or files['audio']
    gate = not files.get('background')  # no clean music stem: use the original with the voices muted

    def read_bed(start, end):
        return ff.load_range(bed_source, start, end, channels=2)

    pieces, starts, total = podcast.plan_timeline(
        [(s.index, s.start, s.end) for s in segments], durations, job.duration_seconds,
        quiet=podcast.quiet_checker(read_bed) if keep else None,
    )
    podcast.render_episode(
        pieces, read_bed=read_bed if keep else None, clip=clip, gate=gate, stretch=ff.change_speed,
        bed_out=str(work / 'bed.raw'), voice_out=str(work / 'voice.raw'),
    )
    bed = ff.raw_to_wav(str(work / 'bed.raw'), str(work / 'bed.wav'), channels=2)
    voice = ff.raw_to_wav(str(work / 'voice.raw'), str(work / 'voice.wav'))
    for raw in ('bed.raw', 'voice.raw'):
        (work / raw).unlink(missing_ok=True)
    files['mix'] = ff.mix(voice, str(work / 'mix.wav'), background=bed if keep else None,
                          loudness=settings.podcast_loudness_lufs)
    timeline = {}
    for seg in segments:
        new_start = starts.get(seg.index, seg.start)
        length = durations.get(seg.index) or (seg.end - seg.start)
        timeline[str(seg.index)] = [round(new_start, 3), round(new_start + length, 3)]
    job.state['timeline'] = timeline
    job.state['output_duration'] = round(total, 3)
    _save_state(job)
    longer = total - (job.duration_seconds or total)
    if longer > 1:
        job.log(f'Natural pacing: the dub runs {podcast.timestamp(longer)} longer than the original '
                f'({100 * longer / job.duration_seconds:.0f}%). Shorter translations bring it closer.')


def _placed(job):
    """(start, end, segment) on the output timeline (podcasts are re-timed in _mix_natural)."""
    timeline = job.state.get('timeline') if job.pacing == 'natural' else None
    for seg in job.segments.select_related('speaker').order_by('index'):
        start, end = (timeline or {}).get(str(seg.index), (seg.start, seg.end))
        yield start, end, seg


def _render_podcast(job, ctx):
    ff, settings = ctx['ff'], ctx['settings']
    files, work = _files(job), job.workdir
    notes = job.state.get('show_notes') or {}
    title = notes.get('title') or (Path(job.name).stem if Path(job.name).suffix.lower() in MEDIA_SUFFIXES else job.name)
    description = notes.get('description') or (job.options.get('show_notes') or {}).get('description', '')
    tags = {
        'title': title,
        'artist': (job.options.get('show_notes') or {}).get('author', ''),
        'album': (job.options.get('show_notes') or {}).get('show', ''),
        'language': job.target_language,
        'comment': description,
        'encoded_by': 'Ndimi (ndimi.jeotamedia.co.ke)',
    }
    mp3 = ff.encode_mp3(files['mix'], str(work / 'dub.mp3'), bitrate_kbps=settings.podcast_bitrate_kbps, tags=tags)
    _replace(job.dubbed_audio, f'{_slug(job)}-{job.target_language}.mp3', mp3)
    placed = list(_placed(job))
    srt = to_srt((start, end, seg.translated_text) for start, end, seg in placed)
    _save_text(job.subtitles_target, f'{job.target_language}.srt', srt)
    text = podcast.transcript_text(
        [(start, str(seg.speaker or 'Speaker'), seg.translated_text) for start, _, seg in placed],
        title=title, notes=description,
    )
    _save_text(job.transcript_target, f'{_slug(job)}-{job.target_language}-transcript.txt', text)
    job.save()


def _save_text(field, name, text):
    if field:
        field.delete(save=False)
    field.save(name, ContentFile(text.encode('utf-8')), save=False)


def stage_render(job, ctx):
    if job.is_podcast:
        return _render_podcast(job, ctx)
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
    source_path = job.source_file.path if job.source_file else None
    project_id, language_id = api.start(
        source_path=source_path, source_url=job.source_url or None, source_lang=job.source_language or None,
        target_lang=job.target_language, name=job.name,
    )
    job.state['el_project_id'] = project_id
    job.state['el_language_id'] = language_id
    job.state['el_started'] = timezone.now().isoformat()
    _save_state(job)
    job.log(f'ElevenLabs accepted the job (project {project_id}).')


def stage_el_wait(job, ctx):
    from datetime import datetime

    api = ElevenLabsDubbing()
    project_id = job.state['el_project_id']
    started = datetime.fromisoformat(job.state['el_started'])
    elapsed = (timezone.now() - started).total_seconds()
    if elapsed > 4 * 3600:
        raise ProviderError('ElevenLabs took more than 4 hours. Try again later.')

    language_id = job.state.get('el_language_id')
    if not language_id:
        project = api.project(project_id)
        if project.get('status') == 'failed':
            raise ProviderError(f'ElevenLabs could not prepare this file: {_el_error(project.get("error"))}')
        if project.get('status') != 'ready':
            job.set_stage('el_wait', 10 + int(30 * min(elapsed / 300, 0.97)))
            raise Paused(status='queued', retry_in=15)
        language_id = api.add_language(project_id, job.target_language)
        job.state['el_language_id'] = language_id
        _save_state(job)

    target = api.language(project_id, language_id)
    status = target.get('status', '')
    if status == 'completed' and (target.get('outputs') or {}).get('lossless_audio'):
        job.state['el_output'] = target['outputs']['lossless_audio']
        _save_state(job)
        return
    if status == 'failed':
        raise ProviderError(f'ElevenLabs could not dub this file: {_el_error(target.get("error"))}')
    job.set_stage('el_wait', 10 + int(80 * min(elapsed / 600, 0.97)))
    raise Paused(status='queued', retry_in=15)


def _el_error(error):
    if isinstance(error, dict):
        return error.get('message') or error.get('detail') or error.get('code') or str(error)
    return error or 'unknown error'


def stage_el_download(job, ctx):
    ff = ctx['ff']
    api = ElevenLabsDubbing()
    project_id, language_id, lang = job.state['el_project_id'], job.state['el_language_id'], job.target_language
    work = job.workdir
    dubbed = str(work / 'el_dub_audio')
    try:
        api.download(job.state['el_output'], dubbed)
    except ProviderError:
        # Signed URLs expire: fetch a fresh one and try once more.
        job.state['el_output'] = (api.language(project_id, language_id).get('outputs') or {}).get('lossless_audio')
        _save_state(job)
        api.download(job.state['el_output'], dubbed)

    # v2 returns audio only. Put it back on the picture when we have the original video.
    source = job.source_file.path if job.source_file else None
    source_info = ff.info(source) if source else {'has_video': False}
    job.duration_seconds = ff.info(dubbed)['duration']
    if source_info['has_video']:
        _replace(job.dubbed_video, f'{_slug(job)}-{lang}.mp4', ff.mux(source, dubbed, str(work / 'el_dub.mp4')))
    else:
        _replace(job.dubbed_audio, f'{_slug(job)}-{lang}.mp3', ff.encode_mp3(dubbed, str(work / 'el_dub.mp3')))

    segments = api.transcript(project_id, language_id)
    if segments:
        items = [(float(s.get('start_s') or 0), float(s.get('end_s') or 0), s.get('translation') or '') for s in segments]
        job.subtitles_target.save(f'{lang}.srt', ContentFile(to_srt(items).encode('utf-8')), save=False)
        source_items = [(a, b, s.get('source_text') or '') for (a, b, _), s in zip(items, segments)]
        if any(t for _, _, t in source_items):
            job.subtitles_source.save('source.srt', ContentFile(to_srt(source_items).encode('utf-8')), save=False)
        job.segments.all().delete()
        Segment.objects.bulk_create([
            Segment(job=job, index=i, start=a, end=b, translated_text=t, status='ready',
                    source_text=segments[i].get('source_text') or '')
            for i, (a, b, t) in enumerate(items)
        ])
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
