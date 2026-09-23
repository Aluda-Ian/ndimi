"""Fit synthesized lines into their time slots, the way a dubbing engineer would."""


def fit(duration, slot_start, slot_end, next_start, max_speedup=1.3, gap=0.08, tolerance=1.1):
    """Return (speed, overflow_seconds).

    Aim for the line's own slot (with 10% tolerance) so the voice stays in sync with the
    speaker on screen, speeding up (pitch preserved) no more than max_speedup. What is
    left may spill into the silence before the next line; anything beyond that is
    overflow a reviewer should fix by shortening the text.
    """
    slot = max(slot_end - slot_start, 0.2)
    room = (next_start - gap - slot_start) if next_start is not None else slot + 1.5
    room = max(room, slot)
    speed = min(max(duration / (slot * tolerance), 1.0), max_speedup)
    overflow = max(0.0, duration / speed - room)
    return round(speed, 3), round(overflow, 3)
