"""STT consent step: size selection, download, contract verify, refusal
(AT-11 task 3.4, design slice 18, Decision 6).  Brain role only.

Hard rules (spec "Explicit STT model consent" / "STT refusal preserves the
degraded journey"):

- NOTHING is downloaded without explicit consent: a size (``tiny``,
  ``base``, ``small``) given by flag, ``HERDR_ONBOARDING_STT`` or an
  interactive answer is the consent.  No answer, ``none``, a blank line or
  EOF is not.  A non-interactive run with no answer is simply "not
  consented" (it prints how to opt in) — it does not fail, so unattended
  startup never blocks.
- The download is the brain's own, only path::

      <python> -m herdr_brain.stt pull        (HERDR_BRAIN_STT_MODEL=<size>,
                                               AGENT_TTS_STT_MODEL=<size>)

  which in the default ``engine`` backend delegates to the engine pull CLI.
  Both variables are set because the two backends read different names.
  stdout is discarded so ``--json`` stays one record; progress streams on
  stderr.  The wizard never imports ``herdr_brain`` or ``agent_tts``.
- Offline checks never download: :func:`model_cached` is a pure read of
  the Hugging Face cache directory layout (the same file set the brain's
  and the engine's cache probes require).  A model already present skips
  the pull and is still verified and recorded.
- After a download the speech-surface contract is verified through the
  public CLI (``herdr-tts --contract-version`` >= 1, ``contracts/
  tts-brain-v1`` §2.1); the choice is persisted to the brain env file only
  once both succeed, so a failed run leaves nothing half-recorded and the
  next run resumes from the offline cache.  Failure exits ``40`` (no
  marker) and names the manual pull command.
- Refusal is a successful, explicit outcome (``stt: "none"``).  The
  runtime already reports the truth: ``/health`` ``stt`` is
  ``loading | ready | unavailable`` (design Decision 6) — ``unavailable``
  after refusal, never ``degraded`` (a ``tts``-only value);
  ``/transcribe`` answers 503; ``/ask`` and its ``audio_url`` do not
  depend on STT.  Nothing here touches the frozen contracts.
- A persisted choice (``HERDR_BRAIN_STT_MODEL`` in the brain env file) is a
  preference: a re-run preserves it and never re-asks or re-downloads.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Mapping, Optional

from herdr_onboarding import prompts, secrets
from herdr_onboarding import resolve as _resolve
from herdr_onboarding.steps.keymap import execute, plugin_launcher

if TYPE_CHECKING:  # pragma: no cover — import-cycle-free typing only
    from herdr_onboarding.wizard import RunContext

SIZES = ("tiny", "base", "small")
CHOICES = SIZES + ("none",)

BRAIN_MODEL_KEY = "HERDR_BRAIN_STT_MODEL"
ENGINE_MODEL_KEY = "AGENT_TTS_STT_MODEL"
PULL_COMMAND = "python -m herdr_brain.stt pull"
MIN_CONTRACT_VERSION = 1

# Approximate on-disk sizes shown so the consent is informed.
_SIZE_HINT = "tiny ~75 MB, base ~145 MB, small ~480 MB"

# Required files, mirroring the brain / engine offline cache probes.
_REQUIRED = ("config.json", "model.bin", "tokenizer.json")
_VOCABULARY = ("vocabulary.txt", "vocabulary.json")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def hf_cache_root(env: Mapping[str, str]) -> Path:
    """Hugging Face hub cache: ``HF_HUB_CACHE`` → ``HF_HOME/hub`` →
    ``XDG_CACHE_HOME/huggingface/hub`` → ``~/.cache/huggingface/hub``."""
    explicit = env.get("HF_HUB_CACHE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    home = env.get("HF_HOME", "").strip()
    if home:
        return Path(home).expanduser() / "hub"
    xdg = env.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser() / "huggingface" / "hub"
    user_home = env.get("HOME", "").strip()
    base = Path(user_home).expanduser() if user_home else Path.home()
    return base / ".cache" / "huggingface" / "hub"


def model_cached(size: str, env: Mapping[str, str]) -> bool:
    """True when a complete snapshot of the ``size`` model is already
    local.  Pure filesystem read: no network, no subprocess, no import of
    the downloader — safe as an offline check."""
    repo_dir = hf_cache_root(env) / f"models--Systran--faster-whisper-{size}"
    snapshots = repo_dir / "snapshots"
    try:
        revisions = [p for p in snapshots.iterdir() if p.is_dir()]
    except OSError:
        return False
    for revision in revisions:
        if all((revision / name).is_file() for name in _REQUIRED) and any(
            (revision / name).is_file() for name in _VOCABULARY
        ):
            return True
    return False


class SttStep:
    name = "stt"
    roles = ("brain",)

    def __init__(
        self,
        runner: Optional[Runner] = None,
        python: Optional[str] = None,
    ):
        self._runner = runner or subprocess.run
        self._python = python or sys.executable

    def run(self, ctx: "RunContext") -> None:
        env_file = secrets.brain_env_path(ctx.env)
        persisted = _resolve._read_env_key(env_file, BRAIN_MODEL_KEY)

        choice = prompts.option_or_env(ctx.options.stt, ctx.env, prompts.STT_ENV)
        if choice is not None:
            choice = prompts.validate_choice(choice, CHOICES, prompts.STT_ENV)
        elif persisted in SIZES:
            ctx.diagnostic(
                f"STT model '{persisted}' already chosen — preserved, nothing "
                "to download"
            )
            ctx.preferences["stt"] = persisted
            return
        else:
            answer = ctx.prompt_optional(
                "Download a speech-to-text (Whisper) model for voice input? "
                f"({'/'.join(CHOICES)}; {_SIZE_HINT}) [blank = none]"
            )
            if answer is None and not ctx.interactive:
                ctx.diagnostic(
                    "no STT consent given: no model will be downloaded — to "
                    "enable voice input pass --stt <tiny|base|small> or set "
                    f"{prompts.STT_ENV}, or run: {PULL_COMMAND}"
                )
            choice = _interpret_answer(answer)

        if choice == "none":
            ctx.preferences["stt"] = "none"
            ctx.diagnostic(
                "STT declined: no model downloaded. Onboarding continues — "
                "/health will report stt: unavailable and /transcribe "
                "answers 503; /ask and its audio_url are unaffected"
            )
            return

        downloaded = not model_cached(choice, ctx.env)
        if downloaded:
            self._pull(ctx, choice)
        else:
            ctx.diagnostic(f"STT model '{choice}' already local — no download")
        self._verify_contract(ctx)
        secrets.merge_env_file(
            env_file, {BRAIN_MODEL_KEY: choice, ENGINE_MODEL_KEY: choice}
        )
        ctx.preferences["stt"] = choice
        ctx.preferences["stt_downloaded"] = downloaded
        ctx.diagnostic(f"STT model '{choice}' ready; /health will report stt: ready")

    def _pull(self, ctx: "RunContext", size: str) -> None:
        child_env = dict(ctx.env)
        child_env[BRAIN_MODEL_KEY] = size
        child_env[ENGINE_MODEL_KEY] = size
        ctx.diagnostic(f"downloading the '{size}' STT model (this can take minutes)…")
        try:
            completed = self._runner(
                [self._python, "-m", "herdr_brain.stt", "pull"],
                env=child_env,
                stdout=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise RuntimeError(
                f"STT model download could not start ({type(exc).__name__}: "
                f"{exc}); nothing was recorded. Retry with: {PULL_COMMAND}"
            ) from None
        if completed.returncode != 0:
            raise RuntimeError(
                f"STT model download failed (exit {completed.returncode}); "
                f"nothing was recorded. Retry with: {PULL_COMMAND}"
            )

    def _verify_contract(self, ctx: "RunContext") -> None:
        try:
            launcher = str(plugin_launcher(ctx.env))
        except _resolve.ResolutionError as exc:
            raise RuntimeError(
                f"speech-surface contract check could not run: {exc}"
            ) from None
        code, output = execute(
            self._runner, ctx, ["bash", launcher, "--contract-version"]
        )
        version = _parse_version(output) if code == 0 else None
        if version is None or version < MIN_CONTRACT_VERSION:
            raise RuntimeError(
                "speech-surface contract check failed: herdr-tts "
                f"--contract-version exited {code} and printed "
                f"{(output or '<nothing>')[:80]!r} (expected >= "
                f"{MIN_CONTRACT_VERSION}); nothing was recorded"
            )


def _interpret_answer(answer: Optional[str]) -> str:
    """Interactive answer → choice.  Anything short of a size is a refusal
    (blank, EOF, ``n``/``no``): consent must be spelled out."""
    if not answer or answer.strip().lower() in ("n", "no"):
        return "none"
    return prompts.validate_choice(answer, CHOICES, "the STT answer")


def _parse_version(output: str) -> Optional[int]:
    lines: List[str] = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return int(lines[-1])
    except ValueError:
        return None
