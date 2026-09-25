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

### [x] T1 — HistoryStore: JSONL append/load/compact/reset
Files: `src/herdr_brain/history.py` (new), `tests/test_history.py` (new).
Thread-safe append (fsync-light: plain write+close is fine for this
scale), `load(last_n)`, boot compaction (>2000 → last 1000), `clear()`.
Path derived from settings state dir (audio_dir.parent), override via
Settings for tests.
Route: delegated-direct (writer).
Checks: `pytest tests/test_history.py` + full suite. Commit: `feat(history)`.

### [x] T2 — Wire /ask writes + /call-history + reset + boot seed
Files: `src/herdr_brain/server.py`, `src/herdr_brain/memory.py`,
`tests/test_call_history_api.py` (new).
/ask handler appends user and assistant turns through HistoryStore;
GET /call-history returns last 200 oldest-first; POST /reset also
clears the file; ConversationStore boot-seeds last 16 from disk.
Route: delegated-direct (writer).
Checks: pytest new + full suite. Commit: `feat(history)`.

### [x] T3 — PWA repaints history on boot
Files: `src/herdr_brain/static/app.js`.
After initial refreshState, fetch `/call-history`; if the drawer has no
real turns yet, addTurn each oldest-first (user → "user", else "brain").
Silent failure (console.warn) if endpoint unavailable — never blocks
boot.
Route: delegated-direct (writer).
Checks: manual smoke via curl + node --test suite still green.
Commit: `feat(ui)`.

### [x] T4 — Paginated /call-history (cursor `before`)
Files: `src/herdr_brain/server.py`, `src/herdr_brain/history.py`,
tests.
User decision 2026-09-25: don't return everything at once. `GET
/call-history?before=<iso-ts>&limit=N` — default limit 25 (cap 200);
returns the `limit` turns strictly OLDER than `before` (omit = newest
page), oldest-first, plus `has_more: bool`. Existing no-param callers
keep working (same shape + has_more added).
Route: delegated-direct (writer).
Checks: pytest pagination cases (page boundaries, has_more flips).
Commit: `feat(history)`.

### [x] T5 — "Ver más" prepends older turns
Files: `src/herdr_brain/static/app.js`, `src/herdr_brain/static/index.html`.
Initial repaint loads ONLY the newest page (25); if `has_more`, render
a "Ver más" button above the transcript. Click: fetch
`before=<oldest rendered ts>`, PREPEND turns keeping the scroll anchor
(no jump), hide button when exhausted. Same T3 empty-drawer guard and
silent-failure idiom.
Route: delegated-direct (writer).
Checks: node --test green; manual reasoning for DOM (no app.js harness).
Commit: `feat(ui)`.

### [ ] T6 — Mini DB: JSONL → SQLite (same HistoryStore API)
Files: `src/herdr_brain/history.py`, tests.
User request 2026-09-25 ("mini bbdd"): store turns in SQLite at
`~/.local/state/herdr-brain/call_history.db` (stdlib sqlite3; table
turns(ts TEXT PK/indexed, role, text)). Keep the public API unchanged
(append / load / load_before / clear + boot compaction now = DELETE
beyond retention). One-time migration: if the old call_history.jsonl
exists, import its records on boot, then stop using the JSONL (rename
to .imported). All endpoint/UI shapes unchanged (server.py, app.js
untouched). NOTE: herdr-tts was checked — it has NO call-history or
DB; the call history belongs to herdr-brain (owner of the call).
Route: delegated-direct (writer).
Checks: pytest history + API tests green with sqlite backend;
migration test (jsonl fixture → imported). Commit: `feat(history)`.

## Acceptance Criteria

- Reload the PWA mid-call or later: previous turns are visible.
- Restart the herdr-brain service: drawer repaints and the LLM still
  has prior context (no cold start).
- POST /reset empties both ring and disk file.

## Progress / Evidence

- 2026-09-25 T1 commit `d906e60` (history.py + 19 unit tests).
- 2026-09-25 T2 commit `bdd8059` (server seam + /call-history + reset +
  boot seed, 11 API tests). Deviation: seam at the /ask route level —
  the approval replay provably adds ZERO records (test); memory.py
  untouched.
- 2026-09-25 T3 commit `2cf666f` (boot repaint guarded on empty drawer,
  `.catch(console.warn)` silent-failure idiom).
- 2026-09-25 T4 commit `05d1323` (`before`+`limit` cursor, tz-aware
  comparison with string fallback, `has_more` via 1-record probe).
- 2026-09-25 T5 commit `b4d66f3` ("Ver más" `.history-more` button,
  buildTurnEl shared prepend path with `data-ts`, scroll anchor kept,
  browser-verified with faked backends).
- Suites at close: pytest 539 passed; js 129/129. Status: COMPLETE.

