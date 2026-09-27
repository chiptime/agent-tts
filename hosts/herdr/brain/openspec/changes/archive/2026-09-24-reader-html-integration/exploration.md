# Exploration: reader-html-integration

## Purpose

Investigate integrating `herdr-tts`'s anchored-HTML reader pipeline (`reader-pipeline/anchors@1`) into `herdr-brain`'s reading experience, turning raw transcript turns into beautifully styled, structured HTML (markdown maquetado) with sentence anchors, while preserving the repository's strict fail-soft principles and testing standards.

---

## 1. Key Investigation Questions & File:Line Evidence

### Q1: Current Listen Flow & Architecture

- **UI "Escuchar" Button**:
  - Found in [`src/herdr_brain/static/app.js`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/app.js#L516-L544) inside `attachReplay(turn, getText, label)`.
  - It creates a button with `btn.textContent = "🔊 Escuchar"`.
  - On click, it sets an immediate loading state (`btn.disabled = true`, `btn.setAttribute("aria-busy", "true")`, `btn.textContent = "⏳ Sintetizando…"`) and calls `speakText(text, label)` ([`app.js:546-567`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/app.js#L546-L567)).
  - `speakText` issues a `POST /tts` fetch with JSON `{ text: text }`.
- **Server-Side TTS Invocation**:
  - `POST /tts` is defined in [`src/herdr_brain/server.py:666-674`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/server.py#L666-L674).
  - It invokes `synth(cfg, body.text, out_path)`, where `synth` defaults to `render_mp3` ([`src/herdr_brain/server.py:202`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/server.py#L202)).
  - `render_mp3` in [`src/herdr_brain/tts.py:103-140`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/tts.py#L103-L140) executes:
    `[str(settings.tts_bin), "--render-text", str(out_path), clean, "--voice", settings.tts_voice, "--rate", settings.tts_rate]`.
  - **Important**: The server NEVER invokes `--speak` (0 occurrences across the codebase).
- **Audio Delivery & Playback Location**:
  - Audio is **streamed to the phone/client**, NOT played on the host PC.
  - The rendered audio file is saved under `settings.audio_dir` via `new_audio_path` ([`src/herdr_brain/tts.py:143-150`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/tts.py#L143-L150)).
  - The server serves it via `GET /audio/{name}` ([`src/herdr_brain/server.py:675-682`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/server.py#L675-L682)).
  - The client enqueues `data.audio_url` in `audioQueue` (`app.js:561`).
  - `pumpAudio()` assigns `player.src = item.url` and calls `player.play()` ([`src/herdr_brain/static/app.js:1093-1118`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/app.js#L1093-L1118)) on `<audio id="player">` ([`src/herdr_brain/static/index.html:1120`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/index.html#L1120)).
  - Note: The PC speakers are only used by the `herdr-tts` daemon background announcement channel ([`src/herdr_brain/tts_daemon.py:1-27`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/tts_daemon.py#L1-L27)), not for web PWA speech playback.
- **Transcript Text Source**:
  - The transcript text shown in `#agent-view` comes from `GET /conversation` ([`src/herdr_brain/server.py:455-463`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/server.py#L455-L463)), which invokes `tools.conversation(pane_id)` ([`src/herdr_brain/tools.py:168-205`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/tools.py#L168-L205)).
  - `tools.conversation` calls `read_turns(target.agent, target.session_value, CONVERSATION_WINDOW)` from [`src/herdr_brain/transcripts.py:1-260`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/transcripts.py#L1-L260), reading OpenCode SQLite (`~/.local/share/opencode/opencode.db`) or Claude Code JSONL files.
- **Modules to Extend**:
  - [`src/herdr_brain/tts.py`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/tts.py): Expose `render_html(settings, text, out_html_path, out_map_path)`.
  - [`src/herdr_brain/server.py`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/server.py): Add reader rendering / caching endpoint (e.g. `GET /conversation/rendered` or `/read`).
  - [`src/herdr_brain/static/app.js`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/app.js): Mount formatted HTML with fallback to plain text.

---

### Q2: UI Surface & Escape Mechanics

- **Inspection of Existing Views**:
  - `conv-sheet` ([`src/herdr_brain/static/index.html:1098-1107`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/index.html#L1098-L1107), [`app.js:325-359`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/app.js#L325-L359)): This is purely an agent/session switcher sheet ("＋ Nueva conversación" + list of herd agent rows). It is **not** a reading surface.
  - `agent-view` ([`src/herdr_brain/static/index.html:1041-1050`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/index.html#L1041-L1050), [`app.js:873-1026`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/app.js#L873-L1026)): Contains `#glance-turns`. This is the primary view where the user inspects recent conversation turns and reads messages.
  - `call-drawer` ([`src/herdr_brain/static/index.html:1067-1081`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/index.html#L1067-L1081), [`app.js:447-510`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/app.js#L447-L510)): Contains `<main id="conversation">`. Sliding bottom drawer active during phone voice calls.
- **Escape Mechanics Today**:
  - In `buildGlanceTurn`: `textEl.textContent = text;` ([`app.js:886`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/app.js#L886)).
  - In `addTurn`: `body.textContent = text;` ([`app.js:472`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/app.js#L472)).
  - In `renderScreen`: `viewScreen.textContent = ""` ([`app.js:867`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/app.js#L867)).
  - **Grep verification**: `innerHTML` is used **0 times** in the entire PWA client (`src/herdr_brain/static/`). All current rendering strictly relies on `textContent` and programmatic DOM creation.
- **Target Surface**:
  - The natural host is **`agent-view` (`#glance-turns`)**. When turns are expanded ("ver más") or when formatted HTML is available, the turn renders the styled GFM document inside a dedicated reader block with CSS rules for headings, lists, tables, code blocks, and sentence spans (`.tts-sent`, `.tts-sent-cont`).

---

### Q3: Sync Feasibility & Recommendation

- **Analysis of IPC & Timing**:
  - `herdr-tts`'s player IPC socket (`/tmp/herdr-tts-player.sock`) is transient: it only exists when miniaudio is actively playing on the host PC (documented in [`src/herdr_brain/tts_daemon.py:17-23`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/tts_daemon.py#L17-L23)).
  - Because `herdr-brain` renders MP3s via `--render-text` and streams them to the phone `<audio>` element, the host PC player socket **does not exist** during phone playback.
  - The sidecar `reader-pipeline/anchors@1` ([`reader-pipeline-contract.md:33-55`](file:///home/bruno/Code/personal/herdr-tts/docs/reader-pipeline-contract.md#L33-L55)) provides sentence indices, paragraph indices, and character ranges, but **zero timing data** (no timestamps or durations).
- **Options Evaluation**:
  - **Option A (Proxy player IPC socket)**: Inviable. The socket is not alive during phone playback. Even for host playback, HTTP polling of a local socket from mobile adds latency and fragility.
  - **Option C (Approximate sync from MP3 position)**: Heuristic char-rate estimates without timing markers lead to erratic desynchronization due to pauses, acronym expansions, code skipping, and speed variation. High complexity, poor user experience.
  - **Option B (Static formatted document + sentence anchors without live sync in v1)**:
    - Render complete formatted HTML with `.tts-sent` and `.tts-sent-cont` anchors.
    - Support manual sentence navigation/tapping and visual section demarcation.
    - Zero timing synchronization baggage; 100% reliable across networks and devices.
- **Recommended v1 Scope**: **Option B**. Focus v1 on rock-solid formatted rendering and anchoring. Real-time audio-to-sentence highlighting should be deferred to a future milestone if/when `herdr-tts` produces sentence timestamps in audio metadata.

---

### Q4: Fail-Soft Rules & Pattern Reuse

- **Existing Pattern**:
  - In [`src/herdr_brain/server.py:219-223`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/server.py#L219-L223), `tts_backend_status` checks the CLI contract fail-soft; missing TTS logs a warning and degrades speech without crashing.
  - In [`src/herdr_brain/server.py:482-486`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/server.py#L482-L486), `speak_answer` wraps rendering in `try...except`, logging a warning and falling back to text-only if TTS fails.
  - In [`src/herdr_brain/tools.py:186`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/tools.py#L186), transcript reading exceptions safely degrade to `raw = None`.
- **Reader Integration Fail-Soft Strategy**:
  - Invocation of `herdr-tts --render-html <in> <out> [--map map.json]` must be wrapped in a fail-soft executor.
  - If `herdr-tts` is missing, returns non-zero (exit code 1, 2, or 3), or the sidecar is unparseable:
    - The backend logs a warning naming the failure.
    - The API response falls back to returning `html: null` or the plain-text turn.
    - The frontend seamlessly falls back to existing `textContent` rendering.
    - The user experience never crashes and never presents an empty message.

---

### Q5: Security & Injection Boundaries

- **Upstream Pipeline Guarantees**:
  - Total escaping: all text nodes and attribute values pass through `html.escape` ([`lib/reader_pipeline.py:20-22`](file:///home/bruno/Code/personal/herdr-tts/lib/reader_pipeline.py#L20-L22)).
  - Raw HTML from input is escaped, never passed through.
  - Link schemes are strictly restricted to `http:` and `https:`; all other schemes are stripped to plain text.
  - Fail-closed secret redaction (`redact_secrets` propagation).
- **Brain-Side Security Posture**:
  - Server currently sends **no CSP headers** (only `Cache-Control: no-cache` in [`src/herdr_brain/server.py:105`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/server.py#L105)).
  - Introducing `innerHTML` into the PWA requires deliberate guardrails:
    1. Scope `innerHTML` assignment strictly to dedicated `.reader-content` containers.
    2. Enforce `target="_blank" rel="noopener noreferrer"` on all links so external navigation cannot access or manipulate the PWA window.
    3. Serve the sidecar mapping as pure `application/json` via an API endpoint, never embedded as inline `<script>` tags.
    4. Introduce a base Content Security Policy header on the index response (`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; media-src 'self' blob:; connect-src 'self'`).

---

### Q6: Caching & Persistence

- **Turn Lifecycle**:
  - Turns are queried from OpenCode SQLite or Claude JSONL via `tools.conversation()` ([`src/herdr_brain/tools.py:168-205`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/tools.py#L168-L205)).
  - Completed turns are immutable.
  - Active turns poll every 5 seconds via `refreshConversation()` ([`src/herdr_brain/static/app.js:1051-1077`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/app.js#L1051-L1077)).
  - Running `--render-html` for 20 turns every 5 seconds would spawn 4 subprocesses per second, causing extreme CPU thrashing.
- **Recommended Caching Architecture**:
  - **Keying**: Content-hashed key using SHA-256 over `(turn.text, lang, max_chars)`.
  - **Tier 1 (In-Memory)**: `@lru_cache(maxsize=128)` in Python holding parsed HTML and sidecar dictionaries.
  - **Tier 2 (On-Disk)**: Cache files stored in `settings.audio_dir.parent / "reader_cache" / f"{content_hash}.html"` and `.map.json`.
  - **Serving**:
    - Lazy on-demand rendering: endpoint `GET /conversation/{pane_id}/rendered` or `GET /read/{turn_hash}`, requested only when a turn is viewed or expanded.

---

### Q7: Test Strategy Seams

- **Pytest Seams (`pytest -q`, 368 existing tests)**:
  - Update session stub in [`tests/conftest.py:38-50`](file:///home/bruno/Code/personal/herdr-brain/tests/conftest.py#L38-L50) to support `--render-html <in> <out> [--map map.json]`.
  - Unit tests in `tests/test_tts.py`:
    - Add `TestRenderHtml` testing command argv, successful HTML + map output, exit code 2 (unreadable/redaction failure), exit code 3 (backend missing), timeout handling, and empty input validation.
  - Server tests in `tests/test_server.py`:
    - Test client endpoint behavior with mocked/fake reader renderer, verifying HTTP status, JSON structure, fail-soft fallback when TTS raises TTSError, and caching hits.
- **Node Test Seams (`node --test tests/js/`, 68 existing tests)**:
  - Extract client-side reader DOM logic into [`src/herdr_brain/static/reader.js`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/reader.js) with UMD export (matching [`src/herdr_brain/static/approval.js:405-412`](file:///home/bruno/Code/personal/herdr-brain/src/herdr_brain/static/approval.js#L405-L412)).
  - Add `tests/js/reader.test.js`:
    - Test sentence anchor parsing (`data-sent-idx`, `data-para-idx`).
    - Test active sentence highlighting state transitions.
    - Test plain-text fallback toggle.

---

## 2. Options Comparison

| Option | Description | Pros | Cons | Complexity |
|---|---|---|---|---|
| **Approach 1: Live Audio-Sync via IPC/Estimation** | Attempt real-time sentence highlight during MP3 playback via player socket or char-rate heuristic | High interactivity if working | Fragile; player socket doesn't exist during phone playback; sidecar has no timestamps; heuristic desyncs | High (High Risk) |
| **Approach 2: In-Place Formatted Reading with Static Sentence Anchors (Recommended v1)** | Render formatted HTML in-place inside `glance-turns` on demand/expand with static sentence anchors and tap-to-select | Clean architecture; robust fail-soft; zero timing bugs; respects mobile layout; easy caching | No automated karaoke-style follow-along during audio | Low-Medium (Low Risk) |
| **Approach 3: Dedicated Full-Screen Reading Modal** | Tapping "Leer" opens a full-screen modal reader showing only the rendered document | Focused reading experience | Extra navigation layer; modal management complexity on mobile PWA | Medium |

---

## 3. Scope Boundary

### In Scope for v1:
1. `src/herdr_brain/tts.py`: Add `render_html` wrapper calling `herdr-tts --render-html <in> <out> [--map map.json]`.
2. Content-hashed caching (in-memory + disk cache under state directory).
3. Backend endpoint `GET /conversation/{pane_id}/rendered` (or extending `/conversation` with optional formatted turns).
4. Fail-soft degradation: any failure returns or displays plain text seamlessly.
5. PWA client rendering in `#glance-turns` with CSS styling for headings, code blocks, lists, tables, and sentence anchors (`.tts-sent`, `.tts-sent-cont`).
6. Comprehensive test suite: `pytest` with updated CLI stub + `node --test tests/js/reader.test.js`.

### Out of Scope (Future Milestones):
1. Real-time karaoke-style sentence highlighting synchronized with MP3 playback (requires upstream audio timing/cues).
2. Editable markdown or in-browser document mutation.
3. IPC player socket integration for remote phones.

---

## 4. Risks & Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| **CPU Spikes from Repeated Rendering** | High | Mandatory content-hash caching (SHA-256 of text); never re-render identical turns during 5s polling. |
| **XSS via Dynamic HTML Injection** | High | Rely on upstream `html.escape` guarantee; add defensive CSP headers on `server.py`; sanitize link targets (`rel="noopener noreferrer"`). |
| **Broken PWA on `herdr-tts` Absence or Exit 2/3** | Medium | Strict fail-soft contract: return plain text on any error; no 500s or UI breakages. |
| **JS Test Suite Drift** | Medium | Strict adherence to `openspec/config.yaml` rule: maintain `node --test tests/js/` alongside pytest. |

---

## 5. Ready for Proposal

**Yes**. The integration path is clear, well-bounded, respects existing architectural patterns, and has well-defined test seams across both Python and JavaScript suites.
