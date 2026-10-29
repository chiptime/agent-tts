"""Operator-authored rows: quick replies, commands, key presets, launchers.

Four TOML files under the brain's config directory (default
``~/.config/herdr-brain``, overridable via ``HERDR_BRAIN_CONFIG_DIR``)
turn the operator's own habits into panel surfaces:

* ``quick-replies.toml`` — one-tap phrases for the reply dock, sent
  verbatim through the existing reply path;
* ``commands.toml`` — the slash palette for agent panes;
* ``keys.toml`` — labelled presets that may only draw keys from the
  existing send-key allowlist (same budget as a dialog answer);
* ``launchers.toml`` — the ONLY executable surface: an allowlist of
  exact argv rows. A client NAMES a row by its id and never supplies a
  command line; rows carrying control characters are skipped, not
  sanitized — an embedded newline could type a second line the operator
  never reviewed.

Validation is row by row, pure and total: a malformed row is discarded
with one reported reason (file, row, reason) while its healthy siblings
keep serving, and a broken or unreadable file is treated as empty
without poisoning the other three. Files reload by mtime on the next
request — one cheap ``stat`` per surface, guarded by a lock, no restart.
"""

from __future__ import annotations

import logging
import os
import shlex
import threading
import tomllib
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, NamedTuple, Optional, Tuple

from .herdr import MAX_SEND_KEYS, SEND_KEY_ALLOWLIST

logger = logging.getLogger(__name__)

#: Directory override for the four operator files; empty or unset falls
#: back to the XDG config home (same convention as ``config.env``).
ENV_CONFIG_DIR = "HERDR_BRAIN_CONFIG_DIR"

#: Directory name under the XDG config home.
CONFIG_DIRNAME = "herdr-brain"

FILE_QUICK_REPLIES = "quick-replies.toml"
FILE_COMMANDS = "commands.toml"
FILE_KEYS = "keys.toml"
FILE_LAUNCHERS = "launchers.toml"

TABLE_REPLIES = "replies"
TABLE_COMMANDS = "commands"
TABLE_KEYS = "keys"
TABLE_LAUNCHERS = "launchers"


# ── rows and reports ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Issue:
    """One validation finding: a skipped row, or a whole broken file.

    ``row`` is ``0`` for file-level problems and otherwise the 1-based
    ordinal of the row inside its ``[[table]]`` array.
    """

    file: str
    row: int
    reason: str

    def describe(self) -> str:
        where = self.file if self.row == 0 else f"{self.file} row {self.row}"
        return f"{where}: {self.reason}"


@dataclass(frozen=True)
class QuickReply:
    """A one-tap phrase for the reply dock."""

    id: str
    label: str
    text: str
    scope: str  # "" = global


@dataclass(frozen=True)
class Command:
    """A slash palette entry; the command text starts with ``/`` and may
    keep a trailing space as an argument hint (the composer then stays
    on ``/cmd `` instead of sending)."""

    id: str
    label: str
    command: str
    scope: str


@dataclass(frozen=True)
class KeyPreset:
    """A labelled sequence over the existing send-key allowlist."""

    id: str
    label: str
    keys: Tuple[str, ...]
    scope: str


@dataclass(frozen=True)
class Launcher:
    """One allowlisted argv row. Launchers start shells only."""

    id: str
    label: str
    argv: Tuple[str, ...]
    cwd: Optional[str]
    no_prompts: bool


@dataclass(frozen=True)
class LaunchPlan:
    """The exact execution a launcher row resolves to.

    ``command_line`` is the shell-quoting of ``argv`` for the one place
    it is ever needed: typing the line into a fresh shell tab. It is
    derived ONLY from operator-authored argv — never from client input.
    """

    row_id: str
    label: str
    argv: Tuple[str, ...]
    cwd: Optional[str]
    command_line: str
    no_prompts: bool


@dataclass(frozen=True)
class PaneConfig:
    """The three dock surfaces resolved for one pane's agent family."""

    quick_replies: Tuple[QuickReply, ...]
    commands: Tuple[Command, ...]
    key_presets: Tuple[KeyPreset, ...]


@dataclass(frozen=True)
class FileReport:
    """Per-file outcome for ``herdr-brain config check``."""

    file: str
    surface: str
    present: bool
    row_count: int
    issues: Tuple[Issue, ...]


# ── refusals ─────────────────────────────────────────────────────────────


class OperatorConfigError(RuntimeError):
    """Base class for operator-config refusals."""


class UnknownRowError(OperatorConfigError):
    """No validated row carries the requested id."""


class LaunchRefusedError(OperatorConfigError):
    """The row exists but may not run as asked (``reason`` says why)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# ── field helpers ────────────────────────────────────────────────────────


def _has_control_characters(value: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in value)


def _slugify(label: str) -> str:
    """A stable id derived from a label: lowercase ascii alphanumerics
    joined by single dashes (``Daily standup`` → ``daily-standup``)."""
    cleaned = "".join(
        char if ("a" <= char <= "z") or ("0" <= char <= "9") else "-"
        for char in label.lower()
    )
    return "-".join(part for part in cleaned.split("-") if part)


def _text_field(row: dict, name: str) -> Tuple[Optional[str], Optional[str]]:
    """A scalar string field, or the reason it is unusable."""
    value = row.get(name)
    if value is None:
        return None, f"missing {name}"
    if not isinstance(value, str):
        return None, f"{name} must be a string"
    if not value.strip():
        return None, f"{name} must not be empty"
    if _has_control_characters(value):
        return None, f"{name} must not contain control characters"
    return value, None


# the per-surface builder: (row, candidate_id, label, scope) -> (row_obj, reason)
Builder = Callable[[dict, str, str, str], Tuple[Optional[object], Optional[str]]]


def _validated_rows(
    doc: object,
    *,
    table: str,
    file_name: str,
    allowed_fields: frozenset,
    build: Builder,
) -> Tuple[tuple, Tuple[Issue, ...]]:
    """Walks one ``[[table]]`` array: every row that fails anywhere is
    skipped with exactly one reason; survivors get a unique stable id.

    Row ordinals are the 1-based position inside the array, so a report
    names the row the operator will see when they open the file."""
    if not isinstance(doc, dict):
        return (), (Issue(file_name, 0, "document must be a table"),)
    raw = doc.get(table)
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        return (), (Issue(file_name, 0, f"[[{table}]] must be an array of tables"),)

    rows: list = []
    issues: list = []
    used_ids: set = set()
    for ordinal, row in enumerate(raw, start=1):
        if not isinstance(row, dict):
            issues.append(Issue(file_name, ordinal, "row must be a table"))
            continue
        unknown = sorted(set(row) - allowed_fields)
        if unknown:
            issues.append(Issue(file_name, ordinal, f"unknown field {unknown[0]!r}"))
            continue
        label, reason = _text_field(row, "label")
        if reason:
            issues.append(Issue(file_name, ordinal, reason))
            continue
        label = label.strip()
        scope = ""
        if "scope" in allowed_fields and row.get("scope") is not None:
            scope_value, reason = _text_field(row, "scope")
            if reason:
                issues.append(Issue(file_name, ordinal, reason))
                continue
            scope = scope_value.strip().lower()
        explicit = row.get("id") is not None
        row_id: Optional[str] = None
        if explicit:
            candidate, reason = _text_field(row, "id")
            if reason:
                issues.append(Issue(file_name, ordinal, reason))
                continue
            row_id = candidate.strip()
            if row_id in used_ids:
                issues.append(Issue(file_name, ordinal, f"duplicate id {row_id!r}"))
                continue
        else:
            row_id = _slugify(label) or f"row-{ordinal}"
        built, reason = build(row, row_id, label, scope)
        if built is None or reason:
            issues.append(Issue(file_name, ordinal, reason or "invalid row"))
            continue
        if not explicit and row_id in used_ids:
            # Two rows share a label: disambiguate deterministically
            # (``daily-standup`` then ``daily-standup-2``).
            suffix = 2
            while f"{row_id}-{suffix}" in used_ids:
                suffix += 1
            built = replace(built, id=f"{row_id}-{suffix}")  # type: ignore[arg-type]
            row_id = built.id  # type: ignore[attr-defined]
        used_ids.add(row_id)
        rows.append(built)
    return tuple(rows), tuple(issues)


# ── pure per-surface validators ─────────────────────────────────────────


def validate_quick_replies(
    doc: object, file_name: str = FILE_QUICK_REPLIES
) -> Tuple[Tuple[QuickReply, ...], Tuple[Issue, ...]]:
    """``[[replies]]`` — label + one verbatim phrase."""

    def build(row: dict, row_id: str, label: str, scope: str):
        text, reason = _text_field(row, "text")
        if reason:
            return None, reason
        return QuickReply(id=row_id, label=label, text=text, scope=scope), None

    return _validated_rows(
        doc,
        table=TABLE_REPLIES,
        file_name=file_name,
        allowed_fields=frozenset({"id", "label", "text", "scope"}),
        build=build,
    )


def validate_commands(
    doc: object, file_name: str = FILE_COMMANDS
) -> Tuple[Tuple[Command, ...], Tuple[Issue, ...]]:
    """``[[commands]]`` — label + slash text for agent panes."""

    def build(row: dict, row_id: str, label: str, scope: str):
        command, reason = _text_field(row, "command")
        if reason:
            return None, reason
        if not command.startswith("/"):
            return None, "command must start with '/'"
        return Command(id=row_id, label=label, command=command, scope=scope), None

    return _validated_rows(
        doc,
        table=TABLE_COMMANDS,
        file_name=file_name,
        allowed_fields=frozenset({"id", "label", "command", "scope"}),
        build=build,
    )


def validate_keys(
    doc: object, file_name: str = FILE_KEYS
) -> Tuple[Tuple[KeyPreset, ...], Tuple[Issue, ...]]:
    """``[[keys]]`` — presets that MUST stay inside the existing send-key
    allowlist and its key budget (the dialog-answer discipline)."""

    def build(row: dict, row_id: str, label: str, scope: str):
        keys = row.get("keys")
        if keys is None:
            return None, "missing keys"
        if not isinstance(keys, list) or not keys:
            return None, "keys must be a non-empty array of key names"
        for key in keys:
            if not isinstance(key, str) or key not in SEND_KEY_ALLOWLIST:
                return None, f"key {key!r} is outside the send-key allowlist"
        if len(keys) > MAX_SEND_KEYS:
            return None, f"keys must use at most {MAX_SEND_KEYS} keys"
        return KeyPreset(id=row_id, label=label, keys=tuple(keys), scope=scope), None

    return _validated_rows(
        doc,
        table=TABLE_KEYS,
        file_name=file_name,
        allowed_fields=frozenset({"id", "label", "keys", "scope"}),
        build=build,
    )


def validate_launchers(
    doc: object, file_name: str = FILE_LAUNCHERS
) -> Tuple[Tuple[Launcher, ...], Tuple[Issue, ...]]:
    """``[[launchers]]`` — the argv allowlist. An array of strings only:
    a shell command line (a plain string) is rejected by shape, control
    characters are rejected by content, and ``cwd`` must be absolute
    here while its existence is a launch-time refusal."""

    def build(row: dict, row_id: str, label: str, scope: str):
        argv = row.get("argv")
        if argv is None:
            return None, "missing argv"
        if not isinstance(argv, list) or not argv:
            return None, (
                "argv must be a non-empty array of strings (never a shell command line)"
            )
        for part in argv:
            if not isinstance(part, str) or not part:
                return None, "argv must contain only non-empty strings"
            if _has_control_characters(part):
                return None, "argv must not contain control characters"
        cwd = None
        if row.get("cwd") is not None:
            cwd_value, reason = _text_field(row, "cwd")
            if reason:
                return None, reason
            if not os.path.isabs(cwd_value):
                return None, "cwd must be an absolute path"
            cwd = cwd_value
        no_prompts = row.get("no_prompts", False)
        if not isinstance(no_prompts, bool):
            return None, "no_prompts must be a boolean"
        return (
            Launcher(id=row_id, label=label, argv=tuple(argv), cwd=cwd,
                     no_prompts=no_prompts),
            None,
        )

    return _validated_rows(
        doc,
        table=TABLE_LAUNCHERS,
        file_name=file_name,
        allowed_fields=frozenset({"id", "label", "argv", "cwd", "no_prompts"}),
        build=build,
    )


# ── config directory ─────────────────────────────────────────────────────


def default_config_dir(environ: Optional[Dict[str, str]] = None) -> Path:
    """Explicit ``HERDR_BRAIN_CONFIG_DIR`` → XDG config home →
    ``~/.config/herdr-brain`` (the directory ``config.env`` lives in)."""
    env = os.environ if environ is None else environ
    override = str(env.get(ENV_CONFIG_DIR, "")).strip()
    if override:
        return Path(override).expanduser()
    xdg = str(env.get("XDG_CONFIG_HOME", "")).strip()
    if xdg:
        return Path(xdg).expanduser() / CONFIG_DIRNAME
    home = str(env.get("HOME", "")).strip()
    base = Path(home).expanduser() if home else Path.home()
    return base / ".config" / CONFIG_DIRNAME


# ── the mtime-cached store ───────────────────────────────────────────────


class _CacheEntry(NamedTuple):
    signature: object  # (st_mtime_ns, st_size, st_ino) | None | ("stat-error",)
    rows: tuple
    issues: Tuple[Issue, ...]


def _stat_signature(path: Path):
    """The cheap per-request probe: a missing file is its own (stable)
    signature, so an absent file is cached as empty without re-statting
    beyond the one stat this already is."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    except OSError:
        return ("stat-error",)
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _load_surface(path: Path, file_name: str, validate) -> Tuple[tuple, Tuple[Issue, ...]]:
    """Reads and validates one file. A missing file is simply empty; an
    unreadable or unparsable one becomes a file-level issue and an empty
    row set — never an exception, never a sibling's problem."""
    try:
        with path.open("rb") as handle:
            doc = tomllib.load(handle)
    except FileNotFoundError:
        return (), ()
    except OSError as exc:
        return (), (Issue(file_name, 0, f"cannot read the file: {exc}"),)
    except tomllib.TOMLDecodeError as exc:
        return (), (Issue(file_name, 0, f"invalid TOML: {exc}"),)
    return validate(doc, file_name=file_name)


_SURFACES = (
    ("quick_replies", FILE_QUICK_REPLIES, validate_quick_replies),
    ("commands", FILE_COMMANDS, validate_commands),
    ("key_presets", FILE_KEYS, validate_keys),
    ("launchers", FILE_LAUNCHERS, validate_launchers),
)


class OperatorConfig:
    """Thread-safe, mtime-cached view of the four operator files.

    One ``stat`` per surface per request; a changed signature reloads
    and re-validates that file alone under the lock. Returned rows are
    immutable, so readers never need the lock."""

    def __init__(self, config_dir: Optional[Path | str] = None,
                 environ: Optional[Dict[str, str]] = None) -> None:
        if config_dir is not None:
            self._dir = Path(config_dir).expanduser()
        else:
            self._dir = default_config_dir(environ)
        self._lock = threading.Lock()
        self._cache: Dict[str, _CacheEntry] = {}
        self._reload_counts: Dict[str, int] = {name: 0 for name, _, _ in _SURFACES}

    @property
    def config_dir(self) -> Path:
        return self._dir

    def reload_counts(self) -> Dict[str, int]:
        """Reloads served per surface — test observability for the
        stat-cache discipline (unchanged files must not reload)."""
        return dict(self._reload_counts)

    def _surface(self, surface_name: str) -> Tuple[tuple, Tuple[Issue, ...]]:
        file_name, validate = next(
            (name_file, fn) for name, name_file, fn in _SURFACES if name == surface_name
        )
        path = self._dir / file_name
        with self._lock:
            signature = _stat_signature(path)
            entry = self._cache.get(surface_name)
            if entry is not None and entry.signature == signature:
                return entry.rows, entry.issues
            rows, issues = _load_surface(path, file_name, validate)
            self._cache[surface_name] = _CacheEntry(signature, rows, issues)
            self._reload_counts[surface_name] += 1
            for issue in issues:
                logger.warning("operator config: %s", issue.describe())
            return rows, issues

    # -- full catalogs (scope attached) ---------------------------------

    def quick_replies(self) -> Tuple[QuickReply, ...]:
        return self._surface("quick_replies")[0]

    def commands(self) -> Tuple[Command, ...]:
        return self._surface("commands")[0]

    def key_presets(self) -> Tuple[KeyPreset, ...]:
        return self._surface("key_presets")[0]

    def launchers(self) -> Tuple[Launcher, ...]:
        return self._surface("launchers")[0]

    # -- resolution for one pane -----------------------------------------

    def for_pane(self, agent_family: str) -> PaneConfig:
        """The effective dock for one agent family.

        Scoped rows REPLACE the global ones for their family (an empty
        scoped result falls back to the global catalog); families are
        matched case-insensitively and ``shell`` is a family like any
        other."""
        family = (agent_family or "").strip().lower()

        def resolve(rows):
            scoped = tuple(row for row in rows if row.scope == family)
            if scoped:
                return scoped
            return tuple(row for row in rows if row.scope == "")

        return PaneConfig(
            quick_replies=resolve(self.quick_replies()),
            commands=resolve(self.commands()),
            key_presets=resolve(self.key_presets()),
        )

    # -- launch resolution -------------------------------------------------

    def launcher(self, row_id: str) -> Launcher:
        """The validated launcher row an id names — the allowlist lookup."""
        for row in self.launchers():
            if row.id == row_id:
                return row
        raise UnknownRowError(f"unknown launcher row {row_id!r}")

    def launch_plan(self, row_id: str, confirm: bool = False) -> LaunchPlan:
        """The exact argv/cwd a row resolves to, or a refusal.

        Refused, never guessed: an unknown id, a ``no_prompts`` row
        without the explicit confirmation, and a configured cwd that no
        longer exists all raise before anything is executed."""
        row = self.launcher(row_id)
        if row.no_prompts and not confirm:
            raise LaunchRefusedError(
                "confirmation_required",
                f"launcher row {row_id!r} sets no_prompts; launch it with an "
                "explicit confirmation",
            )
        if row.cwd is not None and not Path(row.cwd).is_dir():
            raise LaunchRefusedError("cwd_missing", f"cwd {row.cwd} does not exist")
        return LaunchPlan(
            row_id=row.id,
            label=row.label,
            argv=row.argv,
            cwd=row.cwd,
            command_line=shlex.join(row.argv),
            no_prompts=row.no_prompts,
        )

    # -- the config-check report -------------------------------------------

    def check(self) -> Tuple[FileReport, ...]:
        """One report per file: presence, healthy row count and every
        issue — the shape the ``config check`` CLI verb prints."""
        reports = []
        for surface_name, file_name, _validate in _SURFACES:
            rows, issues = self._surface(surface_name)
            reports.append(
                FileReport(
                    file=file_name,
                    surface=surface_name,
                    present=_stat_signature(self._dir / file_name) is not None,
                    row_count=len(rows),
                    issues=issues,
                )
            )
        return tuple(reports)
