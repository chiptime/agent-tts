# Feature: Action Approval Gate (herdr-brain)

**Repo**: `~/Code/personal/herdr-brain`
**Design**: `docs/PRD-action-approval-gate.md` (approved + contracts resolved)
**Created**: 2026-09-24
**Status**: planned — plan approved, implementation NOT started (user paused)

## Objective

No `send_to_session` call ever executes without explicit user approval.
Voice call flow stays hands-free: approve/reject/edit by voice or by
drawer card.

## Problem / Why

STT mishears; today the brain's only mutating tool executes immediately
server-side. Two failure modes (misheard intent, misheard content) reach
agents with zero friction. Hard gate at the tool boundary, never
prompt-only (PRD D1/D2).

## Scope / Constraints

- Gate ONLY `send_to_session`; read-only tools untouched.
- Freeze EXACT dispatch args (`text`, `timeout_ms`, resolved pane) — the
  tool blocks until agent completion, so approve replays the identical call.
- One live gate per session; in-process only (mirror `ConversationStore`
  lock/normalize patterns from `memory.py`).
- Expiry 60 s (`HERDR_BRAIN_APPROVAL_TIMEOUT_S`, lazy check, silent).
- Voice lexicon es/en, short-utterance rule, ambiguous → reprompt ×1 → reject.
- UI: `callState: "confirming"`, card pinned at drawer top, pill
  "Confirmar ▲", reload recovery via `GET /approval/current`.
- TDD mode: OFF (strict) — repo convention is tests alongside code in the
  same work-unit commit (source: existing pytest + tests/js suites).

## Delivery Forecast

- Estimated authored lines: **~800** (additions+deletions, tests included).
- Strategy (USER DECISION 2026-09-24): work-unit commits STRAIGHT to local
  `main` — **never push, no remote, no PRs**. Overrides the default
  branch-first rule by explicit user instruction.

## Tasks

### [x] T1 — ApprovalGateStore + config timeout
Files: `src/herdr_brain/approval.py` (new), `src/herdr_brain/config.py`,
`tests/test_approval.py` (new).
Gate store mirroring `ConversationStore`: lock, `normalize(session_id)`,
`propose/get/resolve/patch/supersede`, states
`proposed→approved|rejected|expired|superseded`, approve-once idempotency,
lazy expiry, `created_at` reset on PATCH, `reprompt_count`.
Config: `DEFAULT_APPROVAL_TIMEOUT_S=60`, Settings field, env override.
Checks: `pytest tests/test_approval.py` + full suite.
Route: delegated-direct.

### [x] T2 — Voice resolve lexicon (pure)
Files: `src/herdr_brain/approval_lexicon.py` (new),
`tests/test_approval_lexicon.py` (new).
`resolve_utterance(text) → approve|reject|replace_intent|unknown`.
es/en sets per PRD §5; equal-or-startswith AND ≤24 chars; "no sé"→reject.
Checks: unit tests incl. edge cases ("no lo envíes", "no sé", long
sentences containing "no").
Route: delegated-direct.

### [x] T3 — Tool loop interception + /ask approval field
Files: `src/herdr_brain/llm.py`, `src/herdr_brain/server.py`,
`tests/test_llm.py`, `tests/test_server.py`.
`BrainLLM.ask`: on `send_to_session` tool resolution → freeze gate, do NOT
execute; deterministic closer "¿Se envía?" appended server-side; response
gains `approval{}` (full text, `expires_in_s`). SYSTEM_PROMPT: state
target+text when gated, never narrate as done. `create_app`: store wiring.
Checks: fake-LLM injection tests — gate opens, no herdr send call happened.
Route: delegated-direct.

### [x] T4 — Approval endpoints
Files: `src/herdr_brain/server.py`, `tests/test_server.py`.
`POST /approval/{id}/approve` (replay frozen args via LLM re-entry →
report answer + TTS `audio_url`), `/reject`, `/resolve` (lexicon +
reprompt budget + `listen_replace`), `PATCH` (text), `GET /approval/current`.
New `/ask` supersedes live gate.
Checks: injection tests — approve replays EXACT args incl. `timeout_ms`;
supersede; expiry at touch; resolve reprompt→reject; 404 on unknown/terminal
gate ids.
Route: delegated-direct.

### [x] T5 — Client confirming state + voice routing
Files: `src/herdr_brain/static/app.js`, `src/herdr_brain/static/approval.js`
(new pure module), `src/herdr_brain/static/index.html` (script + pill
CSS), `src/herdr_brain/server.py` (versioned ref + no-cache route),
`tests/js/approval.test.js` (new), `tests/test_server.py` (asserts).
`callState "confirming"` + PILL_TEXT entry; STT results route to
`/approval/{id}/resolve` (never /ask); handle
approve/reject/listen_replace (one dictation round → PATCH → re-confirm);
reprompt re-echo; countdown from `expires_in_s`; `thinking` during approve
replay; return to `listening` after resolution.
Checks: js tests for the resolve-routing state transitions (runner per
`tests/js/` convention).
Route: delegated-direct.

### [x] T6 — Approval card UI + edit flows
Files: `src/herdr_brain/static/app.js`, `src/herdr_brain/static/index.html`.
Card pinned at drawer conversation top (below interim strip); buttons
✓ Enviar / ✏️ Editar / 🎙 Re-dictar / ✕ Cancelar; 60 s countdown ring;
manual edit → PATCH → re-confirm; expired gray state + system-styled
drawer turn; drawer auto-open on gate.
Checks: full pytest + js suite; manual visual pass.
Route: delegated-direct.

### [x] T7 — Reload recovery + pending-banner coexistence
Files: `src/herdr_brain/static/app.js`.
Boot calls `GET /approval/current` → re-enter confirming with card
restored; pill "Confirmar ▲" reopen while drawer closed; agent pending
banner stays visible, visually secondary to the gate card.
Checks: js tests + full suite.
Route: delegated-direct (small — may inline with T6 leftovers).

### [ ] T8 — ES copy pass + manual smoke + status docs
UI strings review (card/pill/reprompt/expiry, neutral ES); manual E2E
smoke checklist on real call: gate opens → voice approve → voice reject →
voice re-dictate → manual edit → expiry → reload recovery; update PRD
status + this doc.
Checks: smoke checklist observed; suites green.
Route: inline (copy + smoke are per-action).

## Acceptance Criteria

1. AC1 — No path executes `send_to_session` without an approved gate
   (including the "dile que sí" agent-prompt flow).
2. AC2 — Read-only tools never gate; question latency unchanged.
3. AC3 — Hands-free approve/reject/replace; ambiguous → reprompt → reject.
4. AC4 — Card shows full prompt text; manual edit + voice re-dictation
   re-confirm with restarted timer.
5. AC5 — 60 s expiry auto-rejects silently; reload mid-gate recovers.
6. AC6 — One live gate per session; approve executes exactly once.
7. AC7 — pytest + js suites green; every task closes with its work-unit
   commit (Conventional Commits, tests alongside).

## Progress / Evidence

- T1 done — `.venv/bin/python -m pytest tests/test_approval.py -q` → 27 passed;
  `.venv/bin/python -m pytest -q` → 278 passed (1 pre-existing
  starlette/anyio DeprecationWarning, unrelated).
- Commit log:
  - `ed4884f` docs: add action-approval-gate PRD and task plan
  - `5ca7712` feat(approval): add ApprovalGateStore with lazy expiry and config timeout
  - T1 RDD assessment: tier high (`high_risk`, process_boundary signal),
    review_due=true — but RDD switch is OFF (clone-local), so no native
    review runs; ordinary checks only.
- T2 done — `.venv/bin/python -m pytest tests/test_approval_lexicon.py -q`
  → 64 passed; `.venv/bin/python -m pytest -q` → 342 passed (1
  pre-existing starlette/anyio DeprecationWarning, unrelated).
- Full-suite baseline going forward: 342 after T2 (T1's 27 were inside
  its 278 — no double count).
  - `b03767e` feat(approval): add voice resolve lexicon for approval gates
  - Locked tradeoff (PRD-literal): matching is literal startswith, no word
    boundary — "sigue"→approve, "paraguas"→reject. Pinned in
    TestLiteralStartswithSemantics.

- T3 done — `.venv/bin/python -m pytest tests/test_llm.py tests/test_server.py -q`
  → 94 passed; `.venv/bin/python -m pytest -q` → 354 passed (1
  pre-existing starlette/anyio DeprecationWarning, unrelated). Interception
  lives in `BrainLLM._invoke` (send_to_session never reaches dispatch);
  `ask()` returns the frozen `ApprovalGate` as `approval`; server shapes
  `approval{}` via `approval_payload()` and appends the verbatim closer
  "¿Se envía?" before TTS; `create_app` exposes `app.state.approval_store`
  and attaches it to the LLM; every `/ask` supersedes the session's live
  gate first (PRD §4).
- Full-suite baseline going forward: 354 after T3.
  - `6dc49cd` feat(approval): intercept send_to_session behind approval gate
  - Replay note (T4): frozen args live at `gate.action.{text, timeout_ms,
    pane_id, agent}`; `text` is dispatch-raw — sanitization happens inside
    `herdr.send_prompt` at replay, identical to the live path.
- T4 done — `.venv/bin/python -m pytest tests/test_server.py -q` → 71 passed;
  `.venv/bin/python -m pytest -q` → 368 passed (1 pre-existing
  starlette/anyio DeprecationWarning, unrelated); new baseline 368. Approve
  replays the frozen args through `tools.dispatch` on the re-resolved
  target and reports via the normal `ask()` entry (response shaped exactly
  like /ask); unknown/terminal/expired/superseded gate ids → 404 (lazy
  expiry at touch); resolve maps the lexicon → approve / reject /
  listen_replace (gate untouched, client dictates then PATCHes) / reprompt
  (×1 budget, spoken "¿Sí o no?" verbatim) → auto-reject; PATCH restarts
  the timer; GET /approval/current returns `{approval: …|null}`.
- Full-suite baseline going forward: 368 after T4.
  - `7bdb04a` feat(approval): add approval endpoints with exact-args replay
  - T5 contracts: resolve response `{decision, answer, audio_url, approval}`,
    `decision ∈ approve|reject|listen_replace|reprompt`; `listen_replace`
    leaves the gate untouched (client dictates ONE round → PATCH →
    re-confirm); PATCH has no re-echo audio (client re-echoes via
    `POST /tts`); approve response = exact /ask shape; reject is silent.
- T5 done — `node --test tests/js/` → 56 passed (20 new in
  `tests/js/approval.test.js`); `.venv/bin/python -m pytest -q` → 368
  passed (1 pre-existing starlette/anyio DeprecationWarning, unrelated);
  baseline stays 368 (server asserts folded into existing tests). Pure
  flow lives in `static/approval.js` (`window.ApprovalFlow`,
  endpointing.js-style dual-env module, injected clock+fetch); app.js
  wires it: PILL_TEXT `confirming: "Confirmar"` (pill renders
  "Confirmar ▲"), both STT engines dispatch through `routeUtterance()`
  (live gate → resolve, else /ask), mic keeps running under confirming,
  `micBaseState()` returns listening|confirming everywhere the audio
  queue resumes the mic, 1 s always-on `approvalFlow.tick()` owns the
  countdown, dictation utterances go to /resolve first (PRD §5
   precedence) and only a non-command (reprompt) result PATCHes the text.
- Full-suite baseline going forward: 368 pytest / 56 js after T5.
   - `8af8be9` feat(approval): client confirming state with voice resolve routing
   - T6 notes: card reads state from the `approvalFlow` instance
     (`active()/gate()/isDictating()/isExpired()/remainingSeconds()`); expiry
     payload is retained for the gray card. Known pre-existing bug absorbed
     into T6: `#drawer-close` has no click listener (drawer only closes via
     hang-up/back/keyboard) — fix it so pill-reopen works. NOT in scope:
     `pumpAudio` not stopping the mic during server-engine `recording`
     (pre-existing, follow-up).
   - Risk for T8 copy coaching: dictation rounds consume the reprompt
     ambiguity budget (unknown dictation → later ambiguous answer
     auto-rejects one round earlier). Safe direction.
- T6 done — `node --test tests/js/` → 68 passed (12 new: flow button entry
  points approve/reject/patchText/redictate, in `tests/js/approval.test.js`);
  `.venv/bin/python -m pytest -q` → 368 passed (1 pre-existing
  starlette/anyio DeprecationWarning, unrelated); baseline 368 pytest /
  68 js. Card lives in app.js (`renderApprovalCard`, pinned sticky at the
  top of `#conversation`, keyed updates on the 1 s tick + every flow
  setState); approval.js gained thin button entry points reusing the
  tested internals (`approve`/`reject` POST the dedicated endpoints,
  `patchText` is the manual-edit PATCH + `/tts` re-echo, `redictate`
  enters the dictation round — no DOM in the module). `#drawer-close`
  click listener added (minimize; call/mic keep running); pill reopen
  verified. DOM wiring (card render, buttons, edit, expiry gray +
  "⏱ Acción cancelada por tiempo" system turn, drawer close/reopen)
  verified against a vm DOM-stub smoke harness (scratch, not committed);
  repo tests stay pure-module per convention. Manual visual pass pending
   for T8. Commit hash recorded by orchestrator.
- Full-suite baseline going forward: 368 pytest / 68 js after T6.
   - `1d0e6cb` feat(approval): approval card UI with edit and re-dictation flows
   - T7 notes: boot recovery just calls `approvalFlow.open(payload)` — card,
     pill and mic routing follow from existing emissions (card re-renders on
     tick/setState/renderAnswer/playAudio). `openDrawer()`/`closeDrawer()`
     are idempotent. Banner `#pending-banner` sits outside the drawer; its
     visual subordination to the gate card is T7's. T8 items queued: FR11
     edit-box/keyboard coexistence on real device; dictation hint copy
     ("🎙 Dicta el texto nuevo — «sí» envía, «no» cancela") is worker-authored.
- T7 done — `node --test tests/js/` → 74 passed (6 new: recover() live/null/
  fetch-fail/non-ok/zero-window + banner-subordination flags, in
  `tests/js/approval.test.js`); `.venv/bin/python -m pytest -q` → 368 passed
  (1 pre-existing starlette/anyio DeprecationWarning, unrelated); baseline
  stays 368 pytest / 74 js. Recovery lives in `approval.js` as
  `recover(sessionId)` (plain GET `/approval/current?session_id=…`, never
  rejects, resolves to the payload only when a LIVE gate armed — an
  `expires_in_s` 0 payload is expired on the spot via the shared
  `expireIfDue()` refactor, no zombie confirming); app.js boots it fire-and-
  forget, auto-opens the drawer on a live payload (never in text mode), and
  `startCall()` now enters `micBaseState()` so a recalled call resumes under
  confirming (pill "Confirmar ▲"; pill-tap reopen verified). Banner
  coexistence: `renderApprovalCard` toggles `body.gate-live` from card
  presence (incl. the 5 s gray linger); CSS dims/desaturates
  `#pending-banner` under it (visible, secondary, logic untouched). Full DOM
  wiring (boot restore, drawer auto-open, interrupted pill → call under
  confirming, close → pill, pill-tap reopen, expiry gray → system turn →
  dismissal, gate-live on/off) verified on a scratch vm DOM-stub smoke
  harness (3 scenarios, not committed); repo tests stay pure-module per
   convention. Commit hash recorded by orchestrator.
- Full-suite baseline going forward: 368 pytest / 74 js after T7.
  - `bcc320d` feat(approval): reload recovery and pending-banner coexistence
  - T8 notes: recovered card's countdown ring renders full (pct is relative
    to the payload's own `expires_in_s`, not the configured 60 s window —
    the number is correct, the wedge is cosmetic); server payload carries
    no total, so a fix would touch the T4 contract. Verify visually in the
    smoke pass. Smoke must also exercise: real-device reload mid-gate,
    banner+card coexistence legibility, pending-banner tap while a gate is
    live (routes to /ask → gated per PRD).

## Next Step

T8 (ES copy pass + manual smoke + status docs).
