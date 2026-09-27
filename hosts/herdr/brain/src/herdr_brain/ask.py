"""CLI smoke entrypoint: ask the brain a question without any mic or UI.

Usage:
    python -m herdr_brain.ask "dime en que estas trabajando" [--no-audio] [--json]

Works without a microphone; --no-audio additionally skips TTS so it runs on
machines where piper is not configured.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Optional, Sequence

from .config import Settings, load_settings
from .llm import BrainLLM, BrainLLMError
from .tools import BrainTools
from .tts import TTSError, new_audio_path, render_mp3

_LLMFactory = Callable[[Settings, BrainTools], BrainLLM]


def default_llm_factory(settings: Settings, tools: BrainTools) -> BrainLLM:
    return BrainLLM(settings, tools)


def main(
    argv: Optional[Sequence[str]] = None,
    llm_factory: Optional[_LLMFactory] = None,
    tts_renderer: Callable = render_mp3,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m herdr_brain.ask",
        description="Ask the herdr-brain a question (text in, spoken-answer file out).",
    )
    parser.add_argument("text", nargs="+", help="Question for the brain")
    parser.add_argument("--no-audio", action="store_true", help="Skip TTS rendering")
    parser.add_argument("--json", action="store_true", help="Print the full JSON result")
    args = parser.parse_args(argv)

    settings = load_settings()
    factory = llm_factory or default_llm_factory
    try:
        llm = factory(settings, BrainTools(settings))
        question = " ".join(args.text)
        result = llm.ask(question)
    except BrainLLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        import json

        payload = dict(result)
        payload["audio_path"] = None
    else:
        print(result["answer"])

    if args.no_audio:
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        return 0

    out_path = new_audio_path(settings)
    try:
        tts_renderer(settings, result["answer"], out_path)
    except Exception as exc:  # noqa: BLE001 — TTS must never break the answer
        print(f"warning: tts rendering failed ({exc})", file=sys.stderr)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.json:
        payload["audio_path"] = str(out_path)
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"audio: {out_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
