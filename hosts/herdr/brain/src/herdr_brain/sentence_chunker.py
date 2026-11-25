"""Deterministic sentence chunker for streamed TTS boundaries (W3-P1).

PRD-06 FR-05: sentence segmentation MUST be deterministic and safe for
TTS boundaries — abbreviations, decimals, code spans and URLs must not
split mid-token. This is a pure accumulator: the LLM's streamed content
deltas feed :meth:`SentenceChunker.feed` and complete sentences come
back for immediate synthesis, so spoken audio can start on the first
full sentence while generation continues.

Rules (all guards look only at text ALREADY accumulated, so a boundary
decision never depends on future tokens):

- A boundary candidate is a run of terminal punctuation (``. ! ? …``)
  followed by at least one separator character (space, tab, newline).
  A run that ends the buffer is NOT a boundary yet — more tokens may
  follow; :meth:`flush` owns the tail.
- A newline is itself a boundary (paragraph/bullet lists), so speakable
  cadence survives answers without final punctuation.
- Guards that suppress a candidate:
  * inside a code span (backtick-run parity over the accumulated text);
  * inside a URL (a token containing ``://`` or starting ``www.``,
    until its closing separator);
  * the word ending at the punctuation is a known abbreviation
    (ES/EN list, dots stripped, case-insensitive: ``Dr``, ``etc``,
    ``pág``, ``e.g``, ``Sr``...) or a single uppercase initial
    (``John F. Kennedy``).
- Decimals need no guard: ``3.14`` has no separator after the dot, so
  it is never even a candidate.

The chunker is not thread-safe by design: exactly one turn thread feeds
it inside ``BrainLLM.ask``. Whitespace between sentences is dropped;
each emitted sentence is stripped. The full answer text never flows
through here — only the spoken sentences do.
"""

from __future__ import annotations

# Words that end in terminal punctuation without ending a sentence.
# Looked up with dots stripped and lowercased, so ``e.g``/``E.G.`` both
# hit ``eg``. Deliberately conservative: a miss just splits late.
_ABBREVIATIONS = frozenset(
    {
        # Spanish
        "sr", "sra", "srta", "dr", "dra", "lic", "ing", "prof",
        "av", "avda", "etc", "pág", "pag", "pp", "vs", "aprox",
        "máx", "max", "mín", "min", "nº", "núm", "num", "art",
        "cf", "cía", "pej",
        # English
        "mr", "mrs", "ms", "jr", "st", "eg", "ie", "approx",
        "no", "fig", "dept", "univ", "inc", "ltd", "co", "am", "pm",
    }
)

_TERMINALS = frozenset(".!?…")
_SEPARATORS = frozenset(" \t\r\n\f\v")


class SentenceChunker:
    """Accumulates streamed text and emits complete sentences."""

    def __init__(self) -> None:
        self._buf: str = ""
        # Decision cursor: everything before it had its per-character
        # state applied exactly once (code parity, URL, token starts).
        # A terminal run that ends the buffer parks the cursor ON the
        # run — its boundary decision waits for the next token.
        self._state = 0
        # Start of the sentence currently being built.
        self._emit = 0
        # Odd = currently inside a `code span` (backticks toggle parity).
        self._in_code = False
        # A token with :// or a www. prefix — until its separator.
        self._in_url = False
        # Index just past the last separator run (token starts there).
        self._token_start = 0
        self._flushed = False

    # -- public API -------------------------------------------------------

    def feed(self, text: str) -> list[str]:
        """Consumes one piece of streamed content, returns finished
        sentences (possibly none — a boundary closes only once the
        separator AFTER the punctuation is already inside the buffer;
        a punctuation run at the very end waits for the next token)."""
        if self._flushed:
            return []
        if not text:
            return []
        self._buf += text
        return self._scan_boundaries()

    def flush(self) -> str | None:
        """Emits the unfinished tail as the final sentence, or None when
        nothing speakable remains. After flushing, the chunker is done
        (later feeds return nothing)."""
        if self._flushed:
            return None
        self._flushed = True
        tail = self._buf[self._emit:].strip()
        self._buf = ""
        self._state = self._emit = 0
        return tail or None

    # -- boundary scanning -------------------------------------------------

    def _scan_boundaries(self) -> list[str]:
        sentences: list[str] = []
        index = self._state
        while index < len(self._buf):
            char = self._buf[index]
            if char == "`":
                self._in_code = not self._in_code
                index += 1
                continue
            if char == "\n":
                if not self._in_code:
                    sentence, index = self._close(index)
                    if sentence:
                        sentences.append(sentence)
                    continue
                index += 1
                continue
            if char in _SEPARATORS:
                index = self._skip_separators(index)
                continue
            if not self._in_url and index == self._token_start:
                self._in_url = self._buf.startswith("www.", index)
            if char in _TERMINALS:
                end = index
                while end < len(self._buf) and self._buf[end] in _TERMINALS:
                    end += 1
                if end >= len(self._buf):
                    break  # run may still grow/gain a separator: park
                if self._buf[end] in _SEPARATORS:
                    if self._suppress(index):
                        index = end  # separator handled on the next pass
                        continue
                    sentence, index = self._close(end)
                    if sentence:
                        sentences.append(sentence)
                    continue
                index = end
                continue
            if char == "\n":
                if not self._in_code:
                    sentence, index = self._close(index)
                    if sentence:
                        sentences.append(sentence)
                    continue
                index += 1
                continue
            if not self._in_url and self._buf.startswith("://", index):
                self._in_url = True
            index += 1
        self._state = index
        return sentences

    def _skip_separators(self, index: int) -> int:
        """Steps over a separator run; whitespace always exits URL state
        and starts a fresh token afterwards."""
        while index < len(self._buf) and self._buf[index] in _SEPARATORS:
            index += 1
        self._in_url = False
        self._token_start = index
        return index

    def _suppress(self, boundary: int) -> bool:
        """Guard checks for the terminal run starting at ``boundary``."""
        if self._in_code:
            return True
        # A "://" inside the current token (https://host/path): any
        # candidate before the token's own separator is inside a URL.
        if "://" in self._buf[self._token_start:boundary]:
            self._in_url = True
            return True
        word = self._word_before(boundary)
        if word is None:
            return False
        if len(word) == 1 and word.isalpha() and word.isupper():
            return True  # an initial: "John F. Kennedy"
        return word.replace(".", "").lower() in _ABBREVIATIONS

    def _word_before(self, boundary: int) -> str | None:
        """The word ending at ``boundary`` (letters/dots/letters-plus),
        or None when the punctuation follows a separator/quote/number
        edge that cannot be an abbreviation."""
        start = boundary
        while start > self._emit and (
            self._buf[start - 1].isalnum()
            or self._buf[start - 1] == "."
            or self._buf[start - 1] in "ºª'"
        ):
            start -= 1
        word = self._buf[start:boundary]
        return word or None

    def _close(self, run_end: int) -> tuple[str, int]:
        """Emits the sentence ending at ``run_end`` (a terminal run end
        or a newline position), skips the separator, and re-anchors the
        scan state. Returns ``(sentence, next_index)``."""
        sentence = self._buf[self._emit:run_end].strip()
        index = self._skip_separators(run_end)
        self._emit = index
        self._token_start = index
        return sentence, index
