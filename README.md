# agent-tts

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)]()
[![RAM Footprint](https://img.shields.io/badge/RAM-%3C2.5%20MB-green.svg)]()
[![Cost](https://img.shields.io/badge/API%20Keys-Zero%20%28100%25%20Free%20Default%29-brightgreen.svg)]()

> **Lightweight, cross-platform neural TTS engine with interactive IPC seek/pause controls, smart auto-rewind, and deep text sanitization for AI coding agents and terminal workflows.**

---

## 🎯 Why agent-tts?

Most Text-To-Speech tools are built either as heavy desktop applications with bloated browser engines or as rigid CLI scripts that block execution and cannot be paused, seeked, or interrupted without killing the entire process.

Furthermore, raw agent terminal outputs (from Claude Code, OpenCode, Herdr, Aider, etc.) are polluted with ANSI escape codes, Unicode box-drawing borders (`│`, `┌`, `└`), CLI spinners, token counters (`14.2k in | 520 out`), and code blocks that sound nonsensical when read verbatim.

**`agent-tts` was designed specifically to solve these problems:**

1. **Interactive IPC Controls:** Seek backward or forward (`seek -10`, `seek +10`), pause, resume, or stop on the fly via a high-speed Unix domain socket without restarting audio or blocking callers.
2. **Smart Auto-Rewind on Resume:** When resuming playback after being paused, `agent-tts` automatically rewinds 2–3 seconds to help you effortlessly regain your cognitive train of thought.
3. **Semantic Navigation:** Jump between sentences accurately (`--next-sentence`, `--prev-sentence`) and paragraphs (`--next-paragraph`, `--prev-paragraph`) instead of blindly seeking arbitrary seconds.
4. **Visual Karaoke & Word Highlighting:** Real-time ANSI word and sentence highlighting in your terminal synchronized with audio (`--highlight`).
5. **Synchronized Auto-Scroll Reader:** Viewport automatically descends at the pace of the spoken voice (`--autoscroll`).
6. **Bionic Reading Mode:** Bold fixation on initial letters for lightning-fast reading comprehension while listening (`--bionic`).
7. **Zen Mode:** Distraction-free, centered high-contrast teleprompter reader with minimal progress HUD (`--zen`).
8. **Pure C Native Audio (No Media Player Dependencies):** Direct low-latency PCM streaming via `miniaudio` into PulseAudio/PipeWire (Linux), CoreAudio (macOS), and WASAPI (Windows). No external `mpv`, `paplay`, or `afplay` processes needed.
9. **Deep Terminal & Agent Prose Sanitization:** Strips ANSI styling, box borders, token usage telemetry, and converts Markdown/ASCII tables into conversational pauses.
10. **Modular Synthesis Providers:** Works out of the box with zero configuration using high-quality Microsoft Edge Neural voices (100% free), with optional drop-in support for OpenAI Audio TTS (`tts-1`/`tts-1-hd`), ElevenLabs, and offline Piper.
11. **Tiny Footprint:** Runs in < 2.5 MB RAM with zero GPU/VRAM requirement.

---

## 🔌 Agent Connectors

`agent-tts` is the universal voice layer for terminal coding agents, and the missing ecosystem piece is the connector that answers **"what did the agent just say?"** per tool. Text-to-audio is a solved problem; transcript/source knowledge lives here, in the engine (Vision B contract):

> Hosts pass identity — the engine owns the transcript.

Pass `--agent <tool> --session-id <id>` and `agent-tts` itself reads the real last assistant message from that tool's local structured transcript — zero TUI chrome, no regex scraping. Unknown agent names are sniffed from the session id shape (`ses_*` → OpenCode; UUID-like → Claude Code, then Codex, then Antigravity, keeping the first hit). If no connector resolves the session, the provided text is used as an automatic terminal-scrollback fallback.

**Available connectors today:**

| Connector | Agent | Source | Access |
|---|---|---|---|
| OpenCode | `opencode` | `~/.local/share/opencode/opencode.db` | SQLite, read-only URI |
| Claude Code | `claude` | `~/.claude/projects/*/<session>.jsonl` | Reverse tail scan (≤ 8 MB cap) |
| Codex CLI | `codex` | `~/.codex/sessions/*/*/*/rollout-*<session>.jsonl` | Reverse tail scan (≤ 8 MB cap) |
| Antigravity CLI | `antigravity`, `agy` | `~/.gemini/antigravity-cli/brain/<session>/.system_generated/logs/transcript.jsonl` | Reverse tail scan (≤ 8 MB cap) |
| Aider CLI | `aider` | `.aider.chat.history.md` in the project working directory (`AIDER_CHAT_HISTORY` override) | Markdown turn scan, fence-aware parsing |
| Scrollback fallback | any | terminal pane text | legacy extraction + chrome cleaning |

**Roadmap:** gemini-cli, goose, opencode web, auto-detection of the running agent tool.

---

## 📦 Installation

```bash
pip install agent-tts
```

Or install from source for development:

```bash
git clone https://github.com/chiptime/agent-tts.git
cd agent-tts
pip install -e .
```

---

## 🚀 CLI Usage

### Basic Speech & Piping

```bash
# Speak text directly with live visual word highlighting
agent-tts "Hola, la compilación ha terminado con éxito." --highlight

# Zen Mode: Minimalist distraction-free teleprompter reader
agent-tts "Hola mundo, esto es una lectura en modo Zen." --zen

# Synchronized Auto-Scroll Reader with Bionic Reading
agent-tts "Lectura rápida asistida con fijación biónica en la terminal." --autoscroll --bionic

# Smart Architectural Summarizer (TL;DR pre-flight)
git diff | agent-tts --tldr
npm test | agent-tts --tldr --highlight
cat long_build.log | agent-tts --summarize

# Automatic Language Detection & Dynamic Voice Switching
agent-tts "He encontrado este error: fatal: remote origin already exists. Debemos cambiar el origen." --auto-lang

# Private Podcast RSS Feed (listen on mobile podcast apps)
agent-tts "Resumen de cambios para el equipo" --podcast --podcast-title "Sprint Update"
agent-tts --podcast-serve 8844  # Serves RSS feed at http://localhost:8844/podcast.xml

# Pipe output from any command or agent
git status | agent-tts
cat response.md | agent-tts --voice alvaro --rate +15%

# Save synthesized MP3 without playing locally
agent-tts "Report summary" --output /tmp/report.mp3 --no-play

# Play an existing MP3 through the interactive player
agent-tts --play-file /tmp/report.mp3 --highlight
```

### Low-Latency Streaming

Long texts no longer wait for the full audio to be synthesized. With the default `--stream auto`, texts of 400+ characters spoken by the `edge`, `openai`, or `elevenlabs` providers start playing after the first ~250-character sentence group is synthesized (~300–600ms), while a background producer thread synthesizes the remaining sentence groups and appends them to the live PCM buffer in real time — karaoke word highlighting and sentence boundaries merge seamlessly on the fly.

The OpenAI and ElevenLabs providers also consume their HTTP responses incrementally instead of waiting for the complete file: ElevenLabs uses its dedicated `/stream` endpoint and OpenAI streams the `/audio/speech` response chunks as they are encoded, so every group's audio reaches the pipeline as soon as the first MP3 chunks arrive. If a streaming request fails (network hiccup or API error mid-stream), synthesis transparently falls back to the classic full-response request and logs the fallback to stderr. Use `--stream on` to force pipelined playback for any provider or length, or `--stream off` to revert to classic single-shot synthesis. Streaming is incompatible with `--output` and `--podcast`, which always synthesize complete files.

```bash
# Force pipelined streaming on any provider or length (--stream off = classic one-shot)
git log -20 | agent-tts --stream on --highlight
```

### Interactive IPC & Semantic Sentence Navigation

While `agent-tts` is speaking in the background, you can control playback instantly from another shell, tmux keybinding, or script:

```bash
# Jump by sentence (Semantic Navigation)
agent-tts --next-sentence
agent-tts --prev-sentence
agent-tts --current-sentence

# Toggle pause / resume
agent-tts --ipc-cmd toggle-pause

# Fast-forward or rewind
agent-tts --ipc-cmd "seek +10"
agent-tts --ipc-cmd "seek -10"

# Check live playback position, current sentence, and duration
agent-tts --ipc-cmd status
# Output: status=playing pos=14.20 total=45.60 sent_idx=2 sentence=Compilación terminada con éxito.

# Stop immediately
agent-tts --ipc-cmd stop
```

---

## 🪟 Windows & WSL Playback

### Native Windows

`pip install agent-tts` on Windows just works: playback runs natively through WASAPI via miniaudio — no external audio servers. The CLI behaves as on Linux/macOS; transient files (locks, pid, IPC) live in the platform temp directory, and interactive IPC uses loopback TCP instead of Unix sockets.

### Play WSL audio on the Windows host (`winhost`)

Run the audio server once on Windows:

```powershell
pip install agent-tts
agent-tts --winhost
```

Then, from WSL:

```bash
agent-tts "Build finished successfully" --playback winhost
# or persist the choice: export AGENT_TTS_PLAYBACK=winhost
```

PCM streams to the Windows host over TCP and plays natively there (WASAPI) — no PulseAudio intermediaries. The client auto-detects the host: loopback first (WSL2 mirrored networking), then the WSL2 default gateway (classic NAT mode). Default behavior never changes: playback stays local unless `--playback`/`--winhost-host` or the env vars below are used.

### Automatic PowerShell fallback (`wsl-ps`)

If the winhost server is unreachable, one English warning is printed on stderr and the run falls back to zero-install PowerShell playback (each synthesized group plays sequentially via `powershell.exe` from WSL interop — correctness over latency). Use `--playback wsl-ps` to force that mode directly; a clear error exits non-zero when `powershell.exe` is missing or the process is not running under WSL.

### Environment variables

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `AGENT_TTS_PLAYBACK` | `local` | Default playback target (`local`, `winhost`, `wsl-ps`) |
| `AGENT_TTS_WINHOST_HOST` | auto-detect | Explicit Windows host address for winhost clients |
| `AGENT_TTS_WINHOST_PORT` | `7717` | TCP port for the winhost transport |
| `AGENT_TTS_WINHOST_BIND` | `0.0.0.0` | Bind address for `agent-tts --winhost` |

### Protocol note (v1)

One JSON header line then raw s16 PCM over TCP: `{"v":1,"cmd":"play","rate":24000,"channels":1,"format":"s16"}` followed by the payload until the client half-closes; the server plays streaming as bytes arrive. Controls (`pause`, `resume`, `stop`) are one-line JSON messages on separate short-lived connections. The server keeps a single active playback; a new PLAY preempts the current one.

> ⚠️ **Security:** `--winhost` binds `0.0.0.0` by default — the port is open on your LAN and accepts unauthenticated playback requests. Restrict it with `AGENT_TTS_WINHOST_BIND=127.0.0.1` (or a firewall rule) when in doubt.

---

## 🐍 Python Programmatic Library

`agent-tts` can be used directly as a clean Python library in your own agent harnesses, scripts, or daemons:

```python
import asyncio
from agent_tts import speak, synthesize, clean_agent_text, send_ipc_command

# 1. Clean noisy agent output
raw_terminal = "❯ npm test\nPassed: 12 tests\nTokens: 4.2k in | 120 out\n```\nDone\n```"
clean_text = clean_agent_text(raw_terminal)

# 2. Synthesize and play with interactive controls
asyncio.run(speak(
    text=clean_text,
    voice="elvira",
    rate="+20%",
    auto_rewind_sec=2.0,  # Rewinds 2s automatically upon resume
))

# 3. Generate MP3 bytes with OpenAI or ElevenLabs
mp3_bytes = asyncio.run(synthesize(
    text="Build completed",
    provider="openai",
    openai_key="sk-...",
    openai_model="tts-1",
))

# 4. Control active playback from another thread or task
send_ipc_command("seek -10")
send_ipc_command("toggle-pause")
```

---

## 🎙️ Supported Providers

| Provider | Config | Cost | Voice Quality | Typical Latency | Pipelined Streaming |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **`edge` (Default)** | Zero config (No keys required) | 🟢 Free | High (Neural) | ~150–250ms | ✅ `auto` (≥ 400 chars) |
| **`piper` / `local`** | `--piper-model` / `PIPER_MODEL` | 🟢 Free (Offline) | Neural ONNX (Local CPU) | ~80–180ms | ➖ `--stream on` only |
| **`openai`** | `--openai-key` / `OPENAI_API_KEY` | Paid API | Studio Quality | ~300–500ms | ✅ `auto` (≥ 400 chars) + chunked MP3 HTTP |
| **`elevenlabs`** | `--eleven-key` / `ELEVENLABS_API_KEY` | Paid API | Ultra-realistic | ~350–600ms | ✅ `auto` (≥ 400 chars) + chunked MP3 HTTP |

### Voice Shortcuts:
- **Edge:** `elvira` (*default Spanish*), `alvaro`, `ximena`, `dalia`, `jorge`, `en` (*US English Jenny*), or any standard Microsoft Edge voice identifier (e.g. `es-ES-ElviraNeural`).
- **Piper:** Path to `.onnx` model (e.g. `es_ES-davefx-medium.onnx` or configured via `PIPER_MODEL`).
- **OpenAI:** `nova`, `alloy`, `echo`, `fable`, `onyx`, `shimmer` (automatically maps Spanish defaults like `elvira` → `nova`, `alvaro` → `onyx`).
- **ElevenLabs:** `rachel`, `bella`, `antoni`, `adam`, `domi`, `elli`, `josh`, `arnold`, `sam`, or any custom 20-character Voice ID.

---

## ⚙️ CLI Reference

```
usage: agent-tts [-h] [--voice VOICE] [--rate RATE] [--max-chars MAX_CHARS]
                 [--raw] [--output OUTPUT] [--no-play] [--play-file PLAY_FILE]
                 [--highlight] [--next-sentence] [--prev-sentence]
                 [--current-sentence] [--tldr] [--auto-lang] [--podcast]
                 [--podcast-title PODCAST_TITLE] [--podcast-serve [PORT]]
                 [--ipc-cmd IPC_CMD]
                 [--provider {edge,openai,elevenlabs,eleven,piper,local}]
                 [--stream {auto,on,off}]
                 [--playback {local,winhost,wsl-ps}] [--winhost]
                 [--winhost-host WINHOST_HOST] [--winhost-port WINHOST_PORT]
                 [--openai-key OPENAI_KEY] [--openai-base-url OPENAI_BASE_URL]
                 [--openai-model OPENAI_MODEL] [--eleven-key ELEVEN_KEY]
                 [--eleven-model ELEVEN_MODEL] [--piper-model PIPER_MODEL]
                 [--pre-extracted] [--agent AGENT]
                 [--session-id SESSION_ID]
                 [text ...]
```

- **`--stream {auto,on,off}`** (default `auto`): Pipelined playback mode. `auto` streams long texts (≥ 400 chars) with the `edge`, `openai`, or `elevenlabs` providers when playing locally; `on` forces streaming for any provider or length; `off` forces classic single-shot synthesis. `--output` and `--podcast` always use single-shot synthesis.

- **`--pre-extracted`**: Treat input text as the final message (e.g. provided by an integration layer that already resolved the chat transcript); skips terminal-scrollback turn extraction, keeps markdown-to-speech cleaning.

- **`--agent <tool>`**: Agent tool name for the transcript connector layer (e.g. `opencode`, `claude`); unknown names are sniffed from the session id shape.

- **`--session-id <id>`**: Agent session id; resolves the last assistant message from the tool's structured transcript (see [Agent Connectors](#-agent-connectors)) before falling back to the provided text.

---

## 🗺️ Roadmap & Future Capabilities

We have an active vision to expand `agent-tts` into the definitive neural TTS engine for AI coding agents and terminal workflows:

- [x] 🪟 **Pure C Native Audio with Interactive Seek/Pause IPC:**
  - miniaudio-backed playback (PulseAudio/PipeWire, CoreAudio, WASAPI) with frame-accurate seek (`seek ±10`), pause/resume, and smart auto-rewind via a low-latency Unix domain socket.
- [x] 📦 **Modular Provider Backend (Edge, OpenAI, ElevenLabs & Piper):**
  - Zero-config Microsoft Edge Neural voices as the 100% free default, with drop-in OpenAI `tts-1`/`tts-1-hd`, ElevenLabs multilingual models, and fully offline Piper ONNX synthesis.
- [x] 🖍️ **Real-Time Karaoke Highlighting & Synchronized Readers:**
  - Live ANSI word/sentence highlighting (`--highlight`), synchronized auto-scroll reader (`--autoscroll`), Bionic Reading fixation bolding (`--bionic`), and Zen Mode teleprompter (`--zen`).
- [x] 📑 **Semantic Sentence & Paragraph Navigation:**
  - Jump between full sentences (`--next-sentence`, `--prev-sentence`) and paragraphs (`--next-paragraph`, `--prev-paragraph`) instead of blindly seeking arbitrary seconds.
- [x] 💡 **Smart Architectural Summarizer & Technical Pronunciation Lexicon:**
  - Offline TL;DR heuristics (`--tldr`) condense diffs, logs, and stack traces; extensible developer lexicon (`~/.config/agent-tts/lexicon.json`) normalizes jargon, currencies, and units into natural spoken prose.
- [x] 🌐 **Automatic Language Detection with Dynamic Voice Switching:**
  - Fast statistical classifier detects embedded language changes and switches neural voices on the fly (`--auto-lang`).
- [x] 📻 **Private Podcast RSS Feed Generator & HTTP Server:**
  - Publish sessions to an RSS 2.0 / iTunes XML feed (`--podcast`) served locally (`--podcast-serve`) for listening in mobile podcast apps.
- [x] 🗣️ **Robust Terminal Scrollback Cleaning:**
  - Strips ANSI styling, box-drawing borders, CLI spinners, and token counters, and converts Markdown/ASCII tables into conversational pauses before synthesis.
- [x] 🚀 **Pipelined Streaming Synthesis (Low-Latency Playback):**
  - Long texts start playing after the first ~250-character sentence group is synthesized (~300–600ms) while a producer thread appends the remaining groups to the live PCM buffer (`--stream auto`, Edge provider).
- [x] 🪟 **Native Windows Playback (WASAPI, zero changes for POSIX users):**
  - First-class Windows support: tempdir-based transient files, loopback-TCP IPC, and Windows piper binary discovery; playback stays local unless a target is requested.
- [x] 📡 **WSL → Windows `winhost` Transport:**
  - `--playback winhost` streams PCM over TCP to `agent-tts --winhost` on the Windows host and plays it natively there via WASAPI (loopback + gateway auto-detection, one active session with preemption, pause/resume/stop controls).
- [x] 🔁 **Zero-Install PowerShell Fallback Mode (`wsl-ps`):**
  - When the winhost server is unreachable, playback automatically falls back to per-group `powershell.exe` playback (`System.Media.SoundPlayer`); `--playback wsl-ps` forces it directly.
- [x] 🔌 **Agent Connectors (Structured Transcript Reading):**
  - With `--agent` + `--session-id` the engine reads the real last assistant message from the agent tool's local transcript (OpenCode SQLite, Claude Code / Codex CLI / Antigravity CLI JSONL, Aider markdown history) with automatic scrollback fallback.
- [ ] 🎙️ **Per-Provider Pipelining (Piper):**
  - OpenAI and ElevenLabs now stream via chunked MP3 HTTP delivery with transparent full-response fallback (`--stream auto`, ≥ 400 chars); only the local Piper backend remains.
- [ ] ⚡ **Incremental MP3 Frame-Accurate Byte Streaming:**
  - Replace sentence-group pipelining with frame-level MP3 byte streaming for even lower time-to-first-audio.
- [x] 🔒 **Automated Secret & Credential Redaction Engine (`redact.py`):**
  - High-speed heuristic sanitizer that automatically redacts API keys (`sk-...`, `ghp_...`, `glpat-...`), JWTs, authorization headers, generic credential assignments, PEM private keys, and long hashes from terminal text before vocalization or publishing to RSS/ntfy feeds (runs once inside the shared cleaning stage).
- [ ] 🧠 **Hybrid High-Level LLM Summarizer (`--llm-summary`):**
  - Optional one-sentence executive synthesis delegating to locally installed CLIs (`claude -p`, `codex exec`, `ollama`) for long prose, with instant zero-cost fallback to offline `--tldr` heuristics.
- [ ] 🔊 **Next-Gen Neural Local TTS (Kokoro-82M ONNX):**
  - Ultra-natural local CPU neural synthesis via Kokoro 82M (<350MB weights), providing studio-grade offline voice synthesis with zero cloud reliance.
- [ ] 📥 **Automated Voice Model Manager (`agent-tts voice install`):**
  - Built-in CLI voice registry to discover, download, verify, and manage offline ONNX models (Piper & Kokoro) without manual filesystem configuration.
- [ ] 🪟 **Native Windows Audio/Service Parity Testing:**
  - Windows-native playback, `--winhost` server mode, and the WSL transport are implemented; add Windows CI coverage to validate the WASAPI and TCP loopback paths on real hardware.
- [ ] ♿ **Orca screen reader integration (pending owner requirements — not currently used):**

---

## 📄 License

## 📄 License

MIT © 2026 [ChipTime (Bruno Silva)](https://github.com/chiptime)
