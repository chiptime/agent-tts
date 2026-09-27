"""Gapless chain assembly: N audio files as one session (AT-08, BLOQUE 1.3 hito Cadena).

RF-AT-08-4: ``--play-chain FILE...`` reproduces the files IN ORDER with a
configurable inter-item silence (default 0 ms — US-AT-08-3: nothing is
inserted) as ONE continuous PCM stream, and every control (seek, pause,
phrase/paragraph navigation) addresses chain-global positions through
the combined ``BoundaryMap`` this module builds.

Composition model (the smallest clean seam over what already exists):

- Decode reuses the file-decode path of ``--play-file``
  (``miniaudio.decode`` per file; ``merge_chunks_to_audio`` is not
  reused because it outputs bare merged WAV bytes with no boundary
  information — the verified landmine of the pre-implementation audit).
- Boundary composition reuses the streaming pipeline's primitive,
  ``boundaries.shift_boundary_map``: each per-file map is offset by its
  chain-global start (audio of the preceding files PLUS the preceding
  gaps) with sentence/word/paragraph indices rebased to run over the
  whole chain. Files without boundary metadata (the render store keeps
  none) get ONE synthetic navigation unit each — a sentence and a
  paragraph whose text is the file's basename — so phrase navigation
  walks file boundaries honestly instead of inventing text.
- The assembled stream is a decoded-like object (16-bit interleaved
  samples + rate/channels/width/duration): any session ``play()``
  accepts a decoded segment, it accepts the chain. All files must share
  one format (mismatched files raise :class:`ChainError`, mirroring
  ``merge_chunks_to_audio``'s discipline — resampling is out of scope).

Errors are one type, :class:`ChainError` (a ``ValueError``), naming the
offending path or item: the CLI turns them into its stderr+exit-1
discipline BEFORE contacting the daemon, and the daemon surfaces them
through the queue's failure visibility (A5) at dispatch time.
"""

from __future__ import annotations

import array
import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, List, Optional, Sequence, Tuple

import miniaudio

from agent_tts.boundaries import (
    BoundaryMap,
    Paragraph,
    Sentence,
    shift_boundary_map,
)

__all__ = [
    "ChainError",
    "ChainSource",
    "ChainItemView",
    "AssembledChain",
    "missing_chain_files",
    "decode_chain_files",
    "assemble_chain",
    "assemble_chain_files",
]


class ChainError(ValueError):
    """An unusable chain input: missing/undecodable file, format mismatch, bad gap."""


@dataclass(frozen=True)
class ChainSource:
    """One chain item: a decoded audio segment plus optional boundary metadata.

    ``name`` is the display name (the basename for file-loaded sources)
    used as the synthetic navigation unit's text. ``boundaries``, when
    present, is the per-file map composed onto chain-global positions.
    """

    decoded: Any
    name: str = ""
    boundaries: Optional[BoundaryMap] = None


@dataclass(frozen=True)
class ChainItemView:
    """Chain-global placement of one item (the trace seam watches these starts)."""

    index: int
    name: str
    start_sec: float
    duration_sec: float


@dataclass(frozen=True)
class AssembledChain:
    """The play()-ready continuous stream plus the combined boundary map."""

    decoded: Any
    boundaries: BoundaryMap
    items: Tuple[ChainItemView, ...]


def missing_chain_files(paths: Sequence[str]) -> List[str]:
    """Returns the paths that do not exist, in order (client-side discipline)."""
    return [path for path in paths if not os.path.exists(path)]


def decode_chain_files(
    paths: Sequence[str], *, decoder: Callable[[bytes], Any] = miniaudio.decode
) -> List[ChainSource]:
    """Reads and decodes every chain file; raises ChainError naming the offender.

    The decode seam is the same one ``--play-file`` uses
    (Daemon._play_file: read bytes, ``miniaudio.decode``); ``decoder`` is
    injectable for tests.
    """
    if not isinstance(paths, (list, tuple)) or not paths:
        raise ChainError("chain must be a non-empty list of file paths")
    sources = []
    for path in paths:
        if not isinstance(path, str) or not path.strip():
            raise ChainError(f"chain file path must be a non-empty string: {path!r}")
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise ChainError(f"chain file not readable: {path}: {e}") from e
        try:
            decoded = decoder(data)
        except Exception as e:
            raise ChainError(f"chain file not decodable audio: {path}: {e}") from e
        sources.append(ChainSource(decoded=decoded, name=os.path.basename(path)))
    return sources


def _silence_samples(sample_rate: int, nchannels: int, seconds: float) -> array.array:
    """Interleaved zero samples for ``seconds`` of silence (frame-aligned)."""
    return array.array("h", [0] * (int(round(sample_rate * seconds)) * nchannels))


def assemble_chain(sources: Sequence[ChainSource], gap_ms: float = 0.0) -> AssembledChain:
    """Concatenates the sources into one continuous stream + combined map.

    The gap (``gap_ms``, default 0) is inserted BETWEEN items only —
    never around the chain. Every source must share one sample format.
    """
    if not isinstance(gap_ms, (int, float)) or isinstance(gap_ms, bool) or gap_ms < 0:
        raise ChainError(f"chain gap must be a non-negative number of milliseconds: {gap_ms!r}")
    if not sources:
        raise ChainError("chain must contain at least one file")

    first = sources[0].decoded
    sample_rate = first.sample_rate
    nchannels = first.nchannels
    sample_width = getattr(first, "sample_width", 2)
    for index, source in enumerate(sources):
        decoded = source.decoded
        if (
            decoded.sample_rate != sample_rate
            or decoded.nchannels != nchannels
            or getattr(decoded, "sample_width", 2) != sample_width
        ):
            raise ChainError(
                f"chain item {index} format mismatch: "
                f"{decoded.sample_rate}Hz/{decoded.nchannels}ch/"
                f"{getattr(decoded, 'sample_width', 2) * 8}bit != "
                f"{sample_rate}Hz/{nchannels}ch/{sample_width * 8}bit"
            )

    gap_sec = float(gap_ms) / 1000.0
    silence = _silence_samples(sample_rate, nchannels, gap_sec)

    pcm = array.array("h")
    sentences: List[Sentence] = []
    words: List = []
    paragraphs: List[Paragraph] = []
    items: List[ChainItemView] = []
    offset_sec = 0.0
    sent_base = word_base = para_base = 0
    for index, source in enumerate(sources):
        if index > 0:
            pcm.extend(silence)
            offset_sec += gap_sec
        samples = source.decoded.samples
        duration_sec = len(samples) / float(sample_rate * nchannels)
        name = source.name or f"item {index}"
        pcm.extend(samples)

        if source.boundaries is not None:
            shifted = shift_boundary_map(source.boundaries, offset_sec, sent_base, word_base, para_base)
            sentences.extend(shifted.sentences)
            words.extend(shifted.words)
            paragraphs.extend(shifted.paragraphs)
        else:
            # No metadata (the common render-store case): the file itself
            # is one navigation unit — honest text, chain-global bounds.
            sentences.append(
                Sentence(
                    index=sent_base,
                    start_sec=offset_sec,
                    duration_sec=duration_sec,
                    text=name,
                    paragraph_index=para_base,
                )
            )
            paragraphs.append(
                Paragraph(
                    index=para_base,
                    start_sec=offset_sec,
                    duration_sec=duration_sec,
                    text=name,
                    sentence_indices=[sent_base],
                )
            )
        items.append(
            ChainItemView(index=index, name=name, start_sec=offset_sec, duration_sec=duration_sec)
        )
        offset_sec += duration_sec
        sent_base = len(sentences)
        word_base = len(words)
        para_base = len(paragraphs)

    decoded = SimpleNamespace(
        sample_rate=sample_rate,
        nchannels=nchannels,
        sample_width=sample_width,
        duration=offset_sec,
        samples=pcm,
    )
    return AssembledChain(
        decoded=decoded,
        boundaries=BoundaryMap(sentences=sentences, words=words, paragraphs=paragraphs),
        items=tuple(items),
    )


def assemble_chain_files(
    paths: Sequence[str], gap_ms: float = 0.0, *, decoder: Callable[[bytes], Any] = miniaudio.decode
) -> AssembledChain:
    """Convenience: decode every file, then assemble (the daemon runner path)."""
    return assemble_chain(decode_chain_files(paths, decoder=decoder), gap_ms=gap_ms)
