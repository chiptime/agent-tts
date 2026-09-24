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
| **Intermediate-Step Filtering** | ❌ No | ❌ No | 🟢 **Settle window** (intermediate `done` flickers dropped) |
| **Mobile Audio Push** | ❌ No | ⚠️ Call only | 🟢 **Native `ntfy.sh` with inline MP3 player** |
| **Optional Web Deep-Linking** | ❌ No | ❌ No | 🟢 **Collie, custom dashboard, or standalone** |
| **On-Demand Reading** | ❌ Passive trigger only | ⚠️ Phone only | 🟢 **`prefix + r`** (Play/Stop toggle) |
| **Instant Kill-Switch** | ❌ No | ❌ No | 🟢 **`prefix + s`** (<0.2s instant halt) |
| **Agent Terminal Cleaning** | ❌ Minimal (200 char hard limit) | ❌ LLM-generated summary | 🟢 **Deep cleaner** (ANSI, box, tokens) |

### Where it stands in the 2026 landscape

The TTS/voice-notification space for AI agents is growing fast, but existing solutions are typically **single-agent and hook-bound**: they serve one host's events, speak everything by default, stop at text-only remote notifications, and read your terminal verbatim — ANSI noise and secrets included. `herdr-tts` is built around the six capabilities below, and it is the only project in its space that checks all six at once.

**The six boxes only `herdr-tts` checks at once:**

1. **Multi-agent events with real transcript extraction** — OpenCode SQLite, Claude/Codex JSONL connectors plus scrollback fallback, from one plugin.
2. **Full anti-fatigue gating** — settle window (intermediate `done` flickers dropped) + per-pane/global mute & snooze + debounce.
3. **Remote surfaces with audio** — ntfy push with inline MP3 player + private podcast RSS feed. No verified competitor offers any remote audio surface.
4. **Secret redaction & deep terminal cleaning** before anything is spoken, pushed or feeded.
5. **Interactive playback** — pause/seek/sentence navigation with cognitive auto-rewind, TUI dashboard and fzf palette.
6. **Zero-cost zero-config default** — Edge Neural, no API keys, no downloads, native C playback under 2.5 MB RAM.

---

## 🧠 Core Architecture

### Surface contract v1 (for external consumers)

herdr-tts is the OFFICIAL speech backend of herdr-brain, which consumes
ONLY this versioned CLI surface — the engine underneath (venv, python
entrypoints) is herdr-tts's private detail and may change freely:

```bash
bin/herdr-tts --contract-version          # prints "1"; gate on >= 1
bin/herdr-tts --render-text <out.mp3> [texto...] \
      [--voice <voz>] [--rate <vel>] \
      [--agent <tipo>] [--session-id <id>]  # render to MP3, no playback
bin/herdr-tts --speak <texto>               # speak on PC speakers, exit
```

- `--render-text` reads the text from argv (or stdin when piped), renders
  a valid MP3 to the given path and exits 0; any other exit is a failure.
- `--voice`/`--rate` are v1 passthrough (default: the
  `HERDR_TTS_VOICE_OVERRIDE`/config voice and rate).
- The command is self-sufficient: it bootstraps its own venv on first
  use. Consumers must not invoke venv internals.

### Reader pipeline: `--render-html` (HT-15)

On-demand host-layer render (no audio, no daemon; `lib/reader_pipeline.py`):
sanitizes an agent message — ANSI stripped, secrets redacted BEFORE any
transformation, terminal chrome dropped while fences/tables/markers survive —
then emits GFM-subset HTML whose sentence anchors carry `data-sent-idx`,
`data-para-idx` and `id="tts-sent-<idx>"`, index-identical to the pinned
engine's boundary enumeration (the same indices `scroll-info` reports during
playback, so HT-16 can karaoke-highlight `#tts-sent-<sent_idx>`).

```bash
bin/herdr-tts --render-html <in> <out.html> [--map <out.map.json>]
```

- Exit codes: `0` success (including degraded/malformed input and
  `coverage` alignment), `1` usage, `2` input unreadable or redaction
  failure (fail closed — no file written), `3` engine (agent_tts)
  unavailable.
- The sidecar map is opt-in (`--map`, default off). It records the anchor
  contract `reader-pipeline/anchors@1`: per-sentence `text`, DOM
  `selector`, `block_ids`, `fragments`, `exact` flag, the alignment mode
  (`exact` | `coverage`) and the staleness tuple
  (`engine.lang/max_chars/summarize/lexicon_fp`) — anchors are valid only
  against a speech invocation with the same tuple.
- Everything is `html.escape`d; link schemes are restricted to
  http/https (others render as plain text). Zero new dependencies:
  stdlib + the pinned `agent_tts` only.

```
                      ┌──────────────────────────────────────────┐
                      │ Herdr Socket API                         │
                      │ herdr agent wait / agent get / pane read │
                      └──────────────────────────────────────────┘
                                            │  one watcher per agent pane
                                            ▼
                   ┌────────────────────────────────────────────────┐
                   │ 1. SETTLE WINDOW  ·  TTS_SETTLE_SECONDS = 5s   │
                   │ a done must keep holding to fire the pipeline: │
                   │ an intermediate working→done→working flicker   │
                   │ (tool batch, subagent, thinking) is dropped —  │
                   │ no synthesis, no mobile push.  0 = off         │
                   └────────────────────────────────────────────────┘
                                            ▼
                    ┌──────────────────────────────────────────────┐
                    │ 2. GATING LEDGER  (snooze state file)        │
                    │ per-pane mute · snooze 5m/30m/2h pane/global │
                    │ anti-spam debounce 20s per (pane, status)    │
                    └──────────────────────────────────────────────┘
                                            ▼
                    ┌──────────────────────────────────────────────┐
                    │ 3. TEXT ACQUISITION                          │
                    │ known agent → --agent + --session-id forward │
                    │ generic shell → raw scrollback capture       │
                    └──────────────────────────────────────────────┘
                                            ▼
                ┌──────────────────────────────────────────────────────┐
                │ 4. agent-tts ENGINE SYNTHESIS                        │
                │ deep cleaner (ANSI, boxes, spinners, tokens, tables) │
                │ secret redactor · providers: edge (free) · piper ·   │
                │ kokoro · openai · elevenlabs — pipelined streaming   │
                └──────────────────────────────────────────────────────┘
                                            │
                  ┬─────────────────┬───────┴─────────┬─────────────────┬
                  ▼                 ▼                 ▼                 ▼
          ┌───────────────┐  ┌─────────────┐  ┌───────────────┐  ┌────────────┐
          │ MOBILE PUSH   │  │ PODCAST RSS │  │ AUDIO HISTORY │  │ PANE TITLE │
          │ ntfy.sh push, │  │ episode to  │  │ ledger +      │  │ glyphs     │
          │ inline MP3 +  │  │ the :8844   │  │ retention     │  │ done/mute/ │
          │ deep links    │  │ feed        │  │ audio store   │  │ snooze     │
          └───────────────┘  └─────────────┘  └───────────────┘  └────────────┘
                  │
                  ▼  local voice only — re-checked at play time
              ┌──────────────────────────────────────────────────────────┐
              │ 5. LOCAL VOICE GATES: auto-mute off? · scope=focused and │
              │ pane on screen? · audio mutex free (no voice clashes)?   │
              └──────────────────────────────────────────────────────────┘
                                            ▼
             ┌────────────────────────────────────────────────────────────┐
             │ NATIVE PLAYBACK   miniaudio → PulseAudio/PipeWire (Linux), │
             │ /mnt/wslg/PulseServer (WSL2), winhost TCP→WASAPI, wsl-ps,  │
             │ afplay (macOS)                                             │
             └────────────────────────────────────────────────────────────┘

INTERACTIVE SURFACES (user-initiated — never wait for events)
─────────────────────────────────────────────────────────────
declarative keymap.json → prefix chords: r play/stop · p pause
· s stop · t TL;DR · v auto-mute · [ ] seek ±10s · n/N sentence
· m pane mute · z/Z snooze · +/- rate
→ fzf voice palette · one-key voice menu · settings popup
→ dashboard TUI: chat roster, per-chat history, live engine
  status (pause/seek/next ride the engine Unix-socket IPC)
```

### Agent Transcript Connectors

herdr-tts is a **thin host**: it only forwards the agent identity + session id (`--agent` / `--session-id`, read from `herdr agent get` → `.result.agent.agent_session`) to the `agent-tts` engine, which **owns the connector layer** and resolves the real last assistant message itself — zero TUI chrome, no regex scraping. Connectors included in the engine today: **OpenCode** (local SQLite transcript, read-only), **Claude Code** (local JSONL sessions, reverse tail scan), and an automatic **terminal scrollback fallback** for panes without a structured source (Antigravity, shells, any agent the engine cannot resolve).

---

## 📦 Installation

### 1. From the Herdr Plugin Registry (Recommended)

```bash
herdr plugin install chiptime/herdr-tts
```

Herdr runs the `[[build]]` hook (`scripts/bootstrap.sh`), which creates an isolated Python venv and installs the `agent-tts` engine from an **immutable pinned ref** (full commit SHA while agent-tts publishes no tags). The build works on `uv`-only and `python3`-only machines alike: installs route through `uv pip` or `python -m pip`, never through a venv `bin/pip` that may not exist.

There is no `plugin update` in Herdr v1 — reinstall from the registry to refresh, or re-run the installer below (its re-run performs a guarded in-place upgrade).

### 2. curl | sh Fallback (Non-Registry)

```bash
curl -fsSL https://raw.githubusercontent.com/chiptime/herdr-tts/v0.16.0/scripts/install.sh | bash
```

The installer is **tag-pinned** (`v0.16.0` by default) and, in order: verifies prerequisites (`git`, `jq`, `herdr`, plus `uv` or `python3`) before touching anything; refuses to install over a linked dev checkout and tells you the exact `plugin unlink` / `plugin uninstall` command to migrate; clones to `~/.local/share/herdr-tts/plugin`; bootstraps the venv; adopts the collision-free `menu` keymap unless a `keymap.json` already exists (never overwrites; `--no-keymap` skips the step entirely); verifies the daemon and prints the manual uninstall steps.

Re-running it upgrades in place: the checkout's `origin` remote is compared against the canonical URL, the tag is re-fetched, and `agent-tts` is refreshed past the plugin's never-upgrade gate. A mismatched remote aborts before writing anything.

**Escape hatch (mutable ref — use deliberately):**

```bash
# track main instead of the pinned tag
HERDR_TTS_REF=main curl -fsSL https://raw.githubusercontent.com/chiptime/herdr-tts/main/scripts/install.sh | bash
```

### Uninstall

The installer prints these steps at the end of every run:

1. `herdr plugin uninstall herdr.tts` (or `herdr plugin unlink` for a linked checkout)
2. Stop the daemon: `herdr-tts --stop` (or the pid recorded in `~/.local/state/herdr-tts/daemon.pid`)
3. `rm -rf ~/.local/share/herdr-tts` — removes the checkout and the venv
4. Remove the managed keymap block (the lines between the herdr-tts markers) from your herdr config

### 3. Local Development / From Source

```bash
git clone https://github.com/chiptime/herdr-tts.git ~/Code/personal/herdr-tts
herdr plugin link ~/Code/personal/herdr-tts
```

Editable installs of the engine are **opt-in**: `bootstrap.sh` only uses a local `~/Code/personal/agent-tts` checkout when `HERDR_TTS_DEV=1` is set — public installs always resolve the pinned remote ref, regardless of what exists on the machine.

```bash
export HERDR_TTS_DEV=1   # agent-tts editable from ~/Code/personal/agent-tts
bash scripts/bootstrap.sh
```

### 4. Hand It to a Coding Agent (Paste-Ready)

Paste this into Claude Code, OpenCode, Codex or Pi and the agent installs and verifies the plugin itself:

```text
Install the herdr-tts voice-notification plugin for my Herdr setup.

1. Check the prerequisites first: git, jq, the `herdr` CLI, and either `uv`
   or `python3` must be on PATH. Abort and report if any is missing.
2. Run the tag-pinned installer:
   curl -fsSL https://raw.githubusercontent.com/chiptime/herdr-tts/v0.16.0/scripts/install.sh | bash
3. Verify: `herdr-tts --contract-version` must print 1, and
   `herdr-tts --status` must print the current state. Report both outputs.
4. Publish your operator skill so you can drive the plugin later:
   herdr-tts skill install <your-agent>   # claude-code | opencode | codex | pi
5. WARNING: never run `herdr server stop` to restart or reset anything
   TTS-related — it stops the WHOLE Herdr server and kills every agent pane.
   For TTS use `herdr-tts --restart-daemon` (daemon) or `herdr-tts --stop`
   (audio only).
6. Do not edit ~/.config/herdr-tts/config.env directly; use the CLI flags.
Report the final `herdr-tts --status` output when done.
```

### Tested Platforms

Honest coverage as of `v0.16.0`:

- **Linux** — primary target; the platform the smoke suite (`scripts/smoke-tests.sh`) runs on.
- **WSL2** — covered by the installer: the same XDG/bash installer applies, and audio goes through WSLg PulseAudio or the `winhost`/`wsl-ps` Windows-host playback modes.
- **macOS** — covered by the installer (bash + XDG paths, BSD-safe `stat` in the codebase, CoreAudio via the engine); best-effort: no automated suite runs on it yet.

There is **no CI matrix**: the repository ships no GitHub Actions workflows, so "covered" above means the installer and code paths are written and reviewed for these platforms, and the automated smoke suite currently runs on Linux only.

### 5. Publish the Skill into Your Coding Agent

`herdr-tts skill install <agent>` writes a managed `SKILL.md` into the agent's user-level skills directory so it can operate the plugin itself — mute, snooze, status, replay and keymap checks — through the real CLI. Same never-overwrite policy as the keymap: an existing or user-modified file is never replaced without `--force`, and uninstall refuses files that do not carry the managed marker.

```bash
herdr-tts skill install claude-code    # or: opencode | codex | pi
herdr-tts skill list                   # supported agents + install state
herdr-tts skill uninstall claude-code  # marker-guarded removal
```

| Agent | User-level skills directory |
|---|---|
| Claude Code | `~/.claude/skills/herdr-tts/SKILL.md` |
| OpenCode | `~/.config/opencode/skills/herdr-tts/SKILL.md` |
| Codex | `~/.codex/skills/herdr-tts/SKILL.md` (`CODEX_HOME` honored) |
| Pi | `~/.pi/agent/skills/herdr-tts/SKILL.md` |

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

The plugin ships a **voice menu popup**. Bind one free core letter (`u` is free in Herdr core defaults): the popup opens with the full cheat sheet, you press one more key, the action runs, and the popup closes itself (focus is restored) — press `a` instead and the **Ajustes de voz y audio** view opens *inside* the same popup (see [Ajustes de voz y audio](#️-ajustes-de-voz-y-audio)). Zero collisions with Herdr core, and the whole command surface stays on screen instead of memorized. In `keymap.json`:

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
| `R` | Lector en vivo de la reproducción actual (popup karaoke; `q` / `Esc` cierran) |
| `a` | Abrir Ajustes de voz y audio (dentro del menú; índice de categorías → `q`/`Esc`/`Enter` vuelve al índice y luego al menú) |
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
| `menu` | `ctrl+alt+u` | `reader_open` | `ctrl+alt+shift+r` |

(`paragraph_next` / `paragraph_prev` have no suggested chord, and `settings` ships without one too — the settings view lives inside the voice menu (key `a`); bind any of them yourself in `keymap.json` and `keymap apply` installs them too.)

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
| `prefix+R` | Lector en vivo (karaoke) | libre |

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
herdr-tts --auto-on            # Force background auto-speech on: read every finished agent (default)
herdr-tts --auto-off           # Mute background auto-speech: manual only (prefix+r / htr on demand)
herdr-tts --snooze-pane        # Cycle snooze on the focused pane: 5m → 30m → 2h → off (prefix+z)
herdr-tts --snooze-global      # Cycle GLOBAL snooze for all agents, e.g. meetings (prefix+Z)
herdr-tts --mute-pane          # Mute/unmute the focused pane; auto-clears on pane close (prefix+m)
herdr-tts --debounce 20        # Anti-spam window (seconds) per (pane, status); 0 disables
herdr-tts --provider edge      # Select TTS provider: edge (free default), openai, elevenlabs, piper (offline)
herdr-tts --piper-model <path> # Set local Piper ONNX model path (.onnx)
herdr-tts --openai-key <key>   # Set OpenAI API key for tts-1 / tts-1-hd
herdr-tts --eleven-key <key>   # Set ElevenLabs API key
herdr-tts --status             # Show full service configuration, provider, and player state
herdr-tts --restart-daemon     # Restart the TTS daemon (applies pending provider/playback settings)
herdr-tts --voice alvaro       # Set voice (elvira, alvaro, ximena, dalia, jorge, en, nova, rachel)
herdr-tts --rate +25%          # Set speech speed (+0%, +20%, +35%)
herdr-tts --ntfy-topic <topic> # Set ntfy.sh topic for mobile audio notifications (or 'off')
herdr-tts --web-url <url>      # Set optional web UI/dashboard base URL for deep-links (or 'off')
herdr-tts --collie-url <url>   # Alias for --web-url (sets action button to 'Abrir Collie')
herdr-tts --click-redirect on  # Optional: tap notification body to open web URL directly (default: off)
herdr-tts --render-pane <pane> # Export clean assistant speech of a pane directly to .mp3
herdr-tts --speak "Hello"      # Synthesize custom text directly
herdr-tts --render-html in.txt out.html # Sanitize an agent message into anchored reader HTML (no audio; opt-in sidecar: --map out.map.json)
herdr-tts --dashboard          # Live TUI dashboard pane: snooze countdowns, per-pane gating, audio history
herdr-tts --voice-palette      # fzf picker of chats and audio turns (focus / mute / snooze)
herdr-tts --reader             # Live reader popup: karaoke follow-along of the current playback
```

* **Supervised daemon startup:** the plugin's `[[startup]]` runs `_daemon-supervised` — a foreground watchdog that relaunches the daemon if it dies unplanned (5s backoff). Deliberate stops (`--restart-daemon`, the `R` key, single-instance takeover) arm a stop flag the supervisor consumes, so restarts are never fought over. Starts, deaths, relaunches and exit reasons land in `~/.local/state/herdr-tts/daemon.log`, so a silent death can't happen unnoticed.

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

> **Honesty note:** Edge is free but **not contractual** — it rides an undocumented Microsoft service that can throttle or block. It stays the default because it costs you nothing (no keys, no downloads, works out of the box); if that risk matters to you, Kokoro below is the recommended one-command upgrade.

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

### 5. Kokoro-82M ONNX (100% Offline, Studio Quality) — The Recommended Upgrade
- **State-of-the-Art Local Neural TTS:** ~325 MB model delivering studio-grade quality fully on CPU — zero cloud, zero API keys.
- **The direct upgrade from Edge:** one command, no API keys ever; the only cost is a one-time ~325 MB model download, and it becomes a fully on-device default immune to rate limits.
- **Voice Families:** American, British, Spanish and more, mapped through the engine's voice manager.
- **Setup:**
```bash
agent-tts voice install kokoro
herdr-tts --provider kokoro
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
* `windows`: directo al host Windows si el receptor está corriendo; si no, reproducción local (WSLg) — sin PowerShell.
* `auto`: opción que se adapta al entorno — resuelve a `winhost` bajo WSL cuando `powershell.exe` está disponible (con fallback automático a `wsl-ps`) y a `local` en cualquier otro caso (Windows nativo, Linux nativo, o WSL sin interop). Ideal para una config sincronizada entre máquinas: no hay que fijar el modo a mano en cada entorno.

> ⚠️ **Seguridad:** `agent-tts --winhost` escucha por defecto en `0.0.0.0:7717` (puerto abierto en la LAN). Restringe el bind con `AGENT_TTS_WINHOST_BIND=127.0.0.1` o usa un firewall si no confías en tu red.

---

## 🔒 Privacy & Security

herdr-tts speaks your terminal out loud and can push audio to your phone — here is exactly what happens with your data, by default and by option.

**What never leaves your machine:**

* Transcript acquisition, terminal cleaning, secret redaction and — with local providers — synthesis all happen locally. The plugin adds no telemetry, no analytics, no phoning home.
* The LLM summary chain, when wired up, is **strictly opt-in**: nothing is sent to any model unless you configure it yourself.

**What can leave, and how it is protected:**

* **Edge TTS (default):** the sanitized text of the spoken turn is sent to Microsoft's speech endpoint for synthesis. That is the only network call on the default path — and the reason it needs no keys. Fully on-device alternative: Piper or Kokoro.
* **ntfy push (optional):** the event MP3 and title go to your configured ntfy server. Your topic string is the bearer credential — pick an unguessable one, and point `NTFY_SERVER` at a self-hosted instance for sensitive fleets.
* **Podcast feed (`--podcast-serve`):** serves your generated episodes over HTTP on port 8844 — run it on a trusted network only.

**Built-in hygiene, on by default:**

* **Secret redactor (`redact.py`):** common credential shapes (`sk-…`, `ghp_…`, JWTs, `Authorization` headers, PEM blocks) are stripped before anything is synthesized, pushed or feeded — a hygiene step that is rare to nonexistent in this space.
* **Deep terminal cleaner (`cleaner.py`):** ANSI escapes, box borders, spinners and token counters are removed so what gets spoken is prose — never raw terminal soup.

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

# Event filtering windows (seconds):
TTS_DEBOUNCE_SECONDS="20"   # anti-spam window per (pane, status); 0 = off
TTS_SETTLE_SECONDS="5"      # a done must keep holding this long to fire;
                            # intermediate working→done→working flickers are
                            # dropped (no TTS, no push); 0 = off

# Stored turn audio (agent-tts audio store). Persistence is opt-in — set
# a retention window in days to enable it. Precedence:
# HERDR_TTS_ > AGENT_TTS_ > TTS_; default 0 = off:
# HERDR_TTS_AUDIO_RETENTION_DAYS="7"
# Store root override (default $HOME/.local/share/agent-tts/audio):
# AGENT_TTS_AUDIO_DIR="/path/to/audio-store"

# UI language for terminal, notifications and TUI text:
# "en" (default) or "es". The env var HERDR_TTS_LANG overrides the
# config value; anything else falls back to English.
HERDR_TTS_LANG="en"

# Managed by the voice settings view (voice menu → `a`, or
# `herdr-tts --voice-settings`): TTS_PROVIDER,
# TTS_PLAYBACK, HERDR_TTS_AUDIO_RETENTION_DAYS, TTS_SETTLE_SECONDS and
# HERDR_TTS_LANG (interface language, `l` on the settings index; the
# full key list lives in the Ajustes section).
# The popup rewrites
# them in place (every other line is preserved byte-for-byte) and keeps
# the previous version in config.env.bak.
```

---

## 📊 Panel de control (dashboard, v3.1)

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
* **Audio almacenado y retención:** `history.log` gana un 6.º campo TSV opcional con la ruta del audio almacenado (`-` = retención apagada, turno sin almacenar o render propio del llamador; las filas siguen siendo de 4, 5 o 6 campos y nunca se reescriben). Los días de retención se resuelven con precedencia `HERDR_TTS_AUDIO_RETENTION_DAYS` → `AGENT_TTS_AUDIO_RETENTION_DAYS` → `TTS_AUDIO_RETENTION_DAYS` (default **0 = apagado**: nada se almacena hasta que fijas una ventana, p. ej. `7` activa el almacén con purga a 7 días; un valor no numérico cae al default). **La retención se aplica al vuelo**: el watcher re-lee `config.env` en cada turno, así que cambiarla desde el menú (`prefix+u` → `a` → `r`) no requiere reiniciar el daemon; el proveedor y el destino de reproducción sí (snapshot al arranque). Una variable exportada en el entorno manda sobre el fichero.
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
>
> **Frames atómicos (v3.1):** el popup se repinta construyendo el frame completo en un buffer y emitiéndolo en **una sola escritura física** (cursor-home + frame + erase-below): nunca clear completo ni escrituras por línea. Cada línea se recorta al ancho real del popup (los anchos de los campos encogen proporcionalmente bajo presión de columnas) y la altura se limita a `LINES-1`: bajo presión de filas el presupuesto encoge primero el historial y luego el roster — los chats que piden atención y las líneas fijas nunca se sacrifican. Un frame idéntico al anterior no escribe nada.

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

## ⚙️ Ajustes de voz y audio

`prefix+u` abre el **menú de voz**; dentro del menú, la tecla `a` abre los **Ajustes** — ahora en dos niveles: primero un **índice de categorías** y, dentro de cada una, **solo sus ajustes** con el mismo paradigma de siempre (una tecla cicla el valor y se guarda al momento en `~/.config/herdr-tts/config.env`):

| Tecla | Categoría | Ajustes dentro |
|---|---|---|
| `v` | 🎙 Voz | proveedor, voz global, prefijo hablado, auto-asignación, idioma |
| `a` | 🔊 Audio | destino de reproducción, click directo, retención, asentamiento |
| `n` | 🔔 Notificaciones | topic ntfy, podcast feed, redirección web |
| `r` | ⚙️ Lectura automática | auto-lectura, alcance, debounce |

En el **índice** hay además dos teclas globales: `l` cicla el **idioma de la interfaz** (`en` ↔ `es`, se guarda en `HERDR_TTS_LANG` y el propio popup se re-renderiza al instante en el idioma elegido; los procesos nuevos lo leen de `config.env`) y `R` **reinicia el daemon**.

### 🎙 Voz (`v`)

| Tecla | Ajuste | Ciclo |
|---|---|---|
| `p` | Proveedor TTS | `edge` (gratuito, por defecto) → `openai` → `elevenlabs` → `piper` → `kokoro` |
| `g` | Voz global | cicla `elvira` (default) → `alvaro` → `ximena` → `dalia` → `jorge` → `en` → `nova` → `rachel`; es la voz por defecto para los chats sin voz propia asignada en la paleta |
| `n` | Prefijo hablado | `off` ↔ `on` (sintetiza `"agente: "` antes del texto con la voz del chat); escribe `"prefix"` en `voices.json` y aplica al vuelo |
| `u` | Auto-asignación | `off` ↔ `on` (los `agent_type` sin regla rotan una voz determinista del proveedor activo); escribe `"auto_assign"` en `voices.json` y aplica al vuelo |
| `i` | Detección de idioma | `off` ↔ `on` (cambia de voz ES/EN al vuelo según el texto) |

### 🔊 Audio (`a`)

| Tecla | Ajuste | Ciclo |
|---|---|---|
| `d` | Destino de reproducción | `local` → `winhost` → `wsl-ps` → `windows` → `auto` |
| `c` | Click directo | `off` ↔ `on` (con `on`, tocar la tarjeta de notificación abre el navegador) |
| `r` | Audio retenido (días) | `0` (apagado) → `1` → `3` → `7` → `14`; se aplica al vuelo, sin reiniciar |
| `s` | Asentamiento done (segundos) | `0` (instantáneo) → `2` → `5` → `10` → `15` → `30` |

### 🔔 Notificaciones (`n`)

| Tecla | Ajuste | Ciclo |
|---|---|---|
| `t` | Topic ntfy | texto libre: escribe el topic (`Enter` vacío, `off` o `none` = desactivar push); el topic actúa de credencial en ntfy.sh — compártelo solo con tus dispositivos |
| `f` | Podcast feed | `off` ↔ `on` (publica cada audio en el feed RSS privado) |
| `w` | Redirección web | texto libre: escribe la URL (`Enter` vacío = desactivar); guarda `WEB_URL` y `COLLIE_URL`; una URL con "collie" fija `WEB_LABEL="Collie"` si la etiqueta está vacía; el marcador `{pane_id}` se guarda tal cual |

### ⚙️ Lectura automática (`r`)

| Tecla | Ajuste | Ciclo |
|---|---|---|
| `v` | Auto-lectura | activa (te lee al terminar cada agente) ↔ silenciada (solo manual: `prefix+r` / `htr` bajo demanda); persiste en el marcador `auto_muted` — el mismo de `--toggle-auto` — y aplica al vuelo, sin reiniciar el daemon |
| `a` | Alcance auto-lectura | `focused` (solo el chat en foco) ↔ `all` (cualquiera sin solapar) |
| `b` | Debounce anti-spam | `0` (desactivado) → `10` → `20` → `30` → `60` segundos por (pane, estado) |

* **Navegación**: dentro de una categoría, `q` / `Esc` / `Enter` **vuelven al índice**; desde el índice salen de los Ajustes — al menú principal si entraste con `a`, o cierre directo en el popup independiente (`herdr-tts --voice-settings`, acción `voice-settings` del plugin, entrypoint `tts-settings`). `settings` sigue siendo un id ligable en `keymap.json`, pero **sin acorde por defecto**: los ajustes no gastan una tecla de core.
* Kokoro y Piper requieren instalar el modelo antes: `agent-tts voice install <modelo>`.
* La escritura es **gestionada**: solo toca las claves `TTS_PROVIDER`, `TTS_AUTO_SCOPE`, `TTS_AUTO_LANG`, `PODCAST_ENABLED`, `TTS_DEBOUNCE_SECONDS`, `NTFY_TOPIC`, `TTS_VOICE`, `TTS_PLAYBACK`, `HERDR_TTS_AUDIO_RETENTION_DAYS`, `TTS_SETTLE_SECONDS`, `WEB_URL`, `COLLIE_URL`, `WEB_LABEL`, `CLICK_REDIRECT`, `HERDR_TTS_LANG` y `TTS_THEME` de `config.env` — reescribe la línea existente o añade un bloque gestionado al final, preserva el resto del fichero byte a byte, escribe de forma atómica (tmp + mv) y deja la versión previa en `config.env.bak` (las excepciones son `v`, que usa el marcador `auto_muted`, y `n`/`u`, que escriben los flags de `voices.json`). `TTS_THEME` (tema del terminal, `dark`|`light`) es una clave avanzada **sin knob en Ajustes**: se gestiona vía `config_set` o editando `config.env` directamente — pensada para operadores con terminales de fondo claro (p. ej. roster en paneles de agentes).
* La tecla `R` (mayúscula, en el **índice**) **reinicia el daemon** al instante y confirma en pantalla con `✓ Daemon reiniciado`; equivale a `herdr-tts --restart-daemon`.
* Los cambios aplican a **nuevos procesos**: `p`, `g`, `i`, `f`, `b`, `d`, `s` y `w` aplican al reiniciar el daemon — ahora con una tecla (`R` en el índice de Ajustes) o `herdr-tts --restart-daemon`. Las excepciones son `v`, `n`, `u` y `r`: se consultan en cada evento o frame, así que cambian **al vuelo**, sin reiniciar. El idioma de la interfaz (`l`) también cambia al vuelo **dentro del popup** (re-renderiza al instante); el resto de procesos lo aplican al arrancar, con precedencia `HERDR_TTS_LANG` de entorno > `config.env` > `en`. El tema (`TTS_THEME`, sin knob — ver arriba) también se adopta en vivo: un popup abierto re-renderiza con el nuevo tema en el siguiente frame — títulos en color de acento, pistas y marcos en atenuado, notas y avisos coloreados — y el **dashboard en marcha lo adopta en el siguiente fotograma** (re-lee `TTS_THEME` de `config.env` en cada frame); el resto de superficies lo aplican al arrancar, con la misma precedencia para `TTS_THEME`.

---

## 🗣️ Voz por agente y chat (PRD HT-02)

Cada chat —o tipo de agente— puede tener su propia voz para saber **quién** habla sin mirar la pantalla. Las reglas viven en `~/.config/herdr-tts/voices.json` con precedencia **pane > agent_type > voz global** (`g` en Ajustes → Voz). El watcher resuelve la voz en memoria (caché por mtime, cero spawns por evento) y si el fichero es inválido hace fail-open a la voz global con una línea accionable en `daemon.log`.

```json
{
  "agent":      { "claude-code": "alvaro", "opencode": "ximena" },
  "pane":       { "w4:p4": "dalia" },
  "prefix":     false,
  "auto_assign": false
}
```

```bash
herdr-tts --voice-for pane w4:p4                 # Selector fzf de voz para ese chat
herdr-tts --voice-for agent claude-code alvaro   # Voz por tipo de agente
herdr-tts --voice-for pane w4:p4 off             # Volver a la voz global
herdr-tts --voice-prefix on                      # Prefijo hablado ("claude-code: …") antes del texto
```

* En la **paleta de voz**, `ctrl-v` abre el selector de voz del chat seleccionado; el **dashboard** y el preview de la paleta muestran la inicial de la voz (🗣A) cuando el chat tiene regla propia.
* **`prefix: true`** sintetiza el nombre corto del agente antes del texto, con la propia voz del agente (RF-HT-02-5).
* **`auto_assign: true`** (opt-in, default off): los `agent_type` sin regla toman por rotación determinista una voz de la paleta corta del proveedor activo (edge: elvira/alvaro/ximena/dalia · openai: nova/alloy/echo/fable). Piper/kokoro no rotan (su voz depende del modelo instalado).
* Las reglas aplican a la lectura automática, al `play`/`TL;DR` bajo demanda y a los audios renderizados del pane.

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
- [x] 🧘 **Settle Window (Intermediate-Step Filtering):**
  - A `done` only fires the pipeline if it still holds after `TTS_SETTLE_SECONDS` (default 5s, also in Ajustes): the `working→done→working` flickers between the steps of one logical turn (tool batches, subagents, thinking phases) are dropped before synthesis and mobile push. A state change during the window re-targets the event (`done→blocked` fires as blocked); `0` disables it.
- [x] 📊 **Interactive TUI Dashboard Pane:**
  - Native Herdr dashboard pane entrypoint displaying live agent speech states, recent audio logs, countdowns for snoozed panes, volume controls, and provider/voice toggles. (v1: pure-ANSI clear+redraw, read-only gating view, controls reuse the existing mute/snooze/rate functions; audio history via `${XDG_STATE_HOME:-~/.local/state}/herdr-tts/history.log`.)
  - v2: live engine line via the engine IPC status (state with color, mm:ss progress, active provider/voice and text snippet) on a shared every-N-ticks cache cadence, fail-open "motor: no responde" fallback, and real durations in the audio history ledger (MP3 decoded with the engine's own miniaudio, one best-effort probe per render).
  - v3: **per-chat view** — the roster merges `herdr agent list` (agent state + chat title), the snooze/mute ledger and the debounce ledger under the same `pane_id` key, rendering one line per chat with needs-attention-first sorting (done/blocked → working → idle, most-recent-audio tiebreak), voice overlays (🔇 muted, 😴 snooze countdown, ⏱ debounce hold) and a screen-row budget that drops oldest-idle chats first and never hides chats needing attention; the audio history renders **grouped per chat** (`── <title> · N audios · último hace Xm` + last 3 audios each) via a single python pass per tick with correct per-date DST handling, keeping closed panes visible by pane id and hiding the section entirely when the ledger is empty.
- [ ] 🎙️ **Push-to-Talk Two-Way Intercom:**
  - Dictate instructions directly to the focused agent pane via hotkey (`prefix + c`), transcribing locally via lightweight fast STT (Whisper.cpp / whisper-rs) and injecting the prompt directly into Herdr's active pane.
- [x] 📦 **One-Line Install & Packaging:**
  - Idempotent one-command installer (`scripts/install.sh`, tag-pinned `v0.16.0` with a `HERDR_TTS_REF`/`@main` escape hatch), uv-pip bootstrap immune to pip-less uv venvs, immutable agent-tts pin, `--no-keymap` adoption policy and documented uninstall steps. (Homebrew and npm wrappers ship as opt-in scaffolds under `packaging/` — `packaging/npm` delegates to this installer and `packaging/homebrew` holds a tap-ready formula; publishing either remains a deliberate maintainer step.)
- [ ] 🎚️ **Gating Presets & First-Run Experience:**
  - `herdr-tts --preset chatty|focused|quiet|mobile` writes an opinionated, human-readable config block over the existing gating ledger, and `keymap init` offers a preset as its final step — full gating power without reading five config vars first.
- [ ] 🔀 **Provider Failover Chain:**
  - `TTS_PROVIDER_CHAIN="edge,kokoro,piper"` — Edge stays the zero-cost zero-config default, but if it throttles or blocks, the engine degrades to the next installed provider with an actionable note (never a silent download) and logs the switch to `daemon.log`.
- [ ] 🪝 **agent-tts Hook Adapters (outside Herdr):**
  - Thin `agent-tts hook claude-code` / `codex` adapters riding each host's native event hooks, bringing the engine, cleaner, secret redactor and simple gating to single-agent setups — Herdr fleets keep the full multi-agent surfaces.
- [ ] 🔍 **`--audit` Mode (what would it say):**
  - Replay the last 24h of events through cleaner + redactor and show exactly what would have been spoken or pushed — a trust-building demo and a redactor regression test in one command.
- [ ] 📊 **Published Benchmarks:**
  - Reproducible `bench/` script measuring first-audio latency per provider, host+engine resident RAM and end-to-end event→audio latency with the settle window active; results land in `docs/benchmarks.md` with hardware and date.
- [ ] ⏩ **Piper Frame-Level Streaming (benchmark-gated):**
  - Incremental ONNX synthesis streaming into the miniaudio pipeline for long offline reads — pursued only if the benchmark shows Piper winning a scenario over Kokoro, the recommended offline upgrade.
- [ ] 🔁 **Daemon Lifecycle Action:**
  - Expose `herdr.tts.daemon-restart` as a plugin action and document the post-install `herdr plugin action invoke` — start or restart the daemon without `herdr server stop`, matching the ecosystem-idiomatic install flow.
- [ ] ⚡ **Native `plugin_action` Key Bindings:**
  - Migrate the managed keymap block from `type = "shell"` commands to Herdr's `plugin_action` binding type — no shell spawn per keystroke.
- [x] 🤖 **Agent Skill Distribution:**
  - `herdr-tts skill install|uninstall|list` publishes a managed SKILL.md into the user-level skills directory of Claude Code, OpenCode, Codex and Pi so coding agents can operate the plugin themselves — mute, snooze, status, replay — with the keymap's never-overwrite policy (`--force` to replace).
- [x] 📋 **Agent-Install Docs Block & Platform Matrix:**
  - A paste-ready "hand it to an agent" install prompt (with the do-NOT-`herdr server stop` warning) and an honest tested-platforms line now live in the Installation section.



---

## 📄 License

MIT © 2026 [ChipTime (Bruno Silva)](https://github.com/chiptime)
