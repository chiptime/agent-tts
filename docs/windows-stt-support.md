# Windows Speech-to-Text (STT) Platform Boundary and Support Notes

Agent-TTS resident STT worker operates on POSIX Unix domain sockets and requires WSL2 on Windows, while local audio capture, offline model management, and typed platform refusal are supported natively.

## Quick path

1. **Native Windows audio capture**: `agent-tts-stt capture --out recording.wav` records host microphone audio directly via PowerShell without a resident worker.
2. **Native Windows model download**: `agent-tts-stt pull --model small` downloads and verifies faster-whisper assets offline into local cache.
3. **Resident worker & transcription (WSL2)**: Run `agent-tts-stt serve` and worker-dependent transcription inside WSL2. Native Windows execution exits with typed code 7 (`transport_unsupported`) directing users to WSL2.

## Details

| Component | Native Windows Support | WSL2 / POSIX Support | Notes |
|-----------|------------------------|----------------------|-------|
| `agent-tts-stt capture` | Supported | Supported | Windows host microphone capture runs directly via PowerShell; no resident worker required. |
| `agent-tts-stt pull` | Supported | Supported | Direct HuggingFace cache management and offline model asset verification. |
| Configuration & Settings | Supported | Supported | `SttSettings`, environment overrides, audio boundary validation, and framing codecs. |
| `agent-tts-stt serve` | Refused (`transport_unsupported`) | Supported | Resident worker requires POSIX AF_UNIX sockets, mode 0600/0700 creation, and `fcntl` election locks. |
| `agent-tts-stt status` | Refused (`transport_unsupported`) | Supported | Client communication over AF_UNIX socket; typed exit code 7 on native Windows. |
| `agent-tts-stt transcribe` | Refused (`transport_unsupported`) | Supported | Worker-backed transcription requires resident server; client input validation remains active. |
| Snapshot Root Security | Fail-closed (POSIX owner/mode) | Supported | Enforces owner UID (`geteuid`) and mode 0700; not bypassed with relaxed ACLs on Windows. |

## Verification and Test Coverage

- **Portable native tests**: Help smoke, CLI argument parsing, packaging metadata, base installation isolation, input file validation, framing codecs, cache inspection, and offline model resolution run and pass natively on Windows.
- **Typed refusal tests**: Explicitly pin that `serve`, `status`, `transcribe`, and `shutdown` CLI entry points and `SttWorker.start()` reject native Windows execution with typed exit code 7 (`transport_unsupported`) and actionable WSL2 guidance.
- **POSIX-only skips**: Tests requiring live AF_UNIX socket transport, POSIX file modes (0700/0600), process EUID matching, or `fcntl` locks are skipped strictly at test/class scope on `win32` with descriptive reasons.
- **CI Status**: Hosted Windows CI verification remains pending full suite completion; test skips document the deliberate U1 platform boundary and do not constitute a native resident worker implementation.

## Next step

- For Windows users needing speech-to-text inference, install and run `agent-tts-stt serve` within WSL2.
- Local voice workflows can capture host audio via `agent-tts-stt capture` and route audio clips to the WSL2 worker endpoint.
