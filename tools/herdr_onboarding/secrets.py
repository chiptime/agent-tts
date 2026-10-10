"""Secret persistence: the single ``redact()`` boundary and the atomic
mode-600 merge writer for ``~/.config/herdr-brain/env`` (AT-11 task 3.2,
design Decision 2).

Merge discipline (same as the plugin's managed-key writer):

- existing keys are preserved unless a new value is supplied;
- unknown lines and comments pass through byte-identical;
- the write is ``tempfile.mkstemp`` in the **same directory** →
  ``chmod 600`` → ``os.replace`` — atomic, no partial file at the
  target, no mode window;
- the directory is enforced to mode 700 and the file to mode 600;
- a crash at any point before the ``os.replace`` leaves the previous
  content intact and removes the temporary file.

No secret value is ever formatted into an exception message: the only
guarded rejection (a multi-line value) names the key, not the value.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Mapping, Optional, Sequence, Union

from herdr_onboarding import resolve as _resolve

ENV_DIR_MODE = 0o700
ENV_FILE_MODE = 0o600

ENV_DIR_NAME = "herdr-brain"
ENV_FILE_NAME = "env"

_REDACT_MARK = "***"
_REDACT_MIN_LENGTH = 4


def redact(text: str, secret_values: Sequence[Optional[str]]) -> str:
    """The single diagnostic boundary: replace every registered secret.

    Values shorter than :data:`_REDACT_MIN_LENGTH` characters are left
    alone (redacting tiny fragments mangles ordinary text without
    protecting anything).  Longer values are replaced longest-first so
    overlapping fragments cannot shield each other.
    """
    values = sorted(
        {v for v in secret_values if v and len(v) >= _REDACT_MIN_LENGTH},
        key=len,
        reverse=True,
    )
    for value in values:
        text = text.replace(value, _REDACT_MARK)
    return text


def brain_env_path(env: Optional[Mapping[str, str]] = None) -> Path:
    """``<XDG_CONFIG_HOME or ~/.config>/herdr-brain/env``."""
    environ = os.environ if env is None else env
    directory = _resolve._config_dir(dict(environ))
    if directory is None:
        raise _resolve.ResolutionError(
            "cannot resolve the config directory for the brain env file: "
            "set HOME or XDG_CONFIG_HOME"
        )
    return directory / ENV_DIR_NAME / ENV_FILE_NAME


def _split_key(stripped_line: str) -> Optional[str]:
    if "=" not in stripped_line:
        return None
    return stripped_line.split("=", 1)[0].strip()


def merge_env_file(
    path: Union[str, Path],
    updates: Mapping[str, str],
    *,
    _replace=os.replace,
) -> Path:
    """Merge-writes ``updates`` into a sourcable ``KEY=VALUE`` file.

    Unknown lines and comments are preserved byte-identical; updated
    keys keep their original position and line ending; missing keys are
    appended.  The file ends up mode 600 and its directory mode 700.
    ``_replace`` exists for the crash-mid-write test seam; production
    callers never pass it.
    """
    target = Path(path)
    for key, value in updates.items():
        if "\n" in value or "\r" in value:
            raise ValueError(
                f"refusing to write a multi-line value for {key}: env-file "
                "values must be single-line"
            )
    target.parent.mkdir(parents=True, exist_ok=True, mode=ENV_DIR_MODE)
    os.chmod(target.parent, ENV_DIR_MODE)

    text = target.read_text(encoding="utf-8") if target.exists() else ""
    lines = text.splitlines(keepends=True)
    remaining = dict(updates)
    rebuilt: list = []
    for line in lines:
        active = line.split("#", 1)[0].strip()
        key = _split_key(active)
        if key is not None and key in remaining:
            ending = line[len(line.rstrip("\r\n")):]
            rebuilt.append(f"{key}={remaining.pop(key)}{ending}")
        else:
            rebuilt.append(line)
    appended = ""
    if remaining:
        prefix = "" if not text or text.endswith("\n") else "\n"
        appended = prefix + "".join(f"{k}={v}\n" for k, v in remaining.items())

    handle_fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write("".join(rebuilt) + appended)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, ENV_FILE_MODE)
        _replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return target
