def _ts(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def to_srt(items):
    """items: iterable of (start, end, text)."""
    blocks = []
    for n, (start, end, text) in enumerate(items, 1):
        if text and text.strip():
            blocks.append(f'{n}\n{_ts(start)} --> {_ts(end)}\n{text.strip()}\n')
    return '\n'.join(blocks)


def parse_srt(text):
    import re

    items = []
    for block in re.split(r'\n\s*\n', text.replace('\r', '').strip()):
        lines = block.split('\n')
        for i, line in enumerate(lines):
            m = re.match(r'(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)', line)
            if m:
                g = list(map(int, m.groups()))
                start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                items.append((start, end, ' '.join(lines[i + 1:]).strip()))
                break
    return items
