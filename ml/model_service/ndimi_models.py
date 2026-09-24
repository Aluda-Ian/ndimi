"""The model logic behind the Ndimi model service, kept separate from the web layer so it can be tested.

Which model serves which language is set in models.json (see models.example.json).
Models load the first time they are used and then stay in memory.
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import wave

import numpy as np

log = logging.getLogger('ndimi_models')
SAMPLE_RATE = 16000


class NoModel(Exception):
    """No model is configured for what was asked. Ndimi shows the message to the admin."""


class BadAudio(Exception):
    pass


# ---------------------------------------------------------------------------------------
# configuration

def load_config(path=None):
    path = path or os.getenv('NDIMI_MODELS_CONFIG', 'models.json')
    if not os.path.exists(path):
        log.warning('%s not found; no models configured yet. Copy models.example.json to models.json.', path)
        return {'transcribe': {}, 'translate': {}, 'tts': {}}
    with open(path, encoding='utf-8') as fh:
        cfg = json.load(fh)
    for section in ('transcribe', 'translate', 'tts'):
        cfg.setdefault(section, {})
        cfg[section] = {k: v for k, v in cfg[section].items() if not str(k).startswith('_')}
    return cfg


def device():
    try:
        import torch  # type: ignore
        return 0 if torch.cuda.is_available() else -1
    except Exception:  # noqa: BLE001
        return -1


class Models:
    """Lazily loaded Hugging Face pipelines, one per configured model."""

    def __init__(self, config, pipeline_factory=None):
        self.config = config
        self._pipes = {}
        self._lock = threading.Lock()
        self._factory = pipeline_factory or _hf_pipeline

    def pipe(self, task, spec):
        key = (task, spec['model'], spec.get('src_code'), spec.get('tgt_code'))
        with self._lock:
            if key not in self._pipes:
                log.info('Loading %s model %s', task, spec['model'])
                self._pipes[key] = self._factory(task, spec)
            return self._pipes[key]

    # ---- lookups -------------------------------------------------------------------------

    def asr_spec(self, language):
        models = self.config['transcribe']
        if language:
            if language not in models:
                raise NoModel(f'No transcription model for "{language}". Configured: {", ".join(models) or "none"}.')
            return language, models[language]
        if len(models) == 1:
            return next(iter(models.items()))
        raise NoModel('Say which language to transcribe; this server has models for: ' + (', '.join(models) or 'none') + '.')

    def translate_spec(self, source, target):
        models = self.config['translate']
        for key in (f'{source}->{target}', f'*->{target}', f'{source}->*'):
            if key in models:
                return models[key]
        raise NoModel(f'No translation model for {source or "?"} → {target}. Configured: {", ".join(models) or "none"}.')

    def tts_spec(self, language):
        models = self.config['tts']
        if language not in models:
            raise NoModel(f'No voice model for "{language}". Configured: {", ".join(models) or "none"}.')
        return models[language]

    # ---- tasks ---------------------------------------------------------------------------

    def transcribe(self, audio_bytes, filename, language):
        language, spec = self.asr_spec(language)
        audio = decode_audio(audio_bytes, filename)
        duration = len(audio) / SAMPLE_RATE
        pipe = self.pipe('automatic-speech-recognition', spec)
        gen = {'task': 'transcribe'}
        if spec.get('language_token'):
            gen['language'] = spec['language_token']
        phrase_level = False
        try:
            out = pipe({'raw': audio, 'sampling_rate': SAMPLE_RATE}, return_timestamps='word',
                       chunk_length_s=30, batch_size=spec.get('batch_size', 8), generate_kwargs=gen)
        except Exception as e:  # noqa: BLE001 - some fine-tuned models can't give word timings
            log.info('Word timestamps unavailable (%s); falling back to phrase timestamps.', e)
            out = pipe({'raw': audio, 'sampling_rate': SAMPLE_RATE}, return_timestamps=True,
                       chunk_length_s=30, batch_size=spec.get('batch_size', 8), generate_kwargs=gen)
            phrase_level = True
        words = []
        for chunk in out.get('chunks') or []:
            text = (chunk.get('text') or '').strip()
            if not text:
                continue
            start, end = chunk.get('timestamp') or (None, None)
            start = float(start or 0.0)
            end = float(end if end is not None else duration)
            words.append({'start': round(start, 3), 'end': round(max(end, start), 3), 'text': text, 'speaker': 'speaker_0'})
        if not words and (out.get('text') or '').strip():
            words = [{'start': 0.0, 'end': round(duration, 3), 'text': out['text'].strip(), 'speaker': 'speaker_0'}]
            phrase_level = True
        return {'language': language, 'words': words, 'phrase_level': phrase_level, 'duration': round(duration, 3)}

    def translate(self, texts, source, target, glossary=None):
        spec = self.translate_spec(source, target)
        if not texts:
            return {'texts': []}
        pipe = self.pipe('translation', spec)
        kwargs = {}
        if spec.get('src_code'):
            kwargs['src_lang'] = spec['src_code']
        if spec.get('tgt_code'):
            kwargs['tgt_lang'] = spec['tgt_code']
        results = pipe(list(texts), max_length=spec.get('max_length', 400), batch_size=spec.get('batch_size', 16), **kwargs)
        # Ndimi also sends a glossary; plain translation models can't take one, so it is accepted and ignored.
        return {'texts': [r['translation_text'] if isinstance(r, dict) else r[0]['translation_text'] for r in results]}

    def tts(self, text, language, voice=None):
        spec = self.tts_spec(language)
        pipe = self.pipe('text-to-speech', spec)
        result = pipe(text)
        audio = np.asarray(result['audio'], dtype=np.float32).reshape(-1)
        return wav_bytes(audio, int(result['sampling_rate']))


def _hf_pipeline(task, spec):
    from transformers import pipeline  # imported here so the service starts fast and tests don't need it

    kwargs = {'model': spec['model'], 'device': device()}
    if spec.get('revision'):
        kwargs['revision'] = spec['revision']
    if task == 'automatic-speech-recognition':
        try:
            import torch  # type: ignore
            if torch.cuda.is_available():
                kwargs['torch_dtype'] = torch.float16
        except Exception:  # noqa: BLE001
            pass
    return pipeline(task, **kwargs)


# ---------------------------------------------------------------------------------------
# audio helpers

def decode_audio(data: bytes, filename='audio'):
    """Any audio/video format ffmpeg reads -> 16 kHz mono float32."""
    ffmpeg = shutil.which('ffmpeg')
    if not data:
        raise BadAudio('The audio file is empty.')
    if ffmpeg:
        suffix = os.path.splitext(filename or '')[1] or '.bin'
        with tempfile.NamedTemporaryFile(suffix=suffix) as src:
            src.write(data)
            src.flush()
            proc = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-i', src.name, '-ac', '1', '-ar', str(SAMPLE_RATE),
                                   '-f', 's16le', '-'], capture_output=True, timeout=600)
        if proc.returncode != 0 or not proc.stdout:
            raise BadAudio('Could not read the audio: ' + proc.stderr.decode(errors='replace')[-300:])
        return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    # No ffmpeg: accept 16-bit WAV only.
    try:
        with wave.open(io.BytesIO(data), 'rb') as w:
            if w.getsampwidth() != 2:
                raise BadAudio('Install ffmpeg to read this audio format.')
            frames = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
            if w.getnchannels() > 1:
                frames = frames.reshape(-1, w.getnchannels()).mean(axis=1)
            if w.getframerate() != SAMPLE_RATE:
                n = int(len(frames) * SAMPLE_RATE / w.getframerate())
                frames = np.interp(np.linspace(0, len(frames), n, endpoint=False), np.arange(len(frames)), frames)
            return frames.astype(np.float32)
    except wave.Error as e:
        raise BadAudio(f'Install ffmpeg to read this audio format ({e}).')


def wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    audio = np.clip(audio, -1.0, 1.0)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes((audio * 32767).astype('<i2').tobytes())
    return buf.getvalue()
