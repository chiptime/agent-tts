# Feature: create_session Tool — New Panels from herdr-brain (herdr-brain)

**Repo**: `~/Code/personal/herdr-brain`
**Created**: 2026-09-25
**Status**: planned

## Objective

herdr-brain can open a NEW panel with a dedicated agent (e.g. opencode),
give it a title and a first instruction, and return its identifiers —
without touching or requiring any pre-existing session.

## Problem / Why

Today the brain can only send work to the currently selected/focused
session, so new work stomps on sessions doing something else. PRD
(2026-09-25): a `create_session` tool that launches a fresh panel with a
chosen agent kind + title + initial task, gated by the existing approval
flow, returning session/pane ids registered for future sends.

## Feasibility (mapped 2026-09-25)

No `session.create` RPC exists. Native path is tab+agent level:
`herdr tab create --label <TITLE> [--cwd <PATH>]` → `.result.tab` +
`.result.root_pane`, then `herdr agent start <NAME> --kind <KIND>
--pane <PANE>` (kinds include opencode, claude, codex, gemini, …).
Brain talks to the daemon exclusively via the CLI wrapper
(herdr.py:120-131 `_run_cli`), so this feature = new CLI invocations in
HerdrClient + a gated mutating tool. Writer MUST pin exact CLI
flags/output (`herdr tab create --help`, `herdr agent start --help`,
`herdr api schema --json`) before coding — schema field names are not
byte-verified yet.

## Scope / Constraints

- Tool name: `create_session(agent_kind, title, task="")` — LLM-facing
  name stays `create_session` even though herdr-level it is tab+agent.
- Flow inside the handler: tab create → agent start on root pane → if
  task non-empty, send it with the same delivery-guarantee semantics as
  send_to_session (existing send_prompt). Handler must be idempotent-
  UNCALLED: any failure after tab creation reports what was created
  (tab/pane ids) instead of orphaning silently.
- Register for future sends: set `BrainTools.last_active` to the new
  pane; result text includes tab_id + pane_id + agent name so the LLM
  can target it explicitly afterwards.
- Approval gate: create_session is MUTATING → gated exactly like
  send_to_session. Generalize the gate (llm.py:278 keys on the literal
  send name; GateAction carries tool already; server.py:608 replay
  hardcodes SEND_TO_SESSION): replay must dispatch the stored action's
  tool with its frozen args. PATCH edit applies to `task`.
- app.js approval card: variant layout for create_session showing
  agent/title/task (reject/approve unchanged; voice lexicon reuse).
- After approval resolves, trigger an immediate herd refresh so the new
  panel is selectable without waiting for the 5s poll.
- Read-only tools untouched; send_to_session behavior unchanged.
- TDD mode: OFF — tests alongside code, same work-unit commit.
- Delivery: work-unit commits straight to local `master`, never push
  (USER DECISION 2026-09-24).

## Delivery Forecast

- Estimated authored lines: ~500 (tests included). Under the 400-line
  per-PR heuristic is NOT met for the whole feature; tasks are sliced
  into separate work-unit commits (C1, C2, C3) each well under budget,
  no chain needed (no remote, no PRs).

## Tasks

### [x] T1 — HerdrClient: create_tab + start_agent
Files: `src/herdr_brain/herdr.py`, `tests/test_herdr_client.py` (new or
extend existing conventions).
`create_tab(label, cwd=None) -> {tab_id, pane_id}` and
`start_agent(name, kind, pane_id, timeout_ms=None) -> str` wrapping the
pinned CLI shapes; HerdrError on non-zero/parse failure. Fake-subprocess
unit tests (pattern from existing suites).
Route: delegated-direct (writer).
Checks: pytest client tests + full suite. Commit: `feat(herdr)`.

### [x] T2 — create_session tool + schema + dispatch
Files: `src/herdr_brain/tools.py`.
Handler per Scope flow; TOOLS_SCHEMA entry (agent_kind, title required;
task optional); dispatch branch; last_active update; error text names
created ids on partial failure.
Route: delegated-direct (writer).
Checks: pytest tools tests (new cases in existing test files) + full
suite. Commit: `feat(tools)`.

### [x] T3 — Approval gate generalization + PWA card variant
Files: `src/herdr_brain/llm.py`, `src/herdr_brain/approval.py`,
`src/herdr_brain/server.py`, `src/herdr_brain/static/app.js`,
`tests/js/approval.test.js` (extend).
Gate set {send_to_session, create_session}; frozen args carry
tool-specific fields; approval_payload renders create fields;
replay_and_report dispatches action.tool; card variant (agent/title/
task, PATCH edits task); immediate herd refresh on approve.
Route: delegated-direct (writer).
Checks: pytest gate tests + `node --test tests/js/` + full suite.
Commit: `feat(approval)`.

## Acceptance Criteria (from PRD)

- "crea un panel con opencode para X" → after ONE approval, the panel
  exists, the agent is running, and X was delivered to it.
- Tool result carries tab_id + pane_id; future sends can target it
  (last_active moved; explicit target works).
- Other panels stay untouched; user keeps working uninterrupted.
- No create_session execution without the user gate.

## Progress / Evidence

- 2026-09-25 T1 commit `dd9f3c7` (create_tab/start_agent, 12 tests).
  Pinned shapes: `herdr tab create --label` → result.tab.tab_id +
  result.root_pane.pane_id; `herdr agent start <NAME> --kind <KIND>
  --pane <ID> [--timeout MS]` → result.agent.pane_id. Live daemon NOT
  touched during dev (read-only pinning only).
- 2026-09-25 T2 commit `8fe884d` (tool + schema + dispatch, 14 tests;
  2 existing schema-enumeration assertions extended — registry pins).
  Delivery semantics mirror send_to_session: blocked → failure text
  with ids; timeout/stalled → delivered.
- 2026-09-25 T3 commit `8f412be` (gate set generalization, create
  payload, replay dispatch by action.tool, card variant via new pure
  cardModel() in approval.js + CSS, refreshState after approve,
  13 python + 7 js tests). Send paths byte-identical (old tests green
  unmodified).
- Suites: pytest 498 passed; js 124/124. Status: COMPLETE (code).
  Pending: live voice e2e by user ("crea un panel con opencode para X").

