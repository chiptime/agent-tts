"""Content-aware prosody (AT-05): how a sentence group sounds, never what it says.

``classify`` labels one sentence group with exactly one class from
``error``, ``warning``, ``code``, ``question`` or ``normal`` using format
evidence (upper-case markers, ``Traceback``, ``XxxError:``) so the plain
word "error" in prose stays ``normal``. ``plan_groups`` turns the classes
into per-group rate/pitch values and a silence before the group.

The mode ``off`` returns no plan at all, so callers keep the exact global
rate/pitch path. ``subtle`` and ``on`` read a table of deltas that the
user may override per mode and class in
``~/.config/agent-tts/prosody.json`` (or ``AGENT_TTS_PROSODY_CONFIG``):

    {"subtle": {"error": {"rate": -10, "pitch": -4, "pause_ms": 400}}}

``rate`` is a percentage delta, ``pitch`` a delta in Hz and ``pause_ms``
the silence inserted before the group. Providers that ignore pitch keep
the rate and pause (graceful degradation, never an error).
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

from agent_tts.text import split_sentence_groups

MODES = ("off", "subtle", "on")
CLASSES = ("error", "warning", "code", "question", "normal")
FIELDS = ("rate", "pitch", "pause_ms")
DEFAULT_CONFIG_PATH = "~/.config/agent-tts/prosody.json"
ENV_CONFIG = "AGENT_TTS_PROSODY_CONFIG"
ENV_MODE = "AGENT_TTS_PROSODY"

DEFAULT_TABLES: Dict[str, Dict[str, Dict[str, int]]] = {
    "subtle": {
        "error": {"rate": -8, "pitch": -3, "pause_ms": 300},
        "warning": {"rate": -4, "pitch": -2, "pause_ms": 0},
        "code": {"rate": -5, "pitch": 0, "pause_ms": 150},
        "question": {"rate": 0, "pitch": 4, "pause_ms": 0},
        "normal": {"rate": 0, "pitch": 0, "pause_ms": 0},
    },
    "on": {
        "error": {"rate": -15, "pitch": -6, "pause_ms": 300},
        "warning": {"rate": -8, "pitch": -3, "pause_ms": 0},
        "code": {"rate": -10, "pitch": 0, "pause_ms": 150},
        "question": {"rate": 0, "pitch": 8, "pause_ms": 0},
        "normal": {"rate": 0, "pitch": 0, "pause_ms": 0},
    },
}

_ERROR_PATTERNS = (
    re.compile(r"\bTraceback\b"),
    re.compile(r'\bFile "[^"]+", line \d+'),
    re.compile(r"\b(?:ERROR|FAILED|FATAL|FAIL)\b"),
    re.compile(r"\b[A-Z][A-Za-z]*(?:Error|Exception):"),
    re.compile(r"\bpanic:"),
)
_WARNING_PATTERNS = (
    re.compile(r"\b(?:WARNING|WARN|DEPRECATED|BLOCKED)\b"),
    re.compile(r"\b(?:warning|deprecated|bloqueado):", re.IGNORECASE),
)
_CODE_PATTERNS = (
    re.compile(r"\[bloque de código omitido\]"),
    re.compile(r"^\s*comando:", re.IGNORECASE),
)


def classify(group: str) -> str:
    """One class per group; priority error > warning > code > question > normal."""
    if any(p.search(group) for p in _ERROR_PATTERNS):
        return "error"
    if any(p.search(group) for p in _WARNING_PATTERNS):
        return "warning"
    if any(p.search(group) for p in _CODE_PATTERNS):
        return "code"
    stripped = group.strip()
    if stripped.endswith("?") or "¿" in stripped:
        return "question"
    return "normal"


def split_by_class(text: str, max_chars: int = 250) -> List[str]:
    """Sentence groups that never mix prosody classes.

    Sentences are split like ``split_sentence_groups`` (lines, then
    ``.``/``!``/``?``); consecutive sentences of the same class are joined
    while they fit ``max_chars``, so a short announcement mixing prose, an
    error and a question becomes three groups, and a run of one class
    still costs one synthesis call. Sentences longer than the budget go
    through ``split_sentence_groups`` (comma split). Words are never
    altered.
    """
    sentences: List[str] = []
    for line in (text or "").strip().splitlines():
        line = line.strip()
        if line:
            sentences.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", line) if s.strip())
    groups: List[str] = []
    current, current_kind = "", None
    for sentence in sentences:
        kind = classify(sentence)
        if current and kind == current_kind and len(current) + 1 + len(sentence) <= max_chars:
            current = f"{current} {sentence}"
            continue
        if current:
            groups.append(current)
        current, current_kind = sentence, kind
    if current:
        groups.append(current)
    result: List[str] = []
    for group in groups:
        result.extend(split_sentence_groups(group, max_chars) if len(group) > max_chars else [group])
    return result


def parse_signed(value: str) -> float:
    """``"+8%"``/``"-3Hz"``/``"1.2"`` → the signed number (unit dropped)."""
    match = re.match(r"\s*([+-]?\d+(?:\.\d+)?)", value or "")
    return float(match.group(1)) if match else 0.0


def _fmt(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else f"{number:g}"


def shift_rate(base: str, delta_pct: int) -> str:
    """Applies a percentage delta to an edge-style (``+10%``) or multiplier rate."""
    base = (base or "").strip()
    if base and not base.endswith("%"):
        return f"{round(parse_signed(base) * (1 + delta_pct / 100.0), 4):g}"
    total = parse_signed(base) + delta_pct
    return f"{'+' if total >= 0 else ''}{_fmt(total)}%"


def shift_pitch(base: str, delta_hz: int) -> str:
    total = parse_signed(base) + delta_hz
    return f"{'+' if total >= 0 else ''}{_fmt(total)}Hz"


@dataclass(frozen=True)
class GroupProsody:
    kind: str
    rate: str
    pitch: str
    pause_ms: int


def load_tables(config_path: Optional[str] = None) -> Dict[str, Dict[str, Dict[str, int]]]:
    """Default tables merged with the user file; a bad file or entry warns and is ignored."""
    tables = {mode: {k: dict(v) for k, v in classes.items()} for mode, classes in DEFAULT_TABLES.items()}
    path = os.path.expanduser(config_path or os.environ.get(ENV_CONFIG) or DEFAULT_CONFIG_PATH)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return tables
    except (OSError, ValueError) as exc:
        print(f"agent-tts: ignoring prosody config {path}: {exc}", file=sys.stderr)
        return tables
    if not isinstance(data, dict):
        print(f"agent-tts: ignoring prosody config {path}: top level must be an object", file=sys.stderr)
        return tables
    for mode, classes in data.items():
        if mode not in tables or not isinstance(classes, dict):
            print(f"agent-tts: ignoring prosody config entry {mode!r}", file=sys.stderr)
            continue
        for kind, values in classes.items():
            if kind not in CLASSES or not isinstance(values, dict):
                print(f"agent-tts: ignoring prosody config entry {mode}.{kind}", file=sys.stderr)
                continue
            for field, value in values.items():
                valid = field in FIELDS and isinstance(value, (int, float)) and not isinstance(value, bool)
                if not valid or (field == "pause_ms" and not 0 <= value <= 5000):
                    print(f"agent-tts: ignoring prosody config entry {mode}.{kind}.{field}", file=sys.stderr)
                    continue
                tables[mode][kind][field] = int(value)
    return tables


def plan_groups(
    groups: List[str], mode: str, rate: str, pitch: str, config_path: Optional[str] = None
) -> Optional[List[GroupProsody]]:
    """Per-group rate/pitch/pause for ``subtle``/``on``; ``None`` for ``off``."""
    if mode not in MODES:
        raise ValueError(f"unknown prosody mode {mode!r} (expected one of {', '.join(MODES)})")
    if mode == "off":
        return None
    table = load_tables(config_path)[mode]
    plan: List[GroupProsody] = []
    previous = None
    for index, group in enumerate(groups):
        kind = classify(group)
        row = table[kind]
        pause = row["pause_ms"]
        if previous == "code" and kind != "code":
            pause = max(pause, table["code"]["pause_ms"])
        if index == 0:
            pause = 0
        plan.append(GroupProsody(kind, shift_rate(rate, row["rate"]), shift_pitch(pitch, row["pitch"]), pause))
        previous = kind
    return plan


def resolve_mode(value: Optional[str] = None) -> str:
    """Flag value, else ``AGENT_TTS_PROSODY``, else ``off``; unknown values fall back to ``off``."""
    mode = (value or os.environ.get(ENV_MODE) or "off").strip().lower()
    if mode not in MODES:
        print(f"agent-tts: unknown prosody mode {mode!r}; using off", file=sys.stderr)
        return "off"
    return mode
