"""Separation that runs on this machine."""
import shutil
import subprocess
import sys
from pathlib import Path

from .base import ProviderError


class NoSeparator:
    """No separation: the original track is ducked under the new voice."""

    provides_background = False

    def separate(self, audio_path, workdir, ffmpeg):
        return {'vocals': audio_path, 'background': None}


class DemucsSeparator:
    """Demucs (pip install demucs) splits voice from music and effects.

    Gives the cleanest result: the new voice sits on the original music bed. Uses a
    GPU if PyTorch finds one; on CPU it takes roughly as long as the video.
    """

    provides_background = True

    def separate(self, audio_path, workdir, ffmpeg):
        try:
            import demucs  # noqa: F401
        except ImportError:
            raise ProviderError('Demucs is not installed. Run "pip install demucs", or pick another separation option.')
        out_dir = Path(workdir) / 'demucs'
        cmd = [sys.executable, '-m', 'demucs', '--two-stems=vocals', '-n', 'htdemucs', '-o', str(out_dir), str(audio_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise ProviderError(f'Demucs failed: {result.stderr[-800:]}')
        stem_dir = out_dir / 'htdemucs' / Path(audio_path).stem
        vocals, background = stem_dir / 'vocals.wav', stem_dir / 'no_vocals.wav'
        if not vocals.exists() or not background.exists():
            raise ProviderError('Demucs finished but its output files are missing.')
        final_vocals, final_bg = Path(workdir) / 'vocals.wav', Path(workdir) / 'background.wav'
        shutil.move(str(vocals), final_vocals)
        shutil.move(str(background), final_bg)
        shutil.rmtree(out_dir, ignore_errors=True)
        return {'vocals': str(final_vocals), 'background': str(final_bg)}
