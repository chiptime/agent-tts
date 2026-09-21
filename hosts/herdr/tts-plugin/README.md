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

### Agent Transcript Connectors

herdr-tts is a **thin host**: it only forwards the agent identity + session id (`--agent` / `--session-id`, read from `herdr agent get` → `.result.agent.agent_session`) to the `agent-tts` engine, which **owns the connector layer** and resolves the real last assistant message itself — zero TUI chrome, no regex scraping. Connectors included in the engine today: **OpenCode** (local SQLite transcript, read-only), **Claude Code** (local JSONL sessions, reverse tail scan), and an automatic **terminal scrollback fallback** for panes without a structured source (Antigravity, shells, any agent the engine cannot resolve).

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

**How Herdr keys work:** every binding is a single `prefix + X` chord — Herdr core has **no key sequences** (no leader chains like `prefix+u` then `r`). That constraint matters: a one-chord-per-command voice map competes for letters that Herdr core already owns (`r`, `v`, `z`, `n`/`p`, `[`/`]`). Pick **one** of the three styles below.

### The supported flow: `keymap init → (adopt) → check → apply`

Key assignment is **declarative and user-editable**: bindings live in a JSON keymap file, not in hardcoded docs. The `herdr-tts keymap` subcommand family manages the whole loop — `apply` writes the blocks into Herdr's config for you:

```bash
herdr-tts keymap init     # write the default template (never overwrites; --force replaces)
herdr-tts keymap adopt --style ctrlalt   # optional: switch the whole map to a preset family
$EDITOR "${XDG_CONFIG_HOME:-$HOME/.config}/herdr-tts/keymap.json"
herdr-tts keymap check    # validate + conflict-check against Herdr core defaults
herdr-tts keymap apply    # write the bindings into config.toml (managed block + backup)
herdr server reload-config
```

The file maps each **stable command id** to a chord or `null` (unassigned):

```json
{
  "style": "direct",
  "bindings": {
    "play": "prefix+r",
    "tldr": "prefix+t",
    "stop": null
  }
}
```

Chord syntax: `"prefix+X"` (an uppercase key means shift — `prefix+N` ≠ `prefix+n`), `"ctrl+alt+X"`, `"ctrl+alt+shift+X"`, or `null`. Write modifiers literally (they are not reordered); spacing is tolerated. `keymap check` prints one line per binding — `OK`, `⚠️ SHADOWS CORE (<action>)` when the chord collides with a Herdr core default, or `❌` for hard errors — and `keymap apply` installs them into `${HERDR_CONFIG_DIR:-$HOME/.config}/herdr/config.toml` as a managed block between `# >>> herdr-tts keymap >>>` markers: user content outside the markers is never touched, every content-changing write leaves a `config.toml.bak-<timestamp>` backup (last 3 kept), and a failed `herdr config check` rolls the write back automatically. Prefer pasting by hand? `keymap emit` still prints the blocks (`--style ctrlalt` / `--style menu` render preset families). Path override for scripting and tests: `HERDR_TTS_KEYMAP_FILE`.

### Option 1 — Compact map (recommended): one key opens the voice menu

The plugin ships a **voice menu popup**. Bind one free core letter (`u` is free in Herdr core defaults): the popup opens with the full cheat sheet, you press one more key, the action runs, and the popup closes itself (focus is restored). Zero collisions with Herdr core, and the whole command surface stays on screen instead of memorized. In `keymap.json`:

```json
{
  "style": "menu",
  "bindings": { "menu": "prefix+u" }
}
```

Install it with:

```bash
herdr-tts keymap adopt --style menu   # write this map into keymap.json
herdr-tts keymap apply                # install it into config.toml
```

Keys inside the menu (actions target the focused chat):

| Tecla | Acción |
|---|---|
| `r` | Play / Stop del chat enfocado |
| `p` | Pausar / Reanudar |
| `s` | Stop inmediato |
| `t` | TL;DR del chat |
| `v` | Auto-lectura on/off |
| `[` / `]` | Rebobinar / Avanzar 10 s |
| `n` / `N` | Frase siguiente / anterior |
| `m` | Mute del pane (auto al cerrarse) |
| `z` | Snooze del pane (5m → 30m → 2h → off) |
| `Z` | Snooze GLOBAL (reuniones) |
| `+` / `-` | Velocidad ±10% |
| `d` / `o` | Abrir dashboard / paleta de voz |
| `q` / `Esc` | Salir |

### Option 2 — ctrl+alt family (no prefix, no collisions)

Herdr core does not own the `ctrl+alt` family, so every voice command gets its own direct chord with zero conflicts. These are plain chords — no prefix involved. **Caveat:** `ctrl+alt+t` launches a terminal on Ubuntu/Fedora desktops, so TL;DR lives on `ctrl+alt+l` — `keymap emit --style ctrlalt` never suggests `ctrl+alt+t`.

Suggested family (deterministic — `herdr-tts keymap adopt --style ctrlalt` writes exactly this into keymap.json, `keymap apply` installs it):

| Command id | Chord | Command id | Chord |
|---|---|---|---|
| `play` | `ctrl+alt+r` | `seek_back` | `ctrl+alt+[` |
| `pause` | `ctrl+alt+p` | `seek_fwd` | `ctrl+alt+]` |
| `stop` | `ctrl+alt+s` | `sentence_next` | `ctrl+alt+n` |
| `tldr` | `ctrl+alt+l` | `sentence_prev` | `ctrl+alt+shift+n` |
| `auto` | `ctrl+alt+v` | `rate_up` | `ctrl+alt+=` |
| `mute` | `ctrl+alt+m` | `rate_down` | `ctrl+alt+-` |
| `snooze` | `ctrl+alt+z` | `dashboard` | `ctrl+alt+d` |
| `snooze_global` | `ctrl+alt+g` | `palette` | `ctrl+alt+o` |
| `menu` | `ctrl+alt+u` | `settings` | `ctrl+alt+shift+u` |

(`paragraph_next` / `paragraph_prev` have no suggested chord — assign them yourself in `keymap.json` and `keymap apply` installs them too.)

### Option 3 — Direct map (power users)

The original one-chord-per-command map: fastest to press, but several letters **shadow Herdr core defaults** — while this map is installed, those core bindings are unreachable. Opt in knowingly. This is also the default `keymap init` template, so `keymap check` reports every shadow below:

| Tecla | Comando de voz | Conflicto con Herdr core |
|---|---|---|
| `prefix+r` | Play / Stop | ⚠️ `resize` |
| `prefix+p` | Pausa / Reanudar | ⚠️ tab anterior |
| `prefix+v` | Auto-lectura on/off | ⚠️ split right |
| `prefix+z` | Snooze pane | ⚠️ zoom |
| `prefix+n` | Frase siguiente | ⚠️ tab siguiente |
| `prefix+[` | Rebobinar 10 s | ⚠️ copy mode |
| `prefix+]` | Avanzar 10 s | libre en core |
| `prefix+t` | TL;DR | libre |
| `prefix+s` | Stop | libre |
| `prefix+N` | Frase anterior | libre |
| `prefix+Z` | Snooze global | libre |
| `prefix+m` | Mute pane | libre |
| `prefix+=` / `prefix+-` | Velocidad ±10% | libre |
| `prefix+u` | Ajustes de voz y audio (popup, ver [Ajustes](#️-ajustes-de-voz-y-audio-prefixu)) | libre en core |

Install the map (one `[[keys.command]]` per assigned chord) with:

```bash
herdr-tts keymap check   # review the ⚠️ SHADOWS CORE lines first
herdr-tts keymap apply
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
herdr-tts --next-sentence      # Jump to next sentence (prefix+n)
herdr-tts --prev-sentence      # Jump to previous sentence (prefix+N)
herdr-tts --next-paragraph     # Jump to next paragraph in active playback
herdr-tts --prev-paragraph     # Jump to previous paragraph in active playback
herdr-tts --highlight          # Read focused chat with real-time karaoke word highlighting
herdr-tts --autoscroll         # Read focused chat with synchronized auto-scroll reader
herdr-tts --bionic             # Read focused chat with Bionic Reading (initial fixation bolding)
herdr-tts --zen                # Distraction-free high-contrast Zen Mode teleprompter
herdr-tts --rate-up            # Increase voice speed by +10% dynamically (prefix+=)
herdr-tts --rate-down          # Decrease voice speed by -10% dynamically (prefix+-)
herdr-tts --player-status      # Live audio position, duration and playback state
herdr-tts --toggle-auto        # Toggle background auto-speech (muted / active)
herdr-tts --snooze-pane        # Cycle snooze on the focused pane: 5m → 30m → 2h → off (prefix+z)
herdr-tts --snooze-global      # Cycle GLOBAL snooze for all agents, e.g. meetings (prefix+Z)
herdr-tts --mute-pane          # Mute/unmute the focused pane; auto-clears on pane close (prefix+m)
herdr-tts --debounce 20        # Anti-spam window (seconds) per (pane, status); 0 disables
herdr-tts --provider edge      # Select TTS provider: edge (free default), openai, elevenlabs, piper (offline)
herdr-tts --piper-model <path> # Set local Piper ONNX model path (.onnx)
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
herdr-tts --dashboard          # Live TUI dashboard pane: snooze countdowns, per-pane gating, audio history
herdr-tts --voice-palette      # fzf picker of chats and audio turns (focus / mute / snooze)
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

### 4. Piper Local Neural TTS (100% Offline, Zero-Cloud)
- **Zero Cloud & Zero Telemetry:** Runs entirely on your CPU using ONNX Runtime. Zero internet connection required.
- **Ultra-Fast CPU Inference:** Real-time synthesis with minimal RAM footprint (< 50MB).
- **Setup:**
```bash
# Install piper binary or pip package
pip install piper-tts
# Set provider to piper and specify your local model
herdr-tts --provider piper --piper-model ~/.local/share/piper/models/es_ES-davefx-medium.onnx
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

## 🪟 Audio en Windows nativo (opcional)

Si usas WSL2, `herdr-tts` puede enviar el audio al host Windows y reproducirlo allí de forma nativa (WASAPI), sin PulseAudio ni servidores intermedios:

1. **Instala el motor en Windows:** `pip install agent-tts` (desde PowerShell en Windows).
2. **Arranca el servidor de audio en Windows:** `agent-tts --winhost` (queda a la escucha en el puerto 7717).
3. **Configura el modo en WSL:** añade `TTS_PLAYBACK="winhost"` en `~/.config/herdr-tts/config.env`.

Modos disponibles de `TTS_PLAYBACK`:

* `local` (por defecto): reproduce en el propio WSL; comportamiento idéntico al actual.
* `winhost`: envía el PCM por TCP al servidor Windows. Si el servidor no responde, hay **fallback automático** a PowerShell (modo cero instalación) con un aviso en stderr.
* `wsl-ps`: fuerza el modo cero instalación sin servidor: un único `powershell.exe` persistente por ejecución recibe los grupos de audio por stdin y los reproduce casi sin pausas entre frases.
* `auto`: opción que se adapta al entorno — resuelve a `winhost` bajo WSL cuando `powershell.exe` está disponible (con fallback automático a `wsl-ps`) y a `local` en cualquier otro caso (Windows nativo, Linux nativo, o WSL sin interop). Ideal para una config sincronizada entre máquinas: no hay que fijar el modo a mano en cada entorno.

> ⚠️ **Seguridad:** `agent-tts --winhost` escucha por defecto en `0.0.0.0:7717` (puerto abierto en la LAN). Restringe el bind con `AGENT_TTS_WINHOST_BIND=127.0.0.1` o usa un firewall si no confías en tu red.

---

## ⚙️ Configuration

Persisted options live at `~/.config/herdr-tts/config.env`:

```bash
TTS_PROVIDER="edge"         # "edge" (free), "openai", or "elevenlabs"
TTS_VOICE="elvira"
TTS_RATE="+20%"
TTS_MAX_CHARS="0"           # 0 = unlimited
TTS_AUTO_SCOPE="focused"    # "focused" (recommended) or "all"
TTS_PLAYBACK="local"        # "local", "winhost" (native audio on Windows host), "wsl-ps", "windows" or "auto"

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

# Ambient pane-title glyphs (default on):
TTS_TITLE_GLYPHS="1"        # 1 = ✔/🔇/😴 prefixes on pane titles; 0 = fully off

# Stored turn audio (agent-tts audio store). Persistence is opt-in — set
# a retention window in days to enable it. Precedence:
# HERDR_TTS_ > AGENT_TTS_ > TTS_; default 0 = off:
# HERDR_TTS_AUDIO_RETENTION_DAYS="7"
# Store root override (default $HOME/.local/share/agent-tts/audio):
# AGENT_TTS_AUDIO_DIR="/path/to/audio-store"

# Managed by the voice settings popup (prefix+u): TTS_PROVIDER,
# TTS_PLAYBACK and HERDR_TTS_AUDIO_RETENTION_DAYS. The popup rewrites
# them in place (every other line is preserved byte-for-byte) and keeps
# the previous version in config.env.bak.
```

---

## 📊 Panel de control (dashboard, v3)

**Abrir el panel directamente en Herdr:**

```bash
herdr plugin pane open --plugin herdr.tts --entrypoint tts-dashboard
```

Se abre como **popup** (`placement = "popup"`, 90%×90%): modal de sesión que no toca el layout en mosaico y recupera el foco al salir (`q`). Para ligarlo a una tecla, añade esto al `config.toml` de Herdr:

```toml
[[keys.command]]
key = "prefix+d"
type = "shell"
command = "herdr plugin pane open --plugin herdr.tts --entrypoint tts-dashboard"
```

`herdr-tts --dashboard` arranca el mismo panel TUI compacto (ANSI puro, sin dependencias externas). Refresca ~1 vez por segundo y, desde la **v3**, se organiza **por chat** en lugar de por estado interno de TTS:

* **Estado global:** snooze global con cuenta atrás, auto-lectura, proveedor/voz/velocidad, modo de playback y ventana de debounce.
* **Línea de motor en vivo (v2):** cada 3 ticks el panel consulta el estado del motor por IPC (la misma vía que `--toggle-pause`) y renderiza estado con color (▶ reproduciendo / ⏸ pausado / ⏹ detenido / ⌛ sintetizando), progreso mm:ss (`00:12 / 00:48`), proveedor y voz activos, y un fragmento del texto en lectura. Si el motor no responde, la línea muestra el fallback estático del lock local con la nota explícita **"motor: no responde"** (fail-open, nunca rompe el panel).
* **Roster por chat (v3):** una línea por **chat**, fusionando tres fuentes bajo la misma clave `pane_id`: `herdr agent list` (estado del agente + título de la conversación), el ledger de snooze/mute y el de debounce. Cada línea muestra el estado del agente con icono y color — `▶` working (verde), `✔` done/blocked (amarillo, pide atención), `·` idle (tenue); valores desconocidos se muestran tal cual —, el título del chat truncado a 40 caracteres, el tipo de agente y las incidencias de voz acumuladas: 🔇 silenciado, 😴 con cuenta atrás mm:ss si está snoozed, ⏱ con los segundos restantes si la ventana anti-spam (debounce) sigue reteniendo el evento. Orden *needs-attention first*: done/blocked → working → idle; a igualdad, primero el chat con el audio más reciente. Sin CLI de Herdr o sin agentes, la sección muestra "sin datos de herdr" (fail-open).
* **Historial agrupado por chat (v3):** los audios de `history.log` se agrupan por chat: cabecera `── <título> · N audios · último hace Xm` seguida de sus últimos 3 audios (duración mm:ss + antigüedad). Con la retención activa, el watcher persiste el audio de cada turno terminado en el almacén de agent-tts y cada fila cuyo fichero sigue en disco muestra el marcador `▶` (reponible desde la paleta con ctrl-r). Los chats se ordenan por su audio más reciente y el presupuesto global de la sección es de ~24 líneas. Los chats cerrados (pane sin entrada en `herdr agent list`) siguen apareciendo identificados por su `pane_id`; sin historial, la sección se oculta por completo.
* **Audio almacenado y retención:** `history.log` gana un 6.º campo TSV opcional con la ruta del audio almacenado (`-` = retención apagada, turno sin almacenar o render propio del llamador; las filas siguen siendo de 4, 5 o 6 campos y nunca se reescriben). Los días de retención se resuelven con precedencia `HERDR_TTS_AUDIO_RETENTION_DAYS` → `AGENT_TTS_AUDIO_RETENTION_DAYS` → `TTS_AUDIO_RETENTION_DAYS` (default **0 = apagado**: nada se almacena hasta que fijas una ventana, p. ej. `7` activa el almacén con purga a 7 días; un valor no numérico cae al default; **`0` mantiene el almacenamiento desactivado** y el watcher conserva el flujo temporal de siempre: fichero en `/tmp` + borrado tras la reproducción).
* **Purga de retención desde el daemon:** el sweep del daemon ejecuta la purga del almacén de audio como máximo una vez por hora (best-effort y fail-open: un fallo solo se registra en el log y nunca afecta al bucle del daemon; con la retención en `0` se salta sin spawnear nada ni tocar el sello horario), de modo que una máquina idle también cumple la retención aunque no se genere audio nuevo.
* **Presupuesto de pantalla (v3):** el popup tiene ~50-55 filas útiles al 90%: global + motor ≈ 4 líneas, roster 1 línea por chat, historial ≤ 24, pie 1. Si hay más chats que filas, se ocultan primero los idle con el audio más antiguo y se avisa con *"… y N chats más"*; **los chats que piden atención (done/blocked) nunca se ocultan**.

El panel es de **solo lectura** frente al gate/mutex/watcher: toda mutación pasa por las funciones y flags existentes (`--mute-pane`, `--snooze-pane`, `--snooze-global`, `--rate-up/down`).

| Tecla | Acción |
| :--- | :--- |
| `q` | Salir |
| `j` / `k` | Mover el cursor entre chats |
| `m` | Silenciar / reactivar el chat seleccionado (auto-clear al cerrarse el pane) |
| `z` | Ciclar snooze del chat seleccionado: 5m → 30m → 2h → off |
| `Z` | Ciclar snooze GLOBAL (todos los agentes) |
| `+` / `-` | Velocidad de voz +10% / −10% (persistida en config) |

> **Disciplina de coste v3:** `herdr agent list` se consulta cada N ticks (default 3) con caché, el estado del motor comparte esa cadencia, y el historial se agrupa en **una sola pasada de python** por tick (el mismo intérprete del venv del motor; agrupa, ordena por recencia y convierte las marcas locales a epoch con reglas DST correctas por fecha). Todo lo demás por tick es lectura local de ficheros y bash sin forks: nunca un subshell por línea renderizada. Refresco configurable con `HERDR_TTS_DASHBOARD_REFRESH` (segundos, default `1`).

---

## 🏷️ Glifos de estado en el título del pane (v0.11)

El daemon sincroniza el **título del pane** con el estado de voz del chat, aprovechando el barrido que ya hace cada ~4s (un único `herdr agent list` por barrido, cero spawns extra):

* `✔` — el agente terminó o está bloqueado (`done` / `blocked`).
* `🔇` — el chat está silenciado (`prefix+m`).
* `😴` — el chat tiene un snooze activo (`prefix+z`). Los glifos se acumulan: `✔😴 | <título>`.

Reglas de convivencia:

* **Idempotente y rename-only-on-change:** el título original del pane se guarda una sola vez en el fichero de estado (`title_original`) y solo se llama a `herdr pane rename` cuando el título vivo difiere del esperado.
* **La restauración es sagrada:** en cuanto ningún glifo aplica, el pane vuelve a su título original y la caché se borra. Si la caché no existe, se pelan los prefijos conocidos del título actual.
* **Nunca se pelea con otras integraciones:** si el título actual no encaja con lo esperado ni empieza por nuestros marcadores (y no hay caché), se **adopta** como nuevo título original.
* **Opt-out total:** `TTS_TITLE_GLYPHS="0"` en `config.env` desactiva la función por completo — el barrido no genera ni un spawn para títulos. Los panes cerrados purgan su caché en la misma poda del daemon.

---

## 🎛️ Paleta de voz (v0.11, fzf)

Selector interactivo de chats y audios pensado para vivir en un **popup de Herdr** (`tts-palette`, 80%×70%):

```bash
herdr plugin pane open --plugin herdr.tts --entrypoint tts-palette
```

Cada fila muestra `HH:MM · título · duración · fragmento` del texto hablado (los chats sin audios aparecen como `(sin audios)`), con el título del chat truncado y el fragmento real de la última locución. Es una **foto por apertura** (sin bucle en vivo) y el preview muestra la cabecera del chat (agente, estado y gating) con **todos** sus turnos de audio, del más reciente al más antiguo.

| Tecla | Acción |
| :--- | :--- |
| `enter` / `ctrl-o` | Enfocar el chat seleccionado (`herdr pane focus`), confirmación 2s y cierre |
| `ctrl-m` | Silenciar el pane seleccionado (equivale a `prefix+m`) y salir |
| `ctrl-z` | Ciclar snooze del pane seleccionado (5m → 30m → 2h → off) y salir |
| `ctrl-r` | Reproducir el audio almacenado del turno seleccionado (`▶` en el historial) y salir |
| `esc` | Salir sin hacer nada |

La reposición con `ctrl-r` pasa por el **mismo camino de reproducción** que el daemon (`stop_audio` —el mutex que evita solapamientos— y el motor vía `--play-file`); nunca hay un segundo reproductor. Si el fichero almacenado ya no existe (retención vencida o borrado manual), muestra un aviso y no llega a spawnear el motor.

Requiere `fzf` en el `PATH` (si falta, se muestra un error accionable). Keybinding sugerido para abrir la paleta desde la shell:

```toml
[[keys.command]]
key = "prefix+o"
type = "shell"
command = "herdr plugin pane open --plugin herdr.tts --entrypoint tts-palette"
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
- [x] 🎙️ **Modular TTS Provider Backend (Edge, OpenAI, ElevenLabs & Piper):**
  - Modular provider architecture allowing users with API keys to choose ultra-realistic voice models (OpenAI `tts-1`, ElevenLabs) while preserving zero-cost Microsoft Edge Neural TTS as default, plus 100% offline local synthesis via Piper ONNX.
- [x] 📦 **Standalone Core Library Decoupling (`agent-tts`):**
  - Extracted the playback engine, IPC socket server, provider abstractions, audio mutex lock, and text sanitizers into an independent, standalone Python package / CLI ([`chiptime/agent-tts`](https://github.com/chiptime/agent-tts)). `herdr-tts` now consumes it as a clean upstream dependency.
- [x] 🖍️ **Visual Word & Sentence Highlighting:**
  - Real-time ANSI word and sentence highlighting in terminal panes synchronized with temporal audio boundaries (`agent-tts --highlight`).
- [x] 💡 **Smart Architectural Summarizer (TL;DR Pre-Flight):**
  - Instant zero-latency, 100% offline heuristics to condense massive terminal dumps (git diffs, status, test runner logs, compiler stack traces) into crisp 1–2 sentence voice recaps (`herdr-tts --tldr`, `prefix + t`).
- [x] ⏪ **Smart Auto-Rewind on Resume:**
  - Automatically rewinds 2–3 seconds when resuming playback after a pause period, helping the developer immediately regain cognitive context without manual seeking.
- [x] 📑 **Semantic Navigation (Jump by Sentence / Paragraph):**
  - Advance or rewind by full grammatical sentence boundaries (`prefix + n` / `prefix + N`, `--next-sentence`, `--prev-sentence`) and multi-line paragraph boundaries (`--next-paragraph`, `--prev-paragraph`) with intelligent threshold auto-rewind.
- [x] 🗣️ **Technical Pronunciation Lexicon & Text Normalization:**
  - Expanded developer lexicon and SSML phonetic normalization for developer jargon (PostgreSQL, Kubernetes, JWT, UUID, SSH, TLS, JSON, YAML, SQL, IPC, stdout/stderr, git rebase/merge, semver).
  - Conversational currency formatting (`$45.20` → 45 dólares con 20 centavos, `€15` → 15 euros, `£`, `¥`) and common Spanish abbreviations (`p. ej.` → por ejemplo, `aprox.` → aproximadamente, `etc.` → etcétera, `Dr.` → doctor, `núm. 5` → número 5).
  - Conversational formatting for hardware/performance units (`ms`, `s`, `MB`, `GB`, `GHz`, `kHz`, `kbps`) and semantic version tags (`v1.2.3`).
  - User-extensible custom dictionary support via `~/.config/agent-tts/lexicon.json` or `~/.config/herdr-tts/lexicon.json`.
- [x] 🌐 **Automatic Language Detection & Dynamic Voice Switching:**
  - Fast, zero-dependency statistical language classifier detects embedded English code snippets, error traces, or documentation inside Spanish explanations and switches neural voices dynamically on the fly (`--auto-lang`, `herdr-tts --auto-lang on`).
- [x] 📻 **Private Podcast / Audio RSS Feed:**
  - Export and sync generated audio sessions into a standard RSS 2.0 / iTunes XML feed with built-in zero-dependency HTTP server (`herdr-tts --podcast-serve`, `herdr-tts --podcast on`). Listen on mobile apps like Pocket Casts, Overcast, or Apple Podcasts.
- [x] 🔒 **Zero-Cloud Local Neural Synthesis (Piper / Kokoro / Sherpa-ONNX):**
  - Fully offline, on-device neural TTS engine running 100% on CPU without requiring internet access or third-party APIs (`herdr-tts --provider piper`, `agent-tts --provider piper`).
- [x] 🚀 **Low-Latency Streaming Playback:**
  - Long agent responses now begin vocalizing in ~300–600ms because `herdr-tts` inherits `agent-tts`'s pipelined streaming synthesis: the first sentence group plays while the rest is still synthesizing.
- [x] 🔕 **Granular Per-Pane Snooze & Temporary Mute:**
  - Independent time-based snooze cycling per pane (`prefix + z`: 5m, 30m, 2h, off), global snooze (`prefix + Z`) and pane-level mute (`prefix + m`, auto-cleared when the pane closes), preventing notification fatigue in multi-agent workspaces without muting other active panes (clean-room design using local timestamp state).
- [x] ⏱️ **Anti-Spam State Debouncing:**
  - Configurable debounce window (`debounce_seconds = 20` via `TTS_DEBOUNCE_SECONDS` / `--debounce`) preventing rapid re-triggering of repeated completion or blocked states from the same pane within a short time window.
- [x] 📊 **Interactive TUI Dashboard Pane:**
  - Native Herdr dashboard pane entrypoint displaying live agent speech states, recent audio logs, countdowns for snoozed panes, volume controls, and provider/voice toggles. (v1: pure-ANSI clear+redraw, read-only gating view, controls reuse the existing mute/snooze/rate functions; audio history via `${XDG_STATE_HOME:-~/.local/state}/herdr-tts/history.log`.)
  - v2: live engine line via the engine IPC status (state with color, mm:ss progress, active provider/voice and text snippet) on a shared every-N-ticks cache cadence, fail-open "motor: no responde" fallback, and real durations in the audio history ledger (MP3 decoded with the engine's own miniaudio, one best-effort probe per render).
  - v3: **per-chat view** — the roster merges `herdr agent list` (agent state + chat title), the snooze/mute ledger and the debounce ledger under the same `pane_id` key, rendering one line per chat with needs-attention-first sorting (done/blocked → working → idle, most-recent-audio tiebreak), voice overlays (🔇 muted, 😴 snooze countdown, ⏱ debounce hold) and a screen-row budget that drops oldest-idle chats first and never hides chats needing attention; the audio history renders **grouped per chat** (`── <title> · N audios · último hace Xm` + last 3 audios each) via a single python pass per tick with correct per-date DST handling, keeping closed panes visible by pane id and hiding the section entirely when the ledger is empty.
- [ ] 🎙️ **Push-to-Talk Two-Way Intercom:**
  - Dictate instructions directly to the focused agent pane via hotkey (`prefix + c`), transcribing locally via lightweight fast STT (Whisper.cpp / whisper-rs) and injecting the prompt directly into Herdr's active pane.



---

## 📄 License

MIT © 2026 [ChipTime (Bruno Silva)](https://github.com/chiptime)
