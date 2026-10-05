# PRD: herdr-brain — On-Demand Context, Consolidated Reports, and Bounded Autonomous Implementation Loop

**Status: PARCIAL** — This PRD documents intent and confirmed product decisions. It does **not** authorize implementation, worktrees, branches, commits, pushes, or any remote operation. Any future implementation requires explicit later authorization (D10).

> **Nota de estado (2026-10-05)**: Contexto on-demand e informes implementados (`d7de194`, 2026-09-30; seguimiento en `2cb3a50`, 2026-10-01), contrastados con código y Git. Evidencia: [registro de entrega](../../hosts/herdr/brain/odd/tasks/herdr-brain-on-demand-context.md) y [mapa de cobertura](../../hosts/herdr/brain/docs/on-demand-context-coverage.md). El bucle autónomo (FR-42..45, D10) sigue futuro, no habilitado; el cuerpo conserva el diseño y diagnóstico previos.

**Lead decision:** the voice brain keeps its single-model, discovery-then-read tool loop and gains (a) honest on-demand global/historical context with freshness-validated, persistently cached consolidated reports, and (b) a separately specified future autonomous sequential implementation loop running in isolated Git worktrees. Zero open product questions; all decisions D01–D10 are confirmed.

## Quick path

1. Read Objectives/Non-Objectives, then the Decision Ledger (D01–D10).
2. Read Target Architecture vs Verified Current State to separate intent from fact.
3. Review FR-01..FR-45 and the Requirement-to-Test Matrix for acceptance shape.

## Context & Problem

The maintainer directs fleets of coding agents (OpenCode, Claude Code, Antigravity) from Herdr tmux panes and talks to them by voice through the brain service (`hosts/herdr/brain/`). Today the conversational default focuses the selected Herdr session, which is correct for driving one agent but leaves three gaps:

1. **No honest global view.** "What am I working on?" / "What advanced this week?" has no consolidated, evidence-grounded answer spanning all open sessions and historical development chats.
2. **No freshness semantics.** Nothing validates that a spoken status reflects the sources *right now*, distinguishes an empty result from a failed read, or survives followup questions without mutating the selected pane.
3. **No bounded delivery guarantee.** Long aggregation has no budget, no all-or-nothing reporting rule, and no persistent report with references the user can read on screen.

## Persona & User Journeys

**Persona:** the maintainer — hands-busy, voice-first, technically deep, intolerant of fabricated completeness and of agents that silently prefer the newest source when sources conflict.

| Journey | Expected experience |
|---|---|
| J1 Focus chat | "What's it doing?" answers about the **selected** session only; no other chat leaks in automatically. |
| J2 Global now | "What's the current state of my work?" → one consolidated brief per project: advances, pending, blockers; references on screen, never spoken. |
| J3 History | "What did I do this week?" → evidence from OpenCode/Claude/Antigravity chats and/or Engram as needed, resolved to the user's timezone periods. |
| J4 Followup | "And blockers?" / "More about that project?" → answers retain the last successful report's context without touching the Herdr selection, and re-check evidence when the question needs current facts. |
| J5 Failure | A needed source times out → the voice says it could not complete the report; it never presents a partial as complete. |

## Objectives & Non-Objectives

**Objectives**

- Keep the verified conversational focus model (selected session) as the default.
- Add on-demand global/historical retrieval with truthful status, timezone-correct periods, and evidence-grounded consolidated briefs.
- Add a persistent report database (24h retention) with freshness validation on every qualifying query and atomic full-report republication on change.
- Specify (not enable) a future autonomous sequential implementation loop over isolated Git worktrees, with deterministic gates and an actionable terminal blocked state.

**Non-Objectives**

- No automatic injection of all chats into every voice conversation (D01).
- No multi-agent orchestration, no new agent framework (D09).
- No new remote access; local data only (D09).
- No database export to Markdown/JSON; the user selected the database (D08).
- No merge/push/PR automation from the future implementation loop (D10).
- No voice-call-history mining as an additional historical corpus (D03).
- No SDD workflow adoption; SDD remains an optional future route the user may select (D10).

## Confirmed Decision Ledger

The product choices below were confirmed in the requesting conversation. Operational details such as atomic publication, revision checks, DST-safe arithmetic, and isolated execution are engineering constraints derived from those choices, not additional claims of explicit user approval. Every FR traces to one or more decisions.

| ID | Decision (confirmed) |
|---|---|
| D01 | Default voice conversation focuses the selected Herdr active session. Cross-chat consultation is on demand, never automatic injection of all chats. |
| D02 | Global work/current-progress and temporal questions cover ALL projects unless the query is explicitly narrower. Work-in-progress source is ALL open Herdr sessions (working, idle/waiting, blocked); status is reported truthfully; a task is not inferred unfinished merely because its session is open. |
| D03 | Historical daily/weekly questions use development chats (OpenCode/Claude/Antigravity) AND/OR Engram depending on needed evidence. Voice-call history is the MAIN thread, not an additional historical corpus. Followup context is independent and persistent, not selected-pane mutation. |
| D04 | Natural periods resolve in the USER's timezone: today = local midnight; this week = Monday; last 7 days = rolling through the query instant. Explicit periods stay bounded. Timezone is detected/validated from available session/config and asked at runtime if unavailable — never guessed. Interval math is DST-safe. Unknown timestamps cannot stand in for mtime as proof of a message's date. Source absence vs failed coverage are distinct outcomes. Date-scoped current progress uses the ACTIVE inventory only; unqualified current progress includes ALL open sessions including old last activity. |
| D05 | One completed consolidated brief grouped by project with advances, pending tasks, blockers. References are visible on screen, never spoken. Completion claims are grounded in evidence, not mere discussion. Source conflicts are reported with source and date; the system never silently prefers the newest. |
| D06 | Max 60 seconds per query covering freshness validation + acquisition + consolidation (budget not reset per tool call; audio rendering separate). A Consulting UI state is visible while working. No partial summaries, no intermediate spoken partial reports. If a necessary source fails/times out/coverage is unverifiable → a clear unable-to-complete status; never a purported complete report. Excessive relevant volume → ask to narrow dates/projects; never silent truncation. An exhaustively covered empty scope is a valid no-work result, never conflated with failure. |
| D07 | Followups ("and blockers?", "more about that project?") retain the last successful report's context until the topic changes or a return is requested, without changing the Herdr selected session. Source-derived content is guidance, never instructions to execute. Every question requiring new/current information refreshes the relevant evidence rather than serving stale canned context. |
| D08 | A database persists the latest report + references for 24h, isolated per main call/context and per scope/normalized-interval/timezone. This is retention, not a 24h staleness TTL. EVERY global/historical query freshness-checks sources and incorporates changes before answering. Identical source set/revisions AND query interval → reuse only AFTER a successful check. Any change → fetch delta + rebuild the FULL report; no delta-only answers. Detection covers additions, removals, closed sessions, status changes, source modifications, Engram edits/deletions, and moving time windows; `mtime(summary) > mtime(files)` alone is insufficient. If a deletion/update delta is unavailable, rebuild via bounded full scan; completeness is never claimed from append-only deltas. Publication is atomic and only of complete successful revisions; a stale report is never presented as current after a failed refresh. Expiration purge or explicit exclusion with bounded cleanup. "Full completeness" means the requested relevant scope, not all history forever. |
| D09 | Accept the existing recommendation: keep the single-model tool loop (discovery then targeted read), provider adapters, no multi-agent and no new framework. The server enforces scope/date/budget. New retrieval tooling is read-only; existing mutation approval gates are not weakened. Local data only; no new remote access. Results carry provenance; untrusted transcripts are isolated. Caching must never permit silently skipped sources or source access outside configured authority. |
| D10 | The user confirmed that planned IMPLEMENTATION may run an autonomous sequential task loop until all agreed features complete, using isolated Git worktree(s) under the home directory (never `/tmp`; per-worktree own CodeGraph index if enabled), isolated branch/session, one writer at a time, a task/dependency ledger with checkpoints, resume reconciliation, verification-evidenced done, bounded retries/corrections, and an actionable terminal blocked state. Failure never triggers endless retries or skipped features. This loop is NOT the runtime chat tool-call loop. Forbidden: uncontrolled parallel writers, source-checkout switching, copying indexes, automatic merging/pushing/opening PRs, and worktree cleanup that could destroy user changes. Existing TDD config is preserved; native RDD selection/consent stays user-owned; review is never self-approved. Documentation intent does not prove a live loop exists. This PRD authorizes only this document; implementation and worktrees require later explicit authorization. SDD is an optional future route, not a user-approved workflow. Deterministic milestone gates and an all-features acceptance checklist are required. Finite attempt defaults are implementation design within existing native authority — not invented native CLI commands or counters. |

## Architecture: Verified Current State vs Target Design

### Verified current state (source anchors, on-disk 2026-09-30)

Canonical root: `/home/bruno/Code/personal/agent-tts` (branch `feat/port-antigravity-transcript-reader`). Module root: `hosts/herdr/brain/`. Source anchors below are relative to that module unless a repository-relative path is given explicitly.

| Component | Anchor | Verified fact |
|---|---|---|
| Tool surface | `src/herdr_brain/tools.py:83` (`BrainTools`) | Dispatch map: `get_status`, `read_transcript`, `read_screen`, `send_to_session`, `create_session`. Reading views: `conversation()` 20-turn window (`tools.py:206`), `screen_full()` ~120 scrollback lines with viewport fallback (`tools.py:249`), `herd()` listing with per-agent last turn (`tools.py:154`). |
| Write paths | `tools.py:343`, `tools.py:378` | Exactly two: `send_to_session` and `create_session`; both deliver through `HerdrClient.send_prompt` with explicit delivery guarantees. |
| Conversation memory | `src/herdr_brain/memory.py:36` (`ConversationStore`) | In-process, thread-safe ring buffer per session id (16 messages, 4000 chars). **Not persistent.** |
| Call history | `src/herdr_brain/history.py:105` (`HistoryStore`) | Persists voice-call history; serves history APIs. |
| Approval gates | `src/herdr_brain/approval.py:66,78` | `ApprovalGate`/`ApprovalGateStore` gate mutations, wired into `llm.py` and `server.py`. |
| Transcript readers | `src/herdr_brain/transcripts.py:53,109` | `OpencodeTranscript`, `ClaudeTranscript`. Antigravity reader ported as WIP (commits `e000961`, `384dd4f`). |
| Watcher | `src/herdr_brain/watcher.py:116` (`AgentWatcher`) | Tracks agent panes; consumed by `server.py`. |
| Herdr client | `src/herdr_brain/herdr.py:134` | `HerdrClient` over the Herdr CLI; `HerdrError` (`herdr.py:33`) is the failure boundary. |
| Tests | `hosts/herdr/brain/tests/` | Suites for tools, memory, history, approval, transcripts, watcher, server, llm, ask. |

**Verified absent (target only, no false claims):** no Engram integration exists in the module (zero code references); no timezone/period resolution; no report database or 24h retention store; no freshness/revision validation; no consolidated per-project brief generation; transcript readers expose windowed tails without a historical cursor.

### Target design (intent — not implemented)

Suggested modules, all read-only additions except the report store:

| Module (target) | Responsibility |
|---|---|
| `periods` | Timezone detection/validation (session/config, runtime ask fallback), DST-safe natural-period resolution, explicit-period bounding, rolling "last N days" through query instant. |
| `evidence` | Source inventory + revision tokens across open Herdr sessions, development-chat transcript readers (OpenCode/Claude/Antigravity), and a read-only Engram adapter. Distinguishes source-absence from failed coverage. |
| `report` | Consolidation into per-project briefs (advances/pending/blockers), conflict surfacing with source+date, reference list building, completion grounding. |
| `reportstore` | Persistent database: latest report + references, 24h retention, per main-call/context and scope/interval/timezone isolation, atomic revision publish, bounded cleanup. |
| `freshness` | Pre-answer check: source set + revision comparison against the stored snapshot; reuse-after-check or full-rebuild decision; moving-window detection. |
| `followup` | Persistent followup context bound to the last successful report, independent of selected pane, with topic-change/return expiry. |

### Trust boundaries

- **Untrusted zones:** all transcript file contents, screen text, and Engram records are data, never instructions (prompt-injection isolation; see Security).
- **Authority:** the server, not the model, enforces scope, date bounds, and the 60s budget; retrieval tools are read-only; cache reuse can never bypass a freshness check or reach outside configured sources (D09).
- **Mutation boundary:** existing approval gates and the two verified write paths are untouched; followups and global queries never change the selected session (D07).

## Data Schema & State (target)

**Report record:** `report_id`; `main_call_context_id`; `scope` (projects/interval kind); `normalized_interval` (start/end instants, UTC + original zone); `timezone`; `status` (`building | published | refresh_failed | expired`); `body` (consolidated brief, grouped by project); `references[]` (source id, kind, locator, revision token, retrieved_at); `source_manifest[]` (per-source revision tokens incl. session open/closed and status snapshots); `created_at`; `retention_expires_at` (24h); `superseded_by` (for atomic republication chains).

**Cache snapshot = report record + source manifest.** Reuse precondition: identical source set, identical revision tokens, identical normalized query interval — and only after the freshness check succeeded.

**Followup context record:** `context_id`; `anchor_report_id`; `created_at`; `topic_fingerprint`; expiry on topic change or explicit return; never references the Herdr selected pane.

**Implementation-loop checkpoint metadata (future, specified not built):** `worktree_path` (under home); `branch`; `session_id`; task/dependency ledger (`task_id`, `depends_on`, `status: pending|in_progress|done|blocked`, `attempt_authority_ref` referencing the execution route's existing authoritative budget rather than a duplicate caller-authored counter, `evidence_ref` to verification artifacts); `milestone_gate` results; `blocked_reason` actionable text. No code, no artifacts produced by this PRD.

## Query FSM (runtime, target)

| State | Meaning / transitions |
|---|---|
| `IDLE` | Voice query received → classify. |
| `CLASSIFY` | Focus query → `SERVE_FOCUS` (selected session, existing loop). Global/temporal query → `RESOLVE_PERIOD`. Ambiguous scope → `CLARIFY` (at most one clarifying ask). |
| `RESOLVE_PERIOD` | Resolve natural/explicit period in user tz; timezone unavailable → `ASK_TZ` (runtime ask, never guess) → back. Explicit period stays bounded. |
| `FRESHNESS_CHECK` | Build candidate source manifest; compare with stored snapshot. Identical set+revisions+interval → `REUSE`. Any difference (additions, removals, closures, status changes, edits/deletions, window movement) → `ACQUIRE`. Delta unavailable for deletions/updates → `ACQUIRE` in bounded-full-scan mode. |
| `REUSE` | Serve stored report only after successful check → `RENDER`. |
| `ACQUIRE` | Read sources within remaining budget; a necessary source failing/timing out/coverage unverifiable → `UNABLE_TO_COMPLETE`. Excessive volume → `NARROW_ASK`. |
| `CONSOLIDATE` | Build full per-project brief with references and conflict surfacing; empty exhaustive coverage → valid no-work brief. |
| `PUBLISH` | Atomically store the complete revision → `RENDER`. |
| `RENDER` | One completed spoken report; references shown on screen. Consulting UI visible from `FRESHNESS_CHECK` until here. |
| `FOLLOWUP` | Question binds to last successful report; new/current-info questions re-enter `FRESHNESS_CHECK` for the relevant scope. Topic change/return → `IDLE`. |
| `UNABLE_TO_COMPLETE` / `NARROW_ASK` / `ASK_TZ` / `CLARIFY` | Terminal-for-this-turn user-facing states; no partial report is spoken. |

**Budget invariant:** one 60s wall-clock starts when the server accepts a global/historical query and includes classification, period resolution, freshness validation, acquisition, consolidation, and publication; it is never reset per tool call. Audio rendering happens after, outside the budget. Clarification ends that turn; the user's answer starts a new query. Expiry mid-flow → `UNABLE_TO_COMPLETE`. This report-specific deadline does not replace the existing selected-session conversation policy.

## Future Implementation Loop FSM (specified, not built; requires later explicit authorization)

Distinct from the runtime chat FSM above. `AUTHORIZE` (explicit user authorization of worktree root under home, branch, session) → `SELECT_NEXT_TASK` (ledger order, one writer at a time) → `EXECUTE` → `VERIFY` (evidence required for done) → on success `CHECKPOINT` → next task; on bounded correction `EXECUTE` (finite attempts); on exhaustion or unrecoverable error `BLOCKED` — terminal, actionable, never auto-retried, never skipped past. `MILESTONE_GATE` runs deterministic checks between milestones; final state `ALL_FEATURES_ACCEPTANCE` runs the checklist then `HALT_AWAIT_USER`. No automatic merge/push/PR; no source-checkout switching; no index copying; cleanup never destroys user changes.

## Failure, Cancellation, Concurrency & Budget Semantics

- **Failure:** necessary-source failure/timeout/unverifiable coverage → unable-to-complete, never a purported-complete report; empty-vs-failed are distinct outcomes (D06, D04).
- **Cancellation:** user cancels mid-query → no partial spoken report; any half-built revision is not published; the previously published report stays but is marked not-current after a failed refresh (D08).
- **Concurrency:** one writer per report key; atomic publish of complete revisions only; in-process conversation ring stays as-is; report store keyed by main call/context + scope + normalized interval + timezone (D08). Evidence MUST correspond to a recorded snapshot at a declared observation cutoff, not an impossible promise of continuous real-time completeness. A source revision changing during acquisition MUST be re-read within the remaining budget or cause unable-to-complete. A cancelled or superseded request MUST NOT overwrite a newer report or followup anchor.
- **Budget:** single 60s envelope per query; server-enforced; volume overflow → narrowing ask; no silent truncation (D06, D09).
- **Freshness failure:** a failed refresh must not present the old report as current (D08).

## Privacy & Security

- Retrieval is limited to authorized local sources; no new remote access, credentials, or destinations are introduced (D09). The existing configured model service remains in use; local retrieval does not imply local-only inference. Only relevant bounded evidence may be sent to that existing service, never raw credentials or indiscriminate corpus uploads.
- Prompt-injection isolation: transcript/screen/Engram content is untrusted data; consolidated output quotes it as evidence with provenance, never executes or relays embedded instructions (D07 "guidance never instructions", D09 provenance).
- Approval gates for existing write paths are preserved unchanged; new tooling is read-only (D09).
- Cache authority: reuse never silently skips sources nor accesses anything outside the configured source set (D09).
- No inspection or exfiltration of credentials/private env; report references expose locators, not secrets.

## Metrics & Acceptance (targets to be measured during implementation verification — no benchmark has been run)

| Metric | Target |
|---|---|
| Routing correctness (focus vs global/historical, scripted harness) | 100% on the deterministic false-routing suite |
| Budget compliance | No successful query path exceeds the 60s envelope (harness clock) |
| Freshness correctness | Zero staleness escapes across no-change/reuse, change/rebuild, deletion, window-rollover scenarios |
| Honesty | Zero partial reports presented as complete; zero empty-result/failure conflations |
| Followup isolation | Zero selected-pane mutations across all followup scenarios |
| Loop boundedness (future) | Zero unbounded retry cycles; every block terminal with actionable reason |

## Functional Requirements (RFC 2119)

| FR | Requirement | Refs |
|---|---|---|
| FR-01 | The default voice conversation MUST focus the selected Herdr active session. | D01 |
| FR-02 | Cross-chat consultation MUST be on-demand only; the system MUST NOT automatically inject other chats into the default conversation. | D01 |
| FR-03 | Global work/current-progress and temporal questions MUST cover all projects unless the query is explicitly narrower. | D02 |
| FR-04 | Work-in-progress answers MUST derive from all open Herdr sessions (working, idle/waiting, blocked), report status truthfully, and MUST NOT infer a task unfinished solely because its session is open. | D02 |
| FR-05 | Historical questions MUST use development chats (OpenCode/Claude/Antigravity) and/or Engram according to the evidence needed. | D03 |
| FR-06 | Voice-call history MUST be treated as the main thread only and MUST NOT be mined as an additional historical corpus. | D03 |
| FR-07 | Followup context MUST be independent and persistent, and MUST NOT mutate the selected pane. | D03, D07 |
| FR-08 | Natural periods MUST resolve in the user's timezone: today = local midnight; this week = Monday; last 7 days = rolling through the query instant; explicit periods MUST stay bounded. | D04 |
| FR-09 | The timezone MUST be detected/validated from available session/config; if unavailable the system MUST ask at runtime and MUST NOT guess. | D04 |
| FR-10 | Interval arithmetic MUST be DST-safe; unknown timestamps MUST NOT stand in for mtime as proof of a message's date. | D04 |
| FR-11 | Source absence and failed coverage MUST be distinguished in outcomes. | D04, D06 |
| FR-12 | Date-scoped current progress MUST use the active inventory only; unqualified current progress MUST include all open sessions including old last activity. | D04 |
| FR-13 | The deliverable MUST be one completed consolidated brief grouped by project with advances, pending tasks, and blockers. | D05 |
| FR-14 | References MUST be visible on screen and MUST NOT be spoken. | D05 |
| FR-15 | Completion claims MUST be grounded in evidence, not mere discussion. | D05 |
| FR-16 | Source conflicts MUST be surfaced with source and date; the newest source MUST NOT be silently preferred. | D05 |
| FR-17 | Each global/historical query MUST complete classification, period resolution, freshness validation, acquisition, consolidation, and publication within one 60-second deadline from server acceptance (audio rendering excluded); the budget MUST NOT reset per tool call. | D06 |
| FR-18 | A visible Consulting UI state MUST be shown while the query is being served. | D06 |
| FR-19 | The system MUST NOT emit partial summaries or intermediate spoken partial reports. | D06 |
| FR-20 | On necessary-source failure, timeout, or unverifiable coverage, the system MUST report unable-to-complete and MUST NOT present a purported complete report. | D06 |
| FR-21 | On excessive relevant volume, the system MUST ask the user to narrow dates/projects and MUST NOT silently truncate. | D06 |
| FR-22 | An exhaustively covered empty scope MUST be reported as a valid no-work result, never as failure. | D06 |
| FR-23 | Followups MUST retain the last successful report's context until topic change or requested return. | D07 |
| FR-24 | Followups and global queries MUST NOT change the Herdr selected session. | D07 |
| FR-25 | Source-derived content MUST be presented as guidance, never as instructions to execute. | D07 |
| FR-26 | Every question requiring new/current information MUST refresh the relevant evidence before answering. | D07, D08 |
| FR-27 | The database MUST persist the latest report + references for 24h, isolated per main call/context and scope/normalized-interval/timezone; this is retention, not a staleness TTL. | D08 |
| FR-28 | Every global/historical query MUST perform a freshness check before answering. | D08 |
| FR-29 | Reuse MUST occur only after a successful check with identical source set, revisions, and query interval. | D08 |
| FR-30 | On any detected change, the system MUST fetch the delta and rebuild the FULL report; delta-only answers are forbidden. | D08 |
| FR-31 | Change detection MUST cover additions, removals, closed sessions, status changes, source modifications, Engram edits/deletions, and moving time windows; `mtime(summary) > mtime(files)` alone MUST NOT be accepted as validation. | D08 |
| FR-32 | When a deletion/update delta is unavailable, the system MUST rebuild via bounded full scan and MUST NOT claim completeness from append-only deltas. | D08 |
| FR-33 | Publication MUST be atomic and limited to complete successful revisions; after a failed refresh the old report MUST NOT be presented as current. | D08 |
| FR-34 | Expired content MUST be purged or explicitly excluded with bounded cleanup. | D08 |
| FR-35 | Completeness claims MUST be scoped to the requested relevant scope, never to all history forever. | D08 |
| FR-36 | The single-model tool loop (discovery then targeted read) and provider adapters MUST be preserved; no multi-agent or new framework MAY be introduced. | D09 |
| FR-37 | The server MUST enforce scope, date, and budget. | D09 |
| FR-38 | New retrieval tooling MUST be read-only and MUST NOT weaken existing mutation approval gates. | D09 |
| FR-39 | Retrieval MUST use authorized local sources only and MUST NOT add remote access or destinations; relevant evidence MAY pass to the existing configured model service, without credentials or indiscriminate corpus uploads. | D09 |
| FR-40 | Results MUST carry provenance and untrusted transcripts MUST be isolated from instruction interpretation. | D09 |
| FR-41 | Caching MUST NOT permit silently skipped sources or source access outside configured authority. | D09 |
| FR-42 | Any future autonomous implementation loop MUST run only after explicit authorization, sequentially, one writer at a time, in isolated Git worktree(s) under the home directory (never `/tmp`), each with its own CodeGraph index if enabled, on an isolated branch/session. | D10 |
| FR-43 | The loop MUST maintain a task/dependency ledger with checkpoints and resume reconciliation; done MUST be verification-evidenced; retries/corrections MUST be bounded with finite attempt defaults; the blocked state MUST be terminal and actionable; features MUST NOT be skipped and failures MUST NOT loop endlessly. | D10 |
| FR-44 | The loop MUST NOT automatically merge, push, or open PRs; MUST NOT switch the source checkout, copy indexes, run parallel uncontrolled writers, or clean worktrees in ways that destroy user changes; existing TDD configuration MUST be preserved and native RDD selection/consent stays user-owned with no self-approved review. | D10 |
| FR-45 | Implementation progress MUST pass deterministic milestone gates and a final all-features acceptance checklist before being reported complete. | D10 |

## Requirement-to-Test Matrix

Primary: scripted deterministic harness (fake clock, fixture sources with injected revisions/failures). Secondary (optional): real-model evaluation set for routing and consolidation quality. **No test has been run for this PRD; the matrix defines required coverage.**

| Scenario cluster | Coverage | FRs |
|---|---|---|
| False routing | Focus phrasing never triggers global acquisition; global phrasing never silently narrows to the selected pane; ambiguity yields at most one clarify | FR-01..03 |
| Tool off-path | A global/historical answer produced without retrieval evidence MUST be detected as a violation (no current-info claim without fresh acquisition) | FR-26, FR-28 |
| Freshness: no-change | Identical manifest+interval → reuse after successful check; served report identical provenance | FR-28..31 |
| Freshness: deletions | Source removal/Engram deletion with no delta available → bounded full scan rebuild; append-only delta never claimed complete | FR-30, FR-32, FR-35 |
| Time rollover | "Last 7 days" across query instants and DST transitions; Monday/today-midnight boundaries in user tz; unknown-timestamp vs mtime proof | FR-08..10, FR-31 |
| Conflicts | Two sources disagree → both surfaced with source+date; newest not silently preferred | FR-16 |
| Timeout/incomplete | Injected source timeout mid-budget → unable-to-complete; no partial spoken; old report not presented as current | FR-17, FR-19, FR-20, FR-33 |
| Volume | Oversized fixture corpus → narrowing ask; no truncation | FR-21 |
| Empty vs failed | Exhaustive empty scope → no-work result; unreachable source → failure; never conflated | FR-11, FR-22 |
| Ref isolation | Spoken output contains no references; screen artifact contains all | FR-14 |
| Followup isolation | Followup battery with pane-selection assertions: zero selected-session changes; topic change expires context; new-info question re-checks evidence | FR-23..26 |
| Status truthfulness | Open-but-idle/blocked sessions reported truthfully; no "unfinished" inference from open state | FR-04, FR-12 |
| Restart | Service restart mid-build → no partial revision published; resume reconciles from checkpoints (loop FSM) | FR-33, FR-43 |
| Worktree isolation (future loop) | Harness asserts single writer, no checkout switch, no index copy, no merge/push/PR, bounded attempts, terminal blocked state | FR-42..45 |
| Approval gates | Existing mutation paths still gated after changes | FR-38 |
| Historical provider coverage | Enumerate and read fixture conversations from all supported providers and project scopes; choose chats/Engram according to query evidence needs; voice-call turns are excluded from the external corpus | FR-03, FR-05, FR-06, FR-35 |
| Timezone fallback | Unavailable/invalid timezone produces a clarification, never an inferred zone; daily/weekly bounds are server-validated | FR-08..12, FR-37 |
| Consolidated output | Every represented project has grounded advances/pending/blockers or an explicit evidence-based absence; no completion inferred from discussion; visible Consulting state and references | FR-13..19 |
| Persistent context and retention | Restart restores an unexpired report and followup anchor without changing selection; expiration prevents reuse; context/scope keys prevent cross-call leakage; bounded cleanup executes | FR-07, FR-23, FR-24, FR-27, FR-34 |
| Revision race and cancellation | Mid-read revision change forces re-read or failure inside the shared deadline; older/cancelled requests cannot publish over a newer report or anchor | FR-17, FR-28..33, FR-41 |
| Trust and authority | Transcript/Engram injection fixtures cannot invoke writes or expand configured authority; local retrieval adds no remote destination; result provenance survives consolidation | FR-25, FR-36..41 |

## Coverage Definition & Completeness Boundary

"Complete" means: every source in the **configured authority for the requested scope and interval** was successfully checked or read within the budget, with revision tokens recorded. It does **not** mean all history forever, sources outside the configured set, or intervals beyond the requested bounds. A report may honestly state which sub-scopes were empty (valid) versus unreadable (failure). No claim in this PRD asserts coverage of "everything".

## Verified Gaps vs Current Code (honest inventory)

1. No Engram adapter exists anywhere in the module (verified by search) — FR-05 is target work.
2. No timezone/period resolution exists — FR-08..10 are target work.
3. No report database, retention, or atomic revision machinery exists — FR-27..35 are target work.
4. No freshness/revision comparison exists — FR-28..33 are target work.
5. No consolidated per-project brief generation exists — FR-13..16 are target work.
6. Conversation memory is in-process and bounded (`memory.py:36`); persistent followup context (FR-23) is target work.
7. Transcript readers expose windowed tails (`tools.py:206` documents the 20-turn window; no historical cursor) — historical depth reads are target work.
8. The Antigravity transcript reader is WIP on the current branch (commits `e000961`, `384dd4f`); its completion is prerequisite evidence for FR-05.
9. No autonomous implementation loop was established by the inspected brain-module evidence; FR-42..45 specify future development execution, not a new voice-runtime loop. Existing external execution tooling must be assessed during implementation planning; this PRD is not proof that a loop already works.

## Known Constraints

- The 60s budget is wall-clock server-side; slow sources degrade to unable-to-complete by design.
- Timezone availability depends on session/config surfaces; the runtime ask (FR-09) is the fallback, so first-use friction is possible.
- Reading views without a cursor bound how much history a single targeted read can return; deep historical queries need the new evidence layer, not the existing views.
- Voice-call history remains main-thread only, so historical answers cannot lean on it as corpus (FR-06).

## Verification Checklist (for the future implementation; nothing has been run)

- [ ] Deterministic harness green across every cluster in the Requirement-to-Test Matrix
- [ ] Optional real-model eval set executed with recorded scores (no invented baselines)
- [ ] Milestone gates deterministic and recorded per milestone
- [ ] All-features acceptance checklist executed end-to-end
- [ ] No mutation-approval regression (existing gated paths still gated)
- [ ] Report store retention/cleanup verified bounded under sustained use
- [ ] Followup pane-isolation asserted across the full followup battery

## Assumptions (honest, non-blocking)

- A-1: A user timezone is obtainable from an existing session/config surface in most sessions; otherwise FR-09's runtime ask covers the remainder. Not yet verified against a specific config key.
- A-2: Engram exposes a local read path suitable for a read-only adapter; no integration exists today and the access mechanism is implementation design.
- The 60s deadline excludes only audio rendering; this is a specified constraint, not an assumption or a measured performance result.
- A-4: The existing single-model loop can host the new read-only tools without architecture change, per the accepted recommendation (D09); tool-count growth may need prompt-side curation.

## Authorization Boundary

This document records product intent only. Creating it authorized exactly one file write. Implementation, worktrees, branches, commits, pushes, PRs, and remote operations all require separate, explicit user authorization (D10). The future loop described here is a specification, not a running system, and nothing in this PRD may be cited as proof that it exists.
