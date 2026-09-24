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

### [ ] T3 — Tool loop interception + /ask approval field
Files: `src/herdr_brain/llm.py`, `src/herdr_brain/server.py`,
`tests/test_llm.py`, `tests/test_server.py`.
`BrainLLM.ask`: on `send_to_session` tool resolution → freeze gate, do NOT
execute; deterministic closer "¿Se envía?" appended server-side; response
gains `approval{}` (full text, `expires_in_s`). SYSTEM_PROMPT: state
target+text when gated, never narrate as done. `create_app`: store wiring.
Checks: fake-LLM injection tests — gate opens, no herdr send call happened.
Route: delegated-direct.

### [ ] T4 — Approval endpoints
Files: `src/herdr_brain/server.py`, `tests/test_server.py`.
`POST /approval/{id}/approve` (replay frozen args via LLM re-entry →
report answer + TTS `audio_url`), `/reject`, `/resolve` (lexicon +
reprompt budget + `listen_replace`), `PATCH` (text), `GET /approval/current`.
New `/ask` supersedes live gate.
Checks: injection tests — approve replays EXACT args incl. `timeout_ms`;
supersede; expiry at touch; resolve reprompt→reject; 404 on unknown/terminal
gate ids.
Route: delegated-direct.

### [ ] T5 — Client confirming state + voice routing
Files: `src/herdr_brain/static/app.js`, `tests/js/`.
`callState "confirming"` + PILL_TEXT entry; STT results route to
`/approval/{id}/resolve` (never `/ask`); handle
approve/reject/listen_replace (one dictation round → PATCH → re-confirm);
reprompt re-echo; countdown from `expires_in_s`; `thinking` during approve
replay; return to `listening` after resolution.
Checks: js tests for the resolve-routing state transitions (runner per
`tests/js/` convention).
Route: delegated-direct.

### [ ] T6 — Approval card UI + edit flows
Files: `src/herdr_brain/static/app.js`, `src/herdr_brain/static/index.html`.
Card pinned at drawer conversation top (below interim strip); buttons
✓ Enviar / ✏️ Editar / 🎙 Re-dictar / ✕ Cancelar; 60 s countdown ring;
manual edit → PATCH → re-confirm; expired gray state + system-styled
drawer turn; drawer auto-open on gate.
Checks: full pytest + js suite; manual visual pass.
Route: delegated-direct.

### [ ] T7 — Reload recovery + pending-banner coexistence
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

## Next Step

T3 (tool loop interception + /ask approval field).
