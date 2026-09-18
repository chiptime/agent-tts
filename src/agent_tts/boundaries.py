"""Boundary models and utilities for sentence navigation and visual word/sentence highlighting."""

from dataclasses import dataclass
import re
from typing import List, Optional


@dataclass
class Sentence:
    """Represents a spoken sentence and its temporal bounds."""

    index: int
    start_sec: float
    duration_sec: float
    text: str

    @property
    def end_sec(self) -> float:
        return self.start_sec + self.duration_sec


@dataclass
class Word:
    """Represents a single spoken word and its estimated/actual temporal bounds."""

    index: int
    sentence_index: int
    start_sec: float
    duration_sec: float
    text: str

    @property
    def end_sec(self) -> float:
        return self.start_sec + self.duration_sec


class BoundaryMap:
    """Tracks and indexes sentence and word boundaries for audio synchronization."""

    def __init__(
        self,
        sentences: Optional[List[Sentence]] = None,
        words: Optional[List[Word]] = None,
    ):
        self.sentences: List[Sentence] = sentences or []
        self.words: List[Word] = words or []

    def get_sentence_at(self, pos: float) -> Optional[Sentence]:
        """Finds the sentence active at the given playback position in seconds."""
        if not self.sentences:
            return None
        if pos < self.sentences[0].start_sec:
            return self.sentences[0]
        if pos >= self.sentences[-1].end_sec:
            return self.sentences[-1]

        for s in self.sentences:
            if s.start_sec <= pos < s.end_sec:
                return s
        return self.sentences[-1]

    def get_next_sentence(self, pos: float) -> Optional[Sentence]:
        """Returns the next sentence after current position."""
        for s in self.sentences:
            if s.start_sec > pos + 0.15:
                return s
        return None

    def get_prev_sentence(self, pos: float, replay_threshold: float = 1.2) -> Optional[Sentence]:
        """Returns the previous sentence, or start of current sentence if > threshold seconds into it."""
        current = self.get_sentence_at(pos)
        if not current:
            return self.sentences[0] if self.sentences else None

        # If we are noticeably into the current sentence, replay current sentence
        if (pos - current.start_sec) > replay_threshold:
            return current

        # Otherwise go to previous sentence
        idx = current.index
        if idx > 0 and idx - 1 < len(self.sentences):
            return self.sentences[idx - 1]
        return current

    def get_word_at(self, pos: float) -> Optional[Word]:
        """Finds the word active at the given playback position."""
        if not self.words:
            return None
        if pos < self.words[0].start_sec:
            return self.words[0]
        if pos >= self.words[-1].end_sec:
            return self.words[-1]

        for w in self.words:
            if w.start_sec <= pos < w.end_sec:
                return w
        return self.words[-1]

    def format_highlighted_sentence(self, pos: float, ansi: bool = True) -> str:
        """Returns the active sentence with the currently spoken word highlighted in ANSI."""
        sent = self.get_sentence_at(pos)
        if not sent:
            return ""
        if not ansi:
            return sent.text

        words_in_sent = [w for w in self.words if w.sentence_index == sent.index]
        if not words_in_sent:
            return f"\x1b[1;36m{sent.text}\x1b[0m"

        active_word = self.get_word_at(pos)
        parts = []
        for w in words_in_sent:
            if active_word and w.index == active_word.index:
                # Active word: Bold Yellow Underline
                parts.append(f"\x1b[1;33;4m{w.text}\x1b[0m")
            elif w.end_sec < pos:
                # Past word: Dimmed
                parts.append(f"\x1b[2m{w.text}\x1b[0m")
            else:
                # Upcoming word: Normal
                parts.append(w.text)
        return " ".join(parts)


class SynthesisResult(bytes):
    """Subclass of bytes holding raw audio data plus extracted temporal BoundaryMap."""

    boundaries: BoundaryMap

    def __new__(cls, audio_data: bytes, boundaries: Optional[BoundaryMap] = None):
        obj = super().__new__(cls, audio_data)
        obj.boundaries = boundaries or BoundaryMap()
        return obj


def estimate_boundaries_from_text(text: str, total_duration_sec: float) -> BoundaryMap:
    """Estimates sentence and word boundaries proportionally when provider doesn't yield native boundaries."""
    raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not raw_sentences:
        raw_sentences = [text.strip()]

    total_chars = sum(len(s) for s in raw_sentences) or 1
    sentences: List[Sentence] = []
    words: List[Word] = []
    current_time = 0.0
    word_global_idx = 0

    for idx, s_text in enumerate(raw_sentences):
        dur = (len(s_text) / total_chars) * total_duration_sec
        sent = Sentence(index=idx, start_sec=current_time, duration_sec=dur, text=s_text)
        sentences.append(sent)

        raw_words = s_text.split()
        if raw_words:
            w_dur = dur / len(raw_words)
            w_time = current_time
            for w in raw_words:
                words.append(
                    Word(
                        index=word_global_idx,
                        sentence_index=idx,
                        start_sec=w_time,
                        duration_sec=w_dur,
                        text=w,
                    )
                )
                w_time += w_dur
                word_global_idx += 1

        current_time += dur

    return BoundaryMap(sentences=sentences, words=words)
