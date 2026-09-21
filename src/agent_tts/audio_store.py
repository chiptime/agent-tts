"""Rendered-audio store convention: directory layout, file naming, and retention.

Contract (kept deliberately boring):

- WHO WRITES: the herdr-tts bash watcher (separate repo) renders finished
  turn audio directly into this store, passing the path returned by
  :func:`store_path` to the engine via the existing ``--output`` flag.
  This module only owns the convention (directory, naming), never the bytes.
- WHO PRUNES: there is no daemon in this repo. The retention sweep runs
  best-effort at every CLI start (:func:`prune_expired`, hooked from
  ``agent_tts.cli.main``), following the podcast.py prune precedent.
- FAIL-OPEN EVERYWHERE: a broken store (missing dir, bad permissions,
  unreadable entries) must never break synthesis or startup. Pruning
  swallows all filesystem errors and deletes whole date-partition
  directories only — never individual files inside a current partition.

Layout: ``<audio_dir>/YYYY-MM-DD/<epoch>-<pane><suffix>``, e.g.
``~/.local/share/agent-tts/audio/2026-09-20/1758360000-p0.mp3``.
Knob: ``AGENT_TTS_AUDIO_RETENTION_DAYS`` (legacy ``TTS_AUDIO_RETENTION_DAYS``
honored), default 0 (persistence is opt-in); a positive value enables
persistence and defines the pruning window in days.
"""

import os
import re
import shutil
import struct
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import miniaudio

ENV_AUDIO_DIR = "AGENT_TTS_AUDIO_DIR"
ENV_RETENTION_DAYS = "AGENT_TTS_AUDIO_RETENTION_DAYS"
ENV_RETENTION_DAYS_LEGACY = "TTS_AUDIO_RETENTION_DAYS"

DEFAULT_RETENTION_DAYS = 0

# Date-partition directory names are strictly YYYY-MM-DD; anything else in
# the store root is foreign and must be left alone.
_DATE_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Panes come from external tools (tmux, herdr); keep only filesystem-safe chars.
_PANE_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def audio_dir() -> str:
    """Returns the audio store root (env-overridable, read live).

    Deliberately does NOT honor ``XDG_DATA_HOME``: ``voices.get_store_root``
    does not either, and one convention beats two half-conventions.
    """
    return os.environ.get(
        ENV_AUDIO_DIR,
        os.path.join(Path.home(), ".local", "share", "agent-tts", "audio"),
    )


def retention_days() -> int:
    """Returns the retention window in days (default 0: persistence off; > 0 enables)."""
    raw = (
        os.environ.get(ENV_RETENTION_DAYS, "")
        or os.environ.get(ENV_RETENTION_DAYS_LEGACY, "")
    ).strip()
    if not raw:
        return DEFAULT_RETENTION_DAYS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    if value <= 0:
        return 0
    return value


def store_path(pane: str, suffix: str = ".mp3", now: Optional[float] = None) -> str:
    """Returns (and prepares) the store path for one rendered turn.

    Shape: ``<audio_dir>/<YYYY-MM-DD>/<int(epoch)>-<sanitized-pane><suffix>``.
    The date directory is created eagerly so the caller (the herdr watcher)
    can write straight into it.
    """
    epoch = int(time.time() if now is None else now)
    date_dir = os.path.join(audio_dir(), date.fromtimestamp(epoch).isoformat())
    os.makedirs(date_dir, exist_ok=True)
    safe = _PANE_SAFE_RE.sub("_", pane) or "-"
    return os.path.join(date_dir, f"{epoch}-{safe}{suffix}")


def _pcm_to_wav(pcm: bytes, sample_rate: int, nchannels: int) -> bytes:
    """Wraps 16-bit PCM bytes in one canonical 44-byte-header WAV.

    Same header layout as ``providers/kokoro.py``'s ``_floats_to_wav``
    (PCM format 1, 16 bits per sample), generalized over channel count.
    """
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    header += b"fmt " + struct.pack(
        "<IHHIIHH",
        16,
        1,
        nchannels,
        sample_rate,
        sample_rate * nchannels * 2,
        nchannels * 2,
        16,
    )
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def merge_chunks_to_audio(chunks: list[bytes]) -> bytes:
    """Merges pipelined-rendered chunks into one playable audio blob.

    Two modes, sniffed from the FIRST chunk:

    - WAV (first chunk starts with ``b"RIFF"``): every chunk is decoded with
      miniaudio and all chunks must share the same sample rate and channel
      count (otherwise ``ValueError``); the 16-bit samples are concatenated
      in playback order into ONE canonical 44-byte-header WAV built by
      :func:`_pcm_to_wav` (same header layout as ``providers/kokoro.py``).
    - MP3 (anything else): MP3 frames concatenate legitimately, so the chunks
      are simply joined byte-wise.

    An empty list yields ``b""``. Undecodable WAV chunks raise ``ValueError``;
    callers are expected to fail open (playback already succeeded).
    """
    if not chunks:
        return b""
    if not chunks[0].startswith(b"RIFF"):
        return b"".join(chunks)

    sample_rate = 0
    nchannels = 0
    pcm_parts = []
    for idx, chunk in enumerate(chunks):
        try:
            decoded = miniaudio.decode(bytes(chunk))
        except Exception as e:
            raise ValueError(f"chunk {idx} is not decodable audio: {e}") from e
        if sample_rate == 0:
            sample_rate = decoded.sample_rate
            nchannels = decoded.nchannels
        elif decoded.sample_rate != sample_rate or decoded.nchannels != nchannels:
            raise ValueError(
                f"chunk {idx} format mismatch: {decoded.sample_rate}Hz/{decoded.nchannels}ch "
                f"!= {sample_rate}Hz/{nchannels}ch"
            )
        pcm_parts.append(decoded.samples.tobytes())
    return _pcm_to_wav(b"".join(pcm_parts), sample_rate, nchannels)


def prune_expired(now: Optional[float] = None) -> int:
    """Removes expired date-partition directories; returns how many were removed.

    A partition is expired when its date is strictly older than
    ``today - retention_days`` (the boundary day itself survives). Whole
    directories are removed with ``shutil.rmtree`` — never individual files —
    so a partition still being written today can never lose entries mid-run.
    Never raises: retention pruning is best-effort by contract.
    """
    retention = retention_days()
    if retention == 0:
        return 0

    epoch = time.time() if now is None else now
    cutoff = date.fromtimestamp(epoch) - timedelta(days=retention)

    removed = 0
    try:
        with os.scandir(audio_dir()) as entries:
            for entry in entries:
                if not _DATE_NAME_RE.match(entry.name) or not entry.is_dir():
                    continue
                try:
                    dir_date = date.fromisoformat(entry.name)
                except ValueError:
                    continue
                if dir_date >= cutoff:
                    continue
                shutil.rmtree(entry.path, ignore_errors=True)
                if not os.path.exists(entry.path):
                    removed += 1
    except OSError:
        # Unreadable store: keep whatever we removed so far and move on.
        return removed
    return removed
