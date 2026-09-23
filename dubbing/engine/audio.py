"""Audio/video processing with ffmpeg + numpy. No Django imports, so it can be tested on its own."""
import re
import subprocess
from pathlib import Path

import numpy as np

SR = 44100  # working sample rate
CREATE_NO_WINDOW = 0x08000000  # hide console windows on Windows


class FFmpegError(RuntimeError):
    pass


class FFmpeg:
    def __init__(self, path='ffmpeg'):
        self.path = path or 'ffmpeg'

    # ---------- low level ----------

    def run(self, args, input_bytes=None):
        cmd = [self.path, '-hide_banner', '-nostdin', '-y', *map(str, args)]
        kwargs = {}
        if hasattr(subprocess, 'STARTUPINFO'):
            kwargs['creationflags'] = CREATE_NO_WINDOW
        try:
            result = subprocess.run(cmd, input=input_bytes, capture_output=True, **kwargs)
        except FileNotFoundError:
            raise FFmpegError(f'ffmpeg not found at "{self.path}". Install ffmpeg or set its path in Dubbing settings.')
        if result.returncode != 0:
            tail = result.stderr.decode('utf-8', 'replace')[-1200:]
            raise FFmpegError(f'ffmpeg failed: {tail}')
        return result.stdout

    def check(self):
        out = subprocess.run([self.path, '-version'], capture_output=True, text=True)
        return out.stdout.splitlines()[0] if out.returncode == 0 else None

    def info(self, path):
        """Return {'duration': seconds, 'has_video': bool, 'has_audio': bool}."""
        cmd = [self.path, '-hide_banner', '-nostdin', '-i', str(path)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
        except FileNotFoundError:
            raise FFmpegError(f'ffmpeg not found at "{self.path}".')
        text = result.stderr
        match = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', text)
        if not match:
            raise FFmpegError(f'Could not read media file {Path(path).name}. Is it a video or audio file?')
        h, m, s = match.groups()
        return {
            'duration': int(h) * 3600 + int(m) * 60 + float(s),
            'has_video': bool(re.search(r'Stream #.*Video:', text)) and 'attached pic' not in text,
            'has_audio': bool(re.search(r'Stream #.*Audio:', text)),
        }

    # ---------- decode / encode ----------

    def load(self, path, filters=None, mono=True):
        """Decode any audio to float32 numpy at SR (mono by default)."""
        args = ['-i', path]
        if filters:
            args += ['-af', filters]
        args += ['-f', 'f32le', '-acodec', 'pcm_f32le', '-ac', 1 if mono else 2, '-ar', SR, 'pipe:1']
        data = np.frombuffer(self.run(args), dtype=np.float32)
        return data if mono else data.reshape(-1, 2)

    def save(self, samples, out_path, channels=1):
        samples = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
        self.run(['-f', 'f32le', '-ar', SR, '-ac', channels, '-i', 'pipe:0', '-c:a', 'pcm_s16le', out_path],
                 input_bytes=samples.tobytes())
        return out_path

    def load_range(self, path, start, end):
        """Decode only [start, end) seconds (fast seek), mono float32."""
        args = ['-ss', f'{max(0.0, start):.3f}', '-t', f'{max(0.01, end - start):.3f}', '-i', path,
                '-f', 'f32le', '-acodec', 'pcm_f32le', '-ac', 1, '-ar', SR, 'pipe:1']
        return np.frombuffer(self.run(args), dtype=np.float32)

    def extract_audio(self, src, out_wav):
        self.run(['-i', src, '-vn', '-ac', 2, '-ar', SR, '-c:a', 'pcm_s16le', out_wav])
        return out_wav

    def to_speech_mp3(self, src, out_mp3):
        """Small mono file for speech-to-text uploads (stays under provider size limits)."""
        self.run(['-i', src, '-vn', '-ac', 1, '-ar', 16000, '-c:a', 'libmp3lame', '-b:a', '48k', out_mp3])
        return out_mp3

    # ---------- speech clips ----------

    TRIM = ('silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.02,areverse,'
            'silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse')

    def load_speech(self, path):
        """Load a TTS clip with leading/trailing silence trimmed."""
        return self.load(path, filters=self.TRIM)

    def change_speed(self, samples, factor):
        """Time-stretch without changing pitch (atempo)."""
        if abs(factor - 1.0) < 0.01:
            return samples
        chain, remaining = [], factor
        while remaining > 2.0:
            chain.append('atempo=2.0')
            remaining /= 2.0
        while remaining < 0.5:
            chain.append('atempo=0.5')
            remaining /= 0.5
        chain.append(f'atempo={remaining:.4f}')
        raw = self.run(['-f', 'f32le', '-ar', SR, '-ac', 1, '-i', 'pipe:0', '-af', ','.join(chain),
                        '-f', 'f32le', '-acodec', 'pcm_f32le', '-ac', 1, '-ar', SR, 'pipe:1'],
                       input_bytes=np.asarray(samples, dtype=np.float32).tobytes())
        return np.frombuffer(raw, dtype=np.float32)

    def speaker_sample(self, vocals_path, spans, out_mp3, max_seconds=90):
        """Concatenate a speaker's clean speech into one clip for voice cloning."""
        pieces, total = [], 0.0
        for start, end in sorted(spans, key=lambda s: s[1] - s[0], reverse=True):
            if total >= max_seconds:
                break
            if end - start < 0.8:  # skip fragments under 0.8s
                continue
            piece = self.load_range(vocals_path, start, end)
            pieces.append(piece)
            pieces.append(np.zeros(int(SR * 0.25), dtype=np.float32))
            total += len(piece) / SR
        if total < 4:
            return None  # not enough clean speech to clone
        clip = np.concatenate(pieces)
        wav = str(out_mp3) + '.wav'
        self.save(clip, wav)
        self.run(['-i', wav, '-c:a', 'libmp3lame', '-b:a', '128k', out_mp3])
        Path(wav).unlink(missing_ok=True)
        return out_mp3

    # ---------- assembly ----------

    def build_voice_track(self, placements, total_seconds, out_wav):
        """placements: list of (samples, start_seconds). Returns path of a mono track."""
        track = np.zeros(int((total_seconds + 1) * SR), dtype=np.float32)
        for samples, start in placements:
            a = max(0, int(round(start * SR)))
            b = min(len(track), a + len(samples))
            if b > a:
                track[a:b] += samples[:b - a]
        track = track[:int(total_seconds * SR)]
        return self.save(track, out_wav)

    def mix(self, voice_wav, out_wav, *, background=None, original=None, loudness=-16.0, duck_level=0.25):
        """Final soundtrack.

        background: clean music/effects stem (from separation) -> voice on top of it.
        original:   full original audio -> ducked under the new voice (voice-over style).
        """
        norm = f'loudnorm=I={loudness}:TP=-1.5:LRA=11'
        fmt = 'aformat=sample_rates=44100:channel_layouts=stereo'
        if background:
            graph = (f'[0:a]{fmt}[bg];[1:a]{fmt}[v];'
                     f'[bg][v]amix=inputs=2:duration=first:normalize=0,{norm}[out]')
            inputs = ['-i', background, '-i', voice_wav]
        elif original:
            graph = (f'[0:a]{fmt}[orig];[1:a]{fmt},asplit=2[v][key];'
                     f'[orig][key]sidechaincompress=threshold=0.015:ratio=20:attack=15:release=350:makeup=1[duck];'
                     f'[duck]volume={duck_level}[d];[d][v]amix=inputs=2:duration=first:normalize=0,{norm}[out]')
            inputs = ['-i', original, '-i', voice_wav]
        else:
            graph = f'[0:a]{fmt},{norm}[out]'
            inputs = ['-i', voice_wav]
        self.run([*inputs, '-filter_complex', graph, '-map', '[out]', '-ar', SR, '-c:a', 'pcm_s16le', out_wav])
        return out_wav

    def mux(self, video, audio_wav, out_mp4):
        self.run(['-i', video, '-i', audio_wav, '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy',
                  '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', out_mp4])
        return out_mp4

    def encode_audio(self, wav, out_m4a):
        self.run(['-i', wav, '-c:a', 'aac', '-b:a', '192k', out_m4a])
        return out_m4a
