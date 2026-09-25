# Feature: Call History Persistence (herdr-brain)

**Repo**: `~/Code/personal/herdr-brain`
**Created**: 2026-09-25
**Status**: planned

## Objective

The call transcript survives tab/window/app restarts: reopening the PWA
repaints the previous conversation, and the brain keeps LLM context even
after a server restart.

## Problem / Why

Voice call turns live ONLY in the in-process ConversationStore ring
(memory.py, 16 msgs, keyed "default"); nothing is written to disk. On UI
reload the drawer starts empty (ghost bubble only); on service restart
history is gone entirely. User expectation (2026-09-25): visual
conversation persists across tab/window restarts; the brain already
"remembers" within a server lifetime, and that context should survive
restarts too.

## Scope / Constraints

- Append-only JSONL at `~/.local/state/herdr-brain/call_history.jsonl`
  (same state dir the app already uses for audio/reader_cache).
- Record shape: `{ts, role: "user"|"assistant", text}`. Server writes on
  every /ask turn (user + assistant) — single write seam.
- `GET /call-history` → `{turns: [{ts, role, text}, …]}` oldest-first,
  capped at the last 200 (config-independent constant is fine).
- Boot seed: ConversationStore loads the last 16 records from disk so
  LLM context survives service restarts.
- `POST /reset` truncates the persisted file (reset = clean slate, UI
  and disk agree).
- Light compaction: on boot, if the file exceeds 2000 records rewrite it
  down to the last 1000.
- app.js boot paints history into the drawer ONLY when it is still
  empty (no clobbering a live session).
- Lock discipline mirrors ConversationStore (threading.Lock).
- TDD mode: OFF — tests alongside code, same work-unit commit.
- Delivery: work-unit commits straight to local `master`, never push
  (USER DECISION 2026-09-24).

## Delivery Forecast

- Estimated authored lines: ~250 (tests included). No chain needed.

## Tasks

### [ ] T1 — HistoryStore: JSONL append/load/compact/reset
Files: `src/herdr_brain/history.py` (new), `tests/test_history.py` (new).
Thread-safe append (fsync-light: plain write+close is fine for this
scale), `load(last_n)`, boot compaction (>2000 → last 1000), `clear()`.
Path derived from settings state dir (audio_dir.parent), override via
Settings for tests.
Route: delegated-direct (writer).
Checks: `pytest tests/test_history.py` + full suite. Commit: `feat(history)`.

### [ ] T2 — Wire /ask writes + /call-history + reset + boot seed
Files: `src/herdr_brain/server.py`, `src/herdr_brain/memory.py`,
`tests/test_call_history_api.py` (new).
/ask handler appends user and assistant turns through HistoryStore;
GET /call-history returns last 200 oldest-first; POST /reset also
clears the file; ConversationStore boot-seeds last 16 from disk.
Route: delegated-direct (writer).
Checks: pytest new + full suite. Commit: `feat(history)`.

### [ ] T3 — PWA repaints history on boot
Files: `src/herdr_brain/static/app.js`.
After initial refreshState, fetch `/call-history`; if the drawer has no
real turns yet, addTurn each oldest-first (user → "user", else "brain").
Silent failure (console.warn) if endpoint unavailable — never blocks
boot.
Route: delegated-direct (writer).
Checks: manual smoke via curl + node --test suite still green.
Commit: `feat(ui)`.

## Acceptance Criteria

- Reload the PWA mid-call or later: previous turns are visible.
- Restart the herdr-brain service: drawer repaints and the LLM still
  has prior context (no cold start).
- POST /reset empties both ring and disk file.

## Progress / Evidence

- (pending)
