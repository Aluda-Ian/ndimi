"""Turn word timings into dubbing lines (speaker turns split at pauses and sentence ends)."""
from .types import Line

SENTENCE_END = ('.', '?', '!', '…', '。')
CLAUSE_END = (',', ';', ':')


def build_lines(transcript, *, max_len=10.0, pause_split=0.7, min_sentence=1.2):
    words = [w for w in transcript.words if w.text.strip()]
    if not words:
        return []
    if transcript.phrase_level:
        return _merge_phrases(words, max_len=max_len)

    lines, current = [], []

    def flush():
        if current:
            text = _join(w.text for w in current)
            lines.append(Line(current[0].start, current[-1].end, text, current[0].speaker))
            current.clear()

    for word in words:
        if current:
            prev = current[-1]
            duration = word.end - current[0].start
            pause = word.start - prev.end
            if (word.speaker != prev.speaker
                    or pause >= pause_split
                    or (prev.text.rstrip().endswith(SENTENCE_END) and prev.end - current[0].start >= min_sentence)
                    or duration > max_len):
                if duration > max_len and word.speaker == prev.speaker and pause < pause_split:
                    _split_long(current, lines)
                else:
                    flush()
        current.append(word)
    flush()
    return lines


def _split_long(current, lines):
    """Break an over-long run at its last clause mark (or the middle), keep the tail open."""
    cut = None
    for i in range(len(current) - 1, 0, -1):
        if current[i - 1].text.rstrip().endswith(SENTENCE_END + CLAUSE_END):
            cut = i
            break
    if cut is None:
        cut = len(current) // 2
    head, tail = current[:cut], current[cut:]
    lines.append(Line(head[0].start, head[-1].end, _join(w.text for w in head), head[0].speaker))
    current[:] = tail


def _merge_phrases(phrases, *, max_len):
    lines = []
    for p in phrases:
        if (lines and lines[-1].speaker == p.speaker and p.start - lines[-1].end < 0.3
                and (lines[-1].duration < 1.0 or p.end - p.start < 1.0)
                and p.end - lines[-1].start <= max_len):
            lines[-1] = Line(lines[-1].start, p.end, _join([lines[-1].text, p.text]), p.speaker)
        else:
            lines.append(Line(p.start, p.end, p.text.strip(), p.speaker))
    return lines


def _join(parts):
    text = ''
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if text and not part.startswith(('.', ',', '?', '!', ';', ':', "'", '’')):
            text += ' '
        text += part
    return text
