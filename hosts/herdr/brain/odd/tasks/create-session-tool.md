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

### [x] T4 — opencode entry point = user's `oa` (attach to persistent server)
Files: `src/herdr_brain/herdr.py`, `src/herdr_brain/tools.py`,
`src/herdr_brain/config.py`, tests.
User decision 2026-09-25: default opencode entry point is their zsh
`oa` = `opencode attach http://localhost:4096 --dir "$dir"`. herdr
`agent start --kind opencode -- [AGENT_ARG…]` appends args to the
canonical executable (pinned via --help), so compose
`["attach", <url>] + (["--dir", cwd] if cwd)`. Settings: new
`opencode_attach_url` (default `http://localhost:4096`, env override).
create_session gains optional `cwd` param (passed to `tab create --cwd`
AND `attach --dir`); LLM schema description updated (opencode panels
attach to the persistent server; cwd scopes the session). Other kinds
unchanged (no args).
Route: delegated-direct (writer).
Checks: pytest client+tools tests (attach args composition with/without
cwd; non-opencode kinds pass no args). Commit: `feat(tools)`.
RISK: herdr readiness detection might not recognize `opencode attach`
mode TUI; if live e2e fails on start, revert to bare opencode and
re-open this task.

### [ ] T5 — opencode panels launch the user's `oa` CANONICALLY (pane run)
Files: `src/herdr_brain/herdr.py`, `src/herdr_brain/tools.py`,
`src/herdr_brain/config.py`, tests.
User correction 2026-09-25: do NOT duplicate `oa`'s arguments (the
T4 attach-args composition copies a non-canonical snapshot; the user
may edit `oa` any time). herdr pins (read-only): `herdr pane run
<PANE_ID> <COMMAND>` types the command + Enter into the pane's
INTERACTIVE shell, so the dotfiles zsh function `oa` resolves
verbatim. New opencode flow: create_tab → `pane run <pane> "oa [dir]"`
(dir shlex-quoted when given) → readiness = bounded poll of
`agent list` until an agent reports our pane_id, then one bounded
`herdr agent wait <pane> --until idle` (writer pins exact wait flags)
→ existing send_prompt for the task. Evidence this works: the user's
current fleet IS oa-attached panes and send_to_session already
delivers to them. Other kinds keep `agent start --kind`.
REMOVES `Settings.opencode_attach_url` (the duplication the user is
rejecting); T4's args composition for opencode is superseded.
Route: delegated-direct (writer).
Checks: pytest (pane-run argv, readiness loop with fake client,
non-opencode kinds unchanged, config field removed). Commit:
`feat(tools)`.
RISK: herdr agent-detection latency for attach-mode TUIs; bounded
timeout → partial-failure text with created ids (existing pattern).

## Acceptance Criteria (from PRD)

- "crea un panel con opencode para X" → after ONE approval, the panel
  exists, the agent is running, and X was delivered to it.
- Tool result carries tab_id + pane_id; future sends can target it
  (last_active moved; explicit target works).
- Other panels stay untouched; user keeps working uninterrupted.
- No create_session execution without the user gate.

## Progress / Evidence

- 2026-09-25 T1 commit `dd9f3c7` (create_tab/start_agent, 12 tests).
  Pinned: `herdr tab create --label` → result.tab.tab_id +
  result.root_pane.pane_id; `herdr agent start <NAME> --kind <KIND>
  --pane <ID>` → result.agent.pane_id. Live daemon untouched in dev.
- 2026-09-25 T2 commit `8fe884d` (tool + schema + dispatch, 14 tests;
  2 schema-enumeration assertions extended — registry pins).
- 2026-09-25 T3 commit `8f412be` (gate generalization, create payload,
  replay by action.tool, card variant, 13 py + 7 js tests).
- 2026-09-25 T4 commit `241ebf1` (attach args composition; Settings.
  opencode_attach_url; create_session cwd param; schema description).
  Follow-up `107526d`: GateAction freezes cwd so the APPROVED path
  keeps --cwd/--dir (initial T4 dropped it on the gated path — caught
  in review, 5 gate tests added).
- Suites at close: pytest 539 passed; js 129/129. Pending: live voice
  e2e (attach-mode readiness detection by herdr is the residual risk;
  if agent start fails to detect, revert to bare opencode and reopen
  T4).

