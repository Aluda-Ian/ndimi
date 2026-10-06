"""ElevenLabs: Scribe speech-to-text, voice isolator, voice cloning, text-to-speech and the Dubbing API.

API reference: https://elevenlabs.io/docs/api-reference
"""
from pathlib import Path

from ..engine.types import Transcript, Word
from .base import ProviderError, credentials, request

SLUG = 'elevenlabs'
DEFAULT_BASE = 'https://api.elevenlabs.io/v1'


def _auth():
    creds = credentials(SLUG)
    base = (creds['base_url'] or DEFAULT_BASE).rstrip('/')
    return base, {'xi-api-key': creds['api_key']}, creds


class ScribeSTT:
    """POST /v1/speech-to-text with speaker diarization and word timestamps."""

    def transcribe(self, audio_path, language=None, num_speakers=None):
        base, headers, creds = _auth()
        data = {
            'model_id': creds['config'].get('stt_model_id', 'scribe_v2'),
            'diarize': 'true',
            'timestamps_granularity': 'word',
            'tag_audio_events': 'false',
        }
        if language:
            data['language_code'] = language
        if num_speakers:
            data['num_speakers'] = str(num_speakers)
        with open(audio_path, 'rb') as fh:
            response = request('POST', f'{base}/speech-to-text', headers=headers, data=data,
                               files={'file': (Path(audio_path).name, fh, 'audio/wav')}, timeout=1800)
        body = response.json()
        words = [
            Word(start=float(w['start']), end=float(w['end']), text=w['text'], speaker=w.get('speaker_id') or 'speaker_0')
            for w in body.get('words', [])
            if w.get('type', 'word') == 'word' and w.get('start') is not None
        ]
        return Transcript(language=body.get('language_code') or language or '', words=words)


class VoiceIsolator:
    """POST /v1/audio-isolation. Returns only the clean voice; the original is ducked for the background."""

    provides_background = False

    def separate(self, audio_path, workdir, ffmpeg):
        base, headers, _ = _auth()
        with open(audio_path, 'rb') as fh:
            response = request('POST', f'{base}/audio-isolation', headers=headers,
                               files={'audio': (Path(audio_path).name, fh, 'audio/wav')}, timeout=1800)
        vocals = Path(workdir) / 'vocals_isolated.mp3'
        vocals.write_bytes(response.content)
        return {'vocals': str(vocals), 'background': None}


class ElevenLabsTTS:
    """Voice cloning (POST /v1/voices/add) and speech (POST /v1/text-to-speech/{voice_id})."""

    def clone_voice(self, name, sample_paths, language=None):
        base, headers, _ = _auth()
        handles = [open(p, 'rb') for p in sample_paths]
        try:
            files = [('files', (Path(p).name, h, 'audio/mpeg')) for p, h in zip(sample_paths, handles)]
            data = {'name': name[:90], 'remove_background_noise': 'false', 'description': 'Ndimi dub speaker clone'}
            if language:
                data['labels'] = f'{{"language": "{language}"}}'
            response = request('POST', f'{base}/voices/add', headers=headers, data=data, files=files, timeout=600)
        finally:
            for h in handles:
                h.close()
        voice_id = response.json().get('voice_id')
        if not voice_id:
            raise ProviderError('ElevenLabs did not return a voice ID for the clone.')
        return voice_id

    def delete_voice(self, voice_id):
        base, headers, _ = _auth()
        request('DELETE', f'{base}/voices/{voice_id}', headers=headers, retries=2, timeout=60)

    def synthesize(self, text, voice_id, out_path, *, language=None, model_id='eleven_v3',
                   previous_text=None, next_text=None, seed=None):
        base, headers, creds = _auth()
        body = {'text': text, 'model_id': model_id}
        if language:
            body['language_code'] = language
        if seed is not None:
            body['seed'] = seed
        # Continuity context keeps intonation consistent across lines (not used by every model).
        if model_id != 'eleven_v3':
            if previous_text:
                body['previous_text'] = previous_text
            if next_text:
                body['next_text'] = next_text
        settings = creds['config'].get('voice_settings')
        if settings:
            body['voice_settings'] = settings
        response = request('POST', f'{base}/text-to-speech/{voice_id}', headers={**headers, 'Accept': 'audio/mpeg'},
                           params={'output_format': 'mp3_44100_128'}, json=body, timeout=300)
        Path(out_path).write_bytes(response.content)
        return out_path


# Ndimi stores some languages as ISO 639-3 codes; Dubbing v2 wants BCP-47 base tags.
BCP47 = {'eng': 'en', 'swa': 'sw', 'swh': 'sw', 'kik': 'ki', 'som': 'so', 'fra': 'fr', 'ara': 'ar'}


def bcp47(code):
    code = (code or '').strip()
    return BCP47.get(code.lower(), code) if code and code.lower() != 'auto' else ''


class ElevenLabsDubbing:
    """Dubbing v2 (projects API). The old POST /v1/dubbing endpoint is legacy and rejects
    languages that only v2 supports (it answers "language code sw is not supported").

    Flow: POST /v1/dubbing/project (with target_language, which also creates the language target)
    -> GET /v1/dubbing/project/{id} until ready -> GET .../language/{language_id} until completed
    -> download outputs.lossless_audio (signed URL) -> GET .../language/{language_id}/transcript.
    """

    def start(self, *, source_path=None, source_url=None, source_lang=None, target_lang, name=''):
        base, headers, creds = _auth()
        data = {
            'target_language': bcp47(target_lang),
            'model_id': creds['config'].get('dubbing_model_id', 'dubbing_v2'),
            'reference': (name or '')[:500],
        }
        if bcp47(source_lang):
            data['source_language'] = bcp47(source_lang)
        if source_url and not source_path:
            data['source_url'] = source_url
            response = request('POST', f'{base}/dubbing/project', headers=headers, data=data, timeout=600)
        else:
            with open(source_path, 'rb') as fh:
                response = request('POST', f'{base}/dubbing/project', headers=headers, data=data,
                                   files={'file': (Path(source_path).name, fh, 'application/octet-stream')}, timeout=3600)
        body = response.json()
        language_ids = body.get('language_ids') or []
        return body['project_id'], (language_ids[0] if language_ids else None)

    def project(self, project_id):
        base, headers, _ = _auth()
        return request('GET', f'{base}/dubbing/project/{project_id}', headers=headers, timeout=60).json()

    def add_language(self, project_id, target_lang):
        base, headers, _ = _auth()
        body = request('POST', f'{base}/dubbing/project/{project_id}/language', headers=headers,
                       json={'target_language': bcp47(target_lang)}, timeout=120).json()
        return body['language_id']

    def language(self, project_id, language_id):
        base, headers, _ = _auth()
        return request('GET', f'{base}/dubbing/project/{project_id}/language/{language_id}',
                       headers=headers, timeout=60).json()

    def download(self, url, out_path):
        """outputs.* are signed URLs: no API key needed (and none is sent to the storage host)."""
        response = request('GET', url, timeout=1800, stream=True)
        with open(out_path, 'wb') as fh:
            for chunk in response.iter_content(1024 * 1024):
                fh.write(chunk)
        return out_path

    def transcript(self, project_id, language_id):
        """Segments with start_s, end_s, source_text, translation, speaker_id (or [] if unavailable)."""
        base, headers, _ = _auth()
        try:
            body = request('GET', f'{base}/dubbing/project/{project_id}/language/{language_id}/transcript',
                           headers=headers, retries=2, timeout=120).json()
        except ProviderError:
            return []  # subtitles are optional
        return body.get('segments') or []
