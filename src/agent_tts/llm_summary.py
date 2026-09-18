"""Optional LLM-powered executive summary via locally installed CLIs.

When ``--llm-summary`` is requested, the engine delegates the condensation
of long outputs to the first locally installed LLM CLI among:

1. ``claude -p``  (Claude Code print mode)
2. ``codex exec`` (Codex CLI)
3. ``ollama run <model>`` (default model ``qwen2.5:0.5b``, overridable via
   the ``AGENT_TTS_OLLAMA_MODEL`` environment variable)

Safety/quality contract:

- The prompt is ALWAYS piped through stdin with ``shell=False``: there is
  no command-injection surface, and arbitrarily long inputs are supported.
- Every subprocess attempt is capped by a ~10s timeout; a timeout (or any
  other failure) counts as a provider failure.
- Empty, whitespace-only or oversized outputs count as failures.

ANY failure (CLI absent, non-zero exit, timeout, bad output) returns
``None`` so the caller can transparently fall back to the offline
``--tldr`` heuristics in :mod:`agent_tts.summarizer`. Every fallback
reason is logged to stderr.
"""

import os
import shutil
import subprocess
import sys
from typing import Callable, List, Optional

MAX_OUTPUT_CHARS = 400
DEFAULT_TIMEOUT_SEC = 10.0
DEFAULT_OLLAMA_MODEL = "qwen2.5:0.5b"
OLLAMA_MODEL_ENV = "AGENT_TTS_OLLAMA_MODEL"

_SUMMARY_INSTRUCTION = (
    "Summarize the following text as ONE single concise sentence, "
    "written in the SAME language as the input text "
    "(the sentence will be spoken aloud). "
    "Reply with ONLY that sentence: no quotes, no prefixes, no extra text."
)

# Runner contract: (argv, stdin_prompt, timeout_sec) -> stdout text.
# Injected fakes replace this in tests; the default shells out (safely).
Runner = Callable[[List[str], str, float], str]


class ProviderFailedError(Exception):
    """A provider CLI exited with a non-zero status."""


def _resolve_ollama_model(ollama_model: Optional[str] = None) -> str:
    """Explicit argument wins, then the env override, then the default."""
    if ollama_model:
        return ollama_model
    return os.environ.get(OLLAMA_MODEL_ENV, DEFAULT_OLLAMA_MODEL)


def available_providers(ollama_model: str) -> List[List[str]]:
    """Returns the argv prefixes of installed provider CLIs, priority order first."""
    candidates: List[List[str]] = []
    if shutil.which("claude"):
        candidates.append(["claude", "-p"])
    if shutil.which("codex"):
        candidates.append(["codex", "exec"])
    if shutil.which("ollama"):
        candidates.append(["ollama", "run", ollama_model])
    return candidates


def _subprocess_runner(cmd: List[str], prompt: str, timeout_sec: float) -> str:
    """Runs one provider CLI, feeding the prompt via stdin (never a shell)."""
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        shell=False,
    )
    if proc.returncode != 0:
        err_tail = (proc.stderr or "").strip().splitlines()
        detail = f": {err_tail[-1]}" if err_tail else ""
        raise ProviderFailedError(f"exit code {proc.returncode}{detail}")
    return proc.stdout or ""


def _clean_output(raw: str) -> str:
    """Collapses the model output into a single spoken line."""
    return " ".join((raw or "").split())


def summarize_with_llm(
    text: str,
    runner: Optional[Runner] = None,
    ollama_model: Optional[str] = None,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
) -> Optional[str]:
    """Produces ONE executive sentence via the first installed provider CLI.

    Returns ``None`` whenever no CLI is available or every attempt fails;
    the caller then falls back to the offline ``--tldr`` heuristics.
    """
    if runner is None:
        runner = _subprocess_runner

    text_clean = (text or "").strip()
    if not text_clean:
        return None

    providers = available_providers(_resolve_ollama_model(ollama_model))
    if not providers:
        print(
            "LLM summary: no provider CLI found (claude, codex, ollama); "
            "falling back to offline --tldr heuristics",
            file=sys.stderr,
        )
        return None

    prompt = f"{_SUMMARY_INSTRUCTION}\n\n{text_clean}"

    for argv in providers:
        name = argv[0]
        try:
            raw = runner(argv, prompt, timeout_sec)
        except subprocess.TimeoutExpired:
            print(
                f"LLM summary: {name} timed out after {timeout_sec:g}s",
                file=sys.stderr,
            )
            continue
        except Exception as exc:  # missing binary race, non-zero exit, OSError...
            print(f"LLM summary: {name} failed: {exc}", file=sys.stderr)
            continue

        sentence = _clean_output(raw)
        if not sentence:
            print(f"LLM summary: {name} returned empty output", file=sys.stderr)
            continue
        if len(sentence) > MAX_OUTPUT_CHARS:
            print(
                f"LLM summary: {name} output too long "
                f"({len(sentence)} > {MAX_OUTPUT_CHARS} chars)",
                file=sys.stderr,
            )
            continue
        return sentence

    print(
        "LLM summary: all providers failed; falling back to offline --tldr heuristics",
        file=sys.stderr,
    )
    return None
