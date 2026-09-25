# Feature: Announcements Without Call — Delivery 1 (herdr-brain)

**Repo:** herdr-brain
**Created:** 2026-09-25
**Status:** In progress — T1 (commit `49ba184`) and T2 complete; T3/T4/T5 pending (worktree `announcements-d1`, branch `feat/announcements-without-call-d1`)
**PRD:** `docs/PRD-announcements-without-call.md` (Phase 1, delivery 1: FR-01..FR-08, AC1..AC6)
**Engram mirror:** `odd/announcements-without-call/tasks`

## Objective

With the PWA open and no call active, every agent announcement arrives and is
spoken the moment it arrives. If the browser refuses playback, the announcement
stays visible and the user can re-enable voice with one tap.

## Problem / Why

- The client subscribes to SSE `/events` only inside `startCall`
  (`app.js:2415` @ `ab20f8d`), so no announcement arrives before a call.
- On `player.play()` rejection, `pumpAudio` hides the announcement toast
  (`app.js:1296` @ `ab20f8d`): the announcement is lost silently.
- No audio-unlock handling exists outside the call button.

## Scope / Constraints

- In scope: FR-01..FR-08 of the PRD (boot subscription, idempotent
  `openEvents`, play on arrival, `audioBlocked` state + "🔊 Activar voz",
  mute priority, in-call behavior unchanged).
- Out of scope: session naming (delivery 2), Web Push (delivery 3), Phase 2
  hands-free mode, any server change.
- Blocked announcements are never replayed later (PRD FR-05).
- Code style: English code/comments/tests/commits; Spanish UI copy.
- TDD mode: **ON — strict RED → GREEN → REFACTOR**, explicitly selected by
  the user on 2026-09-25 for this delivery. This resolves the conflict between
  the untracked `openspec/config.yaml` (`strict_tdd: true`) and older ODD task
  documents (`TDD mode: OFF`); neither overrides this explicit choice.
- Runners: `node --test tests/js/` for client behavior and
  `.venv/bin/python -m pytest -q` for the full Python suite. Record observed
  RED before production changes, then GREEN and refactor evidence per task.
- Delivery: work-unit commits on a feature branch (branch before the first
  implementation commit if still on the default branch); never push without
  explicit authorization. This planning-only chat makes no commits.
- Concurrency: other sessions committed in the toast/playback area while this
  plan was prepared. At handoff, inspect current HEAD, worktree and affected
  symbols before editing; do not assume the other work has stopped.
- RDD: after each work-unit commit, run `gentle-ai review assess` on it and
  record the tier/outcome below.

## Delivery Forecast

~300 authored changed lines (new pure module + tests ≈ 220, `app.js` wiring ≈ 50,
`index.html` + `test_server.py` ≈ 20, CSS ≈ 10). Under the ~400-line budget:
single slice, no chaining.

## Design Seam

`app.js` is a single IIFE never loaded in node. New decision logic goes into a
**new UMD pure module** `src/herdr_brain/static/announce.js` (same pattern as
`endpointing.js`/`toast.js`: `module.exports` + `global.Announce`), with
injected dependencies (`play`, `showToast`, `hideToast`, `isMuted`,
`isInCall`, `onBlockedChange`). `app.js` keeps only DOM/SSE wiring.

## Tasks

### [x] T1 — Announcement playback policy module
- New `static/announce.js`: `createAnnouncer(deps)` exposing
  `handle(announcement)`, `onPlayRejected(announcement)`, `unlock()`,
  `isBlocked()`; implements the PRD §C decision table (muted → toast only;
  not blocked → play on arrival; blocked → persistent toast + affordance) and
  the `ok ⇄ blocked` state machine; blocked items are dropped, never queued.
- New `tests/js/announce.test.js` (fake deps, like `endpointing.test.js`).
- Covers: FR-03, FR-04 (logic), FR-05 (logic), FR-06, FR-08 (in-call path
  delegates to existing behavior).
- Route: delegated writer (2 non-trivial new files).
- Done 2026-09-25 (strict TDD). Evidence in **Progress / Evidence**.

### [x] T2 — Subscribe at boot through the announcer
- Call `openEvents()` from the boot section; keep the `startCall` call (guard
  keeps it idempotent).
- Route SSE `transition` handling in `openEvents` through `Announce`.
- Load `announce.js` in `index.html` before `app.js`; extend
  `TestStatic` script/`?v=` assertions in `tests/test_server.py`.
- Covers: FR-01, FR-02, FR-07, AC6.
- Route: delegated writer (`app.js`, `index.html`, `test_server.py`).
- Done 2026-09-25 (strict TDD). Evidence in **Progress / Evidence**.

### [ ] T3 — Keep text visible on playback rejection
- In `pumpAudio`'s `play()` rejection path, stop hiding the announcement
  toast; notify the announcer (`onPlayRejected`) so it enters `blocked`.
- Preserve the in-call resume logic and existing teleprompter behavior;
  re-read both in the implementation chat.
- Covers: FR-04, AC3.
- Route: delegated writer (same writer as T2 if run in one batch).

### [ ] T4 — "🔊 Activar voz" affordance and gesture unlock
- Add the affordance next to `#toast` in `index.html` (Spanish label
  "🔊 Activar voz"), hidden unless `blocked`.
- One document-level `pointerdown`/`keydown` listener (active only while
  blocked) plus the button call `unlock()`; the unlock primes `player`
  inside the gesture.
- Extend `TestStatic` id and Spanish-label assertions.
- Covers: FR-05, AC2.
- Route: delegated writer.

### [ ] T5 — Device verification
- Chrome on Android: PWA from home screen (AC1), regular tab (AC2), forced
  rejection (AC3), mute (AC4), in-call regression (AC5), reload (AC6).
- Record results, including whether ASSUMPTION-1 (home-screen autoplay
  exception) held.
- Route: manual (user device) + parent records evidence.

## Acceptance Criteria

PRD AC1–AC6, plus full `pytest` and `node --test tests/js/` suites green at
each task closure.

## Progress / Evidence

- 2026-09-25 Plan created from approved PRD. Code refs refreshed against
  `ab20f8d`; they are a snapshot, not an implementation-time guarantee.
- 2026-09-25 User selected strict TDD and requested documentation only in this
  chat; no implementation, tests, device verification or commits performed.
- 2026-09-25 **T1 done** in worktree `announcements-d1`
  (branch `feat/announcements-without-call-d1`), strict RED → GREEN →
  REFACTOR.

  **Pre-edit revalidation** (CodeGraph index of this worktree, drift vs the
  plan's `ab20f8d` refs): `openEvents` now `app.js:1536` (plan said 1500),
  `muted()` `app.js:1520`, `pumpAudio` `app.js:1295`, play-rejection path
  `app.js:1326-1339`, `MUTE_KEY` `app.js:112`. The `hideToast()`-on-rejection
  defect is still present (T3's target).

  **Baselines** (before any edit): `node --test tests/js/` → 135 pass / 0
  fail. `PYTHONPATH=<worktree>/src /home/bruno/Code/personal/herdr-brain/.venv/bin/python
  -m pytest -q` → 550 passed, 1 warning.

  | Phase | Command | Result |
  |---|---|---|
  | RED | `node --test tests/js/` (after writing `tests/js/announce.test.js`, 22 tests, no module yet) | tests 136, pass 135, **fail 1** — `Error: Cannot find module '../../src/herdr_brain/static/announce.js'` |
  | GREEN | `node --test tests/js/` (after adding `static/announce.js`) | tests 157, pass 157, fail 0 |
  | REFACTOR | extracted duplicated toast formatting into `showAnnouncement(ann, prefix, durationMs)`; no behavior change | `node --test tests/js/` → tests 157, pass 157, fail 0 |
  | REFACTOR (python) | full suite with worktree `PYTHONPATH` as above | 550 passed, 1 warning |

  Flake note: one post-refactor pytest run showed
  `TestApprovalPatchEndpoint::test_patch_updates_text_and_restarts_timer`
  failing; it passes isolated AND on full-suite rerun (550 passed), and T1
  touches only new JS files — timing flake, unrelated.

  Files: `src/herdr_brain/static/announce.js` (new, 141 lines),
  `tests/js/announce.test.js` (new, 22 tests). No app.js / index.html /
  server changes (T2..T4 untouched).

  Runtime harness: N/A — pure logic module with injected deps; the first
  runtime boundary is the T2 wiring into `openEvents`.

  Rollback boundary: delete `src/herdr_brain/static/announce.js` and
  `tests/js/announce.test.js`; nothing else references them yet.

  **Deviations / decisions (T1):**
  - `hideToast` NOT taken as an injected dep (plan listed it): no policy path
    hides a toast — FR-04 forbids hiding on rejection, unlock leaves the
    missed text visible (PRD no-objetivos: "suenan al llegar o se quedan como
    texto"), and the next announcement's toast replaces it naturally.
  - Blocked state intercepts only announcements WITH `audio_url`: text-only
    announcements keep the timed 🔊 toast (nothing was blocked from playing).
  - `announcement.html` rides every module toast (also muted/text-only, which
    today skip html): formatted-avisos parity through the safe toast mount;
    audio policy unchanged.
  - Async safety per contract: a promise returned by `deps.play` gets its
    rejection handled inside the module (never unhandled); a synchronous
    throw from `deps.play` is treated as a rejection (covered by tests).
  - RDD: review not enabled in this worktree (no `openspec/config.yaml`,
    no `.rdd`) → `gentle-ai review assess` NOT run on the T1 commit, per the
    instruction not to start review while disabled.
  - Commit identity: **T1 work-unit commit = `49ba184`** (`feat(announce):
    add announcement playback policy module with tests`; recorded here in
    the T2 doc update — no amend, as planned).

- 2026-09-25 **T2 done** in the same worktree, strict RED → GREEN →
  REFACTOR. FR-01 (boot `openEvents()` in the boot section after
  `setInterval(refreshState)`), FR-02 (`startCall`'s `openEvents()` kept;
  `eventsOpened` guard makes the boot call idempotent), FR-07 (every
  `transition` flows through `announcer.handle(ann)` — no session filter).
  The `system` branch, in-call playback (`pumpAudio` queue), mute policy
  and teleprompter are untouched; the muted/text-only toast calls are
  byte-identical to before (see module fix below).

  **Pre-edit revalidation**: catch-all static route confirmed at
  `server.py:844` (`app.mount("/", StaticFiles(..., html=True))`, "mounted
  last so every API route above wins") → `/announce.js` serves WITHOUT any
  server edit; CSP is `script-src 'self'` (same-origin script allowed);
  index script block at `index.html:1276-1282`; `startCall` at
  `app.js:2443` with its `openEvents()` at 2451; boot section ends
  `app.js:2581-2650`.

  **Baselines at T2 start**: `node --test tests/js/` → 157 pass / 0 fail;
  pytest (worktree `PYTHONPATH`) → 550 passed, 1 warning.

  | Phase | Command | Result |
  |---|---|---|
  | RED | `pytest -q tests/test_server.py::TestStatic -k "announce"` | `test_index_loads_announce_module_before_app` **FAILED** — `AssertionError: assert 'src="/announce.js"' in '<!doctype html>…'`; `test_announce_js_served_by_static_mount` passed (pinning: the catch-all already serves the T1 file — recorded as pin, not RED) |
  | RED | `node --test tests/js/announce.test.js` (html-parity tests rewritten to the exact-preservation contract) | tests 23, pass 22, **fail 1** — `muted and text-only toasts stay plain` (`'🔇 …' == '🔊 …'` family; module then passed html/kind on timed toasts). Companion persistent-html test passed (pinning) |
  | GREEN | `pytest -q tests/test_server.py::TestStatic -k "announce"` | 2 passed |
  | GREEN | `node --test tests/js/` | tests 158, pass 158, fail 0 (after fixing a harness bug in the new test: the module captures `deps.isMuted` at construction, so the override must be a mutable closure flag, not a property reassignment) |
  | REFACTOR | renamed wiring param to `ann` (house style); reviewed hunks; no structural change needed | suites below |
  | Closure | `node --test tests/js/` | tests 158, pass 158, fail 0 |
  | Closure | `PYTHONPATH=<worktree>/src …herdr-brain/.venv/bin/python -m pytest -q` | **552 passed, 1 warning** |

  Files: `src/herdr_brain/static/app.js` (announcer wiring + transition
  routing + boot call), `src/herdr_brain/static/index.html` (+1 script
  tag before app.js), `src/herdr_brain/static/announce.js` (timed toasts
  now exact 2-arg calls), `tests/test_server.py` (TestStatic +2),
  `tests/js/announce.test.js` (parity tests rewritten). **No server.py /
  watcher.py edits.**

  Runtime harness: TestClient HTTP checks (ordered script tag in served
  index; `/announce.js` → 200 `text/javascript` with `createAnnouncer`)
  are the runtime boundary evidence. Browser/SSE behavior at boot is
  device territory (T5); **AC6 is NOT claimed validated** — only the
  idempotence guard and single-connection wiring are in place.

  Rollback boundary: revert the T2 commit — `index.html` script tag,
  `app.js` announcer block + `announcer.handle(ann)` + boot `openEvents()`,
  TestStatic tests, announce.test.js parity tests, and the announce.js
  timed-toast shape (T1 behavior returns; nothing else depends on them).

  **Deviations / decisions (T2):**
  - Plan said "extend TestStatic script/`?v=` assertions": NO `?v=` is
    added for `/announce.js`. Versioning lives in `server.py`
    `_VERSIONED_REFS`; editing it is a server change delivery 1 excludes.
    The catch-all StaticFiles mount serves the asset as-is; the tests
    assert the actual unversioned `src="/announce.js"` honestly.
  - T1's "html rides every module toast" decision REVERSED for timed
    toasts: today's `openEvents` muted/text-only calls are plain
    `(text, 6000)` — routing them through a module that mounted html
    would change toast html behavior. `announce.js` now emits exact 2-arg
    timed calls; html + `kind: null` ride ONLY the persistent blocked
    toast (pumpAudio's shape). T1's evidence table above intentionally
    keeps the original decision recorded as history.
  - `onBlockedChange` is not wired yet (T4 owns the affordance); the
    module treats the missing callback as absent.
  - In T2 the blocked state is unreachable in the wired app
    (`onPlayRejected` arrives with T3; `deps.play` returns undefined
    because `enqueueAudio` does) — by design, not by omission.
  - RDD: still not enabled in this worktree → no assess run on the T2
    commit, per instruction (do not start review disabled).

## Handoff to the implementation chat

Read this plan and `docs/PRD-announcements-without-call.md`; work only on
delivery 1 (FR-01..FR-08, AC1..AC6). Before starting T1, confirm the working
tree and concurrent toast/audio changes, revalidate source references, and run
the configured JS and Python test commands for a baseline. Then work through
T1..T4 with observed RED → GREEN → REFACTOR; T5 requires evidence from the
user's Android device. Do not begin delivery 2, delivery 3, or Phase 2 as part
of this handoff.
