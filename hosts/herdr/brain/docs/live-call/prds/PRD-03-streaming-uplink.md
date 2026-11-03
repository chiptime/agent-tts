# PRD-03: Streaming Uplink — WebSocket `/call/stream`

| Field | Value |
|---|---|
| **Product** | `herdr-brain` (PWA / Service) |
| **Document Type** | PRD (Roadmap Phase 1 — streaming build) |
| **Status** | `draft` |
| **Date** | 2026-11-03 |
| **Depends on** | Decision gate (PRD-09, ROADMAP §7), PRD-02 (before/after measurement) |
| **Composed Skills** | `prd`, `cognitive-doc-design` |
| **Canonical Entrypoint** | `hosts/herdr/brain/docs/live-call/prds/PRD-03-streaming-uplink.md` |
| **Target Repository Root** | `/home/bruno/Code/personal/agent-tts` |

---

## 1. Context & Problem

Today's uplink is batched: MediaRecorder webm/opus segments rotate every 45 s and each is POSTed whole to `/transcribe` (`app.js:212-222`, `app.js:5228-5231`); nothing reaches the server until a segment closes. The server has no WebSocket endpoint at all (verified by search over `server.py`), and authentication today uses a `device_token` query parameter with journal scrubbing (S11.2, `server.py:236-259`, `server.py:3824-3825`).

PRD-04 (streaming STT) and PRD-05 (semantic end of turn) both need continuous audio at the server with sub-second transport latency. This PRD builds only that pipe.

## 2. Objectives & Non-Objectives

### Objectives
- A WebSocket uplink `/call/stream` carrying PCM16 16 kHz mono frames from an AudioWorklet.
- Authenticated with the existing device token; resilient (reconnect, backpressure) with automatic fallback to today's segment-POST transport.
- No change to turn semantics at this phase: the server may simply re-chunk the stream into the existing whole-blob path.

### Non-Objectives
- Any STT behavior change (PRD-04) or turn-decision change (PRD-05).
- Downlink audio over the socket (speech delivery stays on protocol-2 `/speech/{id}/next`, `server.py:2270`, `speech.js:167`).
- Multi-call multiplexing (one socket per call), multi-device calls.

## 3. Architecture Touchpoints

```
AudioWorklet (16 kHz PCM16 frames) ──► WS /call/stream ──► server ring buffer
        │                                                      │
        │ fallback on WS failure                               └─► (this PRD) re-chunk →
        ▼                                                            existing /transcribe path
MediaRecorder 45 s segments ──► POST /transcribe  (unchanged recovery mode)
```

## 4. Functional Requirements

- **FR-01**: The server MUST expose a WebSocket endpoint `/call/stream` that authenticates the call with the existing device token. Browser WebSocket cannot set headers: the token MUST be delivered either as the first message or as the S11.2-scrubbed query parameter pattern (`server.py:285`, `server.py:3824-3825`); first-message auth is preferred (decision in Open Questions).
- **FR-02**: The client MUST capture call audio with an AudioWorklet, downsample to 16 kHz mono PCM16, and send binary frames of roughly 100 ms (frame size ASSUMPTION; small enough for streaming consumers, large enough to bound message rate).
- **FR-03**: The client MUST apply backpressure: a bounded send buffer; when full, drop the oldest frames but retain the last ~2 s (ASSUMPTION: recent audio matters more than completeness for turn decisions; the authoritative record is unaffected because the fallback path and final transcript logic remain).
- **FR-04**: The client MUST reconnect with exponential backoff on socket loss during a call, and MUST treat a call as still live across reconnects (the call state machine is untouched).
- **FR-05**: If the socket cannot be established or keeps failing, the client MUST fall back to the current MediaRecorder segment-POST transport (`app.js:5228-5231`) without user action; the fallback event is observable in the debug panel.
- **FR-06**: The existing turn semantics MUST hold unchanged over the new transport: 10-minute utterance cap (`app.js:223`), silence windows 1.2 s → 2.5 s (`app.js:219-221`), continuation window 1.5 s (`app.js:230`), one `/ask` per turn.
- **FR-07**: The server MUST tolerate partial frames and mid-stream reconnects (sequence/session id on the socket) without crashing the transcribe path.
- **FR-08**: Transport selection (WS vs segment-POST) MUST be observable for PRD-02 metrics (stage timings tagged with the active transport).

## 5. Acceptance Criteria

1. A 5-minute call on the Pixel 7 Pro over WebSocket: no dropped turn, transcript equivalent to the segment-POST path on the same audio.
2. Killing the socket mid-call (airplane-mode toggle for 5 s) recovers: reconnect, continued transcription, call survives.
3. With the socket artificially disabled (server flag), the call proceeds via segment POST exactly as today, with a debug-panel fallback event.
4. Unauthenticated socket connects are rejected.

## 6. Verification & Acceptance Strategy (V1–V3)

- **V1 — unit (node)**: frame packing (Float32 → PCM16 downsample), buffer/backpressure policy, reconnect backoff as pure module tests in `tests/js/`.
- **V2 — integration (pytest)**: FastAPI TestClient `websocket_connect` sessions: auth accept/reject, binary frame echo into a test-visible ring buffer, mid-stream disconnect tolerance; plus a browser-harness call forced onto WS then onto fallback, asserting transcript equality on the same fake-audio fixture (`tests/browser/conftest.py:496-517`).
- **V3 — manual on Pixel 7 Pro**: the 5-minute call, reconnect scenario, and fallback flag scenario of §5; timings captured via PRD-02 for both transports.

| FR | V1 | V2 | V3 | Scenario |
|---|---|---|---|---|
| FR-01 | — | ✔ | — | Socket auth |
| FR-02 | ✔ | ✔ | ✔ | PCM16 frames flow |
| FR-03 | ✔ | ✔ | — | Backpressure policy |
| FR-04 | ✔ | ✔ | ✔ | Reconnect keeps call alive |
| FR-05 | ✔ | ✔ | ✔ | Segment-POST fallback |
| FR-06 | — | ✔ | ✔ | Turn semantics preserved |
| FR-07 | — | ✔ | — | Partial frames / reconnect tolerance |
| FR-08 | — | ✔ | ✔ | Transport tagged in metrics |

## 7. Risks

- **Android Chrome background throttling of WebSockets/AudioWorklet** when the PWA is backgrounded or the screen is off: partially PRD-08's domain; this PRD's reconnect (FR-04) is the in-scope mitigation, and the V3 session includes a screen-off probe to size the problem.
- **Battery cost** of a continuously running AudioWorklet: observed, not optimized, here.
- **Proxy/CDN WebSocket support** on whatever hosts the PWA: deployment prerequisite; verified in V3.

## 8. Open Questions

1. First-message auth vs scrubbed query parameter for the socket (FR-01) — decide once, reuse for PRD-04's downlink messages.
2. Exact frame size (100 ms is an ASSUMPTION) — fixed after a short device trial.
3. Should the server ring buffer live per-call or per-device? (One call per device is today's reality; per-call chosen unless evidence says otherwise.)

## 9. Execution Boundaries

Transport only (FR-01..FR-08). No STT, no turn logic, no downlink changes; those are PRD-04/05/06. Discoveries outside scope are documented under `## Escalated / Out-of-Scope Issues`. This PRD is a `draft` behind the ROADMAP §7 gate; launching implementation is a separate explicit decision.
