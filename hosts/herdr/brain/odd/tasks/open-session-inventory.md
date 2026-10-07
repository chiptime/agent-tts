# Feature: herdr-brain Open-Session Inventory and Routing

**Repo**: `~/Code/personal/agent-tts` (module `hosts/herdr/brain/`)
**Predecessor**: `hosts/herdr/brain/odd/tasks/herdr-brain-on-demand-context.md` (completed; PRD
`docs/prds/herdr-brain-on-demand-context.md`, D01-D03, FR-03/04/12/24). This is a small linked
extension; it does not restate that roadmap.
**Branch at preparation**: `fix/brain-interim-transcript-duplication` @ `e592ef3` (unrelated
transcription work; no branch exists for this feature)
**Source-stage branch**: `fix/brain-open-session-inventory` @ `5e833fe` (clean at writer start;
no branch or commit operations performed)
**Created**: 2026-10-05
**Status**: MERGED TO LOCAL MAIN (unpushed). T1 closed: source implemented, verified, committed
and merged by parent disposition on 2026-10-07; origin push deliberately withheld by the user.
Live-model routing and deployment remain unproven. Engram mirror remains pending.

## Objective

Let the voice user ask "which sessions do I have open?" and get a true, cheap answer from a
read-only inventory of ALL open agent sessions, with routing that keeps four scopes distinct:
selected session, open-session inventory, consolidated global status, and history.

## Problem / Why

- Report (voice): the recognized utterance was `sesiones tengo abiertas` (original:
  `qué sesiones tengo abiertas?`) and the model answered that only the current session is
  visible. The utterance asks for the global inventory, not for the selected session.
- Static evidence (a hypothesis: no live tool trace or deployed revision was captured):
  - `TOOLS_SCHEMA` (`tools.py:763`) and `BrainTools.dispatch` (`tools.py:619`) expose nine tools
    and none lists sessions. `BrainTools.herd()` (`tools.py:181`) builds that list but only feeds
    the UI route `GET /herd` (`server.py:539`).
  - `get_status`, `read_transcript` and `read_screen` act on the selected pane, and the
    per-request live block (`llm.py:73`) renders only the selected agent.
  - `SYSTEM_PROMPT` (`llm.py:36`) speaks only of "the active agent": it has no inventory route
    and does not mention the consult tools.
  - `consult_work_status` (`tools.py:901`) is the only global path. It is described as "SLOW (up
    to ~60s)" and "ONLY when the user explicitly asks about overall/global work state", so a
    plain "what is open?" has no cheap route.
- Root-class triage (systemic-issue-triage): bucket C, one cluster, "cheap global fact missing
  at the LLM tool line". The fact already exists at the provider (`HerdrClient.list_agents`,
  `herdr.py:158`), so this is plumbing. Over-engineering test: T1 may add ONE read-only verb and
  no new state, flag, gate, provider, or parallel data representation.

## Authorization

- Relayed by the parent on 2026-10-05: the user explicitly authorized implementing the missing
  voice-accessible open-session inventory and clearer routing, keeping the three PRD scopes
  (selected session; open sessions / current global status; historical chats + Engram).
- NOT authorized by this tracker: stage, commit, push, merge, branch switch, deploy or restart,
  dependency installs, live agents or models, remote access.

## Scope / Constraints

- Allowed edit surfaces for T1, as stated by the parent (all under `hosts/herdr/brain/`):
  `src/herdr_brain/tools.py`, `src/herdr_brain/llm.py`, `tests/test_tools.py`,
  `tests/test_llm.py`, and `tests/test_consult.py` (registration-only expected
  tool-name update). This tracker may also be updated with observed progress.
- Out of scope: new providers, history/consult redesign, audio/TTS/STT/static UI, `herdr.py`,
  `server.py`, `evidence.py`, maintainer docs.
- Preserved: both approval gates and their tests; `consult_work_status` / `consult_history`
  behavior, signatures and period vocabulary (description wording may be clarified);
  `herd()` and `GET /herd` output with their tests, including `test_herd_empty_on_list_failure`
  (`tests/test_tools.py:182`).
- Inventory rules: read only `list_agents()`; NO status filter (do not reuse `ACTIVE_STATUSES`,
  `evidence.py:291`, which excludes idle); never call `_track` (`tools.py:750`) so the selected
  session is unchanged (D07/FR-24); explicit three-way outcome (listed / empty / unavailable)
  that mirrors `InventoryResult` (`evidence.py:191`, FR-11: a CLI error is failed coverage with
  a detail, an empty success is OK with zero sources, never conflated) instead of a new
  vocabulary.
- Existing pins to keep green: description anchors in `test_descriptions_carry_cost_hints`
  (`FIRST`, `Fallback`, `SLOW`, `Rarely needed`; `tests/test_tools.py:501`), the phrases in
  `TestSystemPromptPolicy` (`tests/test_llm.py:576`), and `system.startswith(SYSTEM_PROMPT)`.

## Execution settings

- Runner, from `hosts/herdr/brain` (no installs; `uv run` is avoided because it may sync the
  environment): focused `.venv/bin/python -m pytest tests/test_tools.py tests/test_llm.py tests/test_consult.py -q`;
  full `.venv/bin/python -m pytest tests/ -q` and `node --test tests/js/`.
- Last mapper baselines, NOT re-run by this preparation: 1201 Python tests and 224 JS tests
  (prior transcription work). The writer records its own fresh baseline before RED.
- TDD: ON, strict RED -> GREEN -> REFACTOR, with observed RED before each GREEN. Source: the
  predecessor tracker (user choice 2026-09-30), applied to this linked extension. The unrelated
  transcription tracker's TDD OFF does not apply here.
- Delivery: `ask-on-risk`. Forecast ~100-200 authored lines (the 400-line heuristic is advisory).
  No commit, push or deploy is authorized and scope is unchanged. If a work-unit commit is later
  authorized: Conventional Commit, explicit paths only (never the unrelated dirty files listed
  under Progress). The parent created `fix/brain-open-session-inventory` from `main` @ `5e833fe`
  before delegating the source stage; subsequent git actions remain parent-owned.
- RDD: OFF (clone-local); no native review transaction.
- Engram: project `dotfiles`, topic `odd/open-session-inventory/tasks`, full mirror of this file
  (the predecessor tracker names project `herdr-brain`).

## Tasks

- [x] **T1** `open-session-inventory`: ONE work unit = read-only inventory tool wired through
  `TOOLS_SCHEMA` and `dispatch`, four-scope routing text (tool descriptions and `SYSTEM_PROMPT`),
  and the tests. Route: delegated-direct (2+ non-trivial source/test files plus reading prep).
  - Approved shape (D-1): tool `list_open_sessions`, no arguments, JSON like
    `get_status`: `{available, count, selected_pane_id, sessions: [{pane_id, agent, status,
    title, cwd, session_id, focused}]}`. On `HerdrError`: `available: false`, a one-line detail,
    and an explicit "do not claim there are no sessions"; `count: null` means unknown, not zero.
    Metadata only, no `last_turn` (cheap,
    and it keeps untrusted transcript text out of the model context); titles via `_enrich`.
  - Closure tests (suggested names; the writer records the final ones). `test_tools.py`: lists
    idle, working and blocked agents; empty is an explicit zero; `HerdrError` is an explicit
    unavailable; selection untouched; dispatch serves it without a gate. `test_llm.py`: scripted
    calls for `sesiones tengo abiertas` and `qué sesiones tengo abiertas?` reach the tool and no
    gate opens; the schema lists the tool; the prompt names the four routes.

## Resolved implementation decision D-1

The parent selected A on 2026-10-05: expose `list_open_sessions` and include
the existing `tests/test_consult.py` registration assertion in the allowed
surface. This is test maintenance required by the authorized tool addition,
not a new product choice. No existing consult/history behavior is changed.

A tenth tool requires updating THREE exact-set assertions: `tests/test_tools.py:491` and
`tests/test_llm.py:138` (inside the surface) and `tests/test_consult.py:1180`
(`names == base five | CONSULT_TOOL_NAMES`, originally outside the four-file surface;
now authorized for that registration assertion only).

- A (recommended): new tool `list_open_sessions`, and add exactly
  `hosts/herdr/brain/tests/test_consult.py` as a fifth allowed path, limited to that one
  expected-name set (registration-only update, disclosed as not-RED, as the predecessor did in
  T7 and T9).
- B: keep four paths and add no tool name; give `get_status` an optional `scope="all"`. Routing
  is weaker (its description says "Rarely needed") and it overloads an existing verb.
- Rejected: leaving `test_consult.py` red (a required suite must pass); live-context-only
  injection (no tool choice to prove with mocked calls, and extra tokens on every turn).

## Acceptance criteria

Closure is named passing tests, never promises.

1. `TOOLS_SCHEMA` exposes a read-only open-session inventory tool and `dispatch` serves it. It
   never opens an approval gate and never changes the selected session.
2. The result lists every open agent returned by `list_agents()`, idle included (not only
   working), each with pane id, agent kind, status, title, cwd and a selected marker.
3. A successful empty listing says explicitly that there are zero open sessions. A `HerdrError`
   says explicitly that the inventory is unavailable and must not be reported as "no sessions".
   The two outputs are distinguishable.
4. Tool descriptions and `SYSTEM_PROMPT` route four scopes apart: selected session (live
   context, `read_transcript` / `read_screen` / `get_status`), open-session inventory (new tool,
   cheap), consolidated global status (`consult_work_status`, slow, explicit overall-state ask),
   history (`consult_history`, Engram). Existing prompt policy stays.
5. Scripted-LLM tests send `sesiones tengo abiertas` and `qué sesiones tengo abiertas?` through
   `BrainLLM.ask`, reach the tool, and surface its result. They prove wiring (schema, dispatch,
   prompt text, tool message), NOT live-model routing.
6. Gate tests, consult tests and `herd()` tests stay green with unchanged behavior.
7. Observed RED -> GREEN -> REFACTOR on the focused runner, then the full pytest and node runs.

## Known limits (do not hide)

- Live-model routing is NOT proven by T1. Closing the original report needs the exact utterance
  replayed against the real model after a deployment, which is not authorized here. The runtime
  mechanism above is inferred from static code only.
- Same root class, deliberately left unchanged to keep T1 small and preserve UI/test contracts:
  a failed listing is still reported as "none" by `herd()`, by `resolve_target` and the live
  block (`llm.py:86`, "no agent panes are running"), and by `get_status` ("no agent panes
  found"). `parse_agent_list` (`herdr.py:51`) also returns `[]` for unparseable output, so even
  the new tool cannot tell garbled CLI output from a real empty list. A single root fix is a
  candidate follow-up only if the parent expands scope.
- No maintainer-doc update is in scope; this tracker is the only doc artifact.

## Progress / Evidence

- 2026-10-05 preparation (read-only; no source or test edits). Anchors above were verified
  against the working tree. Findings beyond the brief: D-1; the sibling silent-empty sites;
  the predecessor's recorded merge SHA `e68a05d` exists as a commit but is not an ancestor of
  HEAD, while the delivered consult code is reachable under other SHAs (for example `d7de194`),
  so cite the predecessor by symbol or subject. HEAD `e592ef3` is contained in `main`
  (`3c9f94b`, 7 commits ahead, 0 branch-only commits).
- Parent confirmed `main` advanced concurrently, but its seven new commits
  do not change the five planned source/test surfaces. Source implementation
  stays on the current branch; no merge, branch switch, or movement of other
  sessions is authorized. Latest-main integration is separate and pending.
- Engram mirror PENDING: preparation's save failed because multiple runtime
  sessions match. No authoritative session identity was supplied; no guessed
  identity or blind retry is permitted. Preserve local recovery state and
  continue safe implementation under the unavailable-mirror exception.
- Unrelated working-tree changes at preparation (none belong to this feature; do not edit, clean
  or stage): modified `hosts/herdr/brain/odd/tasks/transcription-duplication.md`,
  `hosts/herdr/brain/src/herdr_brain/static/endpointing.js`,
  `hosts/herdr/brain/tests/js/endpointing.test.js`; untracked
   `docs/voice-stack/RECONCILIATION-PLAN.md` and `.atl/`.

### Source-stage evidence (2026-10-05)

- Start inspection: clean tree, branch `fix/brain-open-session-inventory`, HEAD `5e833fe`.
  The cancelled attempt left no changes. Both parent-supplied skill paths were read.
- Fresh baseline, before test additions or source edits:
  - `.venv/bin/python -m pytest tests/test_tools.py tests/test_llm.py tests/test_consult.py -q`:
    **141 passed in 2.55s**.
  - `.venv/bin/python -m pytest tests/ -q`: **timed out at 120s**, after 12 progress dots;
    no named test failure or completed full-baseline count was reported.
  - `node --test tests/js/`: **266 passed**, 0 failed (98.893467ms).
  - `git diff --check`: passed, no output.
- RED: the exact focused command above returned **20 failed, 139 passed in 2.77s**, before
  source edits. Missing method, unknown dispatch tool, absent schema entry, and missing scope
  guidance were observed. Two failures were exact-name registration maintenance, not behavioral
  RED. Eighteen new behavior/contract cases failed as intended.
- GREEN: after source implementation and D-1 registration maintenance, the same focused command
  returned **159 passed in 2.70s**. No `herd()`, consult implementation, or gate code changed.
- TRIANGULATE / REFACTOR: mixed agent kinds and opaque status/cwd values supplement idle,
  working, blocked, empty and unavailable cases. Payload layout and the selection variable were
  clarified, and the dispatch test name was corrected. The same focused command remained green:
  **159 passed in 2.81s**.
- Closure-test names (current names; all passed):
  - `TestListOpenSessions.test_lists_idle_working_and_blocked_without_reads_or_selection_change`:
    exact metadata fields, one inventory listing, idle included, non-focused selection preserved;
    transcript/turn/screen reads, active-agent lookup and `_track` are forbidden by the test.
  - `TestListOpenSessions.test_empty_is_explicit_zero_without_changing_selection` and
    `test_list_failure_is_unavailable_not_empty`: explicit zero versus unavailable/unknown count,
    short one-line failure detail and the do-not-claim-no-sessions instruction.
  - `TestListOpenSessions.test_enriches_title_and_preserves_metadata_as_data` and
    `test_dispatch_ignores_target_without_reads_or_writes`: title metadata, opaque status/cwd,
    null session id for non-id kinds, and selection-neutral argument-free dispatch.
  - `TestDispatch.test_inventory_schema_is_argument_free_and_metadata_only` and
    `test_descriptions_distinguish_scopes` (five cases): schema and scope/cost guidance.
  - `TestRoutingPolicy.test_open_session_question_reaches_inventory_and_surfaces_result`
    (both `sesiones tengo abiertas` and `qué sesiones tengo abiertas?`): a scripted tool request
    reaches the real dispatcher and returns inventory metadata; the scripted answer is generated
    from that result. No gate opens or prompt is sent, selection is preserved, and no inventory
    metadata is injected into the per-turn system block. This proves wiring, NOT live routing.
  - `TestSystemPromptPolicy.test_prompt_routes_four_scopes` (four cases) and
    `test_inventory_metadata_is_data_not_instructions`: scope routes and metadata policy.
- Registration-only maintenance, **not behavioral RED**: expected tool-name sets in
  `TestDispatch.test_schema_names`, `TestRoutingPolicy.test_schema_and_system_prompt_passed_to_llm`,
  and `TestApprovalRegression.test_schema_now_exposes_on_demand_surface`. The entire
  `tests/test_consult.py` diff is the single added `list_open_sessions` name.
- Required final verification, each run in the foreground from `hosts/herdr/brain`:
  - `.venv/bin/python -m pytest tests/ -q`: **1381 passed in 335.46s**, with a 600s timeout;
    the completed rerun supersedes the incomplete full-baseline attempt, not its recorded evidence.
  - `node --test tests/js/`: **266 passed**, 0 failed (109.943479ms).
  - `git diff --check`: passed, no output.
- Concurrent unrelated changes observed during implementation: modified `docs/prds/README.md`
  and untracked `docs/prds/herdr-brain-karaoke-fragments.md` and
  `hosts/herdr/brain/odd/tasks/karaoke-fragments.md`; none was read for content or altered.
- Source/test diff, excluding this tracker: **260 insertions, 11 deletions (net +249 lines)**.
  This exceeds the initial ~100-200-line forecast without trimming tests; the six-file source-stage
  diff remains below the advisory 400-line heuristic. No additional edit surfaces were needed.
- Source acceptance is verified locally; T1 closure remains unchecked for parent disposition.
  No commit, stage, push, restart, deployment, live model/agent or remote access was performed.
  The garbled-CLI-output limitation and existing silent-empty sites under Known limits are unchanged.
  The Engram mirror remains PENDING under the unavailable-mirror exception; no memory search,
  invented session id, retry, CLI workaround or passive capture was used.

## Next step

1. T1 disposition (2026-10-07): parent committed the six feature paths with explicit paths
   (tracker included) on a feature branch cut from `main` @ `3656fb9` and merged it into local
   `main` with a merge commit, following repository convention. NO push to origin was performed,
   per explicit user instruction. `.atl/skill-registry.md` remains uncommitted as unrelated
   tooling noise. The stale branch `fix/brain-open-session-inventory` @ `5e833fe` was left
   untouched and remains 21+ commits behind.
2. Repair the pending Engram mirror only with an authoritative runtime session identity; no
   blind retry or invented identity is permitted.
3. Live-model replay of both reported utterances and deployment remain separate, unauthorized
   work. Do not close the original runtime report from scripted wiring tests alone.
