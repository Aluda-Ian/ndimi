"""Podcast dubbing: natural pacing instead of lip-sync.

On video, a dubbed line must fit the moment the speaker's mouth moves, so it is
sped up or flagged. A podcast has no picture: squeezing lines only makes the voice
sound rushed. Here every line gets the time it needs. Where a line runs long, the
rest of the episode slides later; that drift is won back in quiet pauses, while
music (intro, stingers, outro) keeps its full length.

The background is a "bed": the music/effects stem when separation produced one,
otherwise the original audio with the speech muted (so intro music, jingles and
sound effects between lines survive, and the original voices do not).

No Django imports, so it can be tested on its own.
"""
from dataclasses import dataclass

import numpy as np

from .audio import SR


@dataclass
class Piece:
    kind: str          # 'speech' or 'pause'
    src_start: float   # seconds in the original
    src_end: float
    dst_len: float     # seconds in the dub
    index: int = -1    # segment index for speech pieces


def _db(samples):
    if samples.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return 20 * np.log10(max(rms, 1e-6))


def quiet_checker(read, threshold_db=-42.0):
    """read(start, end) -> samples. Returns f(start, end) -> True when that stretch is quiet enough to shorten."""
    def quiet(start, end):
        return _db(read(start, end)) < threshold_db
    return quiet


def plan_timeline(slots, durations, total, *, quiet=None, gap=0.15, min_pause=0.45):
    """Lay out the dubbed episode.

    slots:     [(index, start, end)] of the original lines, in time order.
    durations: {index: seconds the dubbed line lasts (after any speed-up)}.
    total:     length of the original episode in seconds.
    quiet:     f(start, end) -> bool; pauses where it is True may be shortened to win back drift.

    Returns (pieces, starts, new_total). `pieces` cover the whole original in order;
    `starts` maps each line index to its new start time.
    """
    pieces, starts = [], {}
    src = dst = drift = 0.0
    for index, start, end in sorted(slots, key=lambda s: (s[1], s[0])):
        start = max(start, src)          # overlapping lines (cross-talk) are laid end to end
        end = max(end, start)
        pause = start - src
        if pause > 0:
            keep = pause
            if drift > 0 and pause > min_pause and (quiet is None or quiet(src, start)):
                win = min(drift, pause - min_pause)
                keep -= win
                drift -= win
            pieces.append(Piece('pause', src, start, keep))
            dst += keep
        starts[index] = dst
        slot = end - start
        need = durations.get(index, 0.0)
        length = slot if need <= slot else need + gap
        drift += length - slot
        pieces.append(Piece('speech', start, end, length, index))
        dst += length
        src = end
    if total > src:
        pieces.append(Piece('pause', src, total, total - src))
        dst += total - src
    return pieces, starts, dst


def _crossfade_cut(chunk, keep_samples, fade):
    """Shorten `chunk` to keep_samples by removing its middle with a crossfade."""
    if keep_samples >= len(chunk):
        return chunk
    if keep_samples <= 0:
        return chunk[:0]
    head = keep_samples // 2
    tail = keep_samples - head
    n = min(fade, head, tail, len(chunk) - keep_samples)
    a, b = chunk[:head + n], chunk[len(chunk) - tail:]  # overlap n samples, so the result is exactly keep_samples
    if n == 0:
        return np.concatenate([a, b])
    up = np.linspace(0.0, 1.0, n, dtype=np.float32)
    if chunk.ndim == 2:
        up = up[:, None]
    blended = a[len(a) - n:] * (1 - up) + b[:n] * up
    return np.concatenate([a[:len(a) - n], blended, b[n:]])


def _fit_chunk(chunk, want, stretch, fade_n, silence_db=-55.0):
    """Make a bed chunk exactly `want` samples: cut the middle out, or slow it down (pad if silent)."""
    if want == len(chunk):
        return chunk
    if want < len(chunk):
        return _crossfade_cut(chunk, want, fade_n)
    if len(chunk) >= SR // 20 and _db(chunk) >= silence_db:
        chunk = stretch(chunk, len(chunk) / want)
        if len(chunk) >= want:
            return chunk[:want]
    pad = np.zeros((want - len(chunk),) + chunk.shape[1:], dtype=np.float32)
    return np.concatenate([chunk, pad])


def render_episode(pieces, *, read_bed, clip, bed_out, voice_out, stretch, gate=False, fade=0.02, edge_fade=0.03):
    """Stream the dubbed episode, piece by piece, into two raw float32 files at SR.

    read_bed(start, end) -> stereo (n, 2) samples of the bed source, or None for no bed.
    clip(index) -> mono samples of the dubbed line, or None.
    gate=True: the bed source is the original mix, so speech pieces are silenced (the
    original voices drop out) and the pauses around them fade in and out.
    Memory stays small however long the episode is.
    """
    fade_n, edge_n = int(fade * SR), int(edge_fade * SR)
    with open(bed_out, 'wb') as bed_fh, open(voice_out, 'wb') as voice_fh:
        for n, p in enumerate(pieces):
            want = int(round(p.dst_len * SR))
            if want <= 0:
                continue
            if read_bed is None or (gate and p.kind == 'speech'):
                chunk = np.zeros((want, 2), dtype=np.float32)
            else:
                chunk = _fit_chunk(np.asarray(read_bed(p.src_start, p.src_end), dtype=np.float32).reshape(-1, 2),
                                   want, stretch, fade_n).copy()
                m = min(edge_n, len(chunk) // 2)
                if gate and m:
                    ramp = np.linspace(0.0, 1.0, m, dtype=np.float32)[:, None]
                    if n > 0 and pieces[n - 1].kind == 'speech':
                        chunk[:m] *= ramp          # fade in after a muted line
                    if n + 1 < len(pieces) and pieces[n + 1].kind == 'speech':
                        chunk[len(chunk) - m:] *= ramp[::-1]  # fade out before one
            bed_fh.write(chunk.astype(np.float32).tobytes())
            voice = np.zeros(want, dtype=np.float32)
            if p.kind == 'speech':
                samples = clip(p.index)
                if samples is not None and len(samples):
                    samples = np.asarray(samples, dtype=np.float32)[:want]
                    voice[:len(samples)] = samples
            voice_fh.write(voice.tobytes())


def timestamp(seconds):
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f'{h:02}:{m:02}:{s:02}'


def transcript_text(items, title='', notes=''):
    """items: [(start_seconds, speaker_name, text)] -> readable transcript for show notes."""
    lines = []
    if title:
        lines += [title, '=' * min(len(title), 80), '']
    if notes:
        lines += [notes.strip(), '']
    previous = None
    for start, speaker, text in items:
        text = (text or '').strip()
        if not text:
            continue
        if speaker != previous:
            lines.append('')
            lines.append(f'[{timestamp(start)}] {speaker}:')
            previous = speaker
        lines.append(text)
    return '\n'.join(lines).strip() + '\n'
