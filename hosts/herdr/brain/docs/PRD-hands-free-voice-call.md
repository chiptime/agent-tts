# PRD & Execution Loop: Hands-Free Conversational Voice Call ("Panel Call")

| Field | Value |
|---|---|
| **Product** | `herdr-brain` (PWA / Service) & `herdr.plugin` (Desktop Parity) |
| **Document Type** | PRD + Deterministic Execution Pack (Full Pack, Roadmap Mode) |
| **Status** | `ready_for_execution` (Signed & Approved) |
| **Date** | 2026-10-22 |
| **Approval** | Approved by user for autonomous implementation and testing |
| **Composed Skills** | `prd` (`/home/bruno/.dotfiles/ai/agents/opencode/skills/prd/SKILL.md`), `prd-loop` (`/home/bruno/.dotfiles/ai/agents/opencode/skills/prd-loop/SKILL.md`) |
| **Canonical Entrypoint** | `hosts/herdr/brain/docs/PRD-hands-free-voice-call.md` |
| **Target Repository Root** | `/home/bruno/Code/personal/agent-tts` |

---

## 1. Context & Problem Statement

The user's goal is to work with autonomous coding agents (Claude Code, OpenCode, Aider) through a natural, persistent voice call without having to constantly look at the screen, read chat transcripts, or inspect code diffs. The experience should resemble a phone call with a senior staff engineer (similar to Gemini Live), but with deep, bi-directional control over real coding sessions and terminal panes managed by Herdr.

### Current State & Limitations
Today, `herdr-brain` provides a basic continuous call loop (`listening → thinking → speaking → listening`), live context injection from active panes, and an approval gate (`ApprovalGate`). However, several structural barriers prevent genuine hands-free and eyes-free operation:
1. **Turnaround Latency (3s–6s)**: The current pipeline is strictly batch-oriented. It waits for 2000 ms of silence (`DEFAULT_SILENCE_MS = 2000`), uploads audio to `/stt`, runs blocking synchronous LLM tool loops, and renders the entire answer to an MP3 file via `herdr-tts` before playback starts.
2. **Half-Duplex without Voice Barge-In**: The microphone is stopped (`stopListening()`) during audio playback. The user cannot interrupt the agent by speaking; stopping requires looking at the mobile screen and tapping a stop button (`⏹`).
3. **Screen Dependency for Dialogs & Approvals**: While `ApprovalGate` protects against unintended file modifications, interactive terminal prompts (CLI selection menus, approval cards) frequently require visual attention or manual keypad taps (`POST /pane/keys`).
4. **Desktop Disconnect**: Desktop developers running Herdr on their workstation need parity: initiating and managing this contextual voice call directly from the computer (via `herdr-plugin`) alongside the mobile PWA.
5. **Autonomous Audio Testability**: Testing audio applications typically risks relying on physical hardware (human voice, physical speakers) or fragile mocks. To keep the codebase maintainable, the test suite must autonomously send and receive audio through a deterministic, reproducible closed-loop harness.

---

## 2. Objectives & Non-Objectives

### Objectives
- Enable a low-latency, full-duplex conversational voice call where the user can talk naturally to the agent while away from the screen.
- Deliver conversational turn-taking with sub-second endpointing (~700ms–900ms) and first-sentence audio playback split to minimize perceived latency.
- Provide voice-activated barge-in: speaking during playback immediately halts the audio and aborts in-flight server rendering without bypassing approval gates.
- Deliver audio-first UX: high-level conceptual summaries of agent progress, spoken binary decisions, and hands-free voice resolution of interactive terminal prompts.
- Ensure desktop parity via Herdr plugin (`herdr.plugin` / `herdr.brain`), allowing workstation management of all agent sessions with identical voice context.
- Establish a 100% autonomous, reproducible audio test harness (Audio In injection and Audio Out verification) to ensure maintainable continuous integration.

### Non-Objectives
- Multi-modal video/screen streaming (audio only; terminal state is read via existing text/ANSI scrapers).
- In-place raw code dictation (the call is an orchestrator/supervisor, not a line-by-line syntax dictaphone).
- Bypassing or loosening the safety approval gate (`ApprovalGate` remains strictly enforced).
- Cloud-only proprietary vendor lock-in (must work with local engines `faster-whisper` and `herdr-tts` CLI contract v1).

---

## 3. Architecture & Trust Boundaries

```
                 [ User Voice (Mobile PWA / Desktop WebRTC / Mic) ]
                                      │  ▲
                                      │  │ (Full Duplex with AEC & Barge-in)
                                      ▼  │
        ┌───────────────────────────────────────────────────────────────┐
        │                         herdr-brain                           │
        │                                                               │
        │   ┌───────────────────┐               ┌───────────────────┐   │
        │   │  VAD / Endpointer │               │ Fast Audio Stream │   │
        │   │  (~800ms silence) │               │ (First-sentence   │   │
        │   └─────────┬─────────┘               │  split synthesis) │   │
        │             ▼                         └─────────▲─────────┘   │
        │   ┌───────────────────┐                         │             │
        │   │  STT (Local v2 /  │                         │             │
        │   │  faster-whisper)  │                         │             │
        │   └─────────┬─────────┘                         │             │
        │             ▼                                   │             │
        │   ┌─────────────────────────────────────────────┴─────────┐   │
        │   │              Conversational LLM Engine                │   │
        │   │ - Live Context (Active Pane, CWD, Title, Status)      │   │
        │   │ - Audio-First Style: Max 3 speakable sentences        │   │
        │   │ - Interactive Dialog & Keypad Resolver                │   │
        │   └─────────┬───────────────────────────────────▲─────────┘   │
        │             │                                   │             │
        │             ▼                                   │             │
        │   ┌───────────────────┐               ┌─────────┴─────────┐   │
        │   │   Approval Gate   │◄──────────────┤ Background Watcher│   │
        │   │ (Frozen writes)   │               │ & Non-stealing SSE│   │
        │   └─────────┬─────────┘               └───────────────────┘   │
        └─────────────┼─────────────────────────────────────────────────┘
                      │
                      ▼
     ┌───────────────────────────────────┐
     │  Herdr Desktop Plugin & CLI Core  │
     │  - Session Orchestration          │
     │  - Tmux / Pane Scraper & Keypad   │
     │  - Agents (OpenCode, Claude, etc) │
     └───────────────────────────────────┘
```

### Trust Boundaries & Invariants
- **Write Path Isolation**: `send_to_session` and `create_session` MUST NOT execute without explicit user approval.
- **Voice Interruption Safety**: Barge-in cancels audio playback and aborts pending synthesis, but MUST NEVER automatically resolve or dismiss a pending `ApprovalGate`.
- **Acoustic Echo Isolation**: Barge-in listening during playback MUST rely on Web Audio Acoustic Echo Cancellation (AEC) or thresholded VAD to prevent the agent's speaker output from triggering self-cancellation.

---

## 4. State Machine & Flow

The call loop reuses the stable state identifiers: `idle`, `listening`, `thinking`, `speaking`, `paused`, `confirming`.

```
        ┌──────────────────────────────────────────────┐
        │                                              ▼
    [idle] ──(startCall)──► [listening] ──(speech)──► [recording]
                               ▲                           │
                               │                     (silence ~800ms)
                               │                           ▼
                               │                     [transcribing]
                               │                           │
                               │                     (text ready)
                               │                           ▼
                               │                      [thinking]
                               │                           │
                               │                     (split audio)
                               │                           ▼
                               │                      [speaking]
                               │                           │
              (barge-in / end) ┴───────────────────────────┤
                                                           │ (requires approval)
                                                           ▼
                                                     [confirming]
                                                           │
                                            (verbal confirm / cancel)
                                                           │
                                                           ▼
                                                      [listening]
```

---

## 5. Functional Requirements (FR-01 to FR-13)

### Milestone 1: Core Voice Loop, Latency & Autonomous Audio Test Harness
- **FR-01**: The system MUST support adaptive conversational endpointing with a silence threshold of 700 ms to 900 ms (default 800 ms), configurable without breaking the 60-second safety hard cap. (Source: PRD-conversation-mode D4).
- **FR-02**: The `/ask` endpoint MUST support a first-sentence audio split mode (`conversation: true`), returning text and synthesized audio for the first sentence immediately, while queuing remainder synthesis. (Source: PRD-conversation-mode D3).
- **FR-03**: The PWA client MUST support real-time audio playback queuing that seamlessly chains the first-sentence MP3 with subsequent segment MP3s without audible clicks or stutter. (Source: PRD-conversation-mode D3).
- **FR-04**: The client MUST keep the VAD active during audio playback (when AEC is enabled), emitting a barge-in event when user speech is detected. (Source: PRD-conversation-mode D2).
- **FR-05**: On barge-in detection, the client MUST immediately abort in-flight `/ask` fetch requests via `AbortController`, flush the playback queue, and return to `listening`. (Source: PRD-conversation-mode D2).
- **FR-06**: Server-side `/ask` handling MUST detect client disconnection and cleanly terminate ongoing LLM generation or TTS child processes to prevent orphan compute waste. (Source: PRD-conversation-mode D2).
- **FR-07**: The testing harness MUST support fully autonomous, reproducible audio round-trips (synthetic WAV input injection via Chromium fake media flags and programmatic playback verification via HTMLMediaElement events) without requiring physical microphones or speakers. (Source: User requirement).

### Milestone 2: Audio UX, Dialogs & Hands-Free Safety (Roadmap Phase 2)
- **FR-08**: When an agent displays an interactive terminal menu or question dialog (detected via ANSI attributes in `read_screen`), the assistant MUST translate the options into a concise, speakable verbal prompt (e.g. "Claude asks whether to run tests: Yes or No?"). (Source: User requirement).
- **FR-09**: The system MUST allow the user to resolve interactive terminal prompts purely by voice (e.g., "dile que sí", "selecciona la opción 1"), dispatching the corresponding keys via `send_keys_to_pane` without requiring manual keypad touches. (Source: User requirement).
- **FR-10**: Background announcements received via SSE MUST NOT interrupt or cancel active user turns or playback; they MUST queue visually and speak only during conversational idle periods. (Source: PRD-conversation-mode D5).
- **FR-11**: The voice response style in `SYSTEM_PROMPT` MUST prioritize executive audio summaries (problem impact, tests passed/failed, required decision) and explicitly offer the recommended next action. (Source: User requirement).

### Milestone 3: Herdr Desktop Plugin Parity (Roadmap Phase 3)
- **FR-12**: The `herdr.brain` / `herdr.tts` plugin configuration MUST expose a desktop voice interface command/pane (`herdr brain call`), enabling identical call functionality directly on the workstation desktop. (Source: User requirement).
- **FR-13**: Desktop and mobile call sessions MUST share the same session context, agent inventory, and approval queue, allowing seamless handover between phone and laptop. (Source: User requirement).

---

## 6. Verification & Acceptance Strategy (V1 to V3)

### Autonomous Audio Test Harness Architecture

```
                       AUTONOMOUS AUDIO TEST HARNESS
  ┌───────────────────────────────────────────────────────────────────────┐
  │ 1. AUDIO INJECTION (Audio In)                                         │
  │    - Playwright Chromium CLI flags:                                   │
  │      --use-fake-ui-for-media-stream                                   │
  │      --use-fake-device-for-media-stream                               │
  │      --use-file-for-fake-audio-capture=tests/e2e/fixtures/utterance.wav│
  │    - Web Audio VAD: In-memory Float32Array RMS buffers (Node tests)   │
  │    - Fast Transcriber: deterministic FakeModel & audio fixture bytes  │
  ├───────────────────────────────────────────────────────────────────────┤
  │ 2. AUDIO PLAYBACK & EVENT PROBE (Audio Out)                           │
  │    - Browser probe (window.__realMedia) tracking <audio id="player">: │
  │      Events: loadedmetadata, playing, timeupdate, pause, ended        │
  │      Assertions: player.duration > 0, player.paused === false         │
  │    - MP3 Byte & Duration validator for herdr-tts render output        │
  ├───────────────────────────────────────────────────────────────────────┤
  │ 3. CLOSED-LOOP BARGE-IN ASSERTION                                     │
  │    - Step 1: Start call, inject fake utterance WAV                    │
  │    - Step 2: Assert state -> speaking, player receives first_sent.mp3 │
  │    - Step 3: Inject second fake utterance (user interrupt)            │
  │    - Step 4: Assert player pauses in <100ms, AbortController fires,   │
  │              audioQueue is cleared, state returns to listening        │
  └───────────────────────────────────────────────────────────────────────┘
```

### Verification Levels
- **Level 1 (Unit & Fast Deterministic Logic)**:
  - Node test suite for `vad.js`, `endpointing.js`, and audio queue logic with synthetic RMS buffers.
  - Python `pytest` for `/ask` split responses and abort handlers.
- **Level 2 (Integration & Contract Verification)**:
  - FastAPI test client validating cancellation, first-sentence audio delivery, and approval gate integrity.
  - Fake Whisper transcriber and `FakeRunner` TTS subprocess assertions.
- **Level 3 (End-to-End System & Browser Validation)**:
  - Playwright browser tests exercising simulated microphone input, AEC, audio playback, and barge-in interruption.

### Acceptance Scenarios (Gherkin)

```gherkin
Scenario: Autonomous end-to-end barge-in with fake audio injection
  Given a Playwright browser launched with fake audio capture "tests/e2e/fixtures/speech.wav"
  When the assistant begins playing synthesized first-sentence audio
  And the test injects a barge-in speech frame
  Then the <audio id="player"> element must pause within 100 ms
  And the in-flight synthesis request must be aborted
  And the call state must transition to "recording" without requiring screen interaction

Scenario: Hands-free confirmation of a file-modifying task
  Given an agent proposes an action gated by ApprovalGate
  When the assistant speaks "He preparado los cambios en auth.py, ¿quieres que proceda?"
  And the user speaks "adelante"
  Then the utterance must route to POST /approval/{gate_id}/resolve
  And the gate must resolve with status "approved"
  And the agent must execute the task
```

### Requirement-to-Verification Matrix

| Requirement | Verification Level | Test Method | Test Path |
|---|---|---|---|
| FR-01 (Endpointing) | V1 | Deterministic simulated silence frames | `hosts/herdr/brain/tests/js/endpointing.test.js` |
| FR-02 (Split `/ask`) | V2 | FastAPI TestClient checking first-sentence payload | `hosts/herdr/brain/tests/test_ask_split.py` |
| FR-03 (Audio Queue) | V1 / V3 | Mock audio playback chaining & enqueueAudio | `hosts/herdr/brain/tests/js/speech.test.js` |
| FR-04 (Barge-in VAD) | V1 | VAD frame energy detection during playback | `hosts/herdr/brain/tests/js/vad.test.js` |
| FR-05 (Client Abort) | V3 | Playwright abort controller interception | `hosts/herdr/brain/tests/browser/test_barge_in.py` |
| FR-06 (Server Cancel)| V2 | FastAPI disconnect cancellation signal | `hosts/herdr/brain/tests/test_speech_cancel.py` |
| FR-07 (Audio Harness)| V3 | Closed-loop fake audio capture verification | `hosts/herdr/brain/tests/browser/test_audio_harness.py` |
| FR-08 (Dialog Speech)| V2 | Screen ANSI to spoken choices translation | `hosts/herdr/brain/tests/test_view_dialog.py` |
| FR-09 (Voice Keys)   | V2 | Verbal option mapping to `send_keys_to_pane` | `hosts/herdr/brain/tests/test_server_keys.py` |
| FR-10 (Announcements)| V1 | Non-stealing queue priority logic | `hosts/herdr/brain/tests/js/announce.test.js` |
| FR-11 (Audio Style)  | V2 | LLM prompt response shape and sentence cap | `hosts/herdr/brain/tests/test_llm.py` |
| FR-12 (Desktop Pane) | V2 | Herdr plugin manifest & CLI action invoke | `hosts/herdr/brain/tests/test_herdr_plugin.py` |
| FR-13 (Context Sync) | V2 | Shared session ID and state across clients | `hosts/herdr/brain/tests/test_call_history_api.py` |

---

## 7. Execution Loop Plan (Roadmap Mode: Milestones & Tasks)

### Authority Task Registry

#### Milestone 1: Low-Latency Core Voice Loop, Barge-in & Audio Harness
##### T-01 — Setup autonomous audio fixture library & Chromium fake capture harness
- **Intent**: Provide deterministic WAV/MP3 fixtures and Chromium fake audio flags for automated testing (FR-07).
- **Depends**: None
- **Edit allowlist**: `hosts/herdr/brain/tests/e2e/fixtures/`, `hosts/herdr/brain/tests/browser/conftest.py`
- **Test creator**: task creates `hosts/herdr/brain/tests/browser/test_audio_harness.py`
- **Evidence**: `PYTHONPATH=. .venv/bin/pytest tests/browser/test_audio_harness.py` passes with fake audio device.
- **Gate**: G-01

##### T-02 — Tune conversational endpointing and VAD parameters
- **Intent**: Calibrate silence threshold to 800 ms and update VAD hysteresis for conversational turn-taking (FR-01).
- **Depends**: T-01
- **Edit allowlist**: `hosts/herdr/brain/src/herdr_brain/static/endpointing.js`, `hosts/herdr/brain/src/herdr_brain/static/vad.js`
- **Test creator**: existing `hosts/herdr/brain/tests/js/endpointing.test.js`, `hosts/herdr/brain/tests/js/vad.test.js`
- **Evidence**: `node --test tests/js/vad.test.js tests/js/endpointing.test.js` passes with updated thresholds.
- **Gate**: G-02

##### T-03 — Implement first-sentence synthesis split in `/ask`
- **Intent**: Split answer into immediate first-sentence audio and asynchronous remaining synthesis (FR-02).
- **Depends**: T-02
- **Edit allowlist**: `hosts/herdr/brain/src/herdr_brain/server.py`, `hosts/herdr/brain/src/herdr_brain/speech.py`, `hosts/herdr/brain/src/herdr_brain/llm.py`
- **Test creator**: task creates `hosts/herdr/brain/tests/test_ask_split.py`
- **Evidence**: `PYTHONPATH=. .venv/bin/pytest tests/test_ask_split.py` passes with split payload.
- **Gate**: G-03

##### T-04 — Implement audio queue chaining in PWA
- **Intent**: Seamlessly queue and play fragmented audio chunks from split responses (FR-03).
- **Depends**: T-03
- **Edit allowlist**: `hosts/herdr/brain/src/herdr_brain/static/app.js`
- **Test creator**: existing `hosts/herdr/brain/tests/js/speech.test.js`
- **Evidence**: `node --test tests/js/speech.test.js` passes.
- **Gate**: G-04

##### T-05 — Implement client-side barge-in and request abort
- **Intent**: Keep VAD listening during playback with AEC and cancel in-flight audio/fetch on speech (FR-04, FR-05).
- **Depends**: T-04
- **Edit allowlist**: `hosts/herdr/brain/src/herdr_brain/static/app.js`, `hosts/herdr/brain/src/herdr_brain/static/vad.js`
- **Test creator**: task creates `hosts/herdr/brain/tests/browser/test_barge_in.py`
- **Evidence**: `PYTHONPATH=. .venv/bin/pytest tests/browser/test_barge_in.py` passes in browser harness.
- **Gate**: G-05

##### T-06 — Implement server-side client disconnect detection
- **Intent**: Cleanly abort LLM and TTS sub-tasks if the client disconnects or barges in (FR-06).
- **Depends**: T-05
- **Edit allowlist**: `hosts/herdr/brain/src/herdr_brain/server.py`, `hosts/herdr/brain/src/herdr_brain/llm.py`
- **Test creator**: existing `hosts/herdr/brain/tests/test_speech_cancel.py`
- **Evidence**: `PYTHONPATH=. .venv/bin/pytest tests/test_speech_cancel.py` passes.
- **Gate**: G-06

#### Milestone 2: Audio UX, Dialog Navigation & Hands-Free Safety
##### T-07 — Format interactive terminal dialogs for spoken synthesis
- **Intent**: Detect interactive CLI choices and format them into clear spoken options (FR-08).
- **Depends**: T-06
- **Edit allowlist**: `hosts/herdr/brain/src/herdr_brain/view.py`, `hosts/herdr/brain/src/herdr_brain/llm.py`
- **Test creator**: existing `hosts/herdr/brain/tests/test_view_dialog.py`
- **Evidence**: `PYTHONPATH=. .venv/bin/pytest tests/test_view_dialog.py` passes.
- **Gate**: G-07

##### T-08 — Voice-to-key resolver for interactive prompts
- **Intent**: Parse spoken responses to interactive prompts and dispatch logical keys via `send_keys_to_pane` (FR-09).
- **Depends**: T-07
- **Edit allowlist**: `hosts/herdr/brain/src/herdr_brain/tools.py`, `hosts/herdr/brain/src/herdr_brain/server.py`
- **Test creator**: existing `hosts/herdr/brain/tests/test_server_keys.py`
- **Evidence**: `PYTHONPATH=. .venv/bin/pytest tests/test_server_keys.py` passes.
- **Gate**: G-08

##### T-09 — Non-stealing announcement queue policy
- **Intent**: Ensure background announcements do not cut off active user speech or answers (FR-10).
- **Depends**: T-08
- **Edit allowlist**: `hosts/herdr/brain/src/herdr_brain/static/app.js`
- **Test creator**: existing `hosts/herdr/brain/tests/js/announce.test.js`
- **Evidence**: `node --test tests/js/announce.test.js` passes.
- **Gate**: G-09

##### T-10 — Executive audio summaries and next-action prompt styling
- **Intent**: Refine Panel's system prompt for voice-first senior progress reports with clear action hooks (FR-11).
- **Depends**: T-09
- **Edit allowlist**: `hosts/herdr/brain/src/herdr_brain/llm.py`
- **Test creator**: existing `hosts/herdr/brain/tests/test_llm.py`
- **Evidence**: `PYTHONPATH=. .venv/bin/pytest tests/test_llm.py` passes.
- **Gate**: G-10

#### Milestone 3: Herdr Desktop Plugin Parity
##### T-11 — Declare desktop voice action in `herdr-plugin.toml`
- **Intent**: Add desktop CLI / popup action to invoke voice call bridge on the workstation (FR-12).
- **Depends**: T-10
- **Edit allowlist**: `hosts/herdr/brain/herdr-plugin.toml`, `hosts/herdr/brain/bin/herdr-brain`
- **Test creator**: task creates `hosts/herdr/brain/tests/test_herdr_plugin.py`
- **Evidence**: `PYTHONPATH=. .venv/bin/pytest tests/test_herdr_plugin.py` passes.
- **Gate**: G-11

##### T-12 — Desktop audio-stream bridge integration
- **Intent**: Connect desktop microphone/speaker via local WebRTC/pipe to `herdr-brain` service (FR-12, FR-13).
- **Depends**: T-11
- **Edit allowlist**: `hosts/herdr/brain/src/herdr_brain/server.py`, `hosts/herdr/tts-plugin/lib/herdr_reader.py`
- **Test creator**: existing `hosts/herdr/brain/tests/test_herdr_client.py`
- **Evidence**: `PYTHONPATH=. .venv/bin/pytest tests/test_herdr_client.py` passes.
- **Gate**: G-12

---

### Authority Gate Table

| Gate | Command (literal) | cwd | Source policy | Exit meaning |
|---|---|---|---|---|
| G-01 | `PYTHONPATH=. .venv/bin/pytest tests/browser/test_audio_harness.py` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | `tests/e2e/fixtures/`, `tests/browser/conftest.py` | 0 = Fake audio device & fixtures validated; nonzero = harness failure |
| G-02 | `node --test tests/js/vad.test.js tests/js/endpointing.test.js` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | `static/vad.js`, `static/endpointing.js` | 0 = VAD & silence thresholds validated; nonzero = threshold regression |
| G-03 | `PYTHONPATH=. .venv/bin/pytest tests/test_ask_split.py` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | `src/herdr_brain/server.py`, `src/herdr_brain/speech.py` | 0 = Split synthesis returns first sentence; nonzero = split failure |
| G-04 | `node --test tests/js/speech.test.js` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | `static/app.js` | 0 = Queue chains audio chunks; nonzero = playback break |
| G-05 | `PYTHONPATH=. .venv/bin/pytest tests/browser/test_barge_in.py` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | `static/app.js`, `static/vad.js` | 0 = Speech interrupts playback immediately; nonzero = playback persists |
| G-06 | `PYTHONPATH=. .venv/bin/pytest tests/test_speech_cancel.py` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | `src/herdr_brain/server.py` | 0 = Disconnect aborts server loop; nonzero = orphan compute leaked |
| G-07 | `PYTHONPATH=. .venv/bin/pytest tests/test_view_dialog.py` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | `src/herdr_brain/view.py` | 0 = Dialog options parsed to text; nonzero = unparsed ANSI |
| G-08 | `PYTHONPATH=. .venv/bin/pytest tests/test_server_keys.py` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | `src/herdr_brain/tools.py` | 0 = Verbal input dispatches keys; nonzero = key dispatch failed |
| G-09 | `node --test tests/js/announce.test.js` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | `static/app.js` | 0 = Announcements queue without stealing; nonzero = turn interrupted |
| G-10 | `PYTHONPATH=. .venv/bin/pytest tests/test_llm.py` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | `src/herdr_brain/llm.py` | 0 = Prompt respects style & brevity; nonzero = style violation |
| G-11 | `PYTHONPATH=. .venv/bin/pytest tests/test_herdr_plugin.py` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | `herdr-plugin.toml`, `bin/herdr-brain` | 0 = Plugin manifest & actions valid; nonzero = manifest invalid |
| G-12 | `PYTHONPATH=. .venv/bin/pytest tests/test_herdr_client.py` | `/home/bruno/Code/personal/agent-tts/hosts/herdr/brain` | `src/herdr_brain/server.py` | 0 = Desktop client shares session state; nonzero = desync |

---

## 8. Candidate Binding & Execution Contract

- **Candidate Files**:
  - `hosts/herdr/brain/src/herdr_brain/server.py`
  - `hosts/herdr/brain/src/herdr_brain/llm.py`
  - `hosts/herdr/brain/src/herdr_brain/speech.py`
  - `hosts/herdr/brain/src/herdr_brain/tools.py`
  - `hosts/herdr/brain/src/herdr_brain/view.py`
  - `hosts/herdr/brain/src/herdr_brain/static/app.js`
  - `hosts/herdr/brain/src/herdr_brain/static/vad.js`
  - `hosts/herdr/brain/src/herdr_brain/static/endpointing.js`
  - `hosts/herdr/brain/tests/e2e/fixtures/`
  - `hosts/herdr/brain/tests/browser/conftest.py`
  - `hosts/herdr/brain/herdr-plugin.toml`
  - `hosts/herdr/tts-plugin/lib/herdr_reader.py`
- **Remediation Budget**: Maximum 2 remediation rounds per milestone before escalating to user.
- **Stop Conditions**: Uncaught crash, unhandled audio feedback loop on device, approval gate bypass hazard, gate command failure after budget exhaustion.

---

## 9. Launch Request Artifact

```markdown
# Launch Request — Hands-Free Conversational Voice Call (Roadmap Mode)

## Target
- Repository root: /home/bruno/Code/personal/agent-tts
- Pack entrypoint: hosts/herdr/brain/docs/PRD-hands-free-voice-call.md
- Pack identity: hosts/herdr/brain/docs/PRD-hands-free-voice-call.md (git tracked)
- Readiness state: ready_for_execution

## Scope being launched
- Milestones: M1 (Core Low-Latency Loop, Barge-in & Autonomous Harness), M2 (Audio UX & Dialogs), M3 (Desktop Plugin Parity)
- FRs covered: FR-01 to FR-13 (count: 13)
- Tasks: 12 (T-01 to T-12) — Gates: 12 (G-01 to G-12)
- Non-goals: Raw code dictation, video/screen capture streaming, approval gate bypass.

## Execution contract
- Remediation budget: 2 functional rounds per milestone
- Stop conditions: crash, unknown error, gate failure, human consent needed
- Candidate binding: verified local workspace files under hosts/herdr/brain/

## Limitations
This pack defines the technical contract and execution plan; execution requires an explicit follow-up user prompt.
```

---

## 10. Resume Bookmark (For Future Executor)
- **Pack identity**: `hosts/herdr/brain/docs/PRD-hands-free-voice-call.md`
- **Completed**: None (execution not yet launched)
- **Active task**: `T-01` — budget remaining: 2 remediation rounds
- **Next action**: Create `tests/browser/test_audio_harness.py` and run Gate G-01 to establish the autonomous fake audio capture baseline.
