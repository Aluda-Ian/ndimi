"""Google Cloud Translation (v2 REST, API key)."""
import html

from .base import credentials, request

SLUG = 'google-cloud'
URL = 'https://translation.googleapis.com/language/translate/v2'


class GoogleTranslator:
    batch_size = 100

    def translate(self, lines, source, target, glossary=None):
        creds = credentials(SLUG)
        results = {}
        for start in range(0, len(lines), self.batch_size):
            batch = lines[start:start + self.batch_size]
            body = {'q': [l['text'] for l in batch], 'target': target, 'format': 'text'}
            if source:
                body['source'] = source
            data = request('POST', URL, params={'key': creds['api_key']}, json=body, timeout=120).json()
            for line, item in zip(batch, data['data']['translations']):
                results[line['i']] = html.unescape(item['translatedText'])
        return results
