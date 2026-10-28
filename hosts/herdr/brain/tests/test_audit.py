"""Audit trail unit surface (Panel parity F1, doc 02): the pure JSONL
line format (byte-stable, injected clock), sanitization/redaction, the
``HERDR_BRAIN_AUDIT_CONTENT`` knob, file modes, single-generation
rotation and the fire-and-forget writer that never raises.
"""

from __future__ import annotations

import json
import stat
from datetime import datetime, timezone

import pytest

from herdr_brain.audit import (
    MAX_STR,
    REDACTED,
    AuditLog,
    format_audit_line,
    sanitize,
)
from herdr_brain.config import load_settings

# Deterministic clock for byte-stable lines (AC8): 2023-11-14T22:13:20Z.
FIXED_NOW = 1_700_000_000.0
FIXED_TS = datetime.fromtimestamp(FIXED_NOW, tz=timezone.utc).isoformat()


# ── AC8: the line is pure and byte-stable ────────────────────────────────────


def test_line_is_jsonl_byte_stable_with_injected_clock():
    entry = {
        "action": "pane.reply",
        "pane_id": "w1:p2",
        "device": "phone",
        "detail": {"outcome": "delivered", "text": "hola"},
    }
    line = format_audit_line(entry, FIXED_NOW)
    assert line == (
        '{"ts":"' + FIXED_TS + '","action":"pane.reply",'
        '"pane_id":"w1:p2","device":"phone",'
        '"detail":{"outcome":"delivered","text":"hola"}}'
    )
    # Two runs with the same entry and clock render the same line.
    assert format_audit_line(entry, FIXED_NOW) == line
    assert "\n" not in line
    json.loads(line)  # one valid JSON value, single line


def test_optional_fields_are_omitted_not_null():
    line = format_audit_line({"action": "pair", "detail": {}}, FIXED_NOW)
    parsed = json.loads(line)
    assert set(parsed) == {"ts", "action", "detail"}
    # session_id sits between pane_id and device in the stable order.
    with_session = format_audit_line(
        {"action": "ask", "session_id": "s1", "detail": {"chars": 3}}, FIXED_NOW
    )
    assert with_session.index('"action"') < with_session.index('"session_id"')
    assert "device" not in json.loads(with_session)


# ── sanitize: preview truncation, redaction, defensive drops ─────────────────


def test_preview_collapses_newlines_and_truncates_at_120():
    # Every newline run becomes one space — including a trailing one,
    # exactly like Panel's replace(/[\r\n]+/g, " ").
    out = sanitize({"text": "line one\nline two\r\n\r\nend\n"})
    assert out["text"] == "line one line two end "
    out = sanitize({"text": "single line"})
    assert out["text"] == "single line"
    out = sanitize({"text": "x" * (MAX_STR + 10)})
    assert out["text"] == "x" * MAX_STR + "…"
    out = sanitize({"text": "x" * MAX_STR})
    assert out["text"] == "x" * MAX_STR  # exactly at the cap: no ellipsis


def test_none_mode_redacts_strings_but_keeps_metadata_and_scalars():
    detail = {
        "outcome": "delivered",
        "text": "purge the database",
        "keys": ["enter", "esc"],
        "revision": 3,
        "spoken": True,
        "chars": 42,
        "nested": {"reason": "timeout", "text": "inner secret"},
    }
    out = sanitize(detail, "none")
    assert out["text"] == REDACTED
    assert out["outcome"] == "delivered"  # allowlisted metadata survives
    # Array members inherit the array key's verdict: "keys" carries key
    # names, not bodies.
    assert out["keys"] == ["enter", "esc"]
    assert out["revision"] == 3
    assert out["spoken"] is True
    assert out["chars"] == 42
    assert out["nested"]["reason"] == "timeout"
    assert out["nested"]["text"] == REDACTED


def test_sanitize_drops_non_json_values_defensively():
    class Opaque:
        def __str__(self):
            return "boom"

    out = sanitize(
        {
            "fn": lambda: 1,
            "opaque": Opaque(),
            "ok": "x",
            "mixed": [1, lambda: 2, "y"],
            7: "non-string key",
        }
    )
    assert "fn" not in out
    assert "opaque" not in out
    assert out["ok"] == "x"
    # An array slot renders None (what JSON does with a hole), never the
    # dropped value.
    assert out["mixed"] == [1, None, "y"]
    assert 7 not in out and "7" not in out


def test_redacted_marker_is_a_constant_never_a_length():
    out = sanitize({"text": "ninechars"}, "none")
    assert out["text"] == REDACTED
    assert "9" not in out["text"] and "chars" not in out["text"]


# ── config knob ──────────────────────────────────────────────────────────────


def test_audit_content_setting_default_preview_env_none_and_loud_typo():
    assert load_settings({}).audit_content == "preview"
    assert load_settings({"HERDR_BRAIN_AUDIT_CONTENT": "none"}).audit_content == "none"
    with pytest.raises(ValueError):
        load_settings({"HERDR_BRAIN_AUDIT_CONTENT": "everything"})


# ── AuditLog: modes, fail-never, rotation ────────────────────────────────────


def test_record_creates_0600_file_inside_0700_dir(tmp_path):
    state = tmp_path / "state"
    AuditLog(state / "audit.log").record(
        {"action": "pane.reply", "pane_id": "w1:p2",
         "detail": {"outcome": "delivered", "text": "hi"}}
    )
    path = state / "audit.log"
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["action"] == "pane.reply"
    assert parsed["detail"]["outcome"] == "delivered"
    assert parsed["detail"]["text"] == "hi"


def test_record_never_raises_on_an_unwritable_path(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the state dir should be")
    log = AuditLog(blocker / "audit.log")
    log.record({"action": "pane.reply", "detail": {"outcome": "error"}})
    assert not (blocker / "audit.log").exists()


def test_record_swallows_a_format_failure_and_keeps_working(tmp_path):
    path = tmp_path / "state" / "audit.log"
    log = AuditLog(path)
    log.record({"detail": {}})  # no action: format raises inside record()
    log.record({"action": "ok"})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["action"] == "ok"


def test_rotation_moves_to_a_single_generation_and_keeps_writing(tmp_path):
    # Measure one deterministic line first (fixed clock).
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    AuditLog(scratch / "audit.log", clock=lambda: FIXED_NOW).record(
        {"action": "tick", "detail": {"i": 0}}
    )
    line_bytes = (scratch / "audit.log").stat().st_size

    # Cap for three lines; record five: the 4th write rotates (3 >= 3).
    state = tmp_path / "rot"
    state.mkdir()
    path = state / "audit.log"
    log = AuditLog(path, clock=lambda: FIXED_NOW, max_bytes=line_bytes * 3)
    for i in range(5):
        log.record({"action": "tick", "detail": {"i": i}})
    assert (state / "audit.log.1").is_file()
    assert not (state / "audit.log.2").exists()
    rotated = (state / "audit.log.1").read_text(encoding="utf-8").splitlines()
    live = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(l)["detail"]["i"] for l in rotated] == [0, 1, 2]
    assert [json.loads(l)["detail"]["i"] for l in live] == [3, 4]

    # A second rotation REPLACES audit.log.1 — never chains to .2 — and
    # the live file keeps accepting lines.
    for i in range(5, 10):
        log.record({"action": "tick", "detail": {"i": i}})
    assert not (state / "audit.log.2").exists()
    rotated = (state / "audit.log.1").read_text(encoding="utf-8").splitlines()
    live = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(l)["detail"]["i"] for l in rotated] == [6, 7, 8]
    assert [json.loads(l)["detail"]["i"] for l in live] == [9]


def test_failed_rotation_does_not_lose_the_line(tmp_path):
    state = tmp_path / "rotfail"
    state.mkdir()
    path = state / "audit.log"
    # A directory at the rotation target makes os.replace fail.
    (state / "audit.log.1").mkdir()
    log = AuditLog(path, clock=lambda: FIXED_NOW, max_bytes=1)
    for i in range(3):
        log.record({"action": "tick", "detail": {"i": i}})
    # Every line landed in the (oversized) live file: an oversized trail
    # beats a missing line.
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(l)["detail"]["i"] for l in lines] == [0, 1, 2]
    assert (state / "audit.log.1").is_dir()  # the blocker is untouched
