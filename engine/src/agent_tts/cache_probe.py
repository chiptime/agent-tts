"""Read-only prompt-cache measurement over local agent transcripts (HT-19 F2b).

The engine owns the transcript connectors, so it also owns what those
transcripts can say about prompt-cache behavior. This module turns recorded
per-request token usage into an honest per-session report: for each request,
the timestamp, the gap since the previous request, input/cached/written token
counts and whether the cache hit; plus a session summary (hit ratio and the
OBSERVED TTL window — the largest gap that still produced a hit and the
smallest gap that produced a miss). It is pure local analysis: no push, no
daemon changes, no network, read-only access to the agent artifacts.

What each source actually records (verified against real local artifacts,
2026-11-17):

* claude  — ``~/.claude/projects/<munged-cwd>/<session>.jsonl``: assistant
  events carry ``message.usage`` with ``input_tokens``,
  ``cache_read_input_tokens`` and ``cache_creation_input_tokens`` plus a
  top-level ISO ``timestamp``. One API response is written as several
  stream-chunk lines sharing the same ``message.id``, so chunks are grouped
  into one request.
* opencode — ``~/.local/share/opencode/opencode.db`` (SQLite, opened with a
  ``mode=ro`` URI exactly like the connector): ``message.data`` JSON carries
  ``tokens`` with ``input``/``output`` and ``cache: {read, write}`` for every
  provider (Anthropic, GLM, ...). Rows whose counters are all zero carry no
  API usage evidence (synthetic messages) and are skipped.
* codex   — ``~/.codex/sessions/<Y>/<M>/<D>/rollout-*-<session>.jsonl``:
  ``event_msg``/``token_count`` events carry
  ``info.last_token_usage.cached_input_tokens``. The same response can be
  re-reported with an identical snapshot, so consecutive identical snapshots
  are deduplicated into one request.
* antigravity / aider — their transcripts carry no per-request token usage,
  so the report says so explicitly instead of guessing ("not measurable for
  <agent>").
"""

import argparse
import glob
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from agent_tts.sources.base import CLAUDE_SESSION_RE, OPENCODE_SESSION_RE

DEFAULT_CLAUDE_ROOT = os.path.expanduser("~/.claude/projects")
DEFAULT_OPENCODE_DB = os.path.expanduser("~/.local/share/opencode/opencode.db")
DEFAULT_CODEX_ROOT = os.path.expanduser("~/.codex/sessions")
DEFAULT_ANTIGRAVITY_ROOT = os.path.expanduser("~/.gemini/antigravity-cli/brain")

# Probe outcomes. "missing" lets the sniffing router try the next candidate.
STATUS_OK = "ok"
STATUS_NOT_MEASURABLE = "not_measurable"
STATUS_NO_REQUESTS = "no_requests"
STATUS_MISSING = "missing"


@dataclass
class TurnUsage:
    """One measured model request (already deduplicated to request level)."""

    timestamp: float  # epoch seconds (UTC)
    input_tokens: int  # uncached input tokens
    cached_tokens: int  # tokens served from the prompt cache
    cache_write_tokens: int  # tokens written to the cache, 0 when unrecorded


@dataclass
class ProbeResult:
    """Outcome of probing one agent's transcript for a session."""

    agent: str
    session_id: Optional[str]
    status: str
    turns: List[TurnUsage] = field(default_factory=list)
    reason: Optional[str] = None
    source_path: Optional[str] = None


# ─── Shared helpers ──────────────────────────────────────────


def _parse_iso_ts(value) -> Optional[float]:
    """Parses an ISO-8601 timestamp (``...Z`` included) to epoch seconds."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _iso_utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Claude Code producer ────────────────────────────────────


def probe_claude(session_id: str, root: str) -> ProbeResult:
    """Measures requests from a Claude Code session JSONL transcript."""
    result = ProbeResult(agent="claude", session_id=session_id, status=STATUS_MISSING)
    matches = sorted(glob.glob(os.path.join(root, "*", f"{session_id}.jsonl")))
    if not matches:
        return result
    result.source_path = matches[0]

    saw_assistant = False
    raw = []  # (group_key, timestamp, usage) per assistant line with usage
    with open(matches[0], "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(event, dict) or event.get("type") != "assistant":
                continue
            saw_assistant = True
            ts = _parse_iso_ts(event.get("timestamp"))
            message = event.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            if ts is None or not isinstance(usage, dict):
                continue
            key = message.get("id") or event.get("requestId") or id(event)
            raw.append((key, ts, usage))

    # Collapse consecutive stream-chunk lines of one API response (they share
    # the message id and repeat the same usage) into a single request.
    turns: List[TurnUsage] = []
    previous_key = object()
    for key, ts, usage in raw:
        if key == previous_key:
            continue
        previous_key = key
        turns.append(
            TurnUsage(
                timestamp=ts,
                input_tokens=_as_int(usage.get("input_tokens")),
                cached_tokens=_as_int(usage.get("cache_read_input_tokens")),
                cache_write_tokens=_as_int(usage.get("cache_creation_input_tokens")),
            )
        )

    if turns:
        result.status = STATUS_OK
        result.turns = turns
    elif saw_assistant:
        result.status = STATUS_NOT_MEASURABLE
        result.reason = "assistant messages carry no usage data"
    else:
        result.status = STATUS_NO_REQUESTS
    return result


def _as_int(value) -> int:
    return value if isinstance(value, int) else 0


# ─── OpenCode producer ───────────────────────────────────────

_OPENCODE_TURNS_SQL = """
SELECT time_created, data
FROM message
WHERE session_id = ?
  AND json_extract(data, '$.role') = 'assistant'
ORDER BY time_created ASC, id ASC
"""


def probe_opencode(session_id: str, db_path: str) -> ProbeResult:
    """Measures requests from OpenCode's local transcript database (read-only)."""
    result = ProbeResult(agent="opencode", session_id=session_id, status=STATUS_MISSING)
    if not os.path.isfile(db_path):
        return result
    uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            rows = conn.execute(_OPENCODE_TURNS_SQL, (session_id,)).fetchall()
    except (sqlite3.Error, OSError):
        return result
    result.source_path = db_path

    turns: List[TurnUsage] = []
    for time_created, data in rows:
        try:
            record = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            continue
        tokens = record.get("tokens") if isinstance(record, dict) else None
        if not isinstance(tokens, dict):
            continue
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        numbers = (
            _as_int(tokens.get("input")),
            _as_int(tokens.get("output")),
            _as_int(cache.get("read")),
            _as_int(cache.get("write")),
        )
        if numbers == (0, 0, 0, 0):
            # All counters zero: no API usage evidence (synthetic message).
            continue
        turns.append(
            TurnUsage(
                timestamp=time_created / 1000.0,
                input_tokens=numbers[0],
                cached_tokens=numbers[2],
                cache_write_tokens=numbers[3],
            )
        )

    if turns:
        result.status = STATUS_OK
        result.turns = turns
    elif rows:
        result.status = STATUS_NOT_MEASURABLE
        result.reason = "messages carry no token usage data"
    else:
        result.status = STATUS_NO_REQUESTS
    return result


# ─── Codex producer ──────────────────────────────────────────


def probe_codex(session_id: str, sessions_root: str) -> ProbeResult:
    """Measures requests from a Codex CLI rollout transcript."""
    result = ProbeResult(agent="codex", session_id=session_id, status=STATUS_MISSING)
    matches = sorted(
        glob.glob(os.path.join(sessions_root, "*", "*", "*", f"rollout-*{session_id}.jsonl"))
    )
    path = next(
        (p for p in matches if os.path.basename(p).endswith(f"-{session_id}.jsonl")), None
    )
    if path is None:
        return result
    result.source_path = path

    turns: List[TurnUsage] = []
    previous_snapshot = None
    saw_events = False
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(event, dict) or event.get("type") != "event_msg":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            saw_events = True
            info = payload.get("info")
            usage = info.get("last_token_usage") if isinstance(info, dict) else None
            ts = _parse_iso_ts(event.get("timestamp"))
            if not isinstance(usage, dict) or ts is None:
                continue
            snapshot = (
                _as_int(usage.get("input_tokens")),
                _as_int(usage.get("cached_input_tokens")),
                _as_int(usage.get("output_tokens")),
            )
            # The same response is re-reported with an identical snapshot:
            # one request, not two.
            if snapshot == previous_snapshot:
                continue
            previous_snapshot = snapshot
            turns.append(
                TurnUsage(
                    timestamp=ts,
                    input_tokens=snapshot[0],
                    cached_tokens=snapshot[1],
                    cache_write_tokens=0,
                )
            )

    if turns:
        result.status = STATUS_OK
        result.turns = turns
    elif saw_events:
        result.status = STATUS_NOT_MEASURABLE
        result.reason = "rollout carries no usable token_count events"
    else:
        result.status = STATUS_NO_REQUESTS
    return result


# ─── Honest degradation: agents without cache data ───────────


def probe_antigravity(session_id: str, root: str) -> ProbeResult:
    """Antigravity transcripts carry no token usage: declared not measurable."""
    result = ProbeResult(
        agent="antigravity", session_id=session_id, status=STATUS_NOT_MEASURABLE,
        reason="transcript carries no token usage data",
    )
    path = os.path.join(root, session_id, ".system_generated", "logs", "transcript.jsonl")
    if os.path.isfile(path):
        result.source_path = path
    else:
        result.status = STATUS_MISSING
    return result


def probe_aider(session_id: Optional[str]) -> ProbeResult:
    """Aider chat history carries no per-turn token/cache data."""
    return ProbeResult(
        agent="aider", session_id=session_id, status=STATUS_NOT_MEASURABLE,
        reason="aider chat history carries no per-turn token/cache data",
    )


# ─── Analysis ────────────────────────────────────────────────


@dataclass
class SessionSummary:
    requests: int
    hits: int
    hit_ratio: float
    max_hit_gap_s: Optional[float]
    min_miss_gap_s: Optional[float]
    ttl_sentence: str


def summarize(turns: List[TurnUsage]) -> SessionSummary:
    """Hit ratio plus the observed TTL window over request-to-request gaps."""
    hits = sum(1 for turn in turns if turn.cached_tokens > 0)
    requests = len(turns)
    hit_gaps = [
        turns[i].timestamp - turns[i - 1].timestamp
        for i in range(1, requests)
        if turns[i].cached_tokens > 0 and turns[i].timestamp > turns[i - 1].timestamp
    ]
    miss_gaps = [
        turns[i].timestamp - turns[i - 1].timestamp
        for i in range(1, requests)
        if turns[i].cached_tokens == 0 and turns[i].timestamp > turns[i - 1].timestamp
    ]
    max_hit = max(hit_gaps) if hit_gaps else None
    min_miss = min(miss_gaps) if miss_gaps else None

    if requests < 2:
        sentence = "observed TTL: not enough data (need at least two requests)"
    elif max_hit is None and min_miss is None:
        sentence = "observed TTL: not enough data (no positive gaps recorded)"
    elif min_miss is None:
        sentence = (
            f"observed TTL: largest gap with a hit {max_hit:.1f}s; no miss observed "
            f"-> TTL is at least {max_hit:.1f}s"
        )
    elif max_hit is None:
        sentence = (
            f"observed TTL: no hit observed; smallest gap with a miss {min_miss:.1f}s "
            f"-> TTL is below {min_miss:.1f}s"
        )
    elif max_hit < min_miss:
        sentence = (
            f"observed TTL: largest gap with a hit {max_hit:.1f}s; "
            f"smallest gap with a miss {min_miss:.1f}s "
            f"-> TTL between {max_hit:.1f}s and {min_miss:.1f}s"
        )
    else:
        sentence = (
            f"observed TTL: inconclusive - hit at {max_hit:.1f}s but miss at "
            f"{min_miss:.1f}s (overlapping gaps)"
        )
    return SessionSummary(
        requests=requests,
        hits=hits,
        hit_ratio=(hits / requests) if requests else 0.0,
        max_hit_gap_s=max_hit,
        min_miss_gap_s=min_miss,
        ttl_sentence=sentence,
    )


# ─── Report rendering ────────────────────────────────────────


def _gap_pairs(turns: List[TurnUsage]) -> List[Tuple[TurnUsage, Optional[float]]]:
    """Pairs every request with its gap to the previous one (None for the first)."""
    pairs: List[Tuple[TurnUsage, Optional[float]]] = []
    for index, turn in enumerate(turns):
        gap = None
        if index > 0 and turn.timestamp > turns[index - 1].timestamp:
            gap = turn.timestamp - turns[index - 1].timestamp
        pairs.append((turn, gap))
    return pairs


def _turn_lines(pairs: List[Tuple[TurnUsage, Optional[float]]], first_index: int) -> List[str]:
    lines = []
    for offset, (turn, gap) in enumerate(pairs):
        gap_text = f"gap={gap:.1f}s" if gap is not None else "gap=-"
        outcome = "HIT" if turn.cached_tokens > 0 else "MISS"
        lines.append(
            f"  {first_index + offset}  {_iso_utc(turn.timestamp)}  {gap_text}"
            f"  in={turn.input_tokens}  cached={turn.cached_tokens}"
            f"  written={turn.cache_write_tokens}  {outcome}"
        )
    return lines


def render_text(result: ProbeResult, summary: SessionSummary, limit: Optional[int]) -> str:
    lines = [f"cache report: {result.agent} session {result.session_id}"]
    if result.source_path:
        lines.append(f"source: {result.source_path}")
    pairs = _gap_pairs(result.turns)
    first_index = 1
    if limit is not None and limit < len(pairs):
        lines.append(f"showing last {limit} of {len(pairs)} turns")
        pairs = pairs[-limit:]
        first_index = len(result.turns) - len(pairs) + 1
    lines.extend(_turn_lines(pairs, first_index))
    lines.append(f"requests: {summary.requests}  hits: {summary.hits} "
                 f"({summary.hit_ratio * 100:.1f}%)")
    lines.append(summary.ttl_sentence)
    return "\n".join(lines) + "\n"


def render_json(result: ProbeResult, summary: SessionSummary, limit: Optional[int]) -> str:
    pairs = _gap_pairs(result.turns)
    if limit is not None and limit < len(pairs):
        pairs = pairs[-limit:]
    payload = {
        "agent": result.agent,
        "session_id": result.session_id,
        "source_path": result.source_path,
        "measurable": True,
        "turns": [
            {
                "timestamp": _iso_utc(turn.timestamp),
                "gap_s": round(gap, 3) if gap is not None else None,
                "input_tokens": turn.input_tokens,
                "cached_tokens": turn.cached_tokens,
                "cache_write_tokens": turn.cache_write_tokens,
                "hit": turn.cached_tokens > 0,
            }
            for turn, gap in pairs
        ],
        "summary": {
            "requests": summary.requests,
            "hits": summary.hits,
            "hit_ratio": round(summary.hit_ratio, 4),
            "max_hit_gap_s": summary.max_hit_gap_s,
            "min_miss_gap_s": summary.min_miss_gap_s,
            "ttl": summary.ttl_sentence,
        },
    }
    return json.dumps(payload, indent=2) + "\n"


# ─── Routing and latest-session defaults ─────────────────────


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _latest_claude_session(root: str) -> Optional[str]:
    files = glob.glob(os.path.join(root, "*", "*.jsonl"))
    if not files:
        return None
    return os.path.splitext(os.path.basename(max(files, key=os.path.getmtime)))[0]


def _latest_opencode_session(db_path: str) -> Optional[str]:
    if not os.path.isfile(db_path):
        return None
    uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            row = conn.execute(
                "SELECT session_id FROM message "
                "WHERE json_extract(data, '$.role') = 'assistant' "
                "AND json_extract(data, '$.tokens') IS NOT NULL "
                "ORDER BY time_created DESC LIMIT 1"
            ).fetchone()
    except (sqlite3.Error, OSError):
        return None
    return row[0] if row else None


def _latest_codex_session(sessions_root: str) -> Optional[str]:
    files = glob.glob(os.path.join(sessions_root, "*", "*", "*", "rollout-*.jsonl"))
    if not files:
        return None
    newest = max(files, key=os.path.getmtime)
    stem = os.path.splitext(os.path.basename(newest))[0]
    return stem.rsplit("-", 1)[1]


def _latest_antigravity_session(root: str) -> Optional[str]:
    pattern = os.path.join(root, "*", ".system_generated", "logs", "transcript.jsonl")
    files = glob.glob(pattern)
    if not files:
        return None
    newest = max(files, key=os.path.getmtime)
    return os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(newest))))


def _candidates(agent: Optional[str], session_id: str) -> List[str]:
    """Resolves which agent names to try, mirroring the connector sniffing."""
    registry = ["opencode", "claude", "codex", "antigravity", "aider"]
    key = (agent or "").strip().lower()
    if key == "agy":
        key = "antigravity"
    if key in registry:
        return [key]
    if not key and OPENCODE_SESSION_RE.match(session_id):
        return ["opencode"]
    if not key and CLAUDE_SESSION_RE.match(session_id):
        return ["claude", "codex", "antigravity"]
    return []


def handle_cache_report_command(
    argv: List[str],
    out=None,
    err=None,
    claude_root: Optional[str] = None,
    opencode_db: Optional[str] = None,
    codex_root: Optional[str] = None,
    antigravity_root: Optional[str] = None,
) -> int:
    """Entry point for ``agent-tts cache-report``. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        prog="agent-tts cache-report",
        description="Read-only prompt-cache measurement from agent transcripts",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Agent tool name (opencode, claude, codex, antigravity, aider); "
        "omitted, the session id shape is sniffed like the connector layer",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Agent session id; omitted, the agent's most recent session is used",
    )
    parser.add_argument(
        "--limit", type=_positive_int, default=None,
        help="Show only the last N request lines (the summary still covers all)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the report as JSON",
    )
    args = parser.parse_args(argv)
    out = out or sys.stdout
    err = err or sys.stderr

    claude_root = claude_root or DEFAULT_CLAUDE_ROOT
    opencode_db = opencode_db or os.environ.get("OPENCODE_DB") or DEFAULT_OPENCODE_DB
    codex_root = codex_root or DEFAULT_CODEX_ROOT
    antigravity_root = antigravity_root or DEFAULT_ANTIGRAVITY_ROOT

    def _probe(agent: str, session_id: Optional[str]) -> ProbeResult:
        if agent == "claude":
            return probe_claude(session_id, claude_root)
        if agent == "opencode":
            return probe_opencode(session_id, opencode_db)
        if agent == "codex":
            return probe_codex(session_id, codex_root)
        if agent == "antigravity":
            return probe_antigravity(session_id, antigravity_root)
        return probe_aider(session_id)

    def _latest(agent: str) -> Optional[str]:
        if agent == "claude":
            return _latest_claude_session(claude_root)
        if agent == "opencode":
            return _latest_opencode_session(opencode_db)
        if agent == "codex":
            return _latest_codex_session(codex_root)
        if agent == "antigravity":
            return _latest_antigravity_session(antigravity_root)
        return None  # aider has no session store

    session_id = args.session_id
    if session_id:
        names = _candidates(args.agent, session_id)
        if not names:
            print(
                f"Error: no transcript found for {args.agent or 'auto'}/{session_id}",
                file=err,
            )
            return 1
    else:
        names = _candidates(args.agent, "ses_placeholder-sniff")
        if args.agent and not names:
            print(f"Error: unknown agent '{args.agent}'", file=err)
            return 2
        if names == ["aider"]:
            # Aider has no session store: the id is a pass-through label, so
            # the honest degradation works without one.
            session_id = None
        else:
            session_id = next((sid for sid in map(_latest, names) if sid), None)
            if not session_id:
                print(
                    f"Error: no transcript found for {args.agent or names[0]}",
                    file=err,
                )
                return 1

    result: Optional[ProbeResult] = None
    for name in names:
        result = _probe(name, session_id)
        if result.status != STATUS_MISSING:
            break
    assert result is not None
    if result.status == STATUS_MISSING:
        print(
            f"Error: no transcript found for {args.agent or 'auto'}/{session_id}",
            file=err,
        )
        return 1
    if result.status == STATUS_NO_REQUESTS:
        print(
            f"Error: session {session_id} ({result.agent}) has no assistant requests",
            file=err,
        )
        return 1
    if result.status == STATUS_NOT_MEASURABLE:
        body = {
            "agent": result.agent,
            "session_id": result.session_id,
            "source_path": result.source_path,
            "measurable": False,
            "reason": result.reason,
        }
        if args.json:
            print(json.dumps(body), file=out)
        else:
            print(
                f"cache report: {result.agent} session {result.session_id or '-'}",
                file=out,
            )
            if result.source_path:
                print(f"source: {result.source_path}", file=out)
            print(
                f"cache behavior: not measurable for {result.agent} - {result.reason}",
                file=out,
            )
        return 0

    summary = summarize(result.turns)
    if args.json:
        print(render_json(result, summary, args.limit), file=out, end="")
    else:
        print(render_text(result, summary, args.limit), file=out, end="")
    return 0
