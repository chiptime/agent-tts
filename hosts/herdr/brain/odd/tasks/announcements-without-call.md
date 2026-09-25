# Feature: Announcements Without Call — Delivery 1 (herdr-brain)

**Repo:** herdr-brain
**Created:** 2026-09-25
**Status:** In progress — T1 (`49ba184`), T2 (`00fb2be`), T3 (`b93c27c`), T4 (`2def047`) + T4/T3 correction unit complete; T5 (device) pending (worktree `announcements-d1`, branch `feat/announcements-without-call-d1`)
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

### [x] T3 — Keep text visible on playback rejection
- In `pumpAudio`'s `play()` rejection path, stop hiding the announcement
  toast; notify the announcer (`onPlayRejected`) so it enters `blocked`.
- Preserve the in-call resume logic and existing teleprompter behavior;
  re-read both in the implementation chat.
- Covers: FR-04, AC3.
- Route: delegated writer (same writer as T2 if run in one batch).
- Done 2026-09-25 (strict TDD). Evidence in **Progress / Evidence**.

### [x] T4 — "🔊 Activar voz" affordance and gesture unlock
- Add the affordance next to `#toast` in `index.html` (Spanish label
  "🔊 Activar voz"), hidden unless `blocked`.
- One document-level `pointerdown`/`keydown` listener (active only while
  blocked) plus the button call `unlock()`; the unlock primes `player`
  inside the gesture.
- Extend `TestStatic` id and Spanish-label assertions.
- Covers: FR-05, AC2.
- Route: delegated writer.
- Done 2026-09-25 (strict TDD), together with the reopened T3 edge
  (queued-announcements drain). Evidence in **Progress / Evidence**.

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
  - Commit identity: **T2 work-unit commit = `00fb2be`** (`feat(announce):
    subscribe to SSE at boot and route transitions through the announcer`;
    recorded here in the T3 doc update).

- 2026-09-25 **T3 done** in the same worktree, strict RED → GREEN →
  REFACTOR. `pumpAudio`'s `play()` rejection no longer loses the
  announcement text out of call: a CURRENT item's rejection calls
  `announcer.onPlayRejected(item.announcement)` (enters blocked,
  re-shows the text as a persistent toast — FR-04/AC3); in-call keeps
  the exact existing `hideToast()` + queue advance + dead-mic resume
  (FR-08), and non-announcement failures change nothing.

  **Pre-edit revalidation**: rejection handler was `app.js:1326-1340`
  (`if (item.announcement) hideToast();` unconditional at 1329);
  `audioFinished` confirmed as the staleness token (pumpAudio sets it,
  `stopAudio`/`onAudioEnded` clear it — comment at app.js:1380 already
  documents the contract). Git clean at `00fb2be` before work.

  **Baselines at T3 start**: `node --test tests/js/` → 158 pass / 0 fail;
  pytest → 552 passed, 1 warning.

  | Phase | Command | Result |
  |---|---|---|
  | RED | `node --test tests/js/announce-wiring.test.js` (new structural suite) | tests 5, pass 1, **fail 4** — stale guard absent; no `announcer.onPlayRejected(item.announcement)`; old unconditional `if (item.announcement) hideToast();` still present; no out-of-call stopBtn cleanup. The 1 pass pins the preserved in-call mic-resume. Real discrepancy, not a missing import |
  | RED (pins) | `node --test tests/js/announce.test.js` (+2 drain/post-unlock tests) | 25/25 pass — pins of existing T1 module semantics the wiring relies on (affordance fires once per blocked entry, each drained text shown, newest persists; post-unlock rejection re-blocks). Recorded as pins, not RED |
  | GREEN | `node --test tests/js/` | tests 165, pass 165, fail 0 |
  | REFACTOR | reviewed the rewritten handler (separate queue-empty guards kept for distinct concerns: button vs mic); no structural change needed | suites below |
  | Closure | `node --test tests/js/` | **165 pass / 0 fail** |
  | Closure | `PYTHONPATH=<worktree>/src …pytest -q` | **552 passed, 1 warning** |

  Files: `src/herdr_brain/static/app.js` (rejection handler rewritten,
  ~+24/−2 lines), `tests/js/announce-wiring.test.js` (new, 5 structural
  tests), `tests/js/announce.test.js` (+2 module pins). No server edits.

  **Test constraints, stated plainly**: app.js is a browser-only IIFE —
  the catch handler cannot execute under node. Structural tests pin the
  wiring shape (stale guard first, out-of-call-only announcer call,
  in-call-only hideToast, out-of-call stopBtn cleanup) against the
  region between `player.play()` and `onAudioEnded`; pure module tests
  cover the policy the wiring delegates to. Real autoplay rejection is
  device territory (T5, AC3) — NOT claimed validated here.

  Runtime harness: N/A beyond the suites above — no JS/DOM runtime
  boundary exists in this repo for app.js; the honest executable
  boundary is T5 on the user's Android device.

  Rollback boundary: revert the T3 commit — the rewritten catch block
  returns to the pre-existing handler, and the two test files' T3
  additions (or the whole wiring test file) go with it; nothing else
  references them.

  **Deviations / decisions (T3):**
  - "Drop blocked queued items (no delayed play)" implemented as the
    queue's natural drain: each queued announcement takes its own turn,
    its `play()` rejects on the blocked autoplay policy, its rejection
    shows its text persistently; no item is ever held back to play after
    an unlock (FR-05). No queue surgery — pumpAudio semantics stay.
  - The stale-rejection guard (`audioFinished !== item → return`) is
    global (call and no-call): a LATE rejection from a superseded item
    could hide a newer toast or clobber the newer item's `audioBusy`
    even in-call (latent race: stop → late rejection → hideToast of a
    NEW toast). Guarding it is the explicit T3 requirement; the
    non-stale in-call path is byte-identical to before (hideToast +
    pumpAudio + mic resume). The stale corner is a race bug fix, not an
    FR-08 behavior change.
  - NEW out-of-call cleanup: hide "Parar audio" when a rejection drains
    the queue empty — otherwise the floating stop button's `stopAudio()`
    would `hideToast()` the persistent text T3 just saved. In-call keeps
    today's stopBtn behavior untouched.
  - Structural tests are the chosen seam (user-approved fallback):
    focused Node harness of the handler is infeasible (DOM/audio
    runtime); limits documented above.
  - RDD: still disabled in this worktree → no assess, no toggling.
  - Commit identity: **T3 work-unit commit = `b93c27c`** (`fix(announce):
    keep announcement text visible on play rejection`; recorded here in
    the T4 doc update).

- 2026-09-25 **T4 done + reopened T3 edge fixed** in the same worktree,
  strict RED → GREEN → REFACTOR. New `#voice-unlock` button (Spanish
  "🔊 Activar voz", sibling AFTER `#toast` — never inside, showToast
  replaces the toast contents), hidden unless `blocked && !muted()`;
  document-level `pointerdown`+`keydown` (capture) attached/detached
  ONLY across blocked transitions (the module's transition-only
  callback pairs them — no double handlers); button click and gestures
  route through one idempotent `unlockVoice()` that unlocks once and
  primes audio inside the genuine gesture; the prime swaps the player
  to a SILENT wav (never the rejected src — no replay), handles the
  promise both ways (fail-soft, never unhandled) and neutralizes the
  player; priming is skipped during a live call (startCall's own
  gesture already unlocked; priming would kill mid-call playback).
  **Reopened T3 edge (parent review):** after the first out-of-call
  rejection, already-queued announcements are now DROPPED with text
  preserved in arrival order (newest ends visible persistent) instead
  of being blindly attempted by pumpAudio; non-announcement items keep
  today's path; in-call queue/teleprompter/mic-resume untouched.

  **Baselines at T4 start**: `node --test tests/js/` → 165 pass / 0
  fail; pytest → 552 passed, 1 warning. Git clean at `b93c27c`.

  | Phase | Command | Result |
  |---|---|---|
  | RED | `node --test tests/js/announce-wiring.test.js` (+5 tests: drain edge + affordance wiring) | tests 10, pass 5, **fail 5** — queued-announcement drop absent (the reopened bug), affordance visibility/gesture wiring/prime/in-call-guard all absent. Real discrepancies, not import errors |
  | RED | `pytest -q …::TestStatic::test_index_has_voice_unlock_affordance` | **FAILED** — `AssertionError: assert '<button id="voice-unlock" class="hidden"' in html` (button absent) |
  | GREEN | `node --test tests/js/` | tests 170, pass 170, fail 0 |
  | GREEN | `pytest -q tests/test_server.py::TestStatic -k "announce or voice"` | 3 passed |
  | REFACTOR | reviewed both regions (drain loop order-preserving, affordance functions hoisted before use, no structural change needed) | suites below |
  | Closure | `node --test tests/js/` | **170 pass / 0 fail** |
  | Closure | `PYTHONPATH=<worktree>/src …pytest -q` | **553 passed, 1 warning** |

  Files: `src/herdr_brain/static/index.html` (+button sibling of #toast,
  +CSS pill in the toast layer), `src/herdr_brain/static/app.js`
  (rejection-handler drain + affordance/gesture/prime wiring),
  `tests/test_server.py` (TestStatic +1), `tests/js/announce-wiring.test.js`
  (+5). No server.py / watcher.py edits.

  **Test constraints, stated plainly**: same bounds as T3 — app.js is a
  browser-only IIFE, so the affordance/gesture/prime wiring is pinned
  structurally (source-sliced regions), not executed; the module
  semantics each piece relies on (unlock idempotence, transition-only
  onBlockedChange, persistent re-show per onPlayRejected) are
  executable-tested in announce.test.js. The served-DOM contract (button
  id, Spanish label, sibling-of-toast, default-hidden) is asserted via
  TestStatic against the real FastAPI app. Whether the silent-wav prime
  actually unlocks autoplay on Chrome Android / the home-screen PWA is
  precisely what T5 must verify (ASSUMPTION-1/AC2) — NOT claimed here.

  Runtime harness: TestStatic HTTP checks above; no JS/DOM runtime
  boundary exists in-repo for app.js. Rollback boundary: revert the T4
  commit — button + CSS, affordance wiring, drain loop and their tests
  go together; T3's handler otherwise returns to its committed shape.

  **Deviations / decisions (T4):**
  - Affordance visibility is `blocked && !muted()`: mute is a deliberate
    voice-off, so it wins the priority (AC4 "solo toast" keeps holding);
    re-evaluated on every blocked transition and mute toggle.
  - Gesture listeners stay attached across a blocked stretch even if the
    user then mutes (button hides, state stays blocked): unlocking while
    muted is harmless and FR-05 says any gesture unlocks.
  - The T3 natural-drain decision was SUPERSEDED by this unit per parent
    review: queued announcements are dropped at the first rejection
    instead of each burning a play() attempt. T3's evidence keeps the
    original decision recorded as history.
  - Prime uses a data-URI silent wav rather than playing the rejected
    src: guarantees no audible gesture audio and no replay; neutralizes
    the player afterwards so the next arrival starts clean.
  - Prime skipped while `inCall` (guard `if (!inCall) primeAudio()`): a
    live call owns the player and already carries its own unlock
    gesture; priming would neutralize mid-call playback.
  - RDD: still disabled (clone_local, untouched) → no assess, no toggle.

- 2026-09-25 **T4/T3 REOPENED by parent review after `2def047` — both
  defects fixed in one scoped correction unit** (separate work-unit
  commit; hash recorded in the next doc update, no amend).

  **Defect 1 (unlock-before-proof + shared-player hijack):**
  `unlockVoice` called `announcer.unlock()` BEFORE the prime resolved —
  a refused prime (browser still blocking) had already hidden the
  affordance and detached the gesture listeners, violating FR-04/FR-05.
  The prime also overwrote the SHARED player with SILENT_WAV, able to
  interrupt non-announcement audio still queued on it. Fix: new module
  API `requestUnlock(prime)` — unlock-on-proof, EXECUTABLE-tested in
  announce.test.js (7 tests): state flips to ok only when the prime's
  promise fulfills; rejection or sync throw stays blocked with text and
  affordance untouched; one in-flight prime guard collapses rapid
  gestures; failed primes are retryable; legacy no-promise primes unlock
  optimistically; the promise is always handled in the module. Host
  `primeAudio()` now plays a REAL silent wav (800 zero frames of 8 kHz
  16-bit PCM — not the zero-frame file some browsers refuse to decode;
  generated programmatically and byte-verified: RIFF/WAVE, 1644 bytes)
  on an ISOLATED `new Audio()` element — the shared player, the queue
  and any in-call audio are untouched, so the old `!inCall` prime guard
  became unnecessary and was removed.

  **Defect 2 (media 'error' event bypassed FR-04):**
  `player.addEventListener('error', onAudioEnded)` cleared
  `audioFinished` and hid the announcement toast BEFORE the play()
  rejection's `.catch` ran, so the T3 stale guard returned and the text
  was silently lost on media errors. Fix: the failure body moved into a
  shared `handlePlayFailure(item)` used by BOTH the promise catch and a
  new `onAudioError` listener. Whichever arrival comes first CLAIMS the
  failure (`audioFinished = null` right after the stale guard) and the
  late twin no-ops. Out-of-call announcement errors now preserve text,
  enter blocked, drop previously queued announcements with text shown
  (newest visible persistent) and hide the stop button; the normal
  'ended' path, all in-call paths (including mic resume) and
  non-announcement/spurious errors keep `onAudioEnded` exactly as
  before — no bogus unblock anywhere.

  **Baselines at correction start**: `node --test tests/js/` → 170
  pass / 0 fail; pytest → 553 passed, 1 warning. Git clean at
  `2def047`.

  | Phase | Command | Result |
  |---|---|---|
  | RED | `node --test tests/js/announce.test.js` (+7 requestUnlock tests) | tests 32, pass 25, **fail 7** — `requestUnlock is not a function` (new API; the behavioral contracts are the assertions: unlock-on-proof, stay-blocked on refusal/throw, one-in-flight, retry, legacy optimism) |
  | RED | `node --test tests/js/announce-wiring.test.js` (prime/unlock tests rewritten to the isolated-element + requestUnlock contract; +2 error-path tests; obsolete `!inCall` prime-guard test removed) | tests 12, pass 8, **fail 4** — unconditional `announcer.unlock()` still present; prime still on the shared player; error event still wired to `onAudioEnded`; no `handlePlayFailure`. Real discrepancies |
  | GREEN | `node --test tests/js/` | tests 179, pass 179, fail 0 (after fixing one fresh test's slice marker: `onAudioError` sits after `stopAudio`, next to the listeners) |
  | REFACTOR | verified no stale `neutralizePlayer`/`announcer.unlock()` references; shared handler removed the duplication by construction; SILENT_WAV byte-verified via node decode (1644 B, RIFF/WAVE, 800 silent frames) | suites below |
  | Closure | `node --test tests/js/` | **179 pass / 0 fail** |
  | Closure | `PYTHONPATH=<worktree>/src …pytest -q` | **553 passed, 1 warning** |

  Files: `src/herdr_brain/static/announce.js` (+requestUnlock, header
  state-machine note), `src/herdr_brain/static/app.js` (isolated prime
  + real silent wav, unlockVoice via requestUnlock, handlePlayFailure,
  onAudioError + listener swap), `tests/js/announce.test.js` (+7),
  `tests/js/announce-wiring.test.js` (rewritten T4 block + 2 error-path
  tests). No server.py / watcher.py edits; no delivery 2/3/Phase 2.

  **Test bounds, honestly**: the requestUnlock POLICY is executable in
  the pure module (the parent's preference). The app.js wiring
  (isolated element, error routing) remains source-shape pinned — no
  DOM/Audio runtime exists under node for the browser IIFE; whether a
  real gesture prime unlocks Chrome Android autoplay is T5
  (ASSUMPTION-1/AC2), NOT claimed.

  Rollback boundary: revert the correction commit — module loses
  requestUnlock, app returns to the `2def047` wiring, and the test
  changes travel with it.

  **Decisions (correction):**
  - Unlock-on-proof lives IN THE MODULE (requestUnlock) so the
    stay-blocked-on-refusal contract is executable, not structural.
  - The prime's isolated element is released both on success and
    refusal (pause + removeAttribute + load); a refusal rethrows to
    the module, which swallows it — never unhandled.
  - `unlock()` remains exported for direct/idempotent use, but app
    wiring no longer calls it directly.
  - In-call gestures now also route through requestUnlock with the
    isolated prime (safe by construction); startCall's own gesture
    remains the call's primary unlock.
  - RDD: still disabled → no assess, no toggle.

## Handoff to the implementation chat

Read this plan and `docs/PRD-announcements-without-call.md`; work only on
delivery 1 (FR-01..FR-08, AC1..AC6). Before starting T1, confirm the working
tree and concurrent toast/audio changes, revalidate source references, and run
the configured JS and Python test commands for a baseline. Then work through
T1..T4 with observed RED → GREEN → REFACTOR; T5 requires evidence from the
user's Android device. Do not begin delivery 2, delivery 3, or Phase 2 as part
of this handoff.
