# Hands-free conversational voice call
Implement the approved voice-call PRD in an isolated worktree; preserve the original checkout and all its uncommitted changes.
Branch: `feat/hands-free-voice-call`; baseline: `4b13d75293439f753464ef3df9765fa51d5947d9`; delivery: `single-pr` (local work units only, no publication); runners: Node test runner and existing Brain Python environment.

## Specs

Source: the complete 411-line `hosts/herdr/brain/docs/PRD-hands-free-voice-call.md` in the original checkout. Requirements below are quoted verbatim from that input; the untracked input is not silently incorporated into commits.

- S1 / FR-01: "The system MUST support adaptive conversational endpointing with a silence threshold of 700 ms to 900 ms (default 800 ms), configurable without breaking the 60-second safety hard cap."
- S2 / FR-02: "The `/ask` endpoint MUST support a first-sentence audio split mode (`conversation: true`), returning text and synthesized audio for the first sentence immediately, while queuing remainder synthesis."
- S3 / FR-03: "The PWA client MUST support real-time audio playback queuing that seamlessly chains the first-sentence MP3 with subsequent segment MP3s without audible clicks or stutter."
- S4 / FR-04: "The client MUST keep the VAD active during audio playback (when AEC is enabled), emitting a barge-in event when user speech is detected."
- S5 / FR-05: "On barge-in detection, the client MUST immediately abort in-flight `/ask` fetch requests via `AbortController`, flush the playback queue, and return to `listening`."
- S6 / FR-06: "Server-side `/ask` handling MUST detect client disconnection and cleanly terminate ongoing LLM generation or TTS child processes to prevent orphan compute waste."
- S7 / FR-07: "The testing harness MUST support fully autonomous, reproducible audio round-trips (synthetic WAV input injection via Chromium fake media flags and programmatic playback verification via HTMLMediaElement events) without requiring physical microphones or speakers."
- S8 / FR-08: "When an agent displays an interactive terminal menu or question dialog (detected via ANSI attributes in `read_screen`), the assistant MUST translate the options into a concise, speakable verbal prompt (e.g. \"Claude asks whether to run tests: Yes or No?\")."
- S9 / FR-09: "The system MUST allow the user to resolve interactive terminal prompts purely by voice (e.g., \"dile que sí\", \"selecciona la opción 1\"), dispatching the corresponding keys via `send_keys_to_pane` without requiring manual keypad touches."
- S10 / FR-10: "Background announcements received via SSE MUST NOT interrupt or cancel active user turns or playback; they MUST queue visually and speak only during conversational idle periods."
- S11 / FR-11: "The voice response style in `SYSTEM_PROMPT` MUST prioritize executive audio summaries (problem impact, tests passed/failed, required decision) and explicitly offer the recommended next action."
- S12 / FR-12: "The `herdr.brain` / `herdr.tts` plugin configuration MUST expose a desktop voice interface command/pane (`herdr brain call`), enabling identical call functionality directly on the workstation desktop."
- S13 / FR-13: "Desktop and mobile call sessions MUST share the same session context, agent inventory, and approval queue, allowing seamless handover between phone and laptop."
- S14 / user-confirmed security policy: "«Adelante» autoriza **solo la acción concreta que Panel acaba de explicar**, vinculada a su `gate_id`." "Si la referencia es ambigua o la acción ha cambiado, Panel pide aclaración y **no ejecuta nada**." "Una interrupción de voz nunca cuenta como aprobación." The user's answer was "si".
- S15 / execution authorization: "si no hay mas preguntas, inicia el loop y no pares hasta terminar". Continue authorized local implementation and checks; never infer remote, paid, publishing, destructive-operation or candidate-review consent.

Acceptance and checks: retain the PRD's G-01 through G-12 commands and run each from this worktree's `hosts/herdr/brain`, substituting the already installed Python environment's interpreter where the new worktree has no `.venv`. Set `PYTHONPATH=src` to ensure tests import this worktree, not the environment's editable original checkout. V1 is deterministic Node/Python logic; V2 is injected FastAPI/tool/CLI integration; V3 uses Chromium fake capture and real HTMLMediaElement events. Barge-in must pause playback within 100 ms, cancel synthesis, preserve approval gates and capture the new utterance. Observe RED before meaningful behavior changes, then GREEN; use external temporary directories for scratch/cache/artifacts. Run compatibility/differential checks for touched shared interfaces and independent verification for named high-risk items. No physical device or real-provider proof may be invented.

## Tasks

- T-01 [S7] inline; completed; G-01 RED missing fixture, GREEN 13 browser tests (native capture/playback plus existing harness); commit: pending.
- T-02 [S1] inline; in_progress; 800 ms conversational endpointing with 60-second cap; depends T-01; G-02; commit: pending.
- T-03 [S2,S6] inline; pending; compatible first-sentence synthesis and cancellation transport; depends T-02; G-03; commit: pending.
- T-04 [S3] inline; pending; ordered first/remainder playback; depends T-03; G-04; commit: pending.
- T-05 [S4,S5,S14] inline; pending; echo-safe VAD barge-in and client abort; depends T-04; G-05; commit: pending.
- T-06 [S6] inline; pending; request-disconnect cleanup of LLM/TTS work; depends T-05; G-06; commit: pending.
- T-07 [S8] inline; pending; speakable terminal-dialog choices; depends T-06; G-07; commit: pending.
- T-08 [S9,S14] inline; pending; safe voice-to-key mapping and exact explained-gate approval binding; depends T-07; G-08 plus approval API/browser tests; commit: pending.
- T-09 [S10] inline; pending; non-stealing announcement queue; depends T-08; G-09; commit: pending.
- T-10 [S11] inline; pending; executive audio-first summaries; depends T-09; G-10; commit: pending.
- T-11 [S12] inline; pending; desktop `call` command/action and help; depends T-10; G-11; commit: pending.
- T-12 [S12,S13] inline; pending; desktop audio/context bridge and handover; depends T-11; G-12 plus call-history/shared-approval checks; commit: pending.

## Log

- L1 / original request: "usa subagente glm-5.3 para abordar la siguiente prd, leela completa y confirma que la entiendes al 100% hosts/herdr/brain/docs/PRD-hands-free-voice-call.md"
- L2 / clarification request: "antes de empezar tienes alguna duda que pueda hacer parar el loop durante la ejecucion?" The user subsequently answered "si" to the S14 policy.
- L3 / launch request: "si no hay mas preguntas, inicia el loop y no pares hasta terminar"
- L4 / baseline: original `main` contains pre-existing modified source/tests and untracked Mermaid assets/PRD, and changed between observations. Created sibling worktree `/home/bruno/Code/personal/agent-tts-worktrees/hands-free-voice-call` at the committed baseline without copying or committing those unrelated changes. Original checkout remains untouched.
- L5 / route: all implementation is inline because the pack's explicit dependency chain supplies no independent parallel writer units. A read-only explorer mapped existing seams to choose a compatible implementation; long suites may use fresh per-action GLM-5.3 workers, and named security/concurrency/shared-contract changes require an independent verifier. User-requested GLM-5.3 completed the initial full PRD reading.
- L6 / existing seams: reuse `SpeechRegistry`, `SegmentedSpeechProducer`, speech next/cancel endpoints, `ApprovalGateStore`, `ApprovalFlow`, `send_keys_to_pane` and existing browser route isolation. Lexical recognition of "adelante" does not by itself prove exact last-explained-gate binding.
- L7 / reconciliations: interruption first recovers `listening`, then speech onset enters `recording`; use one canonical fake-capture fixture and refer to it consistently; keep G-12 plus the matrix's call-history tests; T-08 owns the previously unassigned verbal-approval acceptance criterion. Extend only the approval client/store/API/test surfaces necessary for the explicitly confirmed S14 policy.
- L8 / risk: items 2 (approval boundaries), 3 (shared HTTP/CLI/client contracts), 4 (speech cancellation/queue ordering), and 6 where synthetic browser evidence cannot establish acoustic quality. RDD is `off`, decided by `clone_local`; do not enable it or launch native review.
- L9 / delivery: forecast approximately 2,200 authored changed lines, excluding generated audio fixtures; keep coherent local work-unit commits with behavior/tests/docs. Strategy is a single eventual PR candidate, not permission to publish one. Independent high-risk units keep separate rollback boundaries.
- L10 / budgets: maximum two functional remediation rounds per milestone. Stop with one concrete decision on exhausted proof, crash, feedback loop, possible approval bypass or required consent; never claim completion while a required gate remains failed, skipped or pending.
- L11 / baseline checks: Node exit 0 (371 dot-reporter cases); Python 1861 passed, four pre-existing `tests/e2e` failures: `test_m4_fallback.py::test_fake_providers_e2e[chromium]`, `test_m4_fallback.py::test_cancel_during_fallback_zero_further_attempts[chromium]`, `test_m4_fallback.py::test_no_duplicate_submissions[chromium]`, and `test_m3_pending.py::test_kill9_host_pending_recovers_uncertain_no_replay`. These are known baseline failures, not permission to ignore new failures.
- L12 / T-01: generated reproducible 48 kHz PCM in external pytest scratch, launched Chromium with all three fake-media flags and kept capture, recording, decoder and media events native. G-01 and the existing browser-harness suite passed together (13 tests, 2.06 s). The pure signal proves deterministic audio I/O, not speech recognition accuracy or physical echo cancellation. Browser artifacts now default outside the checkout. Risk: none for the isolated test-only harness.
