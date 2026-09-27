# herdr-tts ↔ herdr-brain Contract v1 — Normative Specification

> **Normative source of truth for the speech rendering and reader interface between `herdr-tts` (host plugin) and `herdr-brain` (conversational brain).**
>
> Changes to this specification require coordination between host components and must be verified by explicit contract tests.

---

## 1. Scope and Principles

`herdr-tts` is the official speech and rendering backend for `herdr-brain`. `herdr-brain` consumes **strictly** the versioned CLI surface:

- No private Python imports across components (verified by boundary rules).
- No direct dependency on internal venv or engine flags.
- Fail-soft verification at boot with explicit descriptive errors.

---

## 2. CLI Surface Interface

### 2.1 Contract Version Discovery

```bash
herdr-tts --contract-version
```

- **Output**: Writes `1` followed by a newline to `stdout`.
- **Exit code**: `0` on success.
- **Contract rule**: `herdr-brain` probes this flag at boot. A response of `1` (or `>= 1`) is required for `TTS_BACKEND_OK`.

*Verified by:*
- `hosts/herdr/brain/tests/test_tts.py::test_probe_contract_version_match`
- `hosts/herdr/brain/src/herdr_brain/tts.py::tts_backend_status`

---

### 2.2 Speech Rendering (`--render-text`)

```bash
herdr-tts --render-text <OUT.mp3> <TEXT> [--voice <VOICE>] [--rate <RATE>]
```

- **Arguments**:
  - `OUT.mp3`: Destination absolute path for the generated MP3 file.
  - `TEXT`: The text string to synthesize.
  - `--voice <VOICE>` (optional): Voice identifier.
  - `--rate <RATE>` (optional): Speech rate multiplier.
- **Behavior**: Synthesizes speech to the destination file. If destination directory does not exist, fails with non-zero exit code.
- **Exit code**:
  - `0`: Render succeeded, `OUT.mp3` exists and contains valid audio bytes.
  - Non-zero (`1`, `2`, `3`): Render failed, `herdr-brain` raises `TTSError`.

*Verified by:*
- `hosts/herdr/brain/tests/test_tts.py::test_render_speech_success`
- `hosts/herdr/brain/tests/test_tts.py::test_render_speech_failure_raises_tts_error`

---

### 2.3 Reader HTML Pipeline (`--render-html`)

```bash
herdr-tts --render-html <INPUT.md> <OUTPUT.html> --map <MAP.json>
```

- **Arguments**:
  - `INPUT.md`: Absolute path to source Markdown document.
  - `OUTPUT.html`: Destination path for rendered anchored HTML.
  - `--map <MAP.json>`: Destination path for the JSON anchor mapping sidecar (`reader-pipeline/anchors@1`).
- **Behavior**:
  - Renders Markdown into security-hardened, sanitized HTML anchors.
  - Emits sidecar mapping linking source character offsets to DOM element IDs.
- **Exit code**:
  - `0`: Success; both output files written.
  - Non-zero: Render failed; `herdr-brain` raises `ReaderError`.

*Verified by:*
- `hosts/herdr/brain/tests/test_reader.py`
- `hosts/herdr/tts-plugin/docs/reader-pipeline-contract.md`

---

### 2.4 Daemon Liveness Probe

- **Mechanism**: The `herdr-tts` background daemon maintains a PID file at:
  `/tmp/herdr-tts-daemon.pid` (or configured via environment).
- **Liveness probe rule**:
  1. The PID file must exist and contain an integer PID.
  2. The process with that PID must be alive.
  3. Under Linux, `/proc/<pid>/cmdline` must contain `herdr-tts` (guarding against stale PID reuse).
- **Behavior on loss**: If daemon liveness fails, `herdr-brain` logs and speaks fail-noisy warnings over secondary channels (e.g. phone SSE).

*Verified by:*
- `hosts/herdr/brain/tests/test_tts_daemon.py`
- `hosts/herdr/brain/src/herdr_brain/tts_daemon.py::check_daemon_liveness`

---

## 3. Normative Traceability Matrix

| Interface Item | Component Provider | Component Consumer | Authoritative Test File |
|---|---|---|---|
| `--contract-version` | `hosts/herdr/tts-plugin` | `hosts/herdr/brain` | `hosts/herdr/brain/tests/test_tts.py` |
| `--render-text` | `hosts/herdr/tts-plugin` | `hosts/herdr/brain` | `hosts/herdr/brain/tests/test_tts.py` |
| `--render-html` | `hosts/herdr/tts-plugin` | `hosts/herdr/brain` | `hosts/herdr/brain/tests/test_reader.py` |
| Daemon PIDFILE liveness | `hosts/herdr/tts-plugin` | `hosts/herdr/brain` | `hosts/herdr/brain/tests/test_tts_daemon.py` |
