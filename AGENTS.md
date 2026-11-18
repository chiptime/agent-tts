# agent-tts — Agent Instructions

Monorepo with three main components:
- `engine/` — Python TTS engine, providers (`kokoro`, `edge`, etc.), and daemon (`agent_tts`).
- `hosts/herdr/brain/` — Herdr Brain web UI, live call streaming, reader drawer, and WebPush bridge.
- `hosts/herdr/tts-plugin/` — Herdr shell plugin (`bin/herdr-tts`), audio gating, pending queue, and `ntfy`/podcast notifications.

## Test Commands

- **Engine**: `cd engine && uv run --extra dev --extra kokoro python -m pytest` (use `--all-extras` to include `stt`; lint: `uv run --extra dev ruff check src/ tests/`).
  - Always run from `engine/` via `python -m pytest` so `cwd` is on `sys.path` (required by namespace `tests` imports).
- **Herdr Brain**: run from `hosts/herdr/brain/` in **separate processes** (mixing Playwright and API tests breaks both and exceeds 15 min):
  - Unit/API: `uv run --extra dev python -m pytest tests/ --ignore=tests/browser --ignore=tests/e2e`
  - Browser / E2E: `uv run --extra dev python -m pytest tests/browser` and `uv run --extra dev python -m pytest tests/e2e`
  - JS unit tests: `node --test tests/js/`
  - Always invoke via `python -m pytest` (`cwd` on `sys.path`). Pre-existing baseline: 14 environment-dependent failures in `test_doctor`, `test_launcher_repair`, `test_resolve`, and `test_systemd_template` (`herdr-bin`/homebrew discovery) fail identically on base.
- **Herdr TTS plugin**: full Bash suite `bash hosts/herdr/tts-plugin/tests/all_bash_harnesses.sh` (single harness: `bash hosts/herdr/tts-plugin/tests/host_cli_cases.sh`); Python tests under `hosts/herdr/tts-plugin/tests/test_*.py`.
- **Non-Linux CI uses `continue-on-error`**: `.github/workflows/ci.yml` allows Windows/macOS `engine-tests` to fail without reddening the workflow — always read the pytest summary line, not just the job badge (and use `TMPDIR=/tmp` on macOS to avoid 104-byte `AF_UNIX` socket path overflow).

## UI & Playwright Gotchas (`hosts/herdr/brain`)

- **Direct-child CSS only when hiding raw turn text**: in `#conversation .turn.has-rendered`, hide the raw body span with a direct-child selector (`> span:not(.who)`), NEVER a descendant selector (`span:not(.who)`). A descendant selector hides `.reader-content`'s own `tts-sent` spans and collapses bubbles to `0x0`.
- **Re-pin `EXPECTED_DIGESTS` after any PWA static edit**: every byte change to served files in `hosts/herdr/brain/src/herdr_brain/static/` (except `sw.js`) must update its SHA-256 in `hosts/herdr/brain/tests/browser/test_karaoke_harness.py`.
- **Browser harnesses fail-closed on undeclared traffic**: `tests/browser/conftest.py` fails on any unmocked HTTP/WS request (`blocked_requests`). Do not add boot-time PWA fetches without registering a default route in `conftest.py` (or load on demand, like `pushUI.refresh()` on Settings open).
- **Verify CSS/layout fixes in a real browser**: `curl` or unit tests alone do not prove visual rendering. Measure rendered `boundingClientRect` in Playwright against the live brain (`127.0.0.1:8741`).
- **Avoid casual `#call-btn` clicks in automated probes**: clicking `#call-btn` starts a live call session; hang up immediately if a test triggers it.

## Brain Security Invariants (`hosts/herdr/brain`)

- **Outbound-only secret masking (`secrets_mask.py`)**: mask at outbound LLM provider requests and HTTP/SSE serialization seams (never mutate stored transcripts/ring); always mask **before** truncating (unhinted mode on excerpts) and detect multiline secrets (PEM) across joined payloads.
- **Service Worker holds no credentials (`static/sw.js`)**: push/gate action taps only focus or open the PWA; never put approval tokens in push payloads (device bearer lives in page `localStorage`).
