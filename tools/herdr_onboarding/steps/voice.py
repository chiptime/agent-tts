"""Voice step: provider (and optional default voice) preference
(AT-11 task 3.3, design slice 17).

The preference is persisted to the plugin's managed settings store
(``<XDG_CONFIG_HOME or ~/.config>/herdr-tts/config.env``, overridable with
``HERDR_TTS_CONFIG_FILE`` exactly like the launcher), as the same
``KEY="value"`` lines the launcher's ``config_set`` writes.  ``config_set``
is not exposed on the launcher's command line, so this module mirrors its
upsert discipline instead of inventing a second format:

- the first occurrence of the key is replaced in place, duplicates are
  dropped, an absent key is added inside the managed block (or a new
  managed block appended);
- unknown lines and comments pass through byte-identical;
- the write is atomic (same-directory temp file, ``os.replace``) and the
  previous version is kept once as ``config.env.bak``;
- values are always written quoted and rejected when they contain a
  double quote or a line break, so the file stays bash-sourceable.

Answers, in order: flag, ``HERDR_ONBOARDING_VOICE_PROVIDER`` /
``HERDR_ONBOARDING_VOICE``, then one interactive question.  An existing
``TTS_PROVIDER`` is a preference: with no explicit answer it is preserved
and never re-asked (the re-run contract); an explicit answer is a
deliberate change.  Non-interactive runs with no answer leave the file
untouched (the launcher already defaults to ``edge``).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Optional

from herdr_onboarding import prompts
from herdr_onboarding import resolve as _resolve

if TYPE_CHECKING:  # pragma: no cover — import-cycle-free typing only
    from herdr_onboarding.wizard import RunContext

PROVIDERS = ("edge", "openai", "elevenlabs", "piper")
DEFAULT_PROVIDER = "edge"
PROVIDER_KEY = "TTS_PROVIDER"
VOICE_KEY = "TTS_VOICE"

CONFIG_DIR_NAME = "herdr-tts"
CONFIG_FILE_NAME = "config.env"
CONFIG_FILE_ENV = "HERDR_TTS_CONFIG_FILE"
BLOCK_START = "# >>> herdr-tts settings (managed by the settings popup) >>>"
BLOCK_END = "# <<< herdr-tts settings <<<"
NEW_FILE_MODE = 0o600  # the file may carry provider API keys


def config_path(env: Mapping[str, str]) -> Path:
    """The plugin ``config.env`` the launcher sources at startup."""
    override = env.get(CONFIG_FILE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    directory = _resolve._config_dir(dict(env))
    if directory is None:
        raise _resolve.ResolutionError(
            "cannot resolve the config directory for the plugin settings: "
            "set HOME or XDG_CONFIG_HOME"
        )
    return directory / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def read_config_value(path: Path, key: str) -> Optional[str]:
    """Value of ``key`` the launcher would see (``source`` semantics: the
    last assignment wins); ``None`` when absent or unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    found: Optional[str] = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].lstrip()
        if not stripped.startswith(f"{key}="):
            continue
        value = stripped[len(key) + 1:].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        found = value
    return found


def _matches_key(line: str, key: str) -> bool:
    stripped = line.lstrip()
    if stripped.startswith("export "):
        stripped = stripped[len("export "):].lstrip()
    return stripped.startswith(f"{key}=")


def upsert_config_value(path: Path, key: str, value: str, *, _replace=os.replace) -> None:
    """Atomic ``config_set`` mirror: upsert ``key="value"`` in ``path``."""
    if '"' in value or "\n" in value or "\r" in value:
        raise ValueError(
            f"refusing to write {key}: the value must be a single line "
            "without double quotes"
        )
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    out: list = []
    replaced = False
    in_block = False
    for line in existing.splitlines():
        if line == BLOCK_START:
            in_block = True
        if _matches_key(line, key):
            if not replaced:
                out.append(f'{key}="{value}"')
                replaced = True
            continue
        if line == BLOCK_END:
            if in_block and not replaced:
                out.append(f'{key}="{value}"')
                replaced = True
            in_block = False
        out.append(line)
    if not replaced:
        if existing.strip():
            out.append("")
        out.extend([BLOCK_START, f'{key}="{value}"', BLOCK_END])
    new_text = "\n".join(out) + "\n"
    if new_text == existing:
        return  # idempotent re-run: leave the file (and its mtime) alone

    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (path.stat().st_mode & 0o777) if path.is_file() else NEW_FILE_MODE
    if path.is_file():
        backup = path.with_name(path.name + ".bak")
        backup.write_bytes(path.read_bytes())
        os.chmod(backup, mode)
    handle_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(new_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        _replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


class VoiceStep:
    name = "voice"
    roles = ("plugin", "brain")

    def run(self, ctx: "RunContext") -> None:
        path = config_path(ctx.env)
        existing = read_config_value(path, PROVIDER_KEY)

        provider = prompts.option_or_env(
            ctx.options.voice_provider, ctx.env, prompts.VOICE_PROVIDER_ENV
        )
        if provider is not None:
            provider = prompts.validate_choice(
                provider, PROVIDERS, prompts.VOICE_PROVIDER_ENV
            )
        elif existing is None:
            answer = ctx.prompt_optional(
                f"Voice provider ({'/'.join(PROVIDERS)}) "
                f"[blank = {DEFAULT_PROVIDER}, Ctrl-D = keep defaults]"
            )
            if answer is not None:
                provider = prompts.validate_choice(
                    answer or DEFAULT_PROVIDER, PROVIDERS, "the voice provider answer"
                )

        voice = prompts.option_or_env(ctx.options.voice, ctx.env, prompts.VOICE_ENV)

        if provider is not None:
            upsert_config_value(path, PROVIDER_KEY, provider)
            ctx.diagnostic(f"voice provider set to {provider} ({path})")
        elif existing is not None:
            ctx.diagnostic(
                f"voice provider {existing} already configured — preserved"
            )
        if voice is not None:
            upsert_config_value(path, VOICE_KEY, voice)
            ctx.diagnostic(f"default voice set to {voice} ({path})")

        effective = provider or existing or DEFAULT_PROVIDER
        ctx.preferences["voice"] = effective
        if voice is not None:
            ctx.preferences["voice_name"] = voice
