"""Importable themes with WCAG-checked contrast (POST-04 fase A, doc 13 §5).

A theme is a versioned JSON document (``schema: 1``) that overrides a
known set of CSS custom properties — exactly the 21 color variables the
PWA stylesheet defines today on ``:root`` and
``:root[data-theme="dark"]``. Fase A owns the backend: the pure schema,
the mathematical WCAG 2.1 contrast gate, the safe store under
``~/.config/herdr-brain/themes/``, and the HTTP contract (``/themes``).
Wiring a chosen theme into the PWA is fase B (a later milestone) and
touches ``static/`` — deliberately out of this scope.

The contract rules, all enforced on every import:

- The schema is versioned and closed: unknown schema versions, unknown
  top-level fields and unknown token names are refused. A theme file is
  a contract, not a bag of keys.
- Only the tokens the contrast matrix can actually check are required;
  the rest stay optional so fase B can fall back to the stylesheet.
- Contrast is verified with the exact WCAG 2.1 relative-luminance math:
  ≥ 4.5:1 on text/background pairs and ≥ 3.0:1 on controls/accent
  pairs. A failing theme is refused with the pair and the measured
  ratio — nothing is applied and no byte is written.
- ``id`` is a strict slug (``^[a-z0-9][a-z0-9-]{0,47}$``): the filename
  is always ``<id>.json`` inside the themes directory, so path
  traversal is impossible by construction.

The decorative translucent washes (``user-wash``/``user-line``) accept
``rgba()`` and stay outside the contrast matrix — they tint surfaces,
they do not carry text. Same for the ``oc-*`` markdown palette and
``terminal-bg``: they sit on a fixed dark terminal surface, a separate
problem fase B may take on.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .operator_config import default_config_dir

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

#: Outer size bound for a serialized theme document (routes answer 413
#: above it). Per-field limits already keep valid docs far below this.
MAX_FILE_BYTES = 65536

MAX_NAME_CHARS = 64
MAX_DESCRIPTION_CHARS = 200

#: The filename IS the id: no slashes, no dots, no uppercase, 48 max.
#: ``\Z`` (not ``$``): Python's ``$`` also matches before a trailing
#: newline, which would let ``"evil\n"`` pass and reach the filesystem.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}\Z")

_HEX_RE = re.compile(r"^#([0-9a-f]{3}|[0-9a-f]{6})\Z", re.IGNORECASE)
#: ``re.ASCII``: ``\d`` matches Unicode digits (``0.٥``) otherwise, and
#: the alpha grammar admits every valid CSS spelling of 0..1
#: (``1.0`` included) while refusing anything above one.
_RGBA_RE = re.compile(
    r"^rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,"
    r"\s*((?:1(?:\.0+)?|0(?:\.\d+)?|\.\d+))\s*\)\Z",
    re.ASCII,
)

#: The exact color custom properties the stylesheet defines. Bare names
#: (no ``--`` prefix): fase B maps ``token → --token`` directly.
TOKEN_NAMES = frozenset(
    {
        "bg", "panel", "text", "muted", "line",
        "accent", "danger", "warn", "blue", "chip-bg",
        "cockpit-bg", "terminal-bg", "warn-wash",
        "user-wash", "user-line",
        "oc-bg", "oc-panel", "oc-purple", "oc-green", "oc-amber", "oc-blue",
    }
)

#: Translucent tints: ``rgba()`` allowed, contrast not checked (they
#: wash surfaces; text on them rides the underlying tokens).
WASH_TOKENS = frozenset({"user-wash", "user-line"})

#: Tokens the contrast matrix actually needs; everything else in
#: TOKEN_NAMES is optional (fase B falls back to the stylesheet value).
REQUIRED_TOKENS = frozenset(
    {"bg", "panel", "chip-bg", "text", "muted", "accent", "danger", "warn", "blue"}
)

_TOP_LEVEL_KEYS = frozenset({"schema", "id", "name", "description", "tokens"})

#: WCAG 2.1 AA text pairs: 4.5:1 minimum.
TEXT_PAIRS = (
    ("text", "bg"),
    ("text", "panel"),
    ("text", "chip-bg"),
    ("muted", "panel"),
    ("accent", "panel"),
    ("danger", "panel"),
    ("warn", "panel"),
    ("blue", "panel"),
)

#: WCAG 2.1 controls/accent pairs: 3.0:1 minimum (SC 1.4.11).
CONTROL_PAIRS = (
    ("accent", "bg"),
    ("muted", "bg"),
    ("muted", "chip-bg"),
    ("danger", "bg"),
)

_PAIR_MINIMUMS = {pair: 4.5 for pair in TEXT_PAIRS}
_PAIR_MINIMUMS.update({pair: 3.0 for pair in CONTROL_PAIRS})


class ThemeValidationError(ValueError):
    """A refused theme: ``reason`` is one operator-facing line."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ── WCAG 2.1 math ────────────────────────────────────────────────────────────


def _expand_hex(color: str) -> str:
    """``#abc`` → ``#aabbcc``; already-6-digit input returns lowered."""
    digits = color[1:]
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return f"#{digits.lower()}"


def _channels(color: str) -> tuple:
    hex6 = _expand_hex(color)
    return tuple(int(hex6[i : i + 2], 16) for i in (1, 3, 5))


def relative_luminance(color: str) -> float:
    """WCAG 2.1 relative luminance of a ``#rgb``/``#rrggbb`` color."""
    r, g, b = (c / 255 for c in _channels(color))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """The (L1+0.05)/(L2+0.05) ratio; order-independent."""
    l1 = relative_luminance(fg)
    l2 = relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# ── token color parsing ──────────────────────────────────────────────────────


def _normalize_color(token: str, value: Any) -> str:
    """Validate one token's color, returning its normalized string."""
    if isinstance(value, str):
        if _HEX_RE.match(value):
            return _expand_hex(value)
        if token in WASH_TOKENS:
            match = _RGBA_RE.match(value)
            if match:
                r, g, b, alpha = match.groups()
                if int(r) <= 255 and int(g) <= 255 and int(b) <= 255:
                    return f"rgba({int(r)},{int(g)},{int(b)},{alpha})"
    raise ThemeValidationError(f"color inválido para {token}: {value!r}")


# ── contrast matrix ──────────────────────────────────────────────────────────


def contrast_report(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Every matrix pair with its measured ratio and verdict.

    Pure and total: the caller decides what to do with it; the routes
    use it for the 422 reason, the tests use it as the pinned matrix.
    """
    tokens = doc["tokens"]
    report = []
    for (fg, bg), minimum in _PAIR_MINIMUMS.items():
        ratio = contrast_ratio(tokens[fg], tokens[bg])
        report.append(
            {
                "fg": fg,
                "bg": bg,
                "ratio": ratio,
                "minimum": minimum,
                "ok": ratio >= minimum,
            }
        )
    return report


def _contrast_reasons(doc: dict[str, Any]) -> list[str]:
    return [
        f"{entry['fg']}/{entry['bg']} {entry['ratio']:.2f}:1 < {entry['minimum']}:1"
        for entry in contrast_report(doc)
        if not entry["ok"]
    ]


# ── schema validation ────────────────────────────────────────────────────────


def validate_theme(doc: Any) -> dict[str, Any]:
    """Total, pure schema+contrast validation. Returns the normalized
    document or raises :class:`ThemeValidationError` with one reason.
    Never writes anything — the store decides persistence."""
    if not isinstance(doc, dict):
        raise ThemeValidationError("el documento debe ser un objeto JSON")

    unknown = sorted(set(doc) - _TOP_LEVEL_KEYS)
    if unknown:
        raise ThemeValidationError(f"campo desconocido: {unknown[0]}")
    for field in ("schema", "id", "name", "tokens"):
        if field not in doc:
            raise ThemeValidationError(f"falta el campo: {field}")

    if type(doc["schema"]) is not int or doc["schema"] != SCHEMA_VERSION:
        raise ThemeValidationError(f"schema debe ser {SCHEMA_VERSION}")

    theme_id = doc["id"]
    if not isinstance(theme_id, str) or not SLUG_RE.match(theme_id):
        raise ThemeValidationError(
            "id inválido: debe ser slug [a-z0-9][a-z0-9-]{0,47}"
        )

    name = doc["name"]
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > MAX_NAME_CHARS
        or any(ord(ch) < 32 for ch in name)
    ):
        raise ThemeValidationError(
            f"name debe ser texto de 1 a {MAX_NAME_CHARS} caracteres, "
            "sin caracteres de control"
        )

    description = doc.get("description")
    if description is None:
        description = ""
    if not isinstance(description, str) or len(description) > MAX_DESCRIPTION_CHARS:
        raise ThemeValidationError(
            f"description debe ser texto de hasta {MAX_DESCRIPTION_CHARS} caracteres"
        )

    tokens = doc["tokens"]
    if not isinstance(tokens, dict):
        raise ThemeValidationError("tokens debe ser un objeto")
    unknown_tokens = sorted(set(tokens) - TOKEN_NAMES)
    if unknown_tokens:
        raise ThemeValidationError(f"token desconocido: {unknown_tokens[0]}")
    missing = sorted(REQUIRED_TOKENS - set(tokens))
    if missing:
        raise ThemeValidationError(f"falta el token requerido: {missing[0]}")

    clean = {
        "schema": SCHEMA_VERSION,
        "id": theme_id,
        "name": name,
        "description": description,
        "tokens": {
            token: _normalize_color(token, tokens[token])
            # iterate sorted for a stable normalized doc
            for token in sorted(tokens)
        },
    }

    reasons = _contrast_reasons(clean)
    if reasons:
        raise ThemeValidationError("contraste insuficiente: " + "; ".join(reasons))

    if len(serialize_theme(clean).encode("utf-8")) > MAX_FILE_BYTES:
        raise ThemeValidationError(
            f"documento demasiado grande (>{MAX_FILE_BYTES} bytes)"
        )
    return clean


def serialize_theme(doc: dict[str, Any]) -> str:
    """The on-disk / on-the-wire form: stable key order, trailing newline."""
    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


# ── built-in catalog (S3) ────────────────────────────────────────────────────

#: The four catalog presets. ``claro``/``oscuro`` mirror the stylesheet's
#: current tokens exactly (index.html :root / [data-theme="dark"]), so the
#: catalog describes reality; ``alto-contraste-sol`` is the outdoor/sun
#: preset the milestone asks for and ``alto-contraste-noche`` is its night
#: sibling. Every entry validates against the SAME schema and matrix the
#: imports face — pinned by test.
_BUILT_INS_RAW = (
    {
        "schema": SCHEMA_VERSION,
        "id": "claro",
        "name": "Claro",
        "description": "El tema claro del cockpit (por defecto).",
        "tokens": {
            "bg": "#f4f6f9", "panel": "#ffffff", "text": "#17202b",
            "muted": "#5b6572", "line": "#d9dee6",
            "accent": "#12855c", "danger": "#c62f2f", "warn": "#8a6100",
            "blue": "#1d5fbf", "chip-bg": "#e9edf3",
            "cockpit-bg": "#ffffff", "terminal-bg": "#0a0a0a",
            "warn-wash": "#f4e8c3",
            "user-wash": "rgba(18,133,92,0.15)",
            "user-line": "rgba(18,133,92,0.45)",
            "oc-bg": "#f0f2f5", "oc-panel": "#e9edf3",
            "oc-purple": "#6d28a5", "oc-green": "#166534",
            "oc-amber": "#92400e", "oc-blue": "#1d4ed8",
        },
    },
    {
        "schema": SCHEMA_VERSION,
        "id": "oscuro",
        "name": "Oscuro",
        "description": "El tema oscuro del cockpit.",
        "tokens": {
            "bg": "#1b1d22", "panel": "#141414", "text": "#eeeeee",
            "muted": "#808080", "line": "#262626",
            "accent": "#7fd88f", "danger": "#f87171", "warn": "#f5a742",
            "blue": "#5c9cf5", "chip-bg": "#202020",
            "cockpit-bg": "#0a0a0a", "terminal-bg": "#0a0a0a",
            "warn-wash": "#3b2f11",
            "user-wash": "rgba(127,216,143,0.14)",
            "user-line": "rgba(127,216,143,0.40)",
            "oc-bg": "#0a0a0a", "oc-panel": "#141414",
            "oc-purple": "#9d7cd8", "oc-green": "#7fd88f",
            "oc-amber": "#f5a742", "oc-blue": "#5c9cf5",
        },
    },
    {
        "schema": SCHEMA_VERSION,
        "id": "alto-contraste-sol",
        "name": "Alto contraste (sol)",
        "description": "Para exteriores a pleno sol: negro sobre blanco, acentos saturados.",
        "tokens": {
            "bg": "#ffffff", "panel": "#ffffff", "text": "#000000",
            "muted": "#262626", "line": "#595959",
            "accent": "#006400", "danger": "#b91c1c", "warn": "#7a4d00",
            "blue": "#0b3d91", "chip-bg": "#f0f0f0",
            "cockpit-bg": "#ffffff", "terminal-bg": "#0a0a0a",
            "warn-wash": "#f7e9c4",
            "user-wash": "rgba(0,100,64,0.15)",
            "user-line": "rgba(0,100,64,0.45)",
            "oc-bg": "#f0f2f5", "oc-panel": "#e9edf3",
            "oc-purple": "#4c1d95", "oc-green": "#14532d",
            "oc-amber": "#78350f", "oc-blue": "#1e3a8a",
        },
    },
    {
        "schema": SCHEMA_VERSION,
        "id": "alto-contraste-noche",
        "name": "Alto contraste (noche)",
        "description": "Para noche y conducción: blanco sobre negro, acentos luminosos.",
        "tokens": {
            "bg": "#000000", "panel": "#0d0d0d", "text": "#ffffff",
            "muted": "#b8b8b8", "line": "#333333",
            "accent": "#22c55e", "danger": "#ff6b6b", "warn": "#ffb86b",
            "blue": "#7cb3ff", "chip-bg": "#1a1a1a",
            "cockpit-bg": "#000000", "terminal-bg": "#000000",
            "warn-wash": "#3b2f11",
            "user-wash": "rgba(34,197,94,0.14)",
            "user-line": "rgba(34,197,94,0.40)",
            "oc-bg": "#000000", "oc-panel": "#141414",
            "oc-purple": "#c4b5fd", "oc-green": "#86efac",
            "oc-amber": "#fcd34d", "oc-blue": "#93c5fd",
        },
    },
)

#: Validated once at import time: a broken preset is a programming error,
#: not a runtime condition.
BUILT_INS = tuple(validate_theme(doc) for doc in _BUILT_INS_RAW)

_BUILTIN_INDEX = {doc["id"]: doc for doc in BUILT_INS}

BUILTIN_IDS = frozenset(_BUILTIN_INDEX)


def built_in(theme_id: str) -> dict | None:
    """A preset by id (an isolated copy), or None."""
    doc = _BUILTIN_INDEX.get(theme_id)
    if doc is None:
        return None
    return {**doc, "tokens": dict(doc["tokens"])}


# ── safe store (S4) ──────────────────────────────────────────────────────────

#: Directory name under the brain config dir (default
#: ``~/.config/herdr-brain/themes``, ``HERDR_BRAIN_CONFIG_DIR`` aware).
THEMES_DIRNAME = "themes"


class ThemeStore:
    """User themes under ``<config_dir>/themes/<id>.json``.

    Built-ins live in code and are merged into every catalog; a user
    import can never shadow one. Writes are atomic (mkstemp is
    ``O_EXCL`` 0600, then ``os.replace``) inside a 0700 directory, and
    the id — already slug-checked by validation — is re-checked before
    it ever touches a path. Corrupt or invalid user files are skipped
    with a reason instead of breaking the catalog.
    """

    def __init__(self, config_dir=None, environ=None):
        if config_dir is not None:
            self._dir = Path(config_dir).expanduser()
        else:
            self._dir = default_config_dir(environ)

    @property
    def themes_dir(self) -> Path:
        return self._dir / THEMES_DIRNAME

    def _user_path(self, theme_id: str) -> Path | None:
        """The file for a slug id, or None — never a sentinel name a
        caller could theoretically create."""
        if not isinstance(theme_id, str) or not SLUG_RE.match(theme_id):
            return None
        return self.themes_dir / f"{theme_id}.json"

    def list_catalog(self) -> dict:
        """``{"themes": [...], "skipped": [...]}`` — built-ins first
        (fixed order), then user themes by id, every entry carrying its
        normalized tokens and a ``built_in`` flag."""
        entries = [
            {**doc, "built_in": True, "tokens": dict(doc["tokens"])}
            for doc in BUILT_INS
        ]
        skipped: list[dict] = []
        directory = self.themes_dir
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                stem = path.stem
                if not SLUG_RE.match(stem):
                    continue  # not a theme we manage; ignore outright
                if stem in BUILTIN_IDS:
                    # imports already refuse reserved ids; a manually
                    # dropped file must not duplicate the preset either
                    reason = "id reservado: preset integrado"
                    skipped.append({"id": stem, "reason": reason})
                    logger.warning("theme %s skipped: %s", stem, reason)
                    continue
                try:
                    if path.is_symlink():
                        raise ThemeValidationError("symlink no permitido")
                    if path.stat().st_size > MAX_FILE_BYTES:
                        raise ThemeValidationError(
                            f"fichero demasiado grande (>{MAX_FILE_BYTES} bytes)"
                        )
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    continue  # vanished between glob and read; not a skip
                except (OSError, ValueError) as exc:
                    reason = " ".join(str(exc).split()) or "ilegible"
                    skipped.append({"id": stem, "reason": reason})
                    logger.warning("theme %s skipped: %s", stem, reason)
                    continue
                try:
                    doc = validate_theme(raw)
                except ThemeValidationError as exc:
                    skipped.append({"id": stem, "reason": exc.reason})
                    logger.warning("theme %s skipped: %s", stem, exc.reason)
                    continue
                if doc["id"] != stem:
                    reason = "el id del documento no coincide con el fichero"
                    skipped.append({"id": stem, "reason": reason})
                    logger.warning("theme %s skipped: %s", stem, reason)
                    continue
                entries.append({**doc, "built_in": False})
        return {"themes": entries, "skipped": skipped}

    def get_theme(self, theme_id: str) -> dict | None:
        """One theme document (built-in or user), None when absent or
        invalid. Non-slug ids short-circuit before touching the
        filesystem, symlinks are refused, and a file whose stored id
        disagrees with its filename answers None (the catalog reports
        it as skipped instead)."""
        if not isinstance(theme_id, str):
            return None
        doc = _BUILTIN_INDEX.get(theme_id)
        if doc is not None:
            return {**doc, "tokens": dict(doc["tokens"])}
        path = self._user_path(theme_id)
        if path is None:
            return None
        try:
            if path.is_symlink():
                return None
            clean = validate_theme(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None
        if clean["id"] != theme_id:
            return None
        return clean

    def import_theme(self, doc: Any) -> dict:
        """Validate, then atomically upsert a user theme. Returns the
        normalized document; raises :class:`ThemeValidationError`
        WITHOUT writing anything when the doc is invalid or the id is a
        reserved preset."""
        clean = validate_theme(doc)
        if clean["id"] in BUILTIN_IDS:
            raise ThemeValidationError(
                f"id reservado: {clean['id']} es un preset integrado"
            )
        directory = self.themes_dir
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(
            dir=directory, prefix=f".{clean['id']}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(serialize_theme(clean))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, directory / f"{clean['id']}.json")
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        return clean

    def delete_theme(self, theme_id: str) -> bool:
        """Remove one USER theme file. Built-ins and unknown ids return
        False (and never touch the disk)."""
        if theme_id in BUILTIN_IDS:
            return False
        path = self._user_path(theme_id)
        if path is None:
            return False
        try:
            path.unlink()
            return True
        except OSError:
            return False
