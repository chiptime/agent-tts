#!/usr/bin/env python3
"""Segmented speech rendering (voice-stack VS2.2, contract tts-brain-v2.md).

Produces the engine's sentence groups as atomic per-segment MP3s and
re-publishes ``manifest.json`` atomically after EVERY segment (monotonic
``revision``, ``is_complete`` false until the terminal publish). The text
travels by FILE; argv carries only server-generated paths and the opaque
request id, which is an EXPLICIT input carried verbatim into the manifest.

Exit codes: 0 complete · 3 cancelled (terminal manifest already published)
· other non-zero failure. Never a merged artifact; never a hidden sequence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Callable, List, Optional

MANIFEST_NAME = "manifest.json"
TMP_SUFFIX = ".tmp"


class CancelledError(RuntimeError):
    """The producer was cancelled between/inside groups."""


class RenderError(RuntimeError):
    """A group failed to synthesize."""


# --- seams (tests inject these; production uses the engine bridge) ---------


def engine_split_groups(text: str) -> List[str]:
    """The ENGINE's own grouping (agent_tts.text.split_sentence_groups)."""
    import tts_engine  # noqa: F401  — herdr socket/lock env defaults first

    from agent_tts.text import split_sentence_groups

    return split_sentence_groups(text)


def engine_synth(text: str, voice: str, rate: str, stop_checker) -> bytes:
    import tts_engine  # noqa: F401

    from agent_tts.cli import synthesize

    return asyncio.run(
        synthesize(text, voice=voice, rate=rate, stop_checker=stop_checker)
    )


# --- manifest ----------------------------------------------------------------


def write_manifest(out_dir: Path, manifest: dict) -> None:
    """Atomic publish: tmp in the SAME directory + os.replace."""
    tmp = out_dir / (MANIFEST_NAME + TMP_SUFFIX)
    manifest_path = out_dir / MANIFEST_NAME
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, manifest_path)


def build_manifest(request_id: str, voice: str, rate: str, revision: int,
                   is_complete: bool, cancelled: bool, error: Optional[str],
                   segments: List[dict]) -> dict:
    return {
        "speech_request_id": request_id,
        "voice": voice,
        "rate": rate,
        "revision": revision,
        "is_complete": is_complete,
        "cancelled": cancelled,
        "error": error,
        "segments": segments,
    }


# --- render loop ---------------------------------------------------------------


def render_segmented(
    text: str,
    out_dir: Path,
    request_id: str,
    voice: str = "",
    rate: str = "",
    *,
    splitter: Optional[Callable[[str], List[str]]] = None,
    synth: Optional[Callable[[str, str, str, Callable], bytes]] = None,
    stop_checker: Optional[Callable[[], bool]] = None,
) -> int:
    """Runs the whole production; returns the process exit code.

    ``splitter``/``synth`` are seams (tests); production uses the engine
    bridge functions above. ``stop_checker`` is consulted before every group
    (T4: provider stop_checker + abort between groups).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    split = splitter or engine_split_groups
    make_audio = synth or engine_synth
    stopped = stop_checker or (lambda: False)

    groups = split(text)
    segments: List[dict] = []
    revision = 0
    error: Optional[str] = None
    cancelled = False
    try:
        for index, group in enumerate(groups):
            if stopped():
                raise CancelledError("stop requested between groups")
            try:
                data = make_audio(group, voice, rate, stopped)
            except CancelledError:
                raise
            except Exception as exc:  # engine/provider failure: typed, no tmp
                raise RenderError(f"group {index} failed: {exc}") from exc
            if not data:
                if stopped():
                    raise CancelledError("stop requested during synthesis")
                raise RenderError(f"group {index} produced no audio")
            name = f"seg-{index:04d}.mp3"
            tmp = out_dir / (name + TMP_SUFFIX)
            with open(tmp, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, out_dir / name)
            segments.append({"seq": index, "file": name, "bytes": len(data)})
            revision += 1
            write_manifest(out_dir, build_manifest(
                request_id, voice, rate, revision, False, False, None, segments))
        write_manifest(out_dir, build_manifest(
            request_id, voice, rate, revision, True, False, None, segments))
        return 0
    except CancelledError:
        cancelled = True
    except RenderError as exc:
        error = str(exc)
    # terminal publish: sequence metadata stays visible; tmp files die
    for leftover in out_dir.glob("*" + TMP_SUFFIX):
        try:
            leftover.unlink()
        except OSError:
            pass
    write_manifest(out_dir, build_manifest(
        request_id, voice, rate, revision, False, cancelled, error, segments))
    return 3 if cancelled else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="segmented_render.py", description=__doc__.splitlines()[0])
    parser.add_argument("out_dir")
    parser.add_argument("input_text_file",
                        help="text travels by file, never argv (v2 contract)")
    parser.add_argument("--speech-request-id", required=True)
    parser.add_argument("--voice", default="")
    parser.add_argument("--rate", default="")
    args = parser.parse_args(argv)
    text = Path(args.input_text_file).read_text(encoding="utf-8")
    return render_segmented(
        text, Path(args.out_dir), args.speech_request_id,
        args.voice, args.rate)


if __name__ == "__main__":
    sys.exit(main())
