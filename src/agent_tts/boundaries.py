"""Boundary models and utilities for sentence navigation and visual word/sentence highlighting."""

from dataclasses import dataclass
import re
from typing import List, Optional


@dataclass
class Paragraph:
    """Represents a spoken paragraph and its temporal bounds."""

    index: int
    start_sec: float
    duration_sec: float
    text: str
    sentence_indices: List[int]

    @property
    def end_sec(self) -> float:
        return self.start_sec + self.duration_sec


@dataclass
class Sentence:
    """Represents a spoken sentence and its temporal bounds."""

    index: int
    start_sec: float
    duration_sec: float
    text: str
    paragraph_index: int = 0

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
    """Tracks and indexes paragraph, sentence and word boundaries for audio synchronization."""

    def __init__(
        self,
        sentences: Optional[List[Sentence]] = None,
        words: Optional[List[Word]] = None,
        paragraphs: Optional[List[Paragraph]] = None,
    ):
        self.sentences: List[Sentence] = sentences or []
        self.words: List[Word] = words or []
        self.paragraphs: List[Paragraph] = paragraphs or self._build_paragraphs(self.sentences)

    def _build_paragraphs(self, sentences: List[Sentence]) -> List[Paragraph]:
        """Groups sentences into paragraphs based on paragraph_index or newlines."""
        if not sentences:
            return []

        has_distinct_p = any(s.paragraph_index > 0 for s in sentences)
        if has_distinct_p:
            p_groups: dict = {}
            for s in sentences:
                p_groups.setdefault(s.paragraph_index, []).append(s)
            paragraphs = []
            for p_idx in sorted(p_groups.keys()):
                p_sents = p_groups[p_idx]
                start_sec = p_sents[0].start_sec
                end_sec = p_sents[-1].end_sec
                p_text = " ".join(sent.text.strip() for sent in p_sents)
                paragraphs.append(
                    Paragraph(
                        index=p_idx,
                        start_sec=start_sec,
                        duration_sec=max(0.0, end_sec - start_sec),
                        text=p_text,
                        sentence_indices=[sent.index for sent in p_sents],
                    )
                )
            return paragraphs

        current_p_idx = 0
        current_p_sents: List[Sentence] = []
        paragraphs = []

        for s in sentences:
            current_p_sents.append(s)
            s.paragraph_index = current_p_idx
            if "\n" in s.text or s.text.endswith("\n"):
                start_sec = current_p_sents[0].start_sec
                end_sec = current_p_sents[-1].end_sec
                p_text = " ".join(sent.text.strip() for sent in current_p_sents)
                paragraphs.append(
                    Paragraph(
                        index=current_p_idx,
                        start_sec=start_sec,
                        duration_sec=max(0.0, end_sec - start_sec),
                        text=p_text,
                        sentence_indices=[sent.index for sent in current_p_sents],
                    )
                )
                current_p_idx += 1
                current_p_sents = []

        if current_p_sents:
            start_sec = current_p_sents[0].start_sec
            end_sec = current_p_sents[-1].end_sec
            p_text = " ".join(sent.text.strip() for sent in current_p_sents)
            paragraphs.append(
                Paragraph(
                    index=current_p_idx,
                    start_sec=start_sec,
                    duration_sec=max(0.0, end_sec - start_sec),
                    text=p_text,
                    sentence_indices=[sent.index for sent in current_p_sents],
                )
            )

        return paragraphs

    def get_paragraph_at(self, pos: float) -> Optional[Paragraph]:
        """Finds the paragraph active at the given playback position in seconds."""
        if not self.paragraphs:
            return None
        if pos < self.paragraphs[0].start_sec:
            return self.paragraphs[0]
        if pos >= self.paragraphs[-1].end_sec:
            return self.paragraphs[-1]
        for p in self.paragraphs:
            if (p.start_sec - 0.005) <= pos < (p.end_sec - 0.005):
                return p
        return self.paragraphs[-1]

    def get_next_paragraph(self, pos: float) -> Optional[Paragraph]:
        """Returns the next paragraph after current position."""
        for p in self.paragraphs:
            if p.start_sec > pos + 0.2:
                return p
        return None

    def get_prev_paragraph(self, pos: float, replay_threshold: float = 2.0) -> Optional[Paragraph]:
        """Returns previous paragraph, or start of current paragraph if > threshold seconds into it."""
        current = self.get_paragraph_at(pos)
        if not current:
            return self.paragraphs[0] if self.paragraphs else None
        if (pos - current.start_sec) > replay_threshold:
            return current
        if current.index > 0 and current.index - 1 < len(self.paragraphs):
            return self.paragraphs[current.index - 1]
        return current

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

    def format_highlighted_sentence(
        self,
        pos: float,
        ansi: bool = True,
        bionic: bool = False,
    ) -> str:
        """Returns the active sentence with the currently spoken word highlighted in ANSI."""
        sent = self.get_sentence_at(pos)
        if not sent:
            return ""
        if not ansi:
            return sent.text

        words_in_sent = [w for w in self.words if w.sentence_index == sent.index]
        if not words_in_sent:
            if bionic:
                return apply_bionic_reading(sent.text)
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
                # Upcoming word: Bionic fixation if enabled, else normal
                if bionic:
                    parts.append(bionic_word(w.text))
                else:
                    parts.append(w.text)
        return " ".join(parts)


def bionic_word(word: str) -> str:
    """Applies bionic reading fixation to a word (bolds the first 40-50% of the word core)."""
    m = re.match(r"^([^a-zA-Z0-9áéíóúÁÉÍÓÚñÑ]*)([a-zA-Z0-9áéíóúÁÉÍÓÚñÑ]+)(.*)$", word)
    if not m:
        return word
    prefix, core, suffix = m.groups()
    n = len(core)
    if n <= 3:
        fixation = 1
    elif n <= 6:
        fixation = 2
    else:
        fixation = max(2, int(round(n * 0.45)))
    bold_part = core[:fixation]
    rest_part = core[fixation:]
    return f"{prefix}\x1b[1m{bold_part}\x1b[22m{rest_part}{suffix}"


def apply_bionic_reading(text: str) -> str:
    """Formats full text with bionic reading fixation on each word."""
    words = text.split(" ")
    return " ".join(bionic_word(w) for w in words)


class SynthesisResult(bytes):
    """Subclass of bytes holding raw audio data plus extracted temporal BoundaryMap."""

    boundaries: BoundaryMap

    def __new__(cls, audio_data: bytes, boundaries: Optional[BoundaryMap] = None):
        obj = super().__new__(cls, audio_data)
        obj.boundaries = boundaries or BoundaryMap()
        return obj


def estimate_boundaries_from_text(text: str, total_duration_sec: float) -> BoundaryMap:
    """Estimates paragraph, sentence and word boundaries proportionally when provider doesn't yield native boundaries."""
    raw_paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    if not raw_paragraphs:
        raw_paragraphs = [text.strip()] if text.strip() else [""]

    total_chars = sum(len(p) for p in raw_paragraphs) or 1
    paragraphs: List[Paragraph] = []
    sentences: List[Sentence] = []
    words: List[Word] = []
    current_time = 0.0
    sent_global_idx = 0
    word_global_idx = 0

    for p_idx, p_text in enumerate(raw_paragraphs):
        p_dur = (len(p_text) / total_chars) * total_duration_sec
        p_start = current_time
        p_sent_indices = []

        raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", p_text) if s.strip()]
        if not raw_sentences:
            raw_sentences = [p_text]

        p_total_chars = sum(len(s) for s in raw_sentences) or 1
        sent_time = p_start

        for s_text in raw_sentences:
            s_dur = (len(s_text) / p_total_chars) * p_dur
            sent = Sentence(
                index=sent_global_idx,
                paragraph_index=p_idx,
                start_sec=sent_time,
                duration_sec=s_dur,
                text=s_text,
            )
            sentences.append(sent)
            p_sent_indices.append(sent_global_idx)

            raw_words = s_text.split()
            if raw_words:
                w_dur = s_dur / len(raw_words)
                w_time = sent_time
                for w in raw_words:
                    words.append(
                        Word(
                            index=word_global_idx,
                            sentence_index=sent_global_idx,
                            start_sec=w_time,
                            duration_sec=w_dur,
                            text=w,
                        )
                    )
                    w_time += w_dur
                    word_global_idx += 1

            sent_time += s_dur
            sent_global_idx += 1

        paragraphs.append(
            Paragraph(
                index=p_idx,
                start_sec=p_start,
                duration_sec=p_dur,
                text=p_text,
                sentence_indices=p_sent_indices,
            )
        )
        current_time += p_dur

    return BoundaryMap(sentences=sentences, words=words, paragraphs=paragraphs)
