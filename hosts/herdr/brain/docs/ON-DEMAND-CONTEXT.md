# On-Demand Context (consultations, reports, followups)

Maintainer-facing summary of the on-demand context feature: what it does,
how it runs, where state lives, and what is deliberately out of scope.
Full product intent and decisions: `docs/prds/herdr-brain-on-demand-context.md`
(D01–D10, FR-01..45). Test-by-test acceptance map:
`docs/on-demand-context-coverage.md`.

## What it does

The default voice conversation still focuses the **selected** Herdr
session. On demand — only when a query asks for it — the brain builds an
evidence-grounded consolidated report instead of answering from the
selected pane:

| Journey | Experience |
|---|---|
| J1 Focus chat | "What's it doing?" answers about the selected session only; nothing else leaks in. |
| J2 Global now | "What's the current state of my work?" → one brief per project (advances / pending / blockers); references on screen, never spoken. |
| J3 History | "What did I do this week?" → evidence from OpenCode/Claude/Antigravity chats and/or Engram, resolved to the user's timezone periods. |
| J4 Followup | "And blockers?" retains the last successful report's context without touching the Herdr selection. |
| J5 Failure | A needed source failing or timing out → spoken unable-to-complete; never a partial dressed as complete. |

## Tool surface (read-only)

Four tools are registered alongside the existing brain tools
(`tools.py`); none of them can write to a pane:

| Tool | Purpose |
|---|---|
| `consult_work_status` | Global current-progress consultation (optionally date-scoped, single project). |
| `consult_history` | Historical consultation over a natural period (`today`, `this_week`, `last_7_days`) or explicit bounds. |
| `get_followup_context` | Returns the screen summary of the last successful report when the followup fingerprint matches; otherwise instructs the model to re-consult. |
| `end_followup` | Explicitly drops the followup anchor. |

## Pipeline

```
classify ──► periods ──► freshness ──► acquire ──► consolidate ──► publish ──► render
   │            │            │            │             │             │
 intent      timezone +   manifest    read sources   per-project   atomic
 routing     DST-safe     vs stored   (one re-read   brief +       full-report
 (focus is   interval     snapshot:   on mid-read    references    revision
  rejected   resolution   reuse or    token change)  (LLM          only
  outright)  (ask, never  full scan                  summarizer)
              guess)       rebuild)
```

- **classify** (`queryfsm.py`): focus intents are rejected before touching
  any provider, store, or timezone work; historical intents require a
  period; multi-project scopes are rejected with a split hint.
- **periods** (`periods.py`): today = local midnight, this week = Monday,
  last 7 days = rolling through the query instant; DST-safe (gap → first
  valid instant, fold → earliest); naive/fixed-offset/POSIX TZ inputs are
  rejected. If no timezone is available the engine asks (`ask_tz`); it
  never guesses.
- **freshness** (`freshness.py`): builds a candidate source manifest and
  compares revision tokens against the stored snapshot. Identical
  set + tokens + interval → **reuse, but only after the check ran**. Any
  difference (addition, removal, closure, status change, edit/deletion,
  window movement) → full rebuild; providers have no append-only delta
  path, so deletion coverage is always a bounded full scan.
- **acquire** (`evidence*.py`): reads within the remaining budget;
  all-or-nothing — a mid-read revision change gets one re-read, then the
  source is coverage-failed. Empty source and failed coverage are distinct
  outcomes.
- **consolidate** (`report.py` + the LLM summarizer in `consult.py`): the
  deterministic layer groups evidence per project, records provenance, and
  validates the model's brief (citations must reference real sources,
  conflict notes must name source ids and stay screen-only); the model
  writes the prose.
- **publish** (`reportstore.py`): one atomic, complete revision per
  successful build; stale/cancelled builds are discarded.
- **render** (`consult.py` + `static/consult.js`): one completed spoken
  report (prose only — leak-checked before publish) plus the screen
  artifact with references; a Consulting indicator is visible from
  freshness check until render.

## The 60-second budget

One wall-clock deadline per consultation starts when the server accepts
the query and covers classification, period resolution, freshness,
acquisition, consolidation, and publication. It is **never reset per tool
call** — every component shares the same `Deadline` object and the LLM
summarizer's request timeout is the *remaining* budget. Audio rendering
happens after, outside the budget. Expiry at any phase →
unable-to-complete. Over the volume thresholds → a narrowing ask, never
silent truncation.

## Stores and retention (24h is retention, NOT staleness)

| Store | Default path | Semantics |
|---|---|---|
| Report store (`reportstore.py`) | `<audio_dir>/../reports.db` (`HERDR_BRAIN_REPORT_DB` to override) | Latest published report + references per (main-call context, scope, normalized interval, timezone). Rows expire 24h after creation; purge is bounded and opportunistic. |
| Followup store (`followup.py`) | beside the audio dir (`HERDR_BRAIN_FOLLOWUP_DB`) | One anchor per context bound to the last successful report; topic change or explicit `end_followup` expires it; touch never extends life. |

**The 24h horizon is retention, not a freshness TTL.** Every consultation
— even a repeat of the identical query — rebuilds the candidate manifest
and freshness-checks sources before answering. A stored report is served
only after a successful check with identical source set, revision tokens,
and interval. After a failed refresh the prior report is demoted
(`refresh_failed`) and never presented as current.

## Security posture

- All four tools are **read-only**; the two write paths
  (`send_to_session`, `create_session`) and their approval gates are
  untouched (regression-tested in `test_consult.py::TestApprovalRegression`).
- Transcript/screen/Engram content is untrusted **data**: it is delivered
  to the summarizer inside explicit data markers, the summarizer system
  prompt forbids tool use, and even a model that obeys an injected
  instruction cannot execute anything through these tools.
- Retrieval is local-only (SQLite/JSONL under the configured roots);
  no new remote access or destinations.
- Spoken output carries no source ids or references; references exist
  only in the screen artifact (asserted before publish and in the UI
  tests; `consult.js` uses `textContent`/`createElement` only).

## Configuration knobs

| Variable | Default | Purpose |
|---|---|---|
| `HERDR_BRAIN_CONSULT_BUDGET_S` | `60` | Consultation deadline (seconds). |
| `HERDR_BRAIN_CONSULT_NARROW_ITEMS` | `4000` | Evidence-item threshold for the narrowing ask. |
| `HERDR_BRAIN_CONSULT_NARROW_CHARS` | `500000` | Evidence-character threshold for the narrowing ask. |
| `HERDR_BRAIN_REPORT_DB` | beside audio dir | Report store SQLite path. |
| `HERDR_BRAIN_FOLLOWUP_DB` | beside audio dir | Followup store SQLite path. |
| `HERDR_BRAIN_OPENCODE_DB` | auto-detect | OpenCode storage db override. |
| `HERDR_BRAIN_CLAUDE_ROOT` | auto-detect | Claude projects root override. |
| `HERDR_BRAIN_ANTIGRAVITY_ROOT` | auto-detect | Antigravity agent dir override. |
| `HERDR_BRAIN_ENGRAM_DB` | `~/.engram/engram.db` | Engram SQLite override (opened read-only). |
| `TZ` | — | Timezone candidate (runtime ask if unset/invalid). |

Thresholds are provisional (T7 rule decision); tuning is open.

## Known limitations and open product decisions

From the task ledger (`odd/tasks/herdr-brain-on-demand-context.md`) and
the T11 acceptance pass:

- **Antigravity project identity is empty** — the `conversation_summaries.db`
  authority question is deferred, so Antigravity conversations land in the
  unknown-project bucket (cwd fallback only).
- **Conflict detection is model work** — the deterministic layer validates
  and surfaces conflicts with source+date, but *recognizing* a conflict is
  the summarizer's job; no real-model eval has been run.
- **Followup matching is exact fingerprint equality** — no semantic
  continuity; a rephrased followup expires the anchor and re-consults.
- **Multi-project fan-out** — queries naming several projects are rejected
  with a split hint instead of fanning out.
- Open tuning: narrow thresholds, `max_span` / `future_tolerance` defaults,
  POSIX TZ support, purge scheduling/cadence, `consult.js` cache-busting,
  panel dismissal UX.
- **FR-42..45 (autonomous implementation loop) are specification only** —
  deliberately not built by this feature.

## Acceptance

Per-cluster coverage of the PRD Requirement-to-Test Matrix, the
Verification Checklist status, and remaining gaps are recorded in
`docs/on-demand-context-coverage.md`.
