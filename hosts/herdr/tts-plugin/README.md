# Herdr Neural TTS Plugin

[![Herdr Plugin](https://img.shields.io/badge/herdr-plugin-blue.svg)](https://herdr.dev)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20WSL2%20%7C%20macOS-lightgrey.svg)]()
[![RAM Footprint](https://img.shields.io/badge/RAM-%3C2.5%20MB-green.svg)]()
[![Cost](https://img.shields.io/badge/API%20Keys-Zero%20%28100%25%20Free%29-brightgreen.svg)]()

> **Zero-cost, lightweight, concurrency-safe Neural Text-To-Speech for [Herdr](https://herdr.dev) AI agent fleets.**  
> Built for developers running multiple parallel agents who want clear, human-like voice feedback without cloud API bills, overlapping voices, or high memory overhead.

---

## 🎯 Motivations

When orchestrating multiple autonomous coding agents in Herdr (Claude Code, OpenCode, Codex, Pi, Antigravity), developers face significant operational challenges:

1. **Cognitive Fatigue & Context Switching:** Staring at 4–5 terminal splits waiting for agents to finish or ask for human confirmation drains focus and energy.
2. **The "Voice Collision" Problem:** Existing voice tools trigger speech blindly on completion events. When 3 background agents finish within seconds of each other, audio streams fire simultaneously, creating an unintelligible wall of overlapping noise.
3. **Noisy Background Agitation:** When you are actively typing prompt details into Agent A, you don't want Agent C in another workspace abruptly reading 500 words of code output over your thoughts.
4. **Cloud API Friction & Platform Limits:** Prior tools either require paid ElevenLabs subscriptions with strict character quotas or rely on outdated, robotic OS synthesis (like macOS `say`) with zero support for Linux or WSL2.
5. **Terminal Output Isn't Spoken Prose:** Raw agent terminal output is polluted with ANSI color codes, Unicode box borders (`│`, `┌`, `└`), CLI spinners, token counters (`14.2k in | 520 out`), and code blocks that sound nonsensical when read verbatim.

**`herdr-tts` was created to solve these exact developer pain points.**

---

## ⚡ Differential Value: How It Compares

| Feature | Prior Art (e.g. `say-hook`) | Remote Call Bridges (`herdr-call`) | **`herdr-tts` (This Plugin)** |
| :--- | :---: | :---: | :---: |
| **API Keys & Cost** | ❌ Paid ElevenLabs key required | ❌ Paid ElevenLabs key required | 🟢 **100% Free** (Edge Neural TTS) |
| **Linux & WSL2 Support** | ❌ No (macOS only fallback) | ⚠️ Tailscale / WebRTC only | 🟢 **Native Linux, WSLg & macOS** |
| **Memory Footprint** | ~30–60 MB (Node / heavy runtime) | High (WebRTC SIP bridge) | 🟢 **~2.3 MB RAM, 0 VRAM** |
| **Parallel Chat Safety** | ❌ Voices collide & overlap | N/A (Single phone call) | 🟢 **Audio Mutex Lock** |
| **Focus-Aware Filtering** | ❌ Speaks every background event | ❌ No | 🟢 **`scope: focused`** (default) |
| **On-Demand Reading** | ❌ Passive trigger only | ⚠️ Phone only | 🟢 **`prefix + r`** (Play/Stop toggle) |
| **Instant Kill-Switch** | ❌ No | ❌ No | 🟢 **`prefix + s`** (<0.2s instant halt) |
| **Agent Terminal Cleaning** | ❌ Minimal (200 char hard limit) | ❌ LLM-generated summary | 🟢 **Deep cleaner** (ANSI, box, tokens) |

---

## 🧠 Core Architecture

```
                                  ┌────────────────────────┐
                                  │   Herdr Socket API     │
                                  │ (pane & agent events)  │
                                  └───────────┬────────────┘
                                              │
                                              ▼
┌──────────────────────┐           ┌────────────────────────┐
│ User Keybinding / CLI│           │ herdr-tts Background   │
│ (prefix+r, htr, etc.)│           │ Daemon Listener        │
└──────────┬───────────┘           └──────────┬─────────────┘
           │                                  │
           └──────────────────┬───────────────┘
                              │
                              ▼
               ┌──────────────────────────────┐
               │    Focus & Scope Filter      │
               │  Is this pane focused?       │
               │  Is auto-speech muted?       │
               └──────────────┬───────────────┘
                              │
                              ▼
               ┌──────────────────────────────┐
               │     Audio Mutex Lock         │
               │ (/tmp/herdr-tts-playing.lock)│
               │   *Prevents Voice Clashes*   │
               └──────────────┬───────────────┘
                              │
                              ▼
               ┌──────────────────────────────┐
               │   Text Normalization Engine  │
               │  Strips ANSI, borders, code  │
               │  blocks, and token quotas    │
               └──────────────┬───────────────┘
                              │
                              ▼
               ┌──────────────────────────────┐
               │  Microsoft Edge Neural TTS   │
               │     In-Memory PCM Stream     │
               └──────────────┬───────────────┘
                              │
                              ▼
               ┌──────────────────────────────┐
               │   Native Audio Dispatcher    │
               │ Linux: PulseAudio / PipeWire │
               │ WSL2:  /mnt/wslg/PulseServer │
               │ macOS: afplay                │
               └──────────────────────────────┘
```

---

## 📦 Installation

### 1. From Herdr Plugin Registry (Recommended)

```bash
herdr plugin install chiptime/herdr-tts
```

*Herdr automatically runs the `[[build]]` hook, bootstrapping an isolated, lightweight Python virtual environment with `edge-tts` and `miniaudio`.*

### 2. Local Development / From Source

```bash
git clone https://github.com/chiptime/herdr-tts.git ~/Code/personal/herdr-tts
herdr plugin link ~/Code/personal/herdr-tts
```

---

## ⌨️ Recommended Keybindings

Add these bindings to your `~/.config/herdr/config.toml`:

```toml
# Play / Stop reading the currently focused pane on-demand
[[keys.command]]
key = "prefix+r"
type = "shell"
command = "herdr-tts --toggle-play"

# Stop all audio immediately (Emergency Mute)
[[keys.command]]
key = "prefix+s"
type = "shell"
command = "herdr-tts --stop"

# Toggle background automatic speech (Muted vs Active)
[[keys.command]]
key = "prefix+v"
type = "shell"
command = "herdr-tts --toggle-auto"
```

Apply the changes to your running Herdr server:
```bash
herdr server reload-config
```

---

## 🚀 How to Use

### Three Ways to Listen

1. **On-Demand (`prefix + r` / `htr`):**  
   Whenever an agent produces an explanation, diff review, or plan, press `prefix + r` (`Ctrl+b` → `r`). It cleans the text of the active pane and speaks it. Press `prefix + r` again to stop.
2. **Passive Automatic Speech (`scope: focused`):**  
   When enabled, as soon as the agent in your **active pane** finishes working, its response is spoken automatically. Background panes in other workspaces stay quiet to prevent disruptions.
3. **Background Auto-Mute (`prefix + v` / `httt`):**  
   Toggles auto-speech on or off. Even when auto-speech is muted, your explicit on-demand hotkey (`prefix + r`) continues to work whenever you want it.

### CLI Commands

```bash
herdr-tts --toggle-play        # Play / Stop on the currently focused chat
herdr-tts --stop               # Stop audio playback immediately (<0.2s)
herdr-tts --toggle-auto        # Toggle background auto-speech (muted / active)
herdr-tts --scope focused|all  # 'focused' (only active pane) | 'all' (any pane without overlapping)
herdr-tts --status             # Show current status, voice, active locks, and PIDs
herdr-tts --voice alvaro       # Set voice (elvira, alvaro, ximena, dalia, jorge, en)
herdr-tts --rate +25%          # Set speech speed (+0%, +20%, +35%)
herdr-tts --speak "Hello"      # Synthesize custom text directly
```

### Handy Shell Aliases

Add to your `~/.zshrc` or `~/.bashrc`:

```bash
alias htr="herdr-tts --toggle-play"
alias htx="herdr-tts --stop"
alias htt="herdr-tts"
alias htts="herdr-tts --status"
alias httt="herdr-tts --toggle-auto"
```

---

## 🎙️ Available Neural Voices

Set your preferred voice with `herdr-tts --voice <name>`:

| Voice Identifier | Neural Voice Model | Accent / Language | Gender |
| :--- | :--- | :--- | :---: |
| `elvira` *(default)* | `es-ES-ElviraNeural` | Spanish (Spain) | Female |
| `alvaro` | `es-ES-AlvaroNeural` | Spanish (Spain) | Male |
| `ximena` | `es-ES-XimenaNeural` | Spanish (Spain) | Female |
| `dalia` | `es-MX-DaliaNeural` | Spanish (Mexico) | Female |
| `jorge` | `es-MX-JorgeNeural` | Spanish (Mexico) | Male |
| `en` | `en-US-JennyNeural` | English (US) | Female |

*You can also pass any full Microsoft Edge voice code (e.g. `en-GB-SoniaNeural`, `fr-FR-DeniseNeural`) directly.*

---

## ⚙️ Configuration

Persisted options live at `~/.config/herdr-tts/config.env`:

```bash
TTS_VOICE="elvira"
TTS_RATE="+20%"
TTS_MAX_CHARS="500"
TTS_AUTO_SCOPE="focused"    # "focused" (recommended) or "all"
```

---

## 🗺️ Roadmap & Future Capabilities

We have an active vision to expand `herdr-tts` into the definitive audio layer for terminal-based agent harnesses:

- [ ] 🪟 **Native Windows Support (without WSL):**
  - Add native Windows audio pipeline support using WASAPI / Windows Media Foundation / `winsound` / PowerShell audio dispatchers for Windows Terminal users.
- [ ] ⏪ ⏩ **Seek Rewind / Fast-Forward (±10s configurable):**
  - Add interactive seeking controls (`prefix + [` to rewind 10s, `prefix + ]` to jump forward 10s) during playback of long architectural explanations.
- [ ] 📈 **Dynamic Real-Time Speech Rate Adjustments:**
  - Quick hotkeys to step speed up or down on the fly (`prefix + =` / `prefix + -` for +10% / -10% increments) without restarting the daemon.
- [ ] 🖍️ **Visual Word & Sentence Highlighting:**
  - Leverage Edge TTS boundary events (`WordBoundary` / `SentenceBoundary`) to stream synchronized visual highlights directly in terminal panes and Collie mobile PWA cards as the audio plays.
- [ ] 💡 **Smart Architectural Summarizer:**
  - Lightweight heuristics or local summarizer toggle to condense massive terminal dumps (e.g. 50-file diff outputs) into punchy 2-sentence voice recaps before reading.

---

## 📄 License

MIT © 2026 [ChipTime (Bruno Silva)](https://github.com/chiptime)
