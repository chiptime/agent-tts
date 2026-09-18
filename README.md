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
3. **Semantic Navigation:** Jump between sentences accurately (`--next-sentence`, `--prev-sentence`) instead of blindly seeking arbitrary seconds.
4. **Visual Karaoke & Highlighting:** Real-time ANSI word and sentence highlighting in your terminal synchronized with audio (`--highlight`).
5. **Pure C Native Audio (No Media Player Dependencies):** Direct low-latency PCM streaming via `miniaudio` into PulseAudio/PipeWire (Linux), CoreAudio (macOS), and WASAPI (Windows). No external `mpv`, `paplay`, or `afplay` processes needed.
6. **Deep Terminal & Agent Prose Sanitization:** Strips ANSI styling, box borders, token usage telemetry, and converts Markdown/ASCII tables into conversational pauses.
7. **Modular Synthesis Providers:** Works out of the box with zero configuration using high-quality Microsoft Edge Neural voices (100% free), with optional drop-in support for OpenAI Audio TTS (`tts-1`/`tts-1-hd`) and ElevenLabs.
8. **Tiny Footprint:** Runs in < 2.5 MB RAM with zero GPU/VRAM requirement.

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

| Provider | Config | Cost | Voice Quality | Typical Latency |
| :--- | :--- | :---: | :---: | :---: |
| **`edge` (Default)** | Zero config (No keys required) | 🟢 Free | High (Neural) | ~150–250ms |
| **`piper` / `local`** | `--piper-model` / `PIPER_MODEL` | 🟢 Free (Offline) | Neural ONNX (Local CPU) | ~80–180ms |
| **`openai`** | `--openai-key` / `OPENAI_API_KEY` | Paid API | Studio Quality | ~300–500ms |
| **`elevenlabs`** | `--eleven-key` / `ELEVENLABS_API_KEY` | Paid API | Ultra-realistic | ~350–600ms |

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
                 [--openai-key OPENAI_KEY] [--openai-base-url OPENAI_BASE_URL]
                 [--openai-model OPENAI_MODEL] [--eleven-key ELEVEN_KEY]
                 [--eleven-model ELEVEN_MODEL] [--piper-model PIPER_MODEL]
                 [text ...]
```

---

## 📄 License

MIT © 2026 [ChipTime (Bruno Silva)](https://github.com/chiptime)
