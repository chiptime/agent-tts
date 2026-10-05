# Select and follow karaoke fragments in the Brain chat

**Status: APPROVED PRD — documentation only; readiness: `ready_for_bootstrap`. Implementation is NOT AUTHORIZED.** Preserve the whole visible Brain answer, make its existing karaoke windows selectable inline, and keep the floating karaoke synchronized with playback. Application gates and physical/manual proof remain **PENDING**; this is not `ready_for_execution`.

This single self-contained document is the canonical PRD, execution contract and future launch request. The user's explicit consolidation instruction overrides multi-file packaging. Approval evidence is the exact user quote: **“te autorizo a crear y aprobar el prd, pero deja un solo fichero autocontenido”**, followed by **“continue”**. These words authorize creating, approving and consolidating documentation; they do not authorize implementation. No signature, identity or date is invented.

- Requirements: [Functional requirements](#functional-requirements).
- Execution: [Task authority](#task-authority), [Gate authority](#gate-authority), and [Stop and resume](#stop-and-resume).
- Separate future action: [Future launch request](#future-launch-request); its inline text starts nothing.
- Declared FR count: 10.
- Product decisions: confirmed in the parent handoff after cancellation; no open product choices.

## Context & problem

The main Brain conversation shows the user's question and the Brain's answer in `#conversation`. Today each answer is plain text plus **Escuchar**. Replay already splits long text for `/tts`, then divides each piece into floating, approximately two-line karaoke windows. Those windows are not inline controls and cannot seek playback.

This feature belongs to that main conversation, **not** the external agent history in `#glance-turns`. It operates on the fragments the UI already receives and displays. It cannot recover fragments a backend has already combined or omitted.

## Confirmed decision ledger

The source of every D item is the parent-provided user confirmation, not a new inference about the running application.

| Decision | Confirmed choice |
|---|---|
| D-01 | Main Brain chat only: `#conversation .turn.brain`, alongside the visible user turn; exclude `#glance-turns .gturn`. |
| D-02 | Preserve every currently visible answer fragment; do not classify or filter thinking versus final text. |
| D-03 | Reuse `splitForTts` followed by `chunkifyText`; number chunks per turn with global offsets across TTS pieces longer than 8000 characters. |
| D-04 | Show selectable chunks inline; one tap selects/highlights and moves or resumes audio at that chunk. |
| D-05 | At most one active chunk globally; it advances with audio, and the existing floating karaoke remains synchronized and available. |
| D-06 | Approximate chunk-index seeking is sufficient for MVP; exact timestamps are out of scope. |
| D-07 | Keep Escuchar, unchanged user text, and the current conversation layout; no new menu or fragment list. |
| D-08 | Produce a PRD and compact execution package only; launch needs separate explicit authorization. Backend transcript coalescing is a non-goal. |

## Current source evidence

These are original read-time source observations, not runtime test results. [Initial source & gate-input binding](#initial-source--gate-input-binding) preserves their original bytes and modes; line ranges remain historical locators, not a new runtime claim.

| Evidence | Verified location | Observation |
|---|---|---|
| E-01 | [app.js](../../hosts/herdr/brain/src/herdr_brain/static/app.js):524–543, 593–609 | `buildTurnEl` uses `textContent`, adds replay for Brain turns, and is shared by live/history rendering. |
| E-02 | [app.js](../../hosts/herdr/brain/src/herdr_brain/static/app.js):659–754 | Escuchar calls `speakText`; replay builds per-piece chunk arrays and cumulative offsets before sequential `/tts` requests. |
| E-03 | [toast.js](../../hosts/herdr/brain/src/herdr_brain/static/toast.js):95–183 | `chunkifyText` defaults to 80 characters and normalizes whitespace; `splitForTts` defaults to 8000 and trims long-piece boundaries. |
| E-04 | [app.js](../../hosts/herdr/brain/src/herdr_brain/static/app.js):1340–1562 | One player/queue drives the popup. `timeupdate` uses clamped `floor(progress * chunkCount)` and currently stops synchronization when the toast is hidden. Offsets are stored in the announcement but read from the queue item in that listener; the future shared plan must eliminate this divergent lookup. |
| E-05 | [index.html](../../hosts/herdr/brain/src/herdr_brain/static/index.html):956–999, 1383–1392 | Main turns preserve whitespace; styles are inline. Toast loads before app.js. External reader styles are separately scoped under `#glance-turns`. |
| E-06 | [.github/workflows/ci.yml](../../.github/workflows/ci.yml):38–70; [pyproject.toml](../../hosts/herdr/brain/pyproject.toml):6, 19, 28–29; [toast.test.js](../../hosts/herdr/brain/tests/js/toast.test.js):14–19, 294–426 | CI uses Node 20, Python 3.12, `node --test tests/js/`, and pytest. The existing JS tests use built-in Node modules; pytest-playwright is already a dev dependency. |
| E-07 | [app.js](../../hosts/herdr/brain/src/herdr_brain/static/app.js):2501–2513; [speech.js](../../hosts/herdr/brain/src/herdr_brain/static/speech.js):393–403; [browser conftest](../../hosts/herdr/brain/tests/e2e/conftest.py):1–12, 217–261 | Automatic answers can use `/speech` segments without chunk text metadata. The existing browser harness uses real Chromium/MP3 with provider doubles; that is not the simulated-media harness proposed here. |

## A. Objectives & non-objectives

**Objectives:** choose a point in a visible Brain answer without opening another surface; retain its text and the user's question; follow the same approximate windows inline and in the floating popup; handle long answers and asynchronous playback safely.

**Non-goals:** external agent history, backend same-role transcript coalescing, thinking/final classification, new rich-text rendering, exact word/sentence timestamps, changes to `/speech` protocols or providers, new persistence, installation/deployment changes, new menu/list, automated Git or SDD, and any review-mode change.

## Functional requirements

This is the **single FR authority**. Other sections reference these IDs without copying their normative text. RFC 2119 MUST describes the future acceptance contract, not behavior proven today.

| FR | Normative requirement | Decision | Evidence |
|---|---|---|---|
| FR-01 | The feature MUST attach only to main-chat Brain turns, including newly rendered and prepended history turns; it MUST leave user turns and external `.gturn` history unchanged. | D-01, D-07 | E-01, E-05 |
| FR-02 | The feature MUST retain all visible answer text/fragments in original order and preserve the answer body's raw whitespace and literal formatting; it MUST NOT filter, classify, merge, or normalize the displayed content. | D-02, D-08 | E-01, E-03 |
| FR-03 | Inline controls, replay audio, and popup windows MUST derive from one turn plan built by `splitForTts(text, 8000)` then `chunkifyText(piece)`; global indexes MUST include earlier pieces' chunk counts and visible ordinals MUST restart at one for each turn. | D-03, D-05 | E-02, E-03, E-04 |
| FR-04 | Chunks MUST be selectable inline by one tap and by focused Enter/Space, with an accessible name, selection state, and visible focus; interaction MUST preserve native text selection and MUST NOT hijack links or code controls or introduce a menu/list. | D-04, D-07 | E-01, E-05 |
| FR-05 | Selecting a chunk MUST move/resume its owned replay at `localIndex / pieceChunkCount * duration` when finite positive duration is available; otherwise the target MUST wait for matching metadata or fail visibly without claiming playback. Exact alignment MUST NOT be implied. | D-04, D-06 | E-02, E-04 |
| FR-06 | At most one `.karaoke-active` chunk MUST exist globally; owned replay progress MUST advance it and the floating popup using the same clamped global index, including one-window pieces and piece boundaries. Hiding the popup MUST NOT freeze inline progress. | D-05 | E-04 |
| FR-07 | Selecting another turn/chunk or replacing the conversation MUST invalidate obsolete ownership; late synthesis, metadata, play promises, and media callbacks MUST NOT enqueue obsolete audio or alter another turn's active chunk/popup. | D-04, D-05 | E-02, E-04, E-07 |
| FR-08 | Stop, terminal end, target removal, and unrecoverable playback failure MUST clear owned selection/pending work and restore usable controls without changing text. A failed TTS piece MUST be reported; later playable pieces MAY continue under their original global indexes, never renumbered to conceal the gap. | D-02, D-05, D-07 | E-02, E-04 |
| FR-09 | Escuchar MUST remain available through the shared plan, starting its turn at the first chunk. Existing automatic answer, announcement, cancellation, and microphone behavior MUST remain functional; unrelated queued items MUST NOT be purged merely because a karaoke turn is superseded. | D-05, D-07 | E-02, E-04, E-07 |
| FR-10 | Implementation MUST remain client-side, treat answer text as untrusted text, use existing same-origin replay endpoints, and add no backend, provider, persistent store, external origin, service, or deployment change. Verification MUST separate simulated browser proof from physical mobile UAT. | D-01, D-08 | E-01, E-06, E-07 |

## B. Architecture & trust boundaries

Locked routine design, subject to the explicitly unverified feasibility assumptions below:

1. **Plan:** a small `karaoke.js` module, following the existing browser-global/CommonJS pattern, calls the unchanged Toast splitters once for each immutable turn text. It returns per-piece windows, global offsets, and raw source ranges.
2. **Render:** mount inline spans only in the Brain answer body. Raw ranges partition the complete original string; concatenate their text and recover the exact original answer. Whitespace between normalized windows/pieces belongs to the preceding range, leading whitespace to the first, trailing whitespace to the last. Whitespace-only answers stay visible with no empty audio control. Ordinal decoration uses CSS/data attributes, not inserted answer text. Do not add a Markdown/HTML renderer.
3. **Activate:** an injected controller owns one turn and generation. A tap selects the target immediately, reuses ready audio for that piece when available, otherwise synthesizes the selected piece through `/tts` and proceeds through subsequent pieces. Escuchar selects index zero. No synthesis of preceding, deliberately skipped pieces is required for a jump.
4. **Seek/synchronize:** queue metadata refers to that same plan. After matching `loadedmetadata`/`durationchange`, seek proportionally within the chosen piece and call play. On valid `timeupdate`, derive the global index once and update the inline CSS class and existing plain-text popup together. Do not derive offsets independently in the announcement and queue.
5. **Retire:** increment the generation, abandon pending targets, abort obsolete client fetches where possible, and reject stale results even if abort loses a race. Purge only superseded karaoke-owned items. Use existing cancellation/release APIs when preempting identified speech; do not rewrite their protocol or infer ownership from matching text.

**Automatic speech boundary:** current `/ask` full-file and `/speech` segment playback does not provide this replay plan. Do not pretend it has aligned chunk metadata. A chunk tap initiates/transfers to the seekable replay path; unowned automatic audio is a compatibility case, not a second alignment algorithm. Preserve its existing operation and cancellation while testing preemption explicitly (E-07). Foreign audio cannot keep a stale inline active marker.

**Trust boundaries:** server answer text → immutable raw text → safe DOM text nodes; user gesture → owned replay request → same-origin media URL → shared player. The controller is not an authorization mechanism. It must not dispatch agent actions, read transcript stores, or import rendered external-history HTML.

## C. Data schema & state machine

The following is an implementation contract for in-memory state, not a new API or persisted schema.

| Record | Fields and invariants |
|---|---|
| TurnPlan | `turnId` is DOM-local and unique even for equal texts; `rawText` is immutable; `pieces[]` contains `pieceIndex`, synthesis text, local chunks and `chunkOffset`; `chunks[]` contains `globalIndex`, `ordinal`, `pieceIndex`, `localIndex`, raw `[start,end)` UTF-16 ranges and normalized `popupText`. |
| ReplaySession | `ownerTurnId`, increasing `generation`, `phase`, `activeGlobalIndex` or null, pending target or null, and ephemeral ready audio by piece. A text change/rebuild retires the old plan. No cross-session cache/persistence. |
| Owned queue item | Owner ID, generation, piece index and reference to the plan; derive `chunkOffset`/count from it. Existing speech request ID and sequence fields keep their existing meaning. |

| State | Trigger | Next state / visible result |
|---|---|---|
| idle | Tap / Escuchar | preparing: select target, no claim that audio is already playing. |
| preparing | Matching piece audio available | awaiting_metadata, or playing if duration is already finite and positive. |
| awaiting_metadata | Matching valid duration | Seek target, request play, enter playing on success. |
| playing | Pause / another chunk in the same piece | paused / seek and resume; keep at most one selected chunk. |
| playing | Valid progress / next owned piece | Update one global index; retain the plan and ownership. |
| any owned state | Another turn, stop, reset, detached target, terminal end | Retire generation and enter idle; late callbacks are no-ops. |
| preparing / awaiting_metadata / playing | Synthesis/media/play failure | Report failure and clear the failed target; either advance to a later playable planned piece or enter idle. |

Invalid/zero/NaN/infinite duration must never be used for seeking. Waiting ends on metadata failure, the existing request failure path, stop, replacement, or target removal. No timer may revive retired ownership. A duplicated error event and rejected play promise claim the same failure only once.

## D. Security & threat modeling

| Risk | Control / proof |
|---|---|
| Answer text contains script/HTML or malicious links | Keep `textContent`/text nodes; raw markup remains literal. V1/V2 assert zero content-driven HTML injection. |
| Equal text, changed turns, delayed responses | DOM-local owner plus generation and queue-item identity, not text-key ownership; race tests in V1/V2. |
| Chunk handler steals normal interaction | Ignore link/code/native-control targets and non-collapsed selection; do not intercept drag, long-press, context menu, or modified clicks. Test real browser selection and keyboard focus. |
| User sees stale popup/selection after stop/error | Clear both owned surfaces together; retain the answer and existing failure notification. |
| Test accesses real providers/stores or writes shared stub files | Future browser fixture intercepts all routes and simulates media; its literal runner excludes inherited autouse stub/store fixtures with `--confcutdir`; no live backend or provider. |

## E. Success metrics & acceptance criteria

- Zero dropped or reordered raw characters in the answer body; no changed user/external-history content.
- No more than one active chunk; popup and inline global index agree on every checked event.
- Selected ready audio seeks according to the documented formula; pending/failed audio never masquerades as successful playback.
- All rows in the matrix below have recorded proof before functional acceptance. Current functional evidence is **PENDING**, not PASS.
- Coverage means the named positive/negative behaviors in that matrix, not an invented percentage. No repository-wide numerical coverage policy for this feature was established by the inspected CI/config; do not borrow voice-stack maintenance thresholds.

## Verification & acceptance

This chat feature adapts the PRD verification template to user-visible behavior. Installation clean rooms and deployment checks are inapplicable because no installation surface changes.

| Level | Boundary | Trigger / proof |
|---|---|---|
| V1 — deterministic unit tests | Built-in Node tests for the real splitters, raw-range plan, seek arithmetic, controller state, ownership, and failures; fake DOM/media/fetch. | Every change to the new module or main-chat replay glue; test creator and literal runner in the entrypoint. |
| V2 — real-browser integration | Real Chromium loads the actual index/app/modules; all API responses and audio timing/events are simulated, all non-fixture traffic blocked. | Main-chat DOM/CSS or replay/audio changes. New feature harness/suite is FUTURE; the existing real-MP3 suite is evidence of infrastructure, not proof of this contract. |
| V3 — mobile UAT | Human touch, scrolling, selection, focus, readability and actual audible seeking on the existing supported Android Chrome/PWA. | After V1/V2, before accepting this feature; any real service/provider access requires separate human authorization. No automated restart/deploy. |

### Gherkin scenarios

```gherkin
Scenario: SC-01 Keep fragments in the correct conversation [V1+V2+V3]
  Given a visible user question and a Brain answer containing multiple visible fragments
  And external agent history and repeated identical answer texts exist
  When the main answer is rendered and an older Brain turn is prepended
  Then every raw answer character remains in order with the same whitespace
  And only main Brain turns have inline chunk controls and Escuchar remains

Scenario: SC-02 Keep the long-turn plan consistent [V1+V2]
  Given an answer longer than 8000 characters with repeated text and whitespace
  When the existing splitters produce multiple TTS pieces and windows
  Then each inline ordinal resolves to that plan's piece and local window
  And the first window of a later piece uses the preceding pieces' chunk offset
  And the popup and inline progress agree even for a one-window piece

Scenario: SC-03 Tap or type to move and resume [V1+V2+V3]
  Given owned piece audio with finite positive duration that is paused or playing
  When I tap a chunk or press Enter or Space on its focused control
  Then its local index divided by its piece window count determines the seek time
  And playback resumes with only that chunk selected and the popup synchronized

Scenario: SC-04 Wait safely and reject stale work [V1+V2]
  Given synthesis or duration for turn A is pending or invalid
  When I select turn B or stop audio before A's result arrives
  Then A's later fetch, metadata and play completion cannot enqueue or repaint
  And B has at most one selected target without using invalid duration

Scenario: SC-05 Clean up and preserve compatibility [V1+V2+V3]
  Given owned replay, automatic speech or unrelated queued announcements exist
  When playback advances, ends, errors, is stopped, or the conversation is reset
  Then obsolete inline/popup ownership is cleared and controls become usable
  And later playable pieces retain original global indexes after a reported failure
  And automatic speech, cancellation and microphone recovery still operate

Scenario: SC-06 Preserve safe native interaction [V1+V2+V3]
  Given an answer includes literal HTML, code, whitespace and link-like content
  When I select or copy text, long-press, scroll, follow a native link, or use a code control
  Then no chunk playback is triggered by that native operation
  And focus and active-state cues remain readable without adding a menu or list
```

### FR verification matrix

Every check below is planned, not executed. The entrypoint supplies the FR → task → gate trace; MC-01 is its separate physical checklist.

| FR | V1 | V2 | V3 | Scenario |
|---|---|---|---|---|
| FR-01 | scope guards | live/history DOM and excluded surfaces | MC-01 | SC-01 |
| FR-02 | raw partition, Unicode, repeated whitespace | body text equality, no classifier | MC-01 | SC-01, SC-06 |
| FR-03 | split/window identity and offsets | long-piece transition | — | SC-02 |
| FR-04 | handler exclusions and keyboard state | selection/drag/link/code/focus | MC-01 | SC-03, SC-06 |
| FR-05 | seek arithmetic and invalid duration | click/paused playback/metadata | MC-01 | SC-03, SC-04 |
| FR-06 | clamped one-window/global index | CSS class count and popup equality | MC-01 | SC-02, SC-03, SC-05 |
| FR-07 | generation and equal-text races | delayed synthesis/metadata after replacement | — | SC-04 |
| FR-08 | stop/end/error/idempotent cleanup | partial failure/reset/detached turn | MC-01 | SC-05 |
| FR-09 | replay and queue identity regressions | Escuchar/stream preemption/mic recovery | MC-01 | SC-01, SC-05 |
| FR-10 | text-only DOM, no new endpoint/storage | blocked-network harness/security assertions | MC-01 | SC-06 |

## Assumptions & next step

- **A-01 — unverified feasibility:** normalized windows can be mapped back to exact UTF-16 raw ranges without losing characters, including repeated text and trimmed piece separators. T-02 must prove this with behavior tests; inability to partition the raw string is a stop, not permission to normalize the display.
- **A-02 — unverified environment:** this future launching environment has the already-declared Node/Python/pytest-playwright tools and Chromium available. No install/version/runtime check was run while planning; T-01 must prove availability or stop for separate authorization.
- **A-03 — unverified integration:** existing client cancellation and queue hooks support chunk-tap replay preemption without backend changes or loss of unrelated queued audio. T-03 must prove it; missing metadata is not permission to invent stream timestamps or change `/speech`.

No open product question is delegated to the executor. The PRD is approved; only a future explicit, identity-bound launch can start the bounded bootstrap below. No background loop has been started.

## Quick path & binding

1. Read this entire approved contract, including its assumptions, exact allowlists and pending proof.
2. In a future launching session, compute the contract identity externally using [Future launch request](#future-launch-request), capture it in that session, and obtain explicit user authorization for that exact identity, repository, scope and budgets.
3. Validate the original source binding and acquire the source-writer guard before T-01. Execute one task at a time; record evidence in the handoff/bookmark, not by silently rewriting this frozen contract.

| Binding | Value |
|---|---|
| Canonical repository root | `/home/bruno/Code/personal/agent-tts` — parent-selected owning checkout; not the worker's dotfiles root or a different fork/worktree. |
| Planning base HEAD | `5e833fe9bcc9545cf6805307d1e8dc871a9565b8` — original planning observation, not a claim about current HEAD. |
| Observed branch | Original planning observed `fix/brain-open-session-inventory`, local `main` at the same HEAD, a clean initial worktree and HEAD 34 ahead / 0 behind `origin/main`. Consolidation encounters unrelated WIP in inventory, llm, tools and Python tests; it preserves that WIP. No branch switch or remote contact occurred. |
| Tier | Compact: one client subsystem, shared chunk/state logic plus DOM/audio glue and a browser-test foundation; direct one-task work would hide their ordering. |
| Resolved PRD skill | `/home/bruno/.config/opencode/skills/prd/SKILL.md` — successfully read exact runtime skill path. |
| Contract identity | External launch-session SHA-256 and actual POSIX mode; never embedded as a self-hash. |
| Initial input binding ID | `KARAOKE-B0` — original source/gate-input hashes and modes in the table below, excluding planning artifacts. |
| Declared counts | FR=10; tasks=4; gates=5 |
| Index | [PRD index](README.md); existing roadmap entries are preserved. |

Composition inputs read: prd, prd-loop and cognitive-doc-design skills, prd's verification-and-acceptance template, and prd-loop's loop contract and launch-request template. English artifacts are intentional; no SDD or review-mode transition is planned.

## Task authority

This is the single task registry. Paths in edit allowlists are exact repository-relative files, not globs. FUTURE paths are absent in KARAOKE-B0. Tests are authored before their corresponding behavior changes; current planning has no meaningful RED and runs only structural checks.

| Task | Milestone / intent / FRs | Depends | Exact edit allowlist | Test creator | Required proof |
|---|---|---|---|---|---|
| T-01 | M-01: create the isolated real-browser/simulated-media foundation; FR-10 | none | `hosts/herdr/brain/tests/browser/conftest.py`; `hosts/herdr/brain/tests/browser/test_karaoke_harness.py` | T-01 creates both FUTURE files; existing infrastructure reference: `tests/e2e/conftest.py`, not reused as the new fixture scope. | G-01; record baseline G-04 before edits. Harness proof only, not feature acceptance. |
| T-02 | M-02: implement and test the shared raw-range plan and injected replay controller; FR-02, FR-03, FR-05, FR-06, FR-07, FR-08, FR-10 | T-01 | `hosts/herdr/brain/src/herdr_brain/static/karaoke.js`; `hosts/herdr/brain/tests/js/karaoke.test.js` | T-02 creates the FUTURE Node behavior suite using the real existing Toast splitters and local doubles. | G-02, G-04, G-05; observed behavior RED then GREEN and negative cases. |
| T-03 | M-02: wire main-chat rendering, tap/keyboard replay, owned queue/popup progress and cleanup; FR-01, FR-02, FR-03, FR-04, FR-05, FR-06, FR-07, FR-08, FR-09, FR-10 | T-02 | `hosts/herdr/brain/src/herdr_brain/static/app.js`; `hosts/herdr/brain/src/herdr_brain/static/index.html`; `hosts/herdr/brain/src/herdr_brain/static/karaoke.js`; `hosts/herdr/brain/tests/js/karaoke.test.js`; `hosts/herdr/brain/tests/browser/test_karaoke_fragments.py` | T-03 creates the FUTURE browser feature suite and extends T-02's Node controller tests; T-01 owns its fixture. | G-02, G-03, G-04, G-05; observed browser behavior RED before glue changes; SC-01 through SC-06. |
| T-04 | M-03: freeze one candidate and reconcile all automated/manual evidence; FR-01, FR-02, FR-03, FR-04, FR-05, FR-06, FR-07, FR-08, FR-09, FR-10 | T-03 | none — verification/report only; corrections reactivate the originating task inside its remaining milestone budget. | No new tests; use the creator-owned suites. | G-01, G-02, G-03, G-04, G-05 on the same candidate; MC-01 separately. No functional completion while required proof is pending. |

**DAG:** T-01 → T-02 → T-03 → T-04. One active source writer/task. Runtime topology follows the active orchestrator's contract at launch; this document neither requires nor bans delegation. Any worker must inherit the same exact identity, allowlists, gate contract and remaining budgets. Task advancement requires its listed proof, not a checkbox alone.

### T-01 — bootstrap instructions

- Inspect the root, working-tree changes and initial hashes/modes; do not require a commit or erase WIP. The initial binding cannot be silently rebased onto different bytes.
- G-01's smoke test records Python via `sys.version`, pytest/pytest-playwright via `importlib.metadata.version`, Chromium via `browser.version`, and Node via the literal read-only subprocess `node --version`; it asserts Python >=3.11 and Node >=20. CI's observed environment is Node 20 / Python 3.12. These are environment facts to record, not proof already obtained. Missing tools stop; no installer, browser download, dependency edit or alternative runner is authorized.
- Create the runtime-output directory specified below if absent; never clear pre-existing output. The literal browser runners pin `pyproject.toml` and use `--confcutdir=tests/browser` to exclude inherited `_tts_backend_stubs` and `hermetic_stores` fixtures, avoiding the parent's fixed `/tmp` stub and real transcript stores (see bound `tests/conftest.py`). Do not import/start the real Brain service or reuse the e2e fixture scope.
- Load the real `index.html`, scripts and CSS in Chromium through route fulfillment at a fixture-only loopback origin. Intercept every request; fulfill only deterministic fixture/static responses and abort any unexpected origin/path. Stub EventSource and speech recognition before app load; block service workers. No production service, real user data, microphone permission or provider call.
- Inject controllable media duration/currentTime/play/pause and dispatch matching media events on the actual player element. This simulates audio, not the browser DOM. Make source/generation races, rejected play, unknown duration and time advancement controllable for T-03.
- G-01 must launch real Chromium, prove actual app/modules loaded with expected digests, find the existing main/external containers and Escuchar, and verify interception/media doubles. No skipped tests, empty suite or feature assertion that pretends karaoke exists on the base.

### T-02 — implementation/test sequence

- Follow the PRD's architecture/schema; do not rewrite the existing splitter algorithms. Keep the module's browser-global and CommonJS exports consistent with the existing Toast convention.
- RED: add a behavior assertion for raw partition/window identity and capture the intended failure; absence of a module is not sufficient proof of seek/race behavior once its API exists. Add controller expectations before those controller behaviors.
- GREEN: implement the minimum shared plan/controller, then run the task gates. TRIANGULATE: empty/whitespace-only input, one window, repeated equal substrings, tabs/newlines, literal code/HTML, Unicode surrogate pairs, 8000/8001 boundaries, multi-piece global indexes, bad duration, supersession and duplicate failure callbacks.
- Do not make display text equal to normalized popup text. Validate raw ranges recover the exact original string. Failure of A-01 is a stop, not permission to drop whitespace or change the FR.

### T-03 — integration/test sequence

- RED: create the feature suite against the current real app and observe failures for main-turn controls, seek/progress and ownership; do not accept collection errors or broken interception as behavior RED.
- Wire only the main Brain rendering branch of `buildTurnEl`. Preserve live/prepended history paths, user/system/announcement turns and external `attachReplay` callers. Load karaoke.js after Toast and before app.js; keep CSS under `#conversation .turn.brain`.
- Keep answer text nodes/ranges unchanged. Use `.karaoke-chunk` controls with data ordinals and `.karaoke-active`, accessible state/name and focus. Keep existing Spanish UI labels; guard links, code/native controls, modified clicks, selection and drag/long-press. Do not add a rich renderer.
- Use one owned replay session/plan for inline, queue and popup. Capture owner/generation/queue-item identity in every asynchronous path. Seek only after matching valid metadata; never infer an item's offset from an unrelated announcement field or its arrival order. Test the later-piece offset and one-window path explicitly (E-04).
- Test click-before-synthesis, same-piece seek, paused resume, A→B→A with equal text, old metadata after source replacement, stop/reset/detachment while pending, end/error/play-rejection twins, failed pieces without renumbering, hidden-popup inline progress, and unrelated queue items.
- Automatic `/ask`/`/speech` audio remains an unaligned compatibility path. Chunk-tap replay preemption must use existing cancellation/release APIs and leave unrelated queued items intact. If this requires server/protocol edits or a new alignment scheme, stop with A-03 evidence; do not expand scope.
- GREEN/TRIANGULATE: run the required gates, including the existing Node regression suite. Existing source-string tests are read-only inputs; if a legitimate fix needs their modification, propose the exact paths for separate approval, not a silent allowlist expansion.

### T-04 — acceptance instructions

Record a complete candidate binding before final runs. Execute each gate separately in the foreground against those same bytes/modes; no source edits between passes. Any source fix invalidates affected evidence and requires the full affected gate set on the new complete candidate. Record MC-01 separately; a simulated browser pass cannot stand in for audible/mobile proof or native review consent.

## Gate authority

This is the only machine gate table. All rows are **UNRUN / PENDING** during preparation. EXISTING means a command's inputs/runner are established by inspected source/config, not that its runtime availability or success has been observed. FUTURE lasts until its creator has produced the listed inputs and recorded actual proof.

| Gate | Literal command | Exact cwd | Availability / creator | Source policy | Exit meaning |
|---|---|---|---|---|---|
| G-01 | `env -u E2E_JS_COVERAGE_DIR -u PYTEST_ADDOPTS -u PYTEST_PLUGINS TMPDIR=/home/bruno/Code/personal/agent-tts/hosts/herdr/brain/tests/browser/.runtime PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -c pyproject.toml --confcutdir=tests/browser -p no:cacheprovider tests/browser/test_karaoke_harness.py` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | FUTURE: T-01 fixture, smoke suite and runtime directory | Real Chromium; real static index/app/modules; simulated API/media; all unexpected traffic aborted; no inherited/e2e backend fixture. Current full working-tree inputs, including WIP. | 0 = nonempty harness assertions pass without skips; nonzero, missing tools, collection error or any skip = STOP. No feature PASS. |
| G-02 | `node --test tests/js/karaoke.test.js` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | FUTURE: T-02 module and Node suite | Real unchanged Toast helpers plus the new plan/controller; deterministic DOM/media/fetch doubles. | 0 = nonempty behavior suite passes without skips; nonzero or skipped/empty behavior proof = fail. |
| G-03 | `env -u E2E_JS_COVERAGE_DIR -u PYTEST_ADDOPTS -u PYTEST_PLUGINS TMPDIR=/home/bruno/Code/personal/agent-tts/hosts/herdr/brain/tests/browser/.runtime PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -c pyproject.toml --confcutdir=tests/browser -p no:cacheprovider tests/browser/test_karaoke_fragments.py` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | FUTURE: T-01 fixture and T-03 feature suite | Real Chromium/actual app/index/module; simulated audio/provider/API; deterministic fixtures in the suite, no live service or physical media claim. | 0 = nonempty SC behavior assertions pass without skips; nonzero, unavailable browser, collection failure or any skip = fail/STOP. |
| G-04 | `node --test tests/js/` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | EXISTING runner and base suite; includes T-02's new suite once created | All current JS tests and their actual static-source inputs; built-in Node runner, no added npm dependency. | 0 = regression suite passes without new skips; nonzero = failure requiring baseline classification, never an assumed environmental waiver. |
| G-05 | `node --check src/herdr_brain/static/karaoke.js && node --check src/herdr_brain/static/app.js` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | FUTURE: T-02 module; app.js already exists | Current on-disk module/glue bytes; syntax only. | 0 = both scripts parse; nonzero = syntax failure. Not behavioral proof. |

No application gate above was run during planning. Baseline and final output record the literal command, cwd, exit, test counts/skips, candidate binding and observed failures. Gate exit zero cannot hide skipped assertions. Do not run a broad Python suite, linter, formatter, clean-room installer, coverage instrumentation or maintenance script as an extra gate without separate authorization.

## FR-task-gate trace

This is traceability, not another FR/task/gate authority. The [FR verification matrix](#fr-verification-matrix) defines behavior and scenarios. Each row has task and machine proof; physical proof is explicitly separate.

| FR | Tasks | Machine gates | Manual |
|---|---|---|---|
| FR-01 | T-03, T-04 | G-03, G-04, G-05 | MC-01 |
| FR-02 | T-02, T-03, T-04 | G-02, G-03 | MC-01 |
| FR-03 | T-02, T-03, T-04 | G-02, G-03 | — |
| FR-04 | T-03, T-04 | G-02, G-03, G-05 | MC-01 |
| FR-05 | T-02, T-03, T-04 | G-02, G-03 | MC-01 |
| FR-06 | T-02, T-03, T-04 | G-02, G-03 | MC-01 |
| FR-07 | T-02, T-03, T-04 | G-02, G-03 | — |
| FR-08 | T-02, T-03, T-04 | G-02, G-03 | MC-01 |
| FR-09 | T-03, T-04 | G-03, G-04 | MC-01 |
| FR-10 | T-01, T-02, T-03, T-04 | G-01, G-02, G-03, G-04, G-05 | MC-01 |

## Manual check MC-01

**PENDING — human mobile UAT, not a machine gate.** Use the existing supported Android Chrome/PWA on an explicitly approved candidate environment. Record device/browser version, loaded build, complete candidate binding, outcomes and friction; no personal names, credentials or transcripts. This contract authorizes no deploy, restart, provider request, remote service access or paid call. If the environment needs one, stop for separate authorization. Local automated milestones may be handed off with V3 pending, but the feature cannot be functionally accepted yet.

- [ ] SC-01: user question is still visible; all existing Brain fragments, spaces/newlines and literal formatting survive; external agent history and Escuchar are unchanged.
- [ ] SC-03: one touch selects a numbered chunk; audio moves approximately to it and resumes; only one active chunk appears; repeat for another turn and Escuchar.
- [ ] SC-05: progress tracks inline and in the retained floating popup; stop/end/failure/reset do not leave stale active markers or stuck controls; normal voice/microphone flow still works.
- [ ] SC-06: narrow screen and light/dark theme remain readable; long-press/copy/selection/scroll and link/code interactions do not trigger unintended replay; keyboard/focus and accessibility state work with the device's available input/accessibility tools.
- [ ] Human records acceptance or specific failures for the above FR rows. An absent device/approved candidate build remains PENDING, never PASS.

## Initial source & gate-input binding

Modes are POSIX permission bits, including group-write differences, not just Git's executable flag. Every row is the original read-time SHA-256 snapshot. Preserve existing modes; new source files must be non-executable and their actual created modes recorded in the future candidate. No logs, generated output, secret files, private transcript data or installed-environment contents are hashed.

**Consolidation provenance:** the original package also bound the separate PRD and the planning index. This consolidated contract and `docs/prds/README.md` are planning artifacts, excluded from the source baseline and source candidate serialization. Their changed bytes are an authorized documentation revision, not source drift. The original separate PRD/index/entrypoint hashes are retired packaging identities, not reusable launch authority. All source rows below retain their original hashes and modes: consolidation must report any mismatch, never silently rebind. Historical clean-worktree claims above do not erase current unrelated WIP. A static source mismatch blocks launch until explicitly reconciled; structural `ready_for_bootstrap` is not permission to bypass that check.

| Repository-relative path | Mode | SHA-256 | Role |
|---|---|---|---|
| `hosts/herdr/brain/src/herdr_brain/static/app.js` | `0664` | `b4ebee2929930a22debfea83f74735583573bd9a14faf426cbca77be4cd27b3b` | T-03 edit; Node/browser input |
| `hosts/herdr/brain/src/herdr_brain/static/index.html` | `0664` | `115c33475cbaa9e1c1f46660941a90c6f9ae6507fc30f4dec1f151982dffae86` | T-03 edit; DOM/CSS/script input |
| `hosts/herdr/brain/src/herdr_brain/static/toast.js` | `0644` | `6c306cbe005036b7d697af7c3a6faa1fa5b90a6b444ee23176516b197c8df7e3` | Read-only shared splitter/module input |
| `hosts/herdr/brain/src/herdr_brain/static/reader.js` | `0644` | `8aa7aad9dd380acb7d8ed59a686d754d4a10b10449d1a7e84ff90794c56312b6` | Read-only loaded module/Node input |
| `hosts/herdr/brain/src/herdr_brain/static/speech.js` | `0664` | `785b966f634ffaaa70d92a8bafbf9b8a13c658617de2707d99b80736142823e6` | Read-only cancellation/queue input |
| `hosts/herdr/brain/src/herdr_brain/static/announce.js` | `0644` | `b56e0152c4369bbab32177311340882123d75a2373ab2f786d2f5b60b5a500fb` | Read-only loaded module/Node input |
| `hosts/herdr/brain/src/herdr_brain/static/approval.js` | `0644` | `b982f0ca6172380dcdb98f4d2841ac40c11fd691749429ca9424bb268c6b335a` | Read-only loaded module/Node input |
| `hosts/herdr/brain/src/herdr_brain/static/consult.js` | `0664` | `fc2e4d18c206164fcec7a8baf2be1697e5b6d86152ed12c432169a70c1113bc4` | Read-only loaded module/Node input |
| `hosts/herdr/brain/src/herdr_brain/static/endpointing.js` | `0664` | `632421b05ffff5d1d84d7a248719692b835324492a858b19901541f5ea51061d` | Read-only loaded module/Node input |
| `hosts/herdr/brain/src/herdr_brain/static/vad.js` | `0644` | `03d7200920183a0e77a8065d6ff845d3384b7d699f572018547873e42aa287a8` | Read-only loaded module/Node input |
| `hosts/herdr/brain/src/herdr_brain/static/sw.js` | `0644` | `b2336987640e5e56d613102cdb7e98aa1761e74f9d8f73c054ce064deafb832d` | Static request input; worker registration blocked in V2 |
| `hosts/herdr/brain/src/herdr_brain/static/manifest.webmanifest` | `0644` | `fca2f205ebdbacc3cb035f0c86e0abd9fd5b2a214c79ae42dbc3294328a213a5` | Static fixture request input |
| `hosts/herdr/brain/src/herdr_brain/static/icon.svg` | `0644` | `8c08f1d378e4eaf857964f4107e1cebad682dc63dd538500693908dc3b182272` | Static fixture request input |
| `hosts/herdr/brain/tests/js/announce-wiring.test.js` | `0644` | `f32cef05553392df471ba2cf3e222ce7afb6e07165383acfa6d73918006b91bd` | Read-only G-04 input |
| `hosts/herdr/brain/tests/js/announce.test.js` | `0644` | `75439eed65d7d08b857a8bf6b1a2aa38ae21c19f574e85d5f214b97aa591eb53` | Read-only G-04 input |
| `hosts/herdr/brain/tests/js/approval.test.js` | `0644` | `7c5190d88bdb13ae07e6f87cbad6aac28107bbb5a32268f1d4b7a48ee9fc1cc1` | Read-only G-04 input |
| `hosts/herdr/brain/tests/js/consult.test.js` | `0664` | `9c47696f67b453cf874cbc182513fa00184997386f14247ac70b5fa10639929c` | Read-only G-04 input |
| `hosts/herdr/brain/tests/js/endpointing.test.js` | `0664` | `14026f64978622d951faba0c1b8c77db7a3856ef5335e0f18b49db41b760f837` | Read-only G-04 input |
| `hosts/herdr/brain/tests/js/pending.test.js` | `0664` | `ce09763c2fd3d1075479fea9e5b9b74572f8e2fd33c83f4afcc4e0cfd4ac395f` | Read-only G-04 input |
| `hosts/herdr/brain/tests/js/reader.test.js` | `0644` | `b628342b2b45b637dacf9ac53a23a8ef9b254fc89e9a0b5f98fe77b257911a57` | Read-only G-04 input |
| `hosts/herdr/brain/tests/js/speech.test.js` | `0664` | `c680f5f21df6be6d5cc4044ed7d164e9f7f9c1858eb78169d655b792feca71d2` | Read-only G-04 input |
| `hosts/herdr/brain/tests/js/toast.test.js` | `0644` | `44935955bb9abab6cae891e873eb662a7cd624ec437fb666a52d586243631ff9` | Read-only G-04 splitter/DOM input |
| `hosts/herdr/brain/tests/js/vad.test.js` | `0644` | `2d9487d16b6b7bc932322b3da1be1b3b26acfac6c9e7128f263531b8e9287122` | Read-only G-04 input |
| `hosts/herdr/brain/tests/conftest.py` | `0644` | `d54f53dfab313c11340c1c13a4440dad175097c583bdb25495709c9d1c9aeb94` | Read-only fixture reference; inherited side effects excluded by confcutdir |
| `hosts/herdr/brain/tests/e2e/conftest.py` | `0664` | `1e3adeb40d26e8cba3e28af7df23a1fffbe030b172a9f504b2bdf798781da01d` | Read-only infrastructure reference, not V2 fixture import |
| `hosts/herdr/brain/tests/e2e/test_m1_glue_paths.py` | `0664` | `658ade4d9555ea5e913fb318ce6bf5a1894ccc21dd04b52fc7853f2b03cab1d0` | Read-only infrastructure reference, not a new gate |
| `hosts/herdr/brain/pyproject.toml` | `0644` | `b2c0a35d238a66bb2fa5868e1162f49eca9a8b9357073f6a658b9a672a4aebcf` | Python/test/dependency manifest |
| `hosts/herdr/brain/uv.lock` | `0644` | `e1c969331475e2ab3421619fd10393a507c8938fccb37012b381757a90505236` | Dependency lock; no mutation/install |
| `.github/workflows/ci.yml` | `0664` | `ef2a08596b4527e3926a3792b0ca4897feb157041e74611fd07ca34597a263cb` | Node/Python/browser environment and runner evidence |

| FUTURE repository-relative path | State in KARAOKE-B0 | Creator |
|---|---|---|
| `hosts/herdr/brain/src/herdr_brain/static/karaoke.js` | ABSENT; future non-executable mode recorded after creation | T-02 |
| `hosts/herdr/brain/tests/js/karaoke.test.js` | ABSENT; future non-executable mode recorded after creation | T-02 |
| `hosts/herdr/brain/tests/browser/conftest.py` | ABSENT; future non-executable mode recorded after creation | T-01 |
| `hosts/herdr/brain/tests/browser/test_karaoke_harness.py` | ABSENT; future non-executable mode recorded after creation | T-01 |
| `hosts/herdr/brain/tests/browser/test_karaoke_fragments.py` | ABSENT; future non-executable mode recorded after creation | T-03 |

**Future runtime-output allowance only:** `hosts/herdr/brain/tests/browser/.runtime/` for fresh framework-managed pytest/browser temporary children. No cache, pyc, traces, video, coverage, screenshots, logs or generated documents outside that sandbox. No deletion of prior output; do not change .gitignore or stage generated files. The launch must explicitly accept this sandbox as well as source edits. Browser context options must place any test artifacts there and close resources on teardown. No existing user-home/private files are inputs.

The installed interpreter, package versions and Chromium are **not** content-pinned or verified by prep; T-01 records them, and a missing/incompatible runtime stops rather than installing. No Node package manifest is required for the built-in runner. Feature fixture data lives inline in the creator-owned tests, not in an unbound external dataset.

**Candidate identity after launch:** snapshot every bound source/gate input plus every created source/test file, excluding planning artifacts and the runtime sandbox. Sort by repository-relative path; serialize each record as `path + "\t" + four_digit_POSIX_mode + "\t" + lowercase_SHA256 + "\n"`. Compute SHA-256 of the complete UTF-8 serialization and report it as the candidate binding ID. The externally captured contract hash/mode remains a separate frozen authority. Authorized source changes are compared with KARAOKE-B0 and the previous task candidate; read-only inputs must still match KARAOKE-B0. Never update just one hash to launder a changed input. A contract change voids its affected evidence and needs renewed explicit launch approval.

## Budgets and readiness

The following budgets are a **technical proposal until explicit launch**, not measured delivery estimates or native-runtime retry limits.

| Milestone | Tasks | Finite local budget | Advancement |
|---|---|---|---|
| M-01 — bootstrap | T-01 | One initial foundation attempt, at most 2 functional remediation rounds, and 60 minutes of active work. No installer/download escape hatch. | G-01 proof and recorded baseline; no feature acceptance claimed. |
| M-02 — feature | T-02, T-03 | One initial implementation sequence, at most 2 functional remediation rounds shared by the milestone, and 180 minutes of active work. Do not reset counters between tasks. | Required task gates against recorded candidate bytes. |
| M-03 — acceptance | T-04 | At most 2 verification/UAT reconciliation rounds and 45 minutes of active work; human waiting excluded. Source corrections consume the originating M-02 budget, not a fresh M-03 source-fix budget. | All affected gates plus separately accepted MC-01; otherwise report pending proof. |

`ready_for_bootstrap` means the finite foundation, identities, creators and literal contract are structurally defined. It does **not** mean tools are installed or tests pass. Promotion to `ready_for_execution` requires creator-owned critical inputs to exist, commands actually runnable with evidence and no critical FUTURE gates; obtain a newly bound launch if the contract is revised to record promotion. Do not confuse these planning states with feature acceptance or the host's independent native review.

## Stop and resume

- **Source-writer guard:** before editing, claim exclusive ownership of the union of exact task edit paths in [Task authority](#task-authority). Inspect each one, preserve unrelated tracked/untracked WIP, and stop if another writer or unexplained hash/mode change appears in any owned or bound input. Parent/orchestrator owns coordination; clean worktree and commits are not prerequisites.
- Stop on wrong root/source/contract identity; unavailable gate/tool; crash/unknown error; infeasible A-01/A-03; required out-of-allowlist edit; unexpected network/store/service access; changed dependency/runtime; budget exhaustion; or required human/native consent. Never substitute a runner, scope or product interpretation.
- On stop, report task, complete candidate snapshot, observed command/error, work preserved, counters spent/remaining, pending gates/manual proof and one deterministic proposed unblock. A human choice uses `interaction_required`, not a fabricated technical blocker.
- Native prompts, consent, candidate review disposition and native retry counters belong to the exact runtime contract at launch. Do not change review modes/models/permissions, invent an opt-out, search for review tools, or treat writer verification as independent review.
- No automatic stage/commit/push/PR, release, paid/remote operation, SDD, install, service restart or destructive command. Planning and future execution remain local and bounded; any broader action needs its own explicit authorization.

### Known environmental failures

None established for this feature. Application gates were not run during prep. A later failure is not automatically pre-existing; capture the before-edit baseline and stop/classify it without declaring a waiver or changing the requirement.

### Resume bookmark (reported by the future executor)

Seed state: completed tasks **none**; active task **none**; budgets **unused**; next action **T-01 after explicit launch and identity/source-writer checks**. For each subsequent handoff report:

- Contract path, externally captured exact hash and mode from the launching session; canonical root; original source binding and any explicitly reconciled drift.
- Completed task IDs with their literal gate outcomes and full candidate binding ID; manual proof remains distinct.
- Active task, milestone, initial attempt/functional remediation rounds used and remaining, and active-work budget remaining.
- Preserved WIP, stop reason if any, and exact next task/gate. Independent contexts re-read these locators and compare the complete candidate before continuing; global memory never overrides the bookmark.

Do not edit this frozen document simply to record progress: that changes its identity. Report the bookmark to the parent/user; a deliberate contract revision requires a new external hash and approval, without resetting spent budgets.

## Future launch request

**Prepared request, not authorization.** Nothing starts because this section exists or the PRD is approved. Only intentional, explicit user authorization in a future launching session can approve the exact bytes and bounded local work. There is no unresolved product choice; A-01, A-02 and A-03 remain unverified feasibility/environment assumptions with task owners and stop rules.

### Capture identity before asking for launch

Exact cwd: `/home/bruno/Code/personal/agent-tts`. Run these literal read-only commands in the future launching session:

```bash
sha256sum docs/prds/herdr-brain-karaoke-fragments.md
stat -c '%a %n' docs/prds/herdr-brain-karaoke-fragments.md
```

Capture the actual digest and four-digit POSIX mode in that session's handoff/bookmark, outside this document. Recheck before T-01; changed bytes/mode require renewed authorization. The contract contains no self-hash. Source baseline checks remain separate and mandatory; an external document hash does not reconcile source drift.

### Scope and future authorization text

The proposed launch covers M-01, M-02 and M-03, exclusively under [Task authority](#task-authority), [Gate authority](#gate-authority), [Initial source & gate-input binding](#initial-source--gate-input-binding), [Budgets and readiness](#budgets-and-readiness) and [Stop and resume](#stop-and-resume). Tests and local runtime sandbox require explicit launch consent. Physical MC-01 needs a separately approved candidate mobile environment; otherwise V3 remains PENDING. Runtime availability and all application proof remain PENDING.

The following is **suggested future authorization text**, not words already said by the user. Its identity reference resolves only when the preceding command output has been captured in that same session; without that capture and intentional user authorization it is incomplete and starts nothing:

> I authorize the already-approved PRD and execution contract at `docs/prds/herdr-brain-karaoke-fragments.md` in `/home/bruno/Code/personal/agent-tts`, bound to the exact SHA-256 and POSIX mode just computed with the literal commands above and recorded in this launching session. I authorize bounded local bootstrap first and then T-01 through T-04 only within this contract's exact task edit allowlists, literal local gate commands, source-writer guard and `hosts/herdr/brain/tests/browser/.runtime/` output sandbox, after validating the original KARAOKE-B0 source binding without silently rebinding drift. I accept the proposed two-round milestone limits and active-work budgets of 60/180/45 minutes with no counter resets. Runtime topology follows the active orchestrator's contract. Stop on this contract's conditions; keep physical UAT and native human consent separate and report pending proof honestly. I do not authorize installations, remote or paid/provider calls, service/deployment changes, SDD, review-mode changes, scope expansion, staging, commits, pushes or PRs.

This is a prepared contract, not a background service. No launch consent, signature, identity or date has been filled on the user's behalf. Implementation remains NOT AUTHORIZED until that separate future action.

## Planning static check

This read-only command checks the single contract and index, authority IDs/counts, DAG, traces, scenarios, links, original source hashes/modes, absent future files, obsolete-file removal and document whitespace/fences. It neither imports the application nor executes any application gate. Use it before implementation; after authorized source edits compare the saved complete candidate rather than pretending KARAOKE-B0 describes the new bytes. A mismatch prints original and actual source identities and fails; it never updates the baseline.

Exact cwd: `/home/bruno/Code/personal/agent-tts`.

```bash
python3 -c 'from pathlib import Path; text = Path("docs/prds/herdr-brain-karaoke-fragments.md").read_text(); code = text.split("<!-- karaoke-static-check -->\n```python\n", 1)[1].split("\n```", 1)[0]; exec(compile(code, "karaoke-planning-static-check", "exec"))'
git diff --check -- docs/prds/herdr-brain-karaoke-fragments.md hosts/herdr/brain/odd/tasks/karaoke-fragments.md hosts/herdr/brain/odd/tasks/karaoke-fragments-launch.md docs/prds/README.md
```

The embedded checker explicitly checks whitespace in both documents, including the untracked contract that `git diff --check` cannot inspect. Index link checks are scoped to this feature entry; unrelated roadmap entries are preserved.

<!-- karaoke-static-check -->
```python
from pathlib import Path
import hashlib
import re
import stat

root = Path.cwd().resolve()
assert str(root) == "/home/bruno/Code/personal/agent-tts", "wrong root"
contract = root / "docs/prds/herdr-brain-karaoke-fragments.md"
index = root / "docs/prds/README.md"
texts = {f: f.read_text() for f in (contract, index)}
p = texts[contract]
# Inspect prose, not checker literals, for stale references and normative IDs.
body = p.split("<!-- karaoke-static-check -->", 1)[0]

def section(text, heading):
    assert text.count(heading + "\n") == 1, ("heading authority", heading)
    return text.split(heading + "\n", 1)[1].split("\n## ", 1)[0]

def rows(text, heading, prefix):
    return [line.strip("|").strip().split(" | ") for line in
            section(text, heading).splitlines()
            if re.match(r"^\| " + prefix + r"-\d{2} \|", line)]

fr = rows(body, "## Functional requirements", "FR")
tasks = rows(body, "## Task authority", "T")
gates = rows(body, "## Gate authority", "G")
for registry, prefix, count in ((fr, "FR", 10), (tasks, "T", 4), (gates, "G", 5)):
    assert [r[0] for r in registry] == [f"{prefix}-{i:02d}" for i in range(1, count + 1)]
assert int(re.search(r"Declared FR count: (\d+)", body).group(1)) == len(fr)
declared = re.findall(r"FR=(\d+); tasks=(\d+); gates=(\d+)", body)
assert declared and all(tuple(map(int, d)) == (len(fr), len(tasks), len(gates)) for d in declared)
known = {prefix: set(re.findall(r"^\| (" + prefix + r"-\d{2}) \|", body, re.M))
         for prefix in ("D", "E")}
scenarios = re.findall(r"^Scenario: (SC-\d{2}) ", body, re.M)
assert scenarios == [f"SC-{i:02d}" for i in range(1, 7)]
known.update(FR={r[0] for r in fr}, T={r[0] for r in tasks}, G={r[0] for r in gates},
             SC=set(scenarios), MC={"MC-01"}, M={"M-01", "M-02", "M-03"},
             A={"A-01", "A-02", "A-03"})
for prefix, values in known.items():
    assert set(re.findall(r"\b" + prefix + r"-\d{2}\b", body)) <= values, prefix
for r in fr:
    assert "MUST" in r[1] and re.findall(r"D-\d{2}", r[2]) and re.findall(r"E-\d{2}", r[3])
for number, r in enumerate(tasks, 1):
    deps = re.findall(r"T-\d{2}", r[2])
    assert deps == ([] if number == 1 else [f"T-{number - 1:02d}"]), "DAG"
    assert re.findall(r"FR-\d{2}", r[1]) and r[4] and re.findall(r"G-\d{2}", r[5])
    assert re.search(r"### " + r[0] + r" —", body), "missing task instructions"
for r in gates:
    assert r[1].startswith("`") and r[1].endswith("`") and "..." not in r[1]
    assert r[2] == "`/home/bruno/Code/personal/agent-tts/hosts/herdr/brain`"
    assert r[3].startswith(("FUTURE:", "EXISTING")) and r[4] and "0 =" in r[5]
    if r[3].startswith("FUTURE:"):
        assert re.findall(r"T-\d{2}", r[3]), "missing creator"
trace = rows(body, "## FR-task-gate trace", "FR")
matrix = rows(body, "### FR verification matrix", "FR")
assert [r[0] for r in trace] == [r[0] for r in fr] == [r[0] for r in matrix]
task_by_id = {r[0]: r for r in tasks}
used_tasks, used_gates, used_scenarios = set(), set(), set()
for r, m in zip(trace, matrix):
    ts, gs = set(re.findall(r"T-\d{2}", r[1])), set(re.findall(r"G-\d{2}", r[2]))
    assert ts and gs and ts <= known["T"] and gs <= known["G"]
    assert all(r[0] in re.findall(r"FR-\d{2}", task_by_id[t][1]) for t in ts)
    assert gs <= set().union(*(set(re.findall(r"G-\d{2}", task_by_id[t][5])) for t in ts))
    assert ("MC-01" in r[3]) == ("MC-01" in m[3])
    assert any(cell != "—" for cell in m[1:4]) and re.findall(r"SC-\d{2}", m[4])
    used_tasks.update(ts)
    used_gates.update(gs)
    used_scenarios.update(re.findall(r"SC-\d{2}", m[4]))
assert used_tasks == known["T"] and used_gates == known["G"] and used_scenarios == known["SC"]
source_table = section(body, "## Initial source & gate-input binding").split("| FUTURE repository-relative path", 1)[0]
bound = [tuple(cell.strip("`") for cell in line.strip("|").strip().split(" | ")[:3])
         for line in source_table.splitlines() if line.startswith("| `")]
assert len(bound) == 29 and len({r[0] for r in bound}) == len(bound)
assert not {str(f.relative_to(root)) for f in texts} & {r[0] for r in bound}
drift = []
for path, mode, digest in bound:
    assert re.fullmatch(r"0[0-7]{3}", mode) and re.fullmatch(r"[0-9a-f]{64}", digest), path
    f = root / path
    assert f.is_file() and f.resolve().is_relative_to(root)
    actual_mode = f"{stat.S_IMODE(f.stat().st_mode):04o}"
    actual_digest = hashlib.sha256(f.read_bytes()).hexdigest()
    if (actual_mode, actual_digest) != (mode, digest):
        drift.append((path, mode, digest, actual_mode, actual_digest))
for mismatch in drift:
    print("SOURCE DRIFT (path, original mode/hash, actual mode/hash):", mismatch)
assert not drift, "original source binding drift; no silent rebind"
for directory in ("hosts/herdr/brain/src/herdr_brain/static", "hosts/herdr/brain/tests/js"):
    actual = {str(f.relative_to(root)) for f in (root / directory).iterdir() if f.is_file()}
    assert actual <= {r[0] for r in bound}, "unbound static/JS gate input"
future = re.findall(r"^\| `([^`]+)` \| ABSENT;[^|]+\| (T-\d{2}) \|", body, re.M)
assert len(future) == 5 and len({r[0] for r in future}) == len(future)
edits = set().union(*(set(re.findall(r"`([^`]+)`", r[3])) for r in tasks))
assert edits == {r[0] for r in future} | {r[0] for r in bound if r[0].endswith(("/app.js", "/index.html"))}
for path, creator in future:
    assert not (root / path).exists() and path in task_by_id[creator][3], path
old_names = ("karaoke-fragments.md", "karaoke-fragments-launch.md")
for name in old_names:
    assert not (root / "hosts/herdr/brain/odd/tasks" / name).exists(), "obsolete doc exists"
feature_index = section(texts[index], "## Herdr Brain features")
assert feature_index.count("](herdr-brain-karaoke-fragments.md)") == 1
assert "**APPROVED**" in feature_index and "ready_for_bootstrap" in feature_index
assert "Status: APPROVED PRD" in body and "Implementation is NOT AUTHORIZED" in body
assert "te autorizo a crear y aprobar el prd, pero deja un solo fichero autocontenido" in body
assert "sha256sum docs/prds/herdr-brain-karaoke-fragments.md" in body
assert not re.search(r"(?:PRD|Entrypoint|Contract) SHA-256[^\n]*`[0-9a-f]{64}`", body)
for text in (body, feature_index):
    assert "DRAFT" not in text and not any("tasks/" + name in text for name in old_names)
for file, text in texts.items():
    assert text.endswith("\n") and all(line == line.rstrip() for line in text.splitlines()), file
    assert text.startswith("# ") and not re.search(r"\{\{[^}]+\}\}", text), file
    opened = None
    for line in text.splitlines():
        fence = re.match(r"^(`{3,}|~{3,})(.*)$", line)
        if fence:
            marker, suffix = fence.groups()
            if opened is None:
                opened = marker
            elif marker[0] == opened[0] and len(marker) >= len(opened) and not suffix.strip():
                opened = None
    assert opened is None, (file, "unclosed fence")
    link_text = feature_index if file == index else body
    for link in re.findall(r"(?<!!)\[[^\]\n]+\]\(([^)\n]+)\)", link_text):
        path, _, anchor = link.partition("#")
        target = (file.parent / path).resolve() if path else file
        assert target.is_file() and target.is_relative_to(root), (file, link)
        if anchor:
            heads = re.findall(r"^#{1,6} (.+)$", target.read_text(), re.M)
            slugs = {re.sub(r"[^a-z0-9 _-]", "", h.lower()).replace(" ", "-") for h in heads}
            assert anchor in slugs, (file, link)
print(f"STATIC PASS: FR={len(fr)} tasks={len(tasks)} gates={len(gates)} scenarios={len(scenarios)}; unique sequential IDs/DAG/trace/links")
print(f"BINDING PASS: {len(bound)} original source hashes/modes; no drift; {len(future)} FUTURE files absent; planning artifacts excluded")
print("DOCUMENT PASS: one APPROVED contract + index; obsolete docs absent; fences/newlines/whitespace including untracked content verified")
print("FUNCTIONAL PROOF: PENDING; implementation NOT AUTHORIZED; no application gate executed")
```

Preparation proof is actual full readback and static-command output in the parent handoff, not an assertion that application gates passed. The external final hash is reported outside this contract and must be recomputed and authorized in the future launching session.
