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
| **API Keys & Cost** | ❌ Paid ElevenLabs key required | ❌ Paid ElevenLabs key required | 🟢 **100% Free Default** (Edge TTS; optional OpenAI & ElevenLabs) |
| **Linux & WSL2 Support** | ❌ No (macOS only fallback) | ⚠️ Tailscale / WebRTC only | 🟢 **Native Linux, WSLg & macOS** |
| **Memory Footprint** | ~30–60 MB (Node / heavy runtime) | High (WebRTC SIP bridge) | 🟢 **~2.3 MB RAM, 0 VRAM** |
| **Parallel Chat Safety** | ❌ Voices collide & overlap | N/A (Single phone call) | 🟢 **Audio Mutex Lock** |
| **Focus-Aware Filtering** | ❌ Speaks every background event | ❌ No | 🟢 **`scope: focused`** (default) |
| **Mobile Audio Push** | ❌ No | ⚠️ Call only | 🟢 **Native `ntfy.sh` with inline MP3 player** |
| **Optional Web Deep-Linking** | ❌ No | ❌ No | 🟢 **Collie, custom dashboard, or standalone** |
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

# Pause / Resume current audio playback
[[keys.command]]
key = "prefix+p"
type = "shell"
command = "herdr-tts --toggle-pause"

# Fast-Forward 10 seconds
[[keys.command]]
key = "prefix+]"
type = "shell"
command = "herdr-tts --forward"

# Rewind 10 seconds
[[keys.command]]
key = "prefix+["
type = "shell"
command = "herdr-tts --rewind"

# Speed up voice reading by 10%
[[keys.command]]
key = "prefix+="
type = "shell"
command = "herdr-tts --rate-up"

# Slow down voice reading by 10%
[[keys.command]]
key = "prefix+-"
type = "shell"
command = "herdr-tts --rate-down"

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
2. **Interactive Control (`prefix + p`, `prefix + [ / ]`, `prefix + = / -`):**  
   Pause or resume speech at any point (`prefix + p`). Rewind 10 seconds to re-listen to critical code paths (`prefix + [`), or skip ahead 10 seconds (`prefix + ]`). Adjust speed up or down on the fly (`prefix + =` / `prefix + -`).
3. **Passive Automatic Speech (`scope: focused`):**  
   When enabled, as soon as the agent in your **active pane** finishes working, its response is spoken automatically. Background panes in other workspaces stay quiet to prevent disruptions.
4. **Background Auto-Mute (`prefix + v` / `httt`):**  
   Toggles auto-speech on or off. Even when auto-speech is muted, your explicit on-demand hotkeys continue to work whenever you want them.

### CLI Commands

```bash
herdr-tts --toggle-play        # Play / Stop on the currently focused chat
herdr-tts --stop               # Stop audio playback immediately (<0.2s)
herdr-tts --toggle-pause       # Pause / Resume playback on the fly (prefix+p)
herdr-tts --seek +10           # Jump forward or backward by N seconds
herdr-tts --forward            # Jump forward 10 seconds (prefix+])
herdr-tts --rewind             # Jump backward 10 seconds (prefix+[)
herdr-tts --rate-up            # Increase voice speed by +10% dynamically (prefix+=)
herdr-tts --rate-down          # Decrease voice speed by -10% dynamically (prefix+-)
herdr-tts --player-status      # Live audio position, duration and playback state
herdr-tts --toggle-auto        # Toggle background auto-speech (muted / active)
herdr-tts --scope focused|all  # 'focused' (only active pane) | 'all' (any pane without overlapping)
herdr-tts --provider edge      # Select TTS provider: edge (free default), openai, elevenlabs
herdr-tts --openai-key <key>   # Set OpenAI API key for tts-1 / tts-1-hd
herdr-tts --eleven-key <key>   # Set ElevenLabs API key
herdr-tts --status             # Show full service configuration, provider, and player state
herdr-tts --voice alvaro       # Set voice (elvira, alvaro, ximena, dalia, jorge, en, nova, rachel)
herdr-tts --rate +25%          # Set speech speed (+0%, +20%, +35%)
herdr-tts --ntfy-topic <topic> # Set ntfy.sh topic for mobile audio notifications (or 'off')
herdr-tts --web-url <url>      # Set optional web UI/dashboard base URL for deep-links (or 'off')
herdr-tts --collie-url <url>   # Alias for --web-url (sets action button to 'Abrir Collie')
herdr-tts --click-redirect on  # Optional: tap notification body to open web URL directly (default: off)
herdr-tts --render-pane <pane> # Export clean assistant speech of a pane directly to .mp3
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

## 🎙️ Modular TTS Providers & Voices

`herdr-tts` includes a modular synthesis backend supporting zero-cost Edge TTS as well as hyper-realistic commercial APIs:

### 1. Microsoft Edge Neural TTS (Default — 100% Free)
- **Zero Configuration:** No API keys or account required.
- **Ultra-Low Latency:** High-quality neural voices directly streamed.
- **Voices:** `elvira` *(default)*, `alvaro`, `ximena`, `dalia`, `jorge`, `en` (or any Edge voice code like `es-ES-ElviraNeural`).

```bash
herdr-tts --provider edge --voice elvira
```

### 2. OpenAI Audio TTS
- **Models:** `tts-1` (default, fast/low latency) and `tts-1-hd` (high quality).
- **Voices:** `nova`, `alloy`, `echo`, `fable`, `onyx`, `shimmer` (automatic mapping from Spanish defaults like `elvira` → `nova`).
- **Setup:**
```bash
herdr-tts --openai-key "sk-..."
herdr-tts --provider openai --voice nova
```

### 3. ElevenLabs
- **Models:** `eleven_multilingual_v2` (default) and `eleven_turbo_v2_5`.
- **Voices:** `rachel`, `bella`, `antoni`, `adam`, `domi`, `elli`, `josh`, `arnold`, `sam`, or any custom 20-character Voice ID.
- **Setup:**
```bash
herdr-tts --eleven-key "xi-..."
herdr-tts --provider elevenlabs --voice rachel
```

---

## 📱 Mobile Push Notifications (Optional via `ntfy.sh`)

`herdr-tts` can forward agent completion and attention events to your phone using [`ntfy.sh`](https://ntfy.sh):

* **Standalone & Audio-First:** The primary purpose of push integration is delivering the event notification and generated neural `.mp3` directly to your phone. In the ntfy app (Android/iOS), you can press Play directly in the notification or notification feed without unlocking your computer.
* **Strictly Optional Web Redirection:** By default, notifications require zero web interface and do not force redirection. If you use [Collie](https://colliepwa.dev) or another web dashboard, you can configure an optional redirect URL. When enabled, a dedicated action button (*"📱 Abrir Collie"* or *"📱 Abrir Web"*) appears below the notification to jump straight to `/pane/<pane_id>` without hijacking the notification tap or audio player.
* **Clipboard Helper:** Every notification includes a one-tap action button (*"📋 Copiar comando"*) to copy `herdr agent focus <pane_id>` to your clipboard.
* **Zero Additional Daemons:** Runs within the existing `herdr-tts` daemon—zero extra background processes or duplicate TTS synthesis.

### Quick Setup:

1. Install the free **ntfy** app on Android or iOS.
2. Subscribe to your private topic (e.g. `my-secret-topic-9k2`).
3. Enable push in `herdr-tts`:
```bash
herdr-tts --ntfy-topic "my-secret-topic-9k2"
```

To optionally add deep-links to Collie or a custom web UI:
```bash
# Optional Collie integration:
herdr-tts --collie-url "https://my-desktop.tailscale.net"

# Or generic web dashboard (supports {pane_id} template):
herdr-tts --web-url "https://dashboard.example.com/panes/{pane_id}"

# To disable web redirection at any time:
herdr-tts --web-url off
```

---

## ⚙️ Configuration

Persisted options live at `~/.config/herdr-tts/config.env`:

```bash
TTS_PROVIDER="edge"         # "edge" (free), "openai", or "elevenlabs"
TTS_VOICE="elvira"
TTS_RATE="+20%"
TTS_MAX_CHARS="0"           # 0 = unlimited
TTS_AUTO_SCOPE="focused"    # "focused" (recommended) or "all"

# Optional Cloud TTS API Keys & Models:
OPENAI_API_KEY=""
OPENAI_BASE_URL="https://api.openai.com/v1"
OPENAI_TTS_MODEL="tts-1"
ELEVENLABS_API_KEY=""
ELEVENLABS_MODEL="eleven_multilingual_v2"

# Mobile Push Notifications (Optional):
NTFY_TOPIC="my-secret-topic-9k2"
NTFY_SERVER="https://ntfy.sh"

# Optional Web UI / Collie Deep-Linking (off by default):
WEB_URL=""                  # Or "https://my-desktop.tailscale.net"
WEB_LABEL="Collie"
CLICK_REDIRECT="off"        # "off" = tap opens ntfy player; "on" = tap opens web URL
```

---

## 🗺️ Roadmap & Future Capabilities

We have an active vision to expand `herdr-tts` into the definitive audio layer for terminal-based agent harnesses:

- [x] ⏪ ⏩ **Interactive Seek Controls (±10s rewind / forward):**
  - Frame-accurate seek controls (`prefix + [` to rewind 10s, `prefix + ]` to jump forward 10s) and pause/resume (`prefix + p`) via low-latency Unix socket IPC.
- [x] 📈 **Dynamic Real-Time Speech Rate Adjustments:**
  - Quick hotkeys to step speed up or down on the fly (`prefix + =` / `prefix + -` for +10% / -10% increments) with instant notification feedback.
- [x] 🪟 **Native Cross-Platform Audio Engine (Linux, macOS, Windows):**
  - Pure C audio playback via `miniaudio` directly outputting to PulseAudio/PipeWire (Linux), CoreAudio (macOS), and WASAPI (Windows) without requiring external media players (`mpv`, `paplay`, `afplay`).
- [x] 🎙️ **Modular TTS Provider Backend (Edge, ElevenLabs & OpenAI TTS):**
  - Modular provider architecture allowing users with API keys to choose ultra-realistic voice models (OpenAI `tts-1`, ElevenLabs) while preserving zero-cost Microsoft Edge Neural TTS as default.
- [ ] 🖍️ **Visual Word & Sentence Highlighting:**
  - Leverage Edge TTS boundary events (`WordBoundary` / `SentenceBoundary`) to stream synchronized visual highlights directly in terminal panes as audio plays.
- [ ] 💡 **Smart Architectural Summarizer:**
  - Lightweight heuristics or local summarizer toggle to condense massive terminal dumps (e.g. 50-file diff outputs) into punchy 2-sentence voice recaps before reading.



---

## 📄 License

MIT © 2026 [ChipTime (Bruno Silva)](https://github.com/chiptime)
