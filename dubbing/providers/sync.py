"""Sync (sync.so): lip sync. Re-animates the speaker's mouth in the original video to match the dubbed audio.

API: POST {base}/generate with {"model", "input": [{"type": "video", "url"}, {"type": "audio", "url"}], "options"},
then GET {base}/generate/{id} until status is COMPLETED (outputUrl), FAILED or REJECTED.
Both inputs must be URLs the service can download, so lip sync needs cloud storage (R2).
Docs: https://sync.so/docs/api-reference/api-overview
"""
from .base import ProviderError, credentials, request

SLUG = 'sync'
DEFAULT_BASE = 'https://api.sync.so/v2'
DEFAULT_PRICE_PER_SECOND = 0.05  # lipsync-2, Hobbyist/Creator plans (Oct 2026)


def settings():
    """Config of the Sync integration, or None when it isn't turned on."""
    from system.models import Integration

    creds = Integration.credentials(SLUG)
    if not creds or not creds.get('api_key'):
        return None
    return creds


def estimate_usd(seconds):
    creds = settings() or {}
    try:
        rate = float((creds.get('config') or {}).get('price_per_second') or DEFAULT_PRICE_PER_SECOND)
    except (TypeError, ValueError):
        rate = DEFAULT_PRICE_PER_SECOND
    return round(max(seconds or 0, 1) * rate, 2)


class SyncLipsync:
    def _auth(self):
        creds = credentials(SLUG)
        base = (creds['base_url'] or DEFAULT_BASE).rstrip('/')
        return base, {'x-api-key': creds['api_key']}, creds['config'] or {}

    def start(self, *, video_url, audio_url, name='', idempotency_key=None):
        base, headers, config = self._auth()
        body = {
            'model': config.get('model') or 'lipsync-2',
            'input': [{'type': 'video', 'url': video_url}, {'type': 'audio', 'url': audio_url}],
            'options': {'sync_mode': config.get('sync_mode') or 'cut_off'},
        }
        if name:
            body['outputFileName'] = name[:100]
        if idempotency_key:
            headers = {**headers, 'Idempotency-Key': idempotency_key}  # a retry never pays twice
        response = request('POST', f'{base}/generate', headers=headers, json=body, timeout=120)
        generation_id = response.json().get('id')
        if not generation_id:
            raise ProviderError('Sync did not return a generation ID.')
        return generation_id

    def get(self, generation_id):
        base, headers, _ = self._auth()
        return request('GET', f'{base}/generate/{generation_id}', headers=headers, timeout=60).json()

    def download(self, url, out_path):
        response = request('GET', url, timeout=1800, stream=True)  # signed URL, no key needed
        with open(out_path, 'wb') as fh:
            for chunk in response.iter_content(1024 * 1024):
                fh.write(chunk)
        return out_path
