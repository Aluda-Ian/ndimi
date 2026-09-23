import logging
import time

import requests

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """A provider failed. `retryable` means trying the job again later may work."""

    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def credentials(slug, required=('api_key',)):
    from system.models import Integration

    creds = Integration.credentials(slug)
    if creds is None:
        raise ProviderError(f'The "{slug}" integration is not enabled. Turn it on in Admin > Integrations.')
    for key in required:
        if not creds.get(key):
            raise ProviderError(f'The "{slug}" integration has no {key.replace("_", " ")}. Add it in Admin > Integrations.')
    return creds


def request(method, url, *, retries=4, timeout=300, **kwargs):
    """HTTP with retries on rate limits and server errors (exponential backoff)."""
    delay = 2.0
    for attempt in range(1, retries + 1):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as error:
            if attempt == retries:
                raise ProviderError(f'Network error calling {url}: {error}', retryable=True)
            logger.warning('Network error on %s (attempt %s): %s', url, attempt, error)
        else:
            if response.status_code < 400:
                return response
            retryable = response.status_code in (408, 409, 425, 429) or response.status_code >= 500
            if not retryable or attempt == retries:
                detail = response.text[:500]
                raise ProviderError(f'{url} returned {response.status_code}: {detail}', retryable=retryable)
            wait = float(response.headers.get('Retry-After') or delay)
            logger.warning('%s returned %s, retrying in %.0fs', url, response.status_code, wait)
            delay = wait
        # Rewind any file handles before retrying an upload.
        files = kwargs.get('files') or {}
        entries = files.values() if isinstance(files, dict) else [value for _, value in files]
        for f in entries:
            handle = f[1] if isinstance(f, tuple) else f
            if hasattr(handle, 'seek'):
                handle.seek(0)
        time.sleep(delay)
        delay = min(delay * 2, 60)
    raise ProviderError(f'Could not reach {url}', retryable=True)
