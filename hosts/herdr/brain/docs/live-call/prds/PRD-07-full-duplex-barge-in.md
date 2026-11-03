# PRD-07: Full-Duplex Barge-In with Heard-Watermark History

| Field | Value |
|---|---|
| **Product** | `herdr-brain` (PWA / Service) |
| **Document Type** | PRD (Roadmap Phase 5 — streaming build) |
| **Status** | `draft` |
| **Date** | 2026-11-03 |
| **Depends on** | PRD-05 (server-side user-speech detection during playback), PRD-06 (streaming answer to cancel) |
| **Composed Skills** | `prd`, `cognitive-doc-design` |
| **Canonical Entrypoint** | `hosts/herdr/brain/docs/live-call/prds/PRD-07-full-duplex-barge-in.md` |
| **Target Repository Root** | `/home/bruno/Code/personal/agent-tts` |

---

## 1. Context & Problem

Barge-in today is conditional and client-only:

- It exists only when the capture track reports echo cancellation (`tracks[0].getSettings().echoCancellation === true`, `app.js:5663`); otherwise the call is half-duplex — the mic is off during `/ask` and TTS (`app.js:24`).
- When it fires, the client aborts the in-flight `/ask` via `AbortController` (`app.js:4913`, FR-05 of the hands-free PRD) and stops segment playback (`segmentPlayer.stop("barge-in")`, `app.js:5700`).

What is missing for a Gemini-Live-class call:

1. **Server-side detection**: with the PRD-03 socket and PRD-05 VAD running continuously, the server can see user speech during playback without depending on the client's AEC report.
2. **Coordinated cancellation**: the server must cancel GLM generation and pending TTS jobs, not just the client's fetch.
3. **Honest history**: when the user interrupts at sentence 3 of 8, the conversation history must reflect only what was actually heard — not the full eight sentences. Protocol-2 already maintains a per-job segment watermark acked by the client (`server.py:785`, `/speech/{id}/next` acks, `speech.js:322`), which is exactly the needed "heard up to here" signal.

Safety invariant from the hands-free PRD, restated because it is load-bearing here: barge-in cancels audio and generation, but MUST NEVER automatically resolve or dismiss a pending `ApprovalGate`.

## 2. Objectives & Non-Objectives

### Objectives
- Continuous user-speech monitoring during answer playback (server-side VAD over the open uplink).
- On barge-in: cancel LLM generation and TTS jobs server-side; client flushes playback (existing path).
- Truncate conversation history to the heard watermark at interruption time.
- Echo protection without relying solely on AEC availability.

### Non-Objectives
- Continuous overlapped speech (agent and user talking simultaneously at length); barge-in is a mode switch, not a mixer.
- In-browser acoustic echo cancellation DSP (AEC remains the browser's `echoCancellation` constraint, `app.js:5594-5595`).
- Any change to approval-gate resolution rules.

## 3. Functional Requirements

- **FR-01**: During answer playback the uplink MUST remain open and feed server-side speech detection (PRD-05 VAD); the client MUST NOT stop the microphone while the agent speaks on the streaming path.
- **FR-02**: On detected user speech during playback, the server MUST cancel in-flight LLM generation and pending/ongoing TTS jobs for that turn (`/speech/{id}/cancel`, `server.py:2244`), and notify the client, which flushes playback via the existing barge-in path (`app.js:5700`).
- **FR-03**: At interruption, the turn's stored answer in conversation history MUST be truncated to the heard watermark — the sentence-level playback ack the client already sends to `/speech/{id}/next` (`server.py:785`, `speech.js:322`) — plus (ASSUMPTION, to validate) the sentence currently mid-playback, since it was partially heard.
- **FR-04**: Barge-in MUST NOT resolve, dismiss, or alter any pending `ApprovalGate`; a gated turn interrupted mid-question simply re-awaits the gate (invariant from `docs/PRD-hands-free-voice-call.md` §3).
- **FR-05**: Echo protection MUST work in two tiers: (a) primary — the existing AEC-enabled capture path (`app.js:5663`); (b) fallback — when AEC is not reported, server-side heuristics combining the playback watermark (the server knows which segment is audible when) with VAD framing, tuned to avoid self-trigger; the half-duplex mic-off behavior remains the last resort if the fallback proves unreliable on device.
- **FR-06**: Barge-in cancellation latency (user speech onset → playback actually silent) SHOULD be imperceptible (budget ASSUMPTION: ≤ 300 ms, aligned with the hands-free harness's < 100 ms client pause assertion plus network; measured via PRD-02 tooling).
- **FR-07**: All barge-in events MUST be logged with durations (detection→cancel, heard watermark) without storing audio or transcript text in exported metrics.

## 4. Acceptance Criteria

1. On the Pixel 7 Pro with speaker on, saying "para" mid-answer stops the answer within the FR-06 budget; the next turn's context shows only the heard prefix (inspect conversation history).
2. Without AEC (fallback tier enabled by flag), the agent's own voice does not trigger self-cancellation across a 10-answer session; a real interruption still cancels.
3. An approval-gated answer interrupted mid-speech leaves the gate pending and resolvable exactly as before.
4. The existing closed-loop barge-in browser test still passes, extended with the server-cancel assertion.

## 5. Verification & Acceptance Strategy (V1–V3)

- **V1 — unit (pytest + node)**: watermark truncation logic (fixture watermarks + interruption points → truncated answers); tier-b echo heuristic on scripted VAD frames + playback timelines; determinism.
- **V2 — integration (pytest + Playwright)**: closed-loop barge-in, extending the existing harness pattern (`tests/browser/test_barge_in.py`, fake audio per `tests/browser/conftest.py:496-517`): start answer with stubbed multi-segment TTS, inject user-speech frames, assert server cancel (job state + no further segments), client flush, history watermark, and gate preservation.
- **V3 — manual on Pixel 7 Pro**: speaker-on interruptions (§4.1), no-AEC fallback session (§4.2), gate case (§4.3); timings recorded via PRD-02.

| FR | V1 | V2 | V3 | Scenario |
|---|---|---|---|---|
| FR-01 | — | ✔ | ✔ | Mic stays open during playback |
| FR-02 | — | ✔ | ✔ | Server cancels LLM+TTS |
| FR-03 | ✔ | ✔ | ✔ | History truncated to watermark |
| FR-04 | — | ✔ | ✔ | Gate never auto-resolved |
| FR-05 | ✔ | ✔ | ✔ | Two-tier echo protection |
| FR-06 | — | ✔ | ✔ | Cancellation latency budget |
| FR-07 | ✔ | — | — | Event logging, no content export |

## 6. Risks

- **Self-trigger without AEC** is the central risk: tier-b heuristics may be unreliable on real speaker audio; the explicit last resort is falling back to half-duplex (today's behavior) — honesty over cleverness.
- **Watermark accuracy**: ack latency may lag actual audibility by a segment fraction; FR-03's "plus current sentence" absorbs it (ASSUMPTION validated in V3 by history inspection).
- **Race between cancel and next-turn dispatch**: the interrupted user's new utterance must not race the cancellation; ordering is asserted in V2.

## 7. Open Questions

1. Does tier-b (no-AEC) pass the 10-answer no-self-trigger session on device, or does half-duplex remain the default without AEC? (Device-dependent; decided by V3 evidence.)
2. Exact FR-06 budget after measurement (300 ms is an ASSUMPTION).

## 8. Execution Boundaries

Barge-in composition only (FR-01..FR-07) on top of PRD-05 and PRD-06. No mixing, no DSP, no gate changes. Out-of-scope discoveries go to `## Escalated / Out-of-Scope Issues`. This PRD is a `draft` behind the ROADMAP §7 gate; launching implementation is a separate explicit decision.
