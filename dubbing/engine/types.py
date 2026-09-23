from dataclasses import dataclass, field


@dataclass
class Word:
    start: float
    end: float
    text: str
    speaker: str = 'speaker_0'


@dataclass
class Transcript:
    language: str
    words: list = field(default_factory=list)  # list[Word]
    # Some providers (e.g. Whisper) return phrases, not words; each is a Word with multi-word text.
    phrase_level: bool = False


@dataclass
class Line:
    """One dubbing segment: a speaker's phrase with its time slot."""
    start: float
    end: float
    text: str
    speaker: str = 'speaker_0'

    @property
    def duration(self):
        return max(0.0, self.end - self.start)
