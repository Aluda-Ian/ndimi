"""OpenAI: Whisper speech-to-text and context-aware, timing-aware translation."""
import json
from pathlib import Path

from ..engine.types import Transcript, Word
from .base import ProviderError, credentials, request
from .languages import char_budget, language_name

SLUG = 'openai'
DEFAULT_BASE = 'https://api.openai.com/v1'


def _auth():
    creds = credentials(SLUG)
    base = (creds['base_url'] or DEFAULT_BASE).rstrip('/')
    headers = {'Authorization': f'Bearer {creds["api_key"]}'}
    if creds['config'].get('organization'):
        headers['OpenAI-Organization'] = creds['config']['organization']
    if creds['config'].get('project'):
        headers['OpenAI-Project'] = creds['config']['project']
    return base, headers, creds


class WhisperSTT:
    """POST /v1/audio/transcriptions. Phrase-level timings, no speaker labels (upload limit 25 MB)."""

    def transcribe(self, audio_path, language=None, num_speakers=None):
        base, headers, creds = _auth()
        data = {
            'model': creds['config'].get('stt_model', 'whisper-1'),
            'response_format': 'verbose_json',
            'timestamp_granularities[]': 'segment',
        }
        if language and len(language) == 2:
            data['language'] = language
        with open(audio_path, 'rb') as fh:
            response = request('POST', f'{base}/audio/transcriptions', headers=headers, data=data,
                               files={'file': (Path(audio_path).name, fh, 'audio/mpeg')}, timeout=1800)
        body = response.json()
        words = [
            Word(start=float(s['start']), end=float(s['end']), text=s['text'].strip())
            for s in body.get('segments', []) if s.get('text', '').strip()
        ]
        return Transcript(language=body.get('language', language or ''), words=words, phrase_level=True)


SYSTEM_PROMPT = """You are a professional dubbing translator working with Kenyan languages.
Translate each line from {source} into {target} for voice-over dubbing.

Rules:
- Natural spoken {target}, the way a native speaker would say it on screen. Not word-for-word.
- Keep names, brands and numbers correct. Keep the speaker's tone (formal, casual, excited).
- Each line has a time slot. Keep the translation close to "max_chars" so it can be spoken in time.
  Shorten or rephrase rather than overflow. Never leave a line empty.
- Use the surrounding lines for context, but translate every line separately.
{glossary}
Reply with JSON only: {{"lines": [{{"i": <index>, "text": "<translation>"}}, ...]}}"""


class GPTTranslator:
    batch_size = 40

    def translate(self, lines, source, target, glossary=None):
        """lines: list of dicts {i, text, duration, speaker}. Returns {i: translation}."""
        base, headers, creds = _auth()
        model = creds['config'].get('translation_model') or 'gpt-4o-mini'
        glossary_text = ''
        if glossary:
            glossary_text = '- Always use these translations: ' + '; '.join(f'"{k}" → "{v}"' for k, v in glossary.items())
        system = SYSTEM_PROMPT.format(source=language_name(source), target=language_name(target), glossary=glossary_text)
        results = {}
        for start in range(0, len(lines), self.batch_size):
            batch = lines[start:start + self.batch_size]
            context = [l['text'] for l in lines[max(0, start - 5):start]]
            payload = {
                'previous_lines_for_context': context,
                'lines': [
                    {'i': l['i'], 'speaker': l.get('speaker', ''), 'text': l['text'],
                     'max_chars': char_budget(l['duration'], target)}
                    for l in batch
                ],
            }
            body = {
                'model': model,
                'response_format': {'type': 'json_object'},
                'messages': [
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
                ],
            }
            if creds['config'].get('temperature') is not None:
                body['temperature'] = creds['config']['temperature']
            response = request('POST', f'{base}/chat/completions', headers=headers, json=body, timeout=300)
            content = response.json()['choices'][0]['message']['content']
            try:
                parsed = json.loads(content)['lines']
                for item in parsed:
                    results[int(item['i'])] = str(item['text']).strip()
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                raise ProviderError(f'OpenAI returned an unexpected translation format: {content[:200]}', retryable=True)
        missing = [l['i'] for l in lines if not results.get(l['i'])]
        if missing:
            raise ProviderError(f'Translation missing for lines {missing[:10]}', retryable=True)
        return results
