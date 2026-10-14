"""Automatic model choice: which AI model handles each step for each language.

With a step set to "Automatic" in Dubbing settings, the pipeline asks this module for the
best usable model. A model is usable when it is:

1. enabled, for that task, and covers the language (and, for translation, the source language);
2. allowed commercially, unless "Allow non-commercial models" is on (internal tests only);
3. reachable: its integration is switched on with a key (ElevenLabs, OpenAI, Google Cloud,
   or "Ndimi models" with a base URL for models on our own GPU server).

Among usable models the lowest priority number wins. Speech-to-text and translation fall back to
the next model if one fails, so a broken or missing model never stops a dub on its own.
"""
from .models import AIModel, DubbingSettings
from .providers import ProviderError

INTEGRATION_FOR_RUNNER = {'elevenlabs': 'elevenlabs', 'openai': 'openai', 'google': 'google-cloud', 'ndimi': 'ndimi-models'}
TASK_NAMES = dict(AIModel.TASKS)


def runner_ready(runner):
    from system.models import Integration

    creds = Integration.credentials(INTEGRATION_FOR_RUNNER[runner])
    if creds is None:
        return False
    if runner == 'ndimi':
        return bool(creds.get('base_url'))
    return bool(creds.get('api_key') or (creds.get('secrets') or {}).get('service_account'))


def why_not(model, language, source=None, settings=None):
    """Plain reason a model can't be used for this language, or '' if it can."""
    settings = settings or DubbingSettings.load()
    if not model.enabled:
        return 'turned off'
    if not model.covers(language, source):
        return 'does not cover this language'
    if not model.commercial_use and not settings.allow_noncommercial_models:
        return 'non-commercial licence'
    if not runner_ready(model.runner):
        return f'{dict(AIModel.RUNNERS)[model.runner]} is not set up in Integrations'
    return ''


def candidates(task, language, source=None, settings=None):
    """Usable models for a task and language, best first."""
    settings = settings or DubbingSettings.load()
    ready = {}
    out = []
    for model in AIModel.objects.filter(task=task, enabled=True).order_by('priority', 'name'):
        if not model.covers(language, source):
            continue
        if not model.commercial_use and not settings.allow_noncommercial_models:
            continue
        if model.runner not in ready:
            ready[model.runner] = runner_ready(model.runner)
        if ready[model.runner]:
            out.append(model)
    return out


def choose(task, language, source=None, settings=None):
    found = candidates(task, language, source, settings)
    if not found:
        raise ProviderError(
            f'No usable {TASK_NAMES[task].lower()} model for "{language or "unknown language"}". '
            'Open Settings > AI models: enable a model that covers this language, or set up its integration.'
        )
    return found[0]


def provider_for(model):
    """The provider object that runs a model."""
    from .providers import custom, elevenlabs, google, openai

    if model.runner == 'ndimi':
        return {'stt': custom.CustomSTT, 'translate': custom.CustomTranslator, 'tts': custom.CustomTTS}[model.task](model=model.key)
    table = {
        ('stt', 'elevenlabs'): elevenlabs.ScribeSTT, ('stt', 'openai'): openai.WhisperSTT,
        ('translate', 'openai'): openai.GPTTranslator, ('translate', 'google'): google.GoogleTranslator,
        ('tts', 'elevenlabs'): elevenlabs.ElevenLabsTTS,
    }
    cls = table.get((model.task, model.runner))
    if cls is None:
        raise ProviderError(f'{model.name} can\'t do {TASK_NAMES[model.task].lower()}.')
    return cls()


def overview(languages, settings=None):
    """For the dashboard: which model each step uses for each language right now."""
    settings = settings or DubbingSettings.load()
    rows = []
    for code, name in languages:
        row = {'code': code, 'name': name}
        for task in ('stt', 'translate', 'tts'):
            found = candidates(task, code, 'en' if task == 'translate' else None, settings)
            row[task] = found[0].name if found else 'none'
        rows.append(row)
    return rows
