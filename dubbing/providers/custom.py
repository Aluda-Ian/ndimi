"""Your own models behind a simple HTTP API (e.g. trained on AfriVoices-KE).

Configure the "ndimi-models" integration with a base URL and optional bearer token.
The server must implement:

POST {base}/transcribe  multipart: file, language   -> {"language": "luo", "words": [{"start", "end", "text", "speaker"}]}
POST {base}/translate   JSON {"texts": [...], "source", "target"} -> {"texts": [...]}
POST {base}/tts         JSON {"text", "language", "voice"}      -> audio bytes (mp3 or wav)
POST {base}/voices      multipart: files[], name, language      -> {"voice_id": "..."}   (optional)
"""
from pathlib import Path

from ..engine.types import Transcript, Word
from .base import ProviderError, credentials, request

SLUG = 'ndimi-models'


def _auth():
    creds = credentials(SLUG, required=())
    if not creds['base_url']:
        raise ProviderError('Set the base URL of the "ndimi-models" integration.')
    headers = {'Authorization': f'Bearer {creds["api_key"]}'} if creds['api_key'] else {}
    timeout = int(creds['config'].get('timeout_seconds', 300))
    return creds['base_url'].rstrip('/'), headers, timeout


class CustomSTT:
    def transcribe(self, audio_path, language=None, num_speakers=None):
        base, headers, timeout = _auth()
        with open(audio_path, 'rb') as fh:
            body = request('POST', f'{base}/transcribe', headers=headers, data={'language': language or ''},
                           files={'file': (Path(audio_path).name, fh, 'audio/mpeg')}, timeout=timeout).json()
        words = [Word(float(w['start']), float(w['end']), w['text'], w.get('speaker') or 'speaker_0') for w in body['words']]
        return Transcript(language=body.get('language', language or ''), words=words, phrase_level=bool(body.get('phrase_level')))


class CustomTranslator:
    def translate(self, lines, source, target, glossary=None):
        base, headers, timeout = _auth()
        body = request('POST', f'{base}/translate', headers=headers, timeout=timeout,
                       json={'texts': [l['text'] for l in lines], 'source': source, 'target': target,
                             'max_chars': [None for _ in lines], 'glossary': glossary or {}}).json()
        return {l['i']: t for l, t in zip(lines, body['texts'])}


class CustomTTS:
    def clone_voice(self, name, sample_paths, language=None):
        base, headers, timeout = _auth()
        handles = [open(p, 'rb') for p in sample_paths]
        try:
            files = [('files', (Path(p).name, h, 'audio/mpeg')) for p, h in zip(sample_paths, handles)]
            body = request('POST', f'{base}/voices', headers=headers, data={'name': name, 'language': language or ''},
                           files=files, timeout=timeout).json()
        finally:
            for h in handles:
                h.close()
        return body['voice_id']

    def delete_voice(self, voice_id):
        pass

    def synthesize(self, text, voice_id, out_path, *, language=None, **kwargs):
        base, headers, timeout = _auth()
        response = request('POST', f'{base}/tts', headers=headers, timeout=timeout,
                           json={'text': text, 'language': language, 'voice': voice_id})
        Path(out_path).write_bytes(response.content)
        return out_path
