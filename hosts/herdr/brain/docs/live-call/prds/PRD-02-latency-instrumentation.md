# PRD-02: Latency Instrumentation and Baseline

| Field | Value |
|---|---|
| **Product** | `herdr-brain` (PWA / Service) |
| **Document Type** | PRD (Roadmap — baseline) |
| **Status** | `draft` |
| **Date** | 2026-11-03 |
| **Depends on** | PRD-00 (stable turn semantics: continuation window, engine choice) |
| **Composed Skills** | `prd`, `cognitive-doc-design` |
| **Canonical Entrypoint** | `hosts/herdr/brain/docs/live-call/prds/PRD-02-latency-instrumentation.md` |
| **Target Repository Root** | `/home/bruno/Code/personal/agent-tts` |

---

## 1. Context & Problem

Today's turnaround latency is known only as anecdotes (the hands-free PRD cites "3s–6s" as the pre-split state, `docs/PRD-hands-free-voice-call.md` §1). No stage of the pipeline records how long it takes:

- The client knows when VAD closes the turn and when `/ask` is sent (`app.js:5231`, one `/ask` per turn) but keeps only counters in the debug panel (`app.js:3855`).
- The server runs `/transcribe` in an executor (`server.py:1459-1460`) and a synchronous GLM tool loop inside `/ask`, then sentence-split TTS with protocol-2 segment delivery (`server.py:2175-2176`) — none of the stages are timed end to end.
- The one number that matters to the user — **end of speech → first audible audio** — is measured nowhere.

Every later roadmap decision (PRD-09's buy comparison, PRD-03..07's improvement targets) needs this baseline. Building streaming first and measuring later would make "did it help?" unanswerable.

## 2. Objectives & Non-Objectives

### Objectives
- Record per-stage, per-turn timestamps across client and server.
- Surface end-of-speech → first-audio as a first-class number (debug panel + call history).
- Produce a written baseline on the Pixel 7 Pro before PRD-03 starts.

### Non-Objectives
- Any behavioral change to the call pipeline (this PRD only observes).
- Dashboards, alerting, or long-term storage beyond the existing call history entries.
- Measuring TTS provider internals or GLM provider internals beyond request-span durations.

## 3. Functional Requirements

- **FR-01**: The client MUST record, per turn, at least: `t_silence_close` (VAD closes the utterance), `t_ask_sent`, `t_first_audio_started` (first protocol-2 segment actually begins playback via the existing segmentPlayer path, `speech.js:167`), and `t_audio_ended`.
- **FR-02**: The client MUST compute and display `t_first_audio_started − t_silence_close` in the debug panel alongside the existing call counters (`app.js:3843-3863`).
- **FR-03**: The server SHOULD attach per-stage durations to its logs and response metadata for `/transcribe` (queue wait vs transcription time) and `/ask` (LLM tool-loop time, first-sentence TTS ready time, first segment available time at `/speech/{id}/next`, `server.py:2270`).
- **FR-04**: Each call history entry MUST persist the turn's stage timings so a session can be reviewed after the fact.
- **FR-05**: Timings MUST be exportable as JSON from the debug panel (copy button) for attaching to PRDs and the roadmap baseline.
- **FR-06**: Metrics MUST contain durations only — no audio, no transcript text, no tokens (privacy of call content).
- **FR-07**: Instrumentation MUST be negligible-cost (timestamps and arithmetic; no extra network round-trips beyond an optional header/field on existing requests).

## 4. Acceptance Criteria

1. After one call turn on desktop, the debug panel shows a non-null end-of-speech → first-audio value and the JSON export contains all stages for every turn.
2. A call history entry reopened later still shows the timings.
3. With instrumentation compiled out/hidden, no test in the existing suites changes behavior (pure observation).

## 5. Verification & Acceptance Strategy (V1–V3)

- **V1 — unit (node)**: the timing module as a pure JS test (stage recording, computed deltas, JSON export shape, null handling for aborted turns) in `tests/js/`.
- **V2 — integration (pytest + Playwright fake audio)**: a browser-harness turn with stubbed server delays asserts each stage timestamp is recorded in order and the panel shows the expected delta (`tests/browser/conftest.py:496-517` fixtures).
- **V3 — manual baseline on Pixel 7 Pro**: a scripted 10-turn session (short questions, long answers, one barge-in); results written into this PRD's §7 and the ROADMAP §6 exit criteria as the official baseline numbers.

| FR | V1 | V2 | V3 | Scenario |
|---|---|---|---|---|
| FR-01/02 | ✔ | ✔ | ✔ | Stage timestamps + first-audio delta |
| FR-03 | — | ✔ (TestClient) | ✔ | Server stage durations |
| FR-04 | — | ✔ | ✔ | History persistence |
| FR-05 | ✔ | ✔ | — | JSON export |
| FR-06 | ✔ | ✔ | — | Durations only |
| FR-07 | — | ✔ | ✔ | No added round-trips |

## 6. Risks

- **Clock skew** between client and server timestamps if stages are compared across machines: mitigate by keeping user-facing deltas client-side (FR-01/02) and server durations server-side (FR-03); never subtract across clocks.
- **History schema growth**: call history entries gain optional fields; migration must tolerate old entries without timings (additive change only).

## 7. Open Questions

1. Exact baseline numbers — filled in by the V3 session (intentionally blank; do not invent).
2. Should server stage durations ride the existing response payloads or a debug header? (Implementation detail; additive either way.)

## 8. Execution Boundaries

Instrumentation only (FR-01..FR-07). Any latency improvement discovered as "obvious" while implementing belongs to its own PRD, not to silent fixes here. This PRD is a `draft`; launching implementation is a separate explicit decision.
