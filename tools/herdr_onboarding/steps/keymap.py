"""Keymap step: adopt a keymap style, apply it, reload herdr
(AT-11 task 3.3, design slice 17).

The wizard reuses the plugin's own adoption mechanism — the very same
launcher commands the installer runs (``scripts/install.sh``, "Keymap
adoption policy"), never a parallel implementation::

    bash <tts-plugin>/bin/herdr-tts keymap adopt --style <style> [--force]
    bash <tts-plugin>/bin/herdr-tts keymap apply
    herdr server reload-config        # automatic: no manual reload step

Policy (spec "Voice and keymap preferences"):

- an existing ``keymap.json`` is NEVER overwritten without explicit
  consent (``--replace-keymap``, ``HERDR_ONBOARDING_REPLACE_KEYMAP`` or an
  interactive yes); without consent the file stays byte-identical and the
  user is told so; consent maps to the launcher's own ``--force``;
- ``none`` is an explicit opt-out: no command runs and no artifact is
  created;
- with no explicit style an existing keymap is preserved (never re-asked);
  without one, an interactive run offers ``menu`` (the installer's
  collision-free style) and a non-interactive run changes nothing — the
  installer already adopted ``menu`` on a fresh install;
- like the installer, a failing adopt/apply/reload is a warning with the
  command to finish later, not a failed onboarding: the install stays
  healthy and nothing half-written is left behind (the launcher writes
  ``keymap.json`` atomically and ``keymap apply`` rolls back).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Mapping, Optional, Tuple

from herdr_onboarding import prompts
from herdr_onboarding import resolve as _resolve

if TYPE_CHECKING:  # pragma: no cover — import-cycle-free typing only
    from herdr_onboarding.wizard import RunContext

STYLES = ("menu", "direct", "ctrlalt")
CHOICES = STYLES + ("none",)
DEFAULT_STYLE = "menu"

KEYMAP_FILE_ENV = "HERDR_TTS_KEYMAP_FILE"
TTS_HOME_ENV = "HERDR_TTS_HOME"
PLUGIN_SUBDIR = Path("hosts") / "herdr" / "tts-plugin"
COMMAND_TIMEOUT_S = 60

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def keymap_path(env: Mapping[str, str]) -> Path:
    """``<XDG_CONFIG_HOME or ~/.config>/herdr-tts/keymap.json`` (the
    launcher's ``KEYMAP_FILE``, honouring ``HERDR_TTS_KEYMAP_FILE``)."""
    override = env.get(KEYMAP_FILE_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    directory = _resolve._config_dir(dict(env))
    if directory is None:
        raise _resolve.ResolutionError(
            "cannot resolve the config directory for the keymap: set HOME "
            "or XDG_CONFIG_HOME"
        )
    return directory / "herdr-tts" / "keymap.json"


def plugin_launcher(env: Mapping[str, str]) -> Path:
    """The plugin launcher: an explicit usable ``HERDR_TTS_HOME`` wins,
    otherwise the resolved monorepo root's ``hosts/herdr/tts-plugin``."""
    explicit = env.get(TTS_HOME_ENV, "").strip()
    if explicit and Path(explicit).expanduser().is_dir():
        base = Path(explicit).expanduser()
    else:
        base = _resolve.resolve_root(env=dict(env)) / PLUGIN_SUBDIR
    return base / "bin" / "herdr-tts"


def execute(
    runner: Runner, ctx: "RunContext", argv: List[str]
) -> Tuple[int, str]:
    """Runs one external command; ``(returncode, combined output)``.

    A missing executable or a timeout is a non-zero result, not an
    exception, so callers decide how a failure degrades.
    """
    try:
        completed = runner(
            argv,
            capture_output=True,
            text=True,
            env=dict(ctx.env),
            timeout=COMMAND_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    text = f"{completed.stdout or ''}{completed.stderr or ''}".strip()
    return int(completed.returncode), text


class KeymapStep:
    name = "keymap"
    roles = ("plugin", "brain")

    def __init__(self, runner: Optional[Runner] = None):
        self._runner = runner or subprocess.run

    def run(self, ctx: "RunContext") -> None:
        keymap_file = keymap_path(ctx.env)
        exists = keymap_file.exists()

        style = prompts.option_or_env(
            ctx.options.keymap_style, ctx.env, prompts.KEYMAP_STYLE_ENV
        )
        if style is not None:
            style = prompts.validate_choice(style, CHOICES, prompts.KEYMAP_STYLE_ENV)
        elif exists:
            ctx.diagnostic(f"existing keymap at {keymap_file} — left untouched")
            ctx.preferences["keymap"] = "existing"
            return
        else:
            answer = ctx.prompt_optional(
                f"Keymap style ({'/'.join(CHOICES)}) "
                f"[blank = {DEFAULT_STYLE}, Ctrl-D = skip]"
            )
            if answer is None:
                ctx.preferences["keymap"] = "skipped"
                return
            style = prompts.validate_choice(
                answer or DEFAULT_STYLE, CHOICES, "the keymap style answer"
            )

        if style == "none":
            ctx.diagnostic("keymap setup declined (none) — no keymap artifacts created")
            ctx.preferences["keymap"] = "none"
            return

        if exists and not self._consent_to_replace(ctx, style):
            ctx.diagnostic(
                f"existing keymap at {keymap_file} preserved — pass "
                "--replace-keymap (or answer yes) to replace it with the "
                f"'{style}' map"
            )
            ctx.preferences["keymap"] = "existing"
            return

        self._adopt_apply_reload(ctx, style, force=exists)

    def _consent_to_replace(self, ctx: "RunContext", style: str) -> bool:
        if ctx.options.replace_keymap or prompts.is_truthy(
            ctx.env.get(prompts.REPLACE_KEYMAP_ENV)
        ):
            return True
        answer = ctx.prompt_optional(
            f"Replace your existing keymap with the '{style}' map? [y/N]"
        )
        return prompts.is_truthy(answer)

    def _adopt_apply_reload(self, ctx: "RunContext", style: str, *, force: bool) -> None:
        try:
            launcher = str(plugin_launcher(ctx.env))
        except _resolve.ResolutionError as exc:
            self._failed(ctx, f"cannot locate the plugin launcher: {exc}")
            return
        adopt = ["bash", launcher, "keymap", "adopt", "--style", style]
        if force:
            adopt.append("--force")
        for argv in (adopt, ["bash", launcher, "keymap", "apply"]):
            code, output = execute(self._runner, ctx, argv)
            if code != 0:
                self._failed(ctx, f"{' '.join(argv[2:])} exited {code}: {output}")
                return
        ctx.preferences["keymap"] = style
        ctx.diagnostic(f"keymap '{style}' adopted and applied")

        herdr = _resolve.resolve_herdr_bin(dict(ctx.env))
        code, output = execute(self._runner, ctx, [herdr, "server", "reload-config"])
        ctx.preferences["keymap_reloaded"] = code == 0
        if code == 0:
            ctx.diagnostic("herdr config reloaded with the new bindings")
        else:
            ctx.diagnostic(
                f"'herdr server reload-config' failed ({output}) — run it "
                "manually later"
            )

    @staticmethod
    def _failed(ctx: "RunContext", detail: str) -> None:
        ctx.preferences["keymap"] = "failed"
        ctx.diagnostic(
            f"keymap setup failed ({detail}); the install stays healthy — "
            "finish it later with: herdr-tts keymap init"
        )
