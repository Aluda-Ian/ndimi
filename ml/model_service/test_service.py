"""Checks the model service logic without downloading any models (fake pipelines stand in for them).

Run from ml/model_service:  python test_service.py
"""
import io
import math
import shutil
import struct
import subprocess
import tempfile
import wave

import numpy as np

from ndimi_models import BadAudio, Models, NoModel, decode_audio, wav_bytes


def make_wav(seconds=2.0, rate=44100, channels=2):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b''.join(struct.pack('<h', int(8000 * math.sin(i / 30))) * channels for i in range(int(seconds * rate)))
        w.writeframes(frames)
    return buf.getvalue()


class FakeASR:
    def __init__(self, words=True):
        self.words, self.calls = words, []

    def __call__(self, audio, return_timestamps=None, **kw):
        self.calls.append((return_timestamps, kw.get('generate_kwargs')))
        if return_timestamps == 'word' and not self.words:
            raise ValueError('no alignment heads')
        if return_timestamps == 'word':
            return {'text': 'ber ahinya', 'chunks': [{'text': ' ber', 'timestamp': (0.1, 0.4)},
                                                     {'text': ' ahinya', 'timestamp': (0.5, None)}]}
        return {'text': 'ber ahinya', 'chunks': [{'text': ' ber ahinya', 'timestamp': (0.0, 1.9)}]}


class FakeMT:
    def __call__(self, texts, **kw):
        assert kw['src_lang'] == 'eng_Latn' and kw['tgt_lang'] == 'luo_Latn'
        return [{'translation_text': t.upper()} for t in texts]


class FakeTTS:
    def __call__(self, text):
        return {'audio': np.zeros((1, 1600), dtype=np.float32), 'sampling_rate': 16000}


def factory_for(fakes):
    return lambda task, spec: fakes[task]


CONFIG = {
    'transcribe': {'luo': {'model': 'm/luo', 'language_token': 'swahili'}},
    'translate': {'en->luo': {'model': 'nllb', 'src_code': 'eng_Latn', 'tgt_code': 'luo_Latn'}},
    'tts': {'luo': {'model': 'tts/luo'}},
}


def test_all():
    asr = FakeASR()
    m = Models(CONFIG, factory_for({'automatic-speech-recognition': asr, 'translation': FakeMT(), 'text-to-speech': FakeTTS()}))

    # Audio: stereo 44.1 kHz WAV -> 16 kHz mono
    audio = decode_audio(make_wav(2.0), 'a.wav')
    assert abs(len(audio) / 16000 - 2.0) < 0.02, len(audio)
    if shutil.which('ffmpeg'):
        with tempfile.NamedTemporaryFile(suffix='.wav') as src, tempfile.NamedTemporaryFile(suffix='.mp3') as mp3:
            src.write(make_wav(1.5)); src.flush()
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', src.name, mp3.name], check=True)
            assert abs(len(decode_audio(open(mp3.name, 'rb').read(), 'x.mp3')) / 16000 - 1.5) < 0.1
    try:
        decode_audio(b'not audio', 'x.mp3')
        raise AssertionError('expected BadAudio')
    except BadAudio:
        pass

    # Transcribe: word timings, the shape dubbing/providers/custom.py reads
    out = m.transcribe(make_wav(2.0), 'a.wav', 'luo')
    assert out['language'] == 'luo' and not out['phrase_level']
    assert [w['text'] for w in out['words']] == ['ber', 'ahinya']
    assert out['words'][1]['end'] == out['duration']          # open-ended last word gets the clip end
    assert all(set(w) == {'start', 'end', 'text', 'speaker'} for w in out['words'])
    assert asr.calls[-1][1] == {'task': 'transcribe', 'language': 'swahili'}
    # Language left blank: the only configured model is used
    assert m.transcribe(make_wav(1.0), 'a.wav', '')['language'] == 'luo'
    # Model without word timings: falls back to phrases
    m2 = Models(CONFIG, factory_for({'automatic-speech-recognition': FakeASR(words=False)}))
    out2 = m2.transcribe(make_wav(2.0), 'a.wav', 'luo')
    assert out2['phrase_level'] and out2['words'][0]['text'] == 'ber ahinya'
    # Unknown language: a clear message, not a crash
    try:
        m.transcribe(make_wav(1.0), 'a.wav', 'kln')
        raise AssertionError('expected NoModel')
    except NoModel as e:
        assert 'kln' in str(e) and 'luo' in str(e)

    # Translate
    assert m.translate(['hello', 'water is life'], 'en', 'luo') == {'texts': ['HELLO', 'WATER IS LIFE']}
    try:
        m.translate(['x'], 'en', 'kln')
        raise AssertionError('expected NoModel')
    except NoModel:
        pass

    # TTS -> a valid WAV
    data = m.tts('ber', 'luo')
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == 16000 and w.getnframes() == 1600
    assert wav_bytes(np.array([2.0, -2.0], dtype=np.float32), 8000)[:4] == b'RIFF'

    print('All model service checks passed.')


if __name__ == '__main__':
    test_all()
