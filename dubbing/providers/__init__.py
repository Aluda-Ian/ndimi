"""Swappable providers for each pipeline stage.

Each stage asks the registry for the provider chosen in Dubbing settings. To add
a provider, write a class with the same method as the others and register it here.
"""
from .base import ProviderError  # noqa: F401


def get_stt(name):
    from . import custom, elevenlabs, openai
    return {'elevenlabs': elevenlabs.ScribeSTT, 'openai': openai.WhisperSTT, 'custom': custom.CustomSTT}[name]()


def get_translator(name):
    from . import custom, google, openai
    if name == 'none':
        return None
    return {'openai': openai.GPTTranslator, 'google': google.GoogleTranslator, 'custom': custom.CustomTranslator}[name]()


def get_tts(name):
    from . import custom, elevenlabs
    return {'elevenlabs': elevenlabs.ElevenLabsTTS, 'custom': custom.CustomTTS}[name]()


def get_separator(name):
    from . import elevenlabs, local
    return {'demucs': local.DemucsSeparator, 'elevenlabs': elevenlabs.VoiceIsolator, 'duck': local.NoSeparator}[name]()
