# Herdr Neural TTS Plugin

A lightweight, concurrency-safe, neural text-to-speech plugin for [Herdr](https://herdr.dev). Vocalizes AI agent responses (OpenCode, Claude, etc.) using Microsoft Edge Neural TTS with high-quality natural voices.

## Features

- 🔊 **Zero-friction speech:** Uses Microsoft Edge Neural TTS (free, highly natural Spanish/English neural voices).
- ⚡ **Lightweight:** Decodes audio in memory using `miniaudio` and streams to PulseAudio/PipeWire/macOS with zero distortion and ~2.5 MB RAM overhead. 0 VRAM usage.
- 🛡️ **Concurrency-safe (Audio Mutex):** If you run 4–5 parallel agent chats, voices will **never** talk over each other.
- 🎯 **Focus-aware (`scope: focused` by default):** Only auto-reads the agent you are actively watching. Background agents in other workspaces finish in silence so you can focus.
- ⏯️ **On-demand Play & Stop:** Read any chat when you want with `prefix + r`, or kill playing audio instantly (<0.2s) with `prefix + s`.
- 🔇 **Mute independence:** Silencing background auto-speech (`prefix + v`) never blocks explicit on-demand reading (`prefix + r`).
- 🌐 **Cross-platform:** Works on Native Linux (PulseAudio/PipeWire), WSL2 (WSLg PulseServer), and macOS (`afplay`).

---

## Installation

### Managed via Herdr (Recommended)

```bash
herdr plugin install chiptime/herdr-tts
```

Herdr will automatically download the plugin, run the bootstrap step to configure the Python virtual environment (`edge-tts`, `miniaudio`), and register the plugin.

### Local Development / Symlink

```bash
git clone https://github.com/chiptime/herdr-tts ~/Code/personal/herdr-tts
herdr plugin link ~/Code/personal/herdr-tts
```

---

## Keybindings (`ctrl+b` → key)

Add to your Herdr configuration (`~/.config/herdr/config.toml`):

```toml
# Play / Stop toggle on focused chat
[[keys.command]]
key = "prefix+r"
type = "shell"
command = "herdr-tts --toggle-play"

# Stop audio playback immediately
[[keys.command]]
key = "prefix+s"
type = "shell"
command = "herdr-tts --stop"

# Toggle automatic background voice feedback
[[keys.command]]
key = "prefix+v"
type = "shell"
command = "herdr-tts --toggle-auto"
```

Then reload configuration with:
```bash
herdr server reload-config
```

---

## CLI Usage

The `herdr-tts` CLI can be called directly or aliased in your shell:

```bash
herdr-tts --toggle-play        # Play / Stop on the currently focused chat
herdr-tts --stop               # Stop audio playback immediately
herdr-tts --toggle-auto        # Toggle background auto-speech (muted / active)
herdr-tts --scope focused|all  # 'focused' (only active pane) | 'all' (any pane without overlapping)
herdr-tts --status             # Show current status, voice, active locks, and PIDs
herdr-tts --voice alvaro       # Set voice (elvira, alvaro, ximena, dalia, jorge, en)
herdr-tts --rate +25%          # Set speech speed
herdr-tts --speak "Hello"      # Direct speech synthesis
```

### Handy Shell Aliases

```bash
alias htr="herdr-tts --toggle-play"
alias htx="herdr-tts --stop"
alias htt="herdr-tts --toggle-auto"
alias htts="herdr-tts --status"
alias httt="herdr-tts --speak"
```

---

## Available Voices

- `elvira`: `es-ES-ElviraNeural` (Default Spanish Spain female)
- `alvaro`: `es-ES-AlvaroNeural` (Spanish Spain male)
- `ximena`: `es-ES-XimenaNeural` (Spanish Spain female)
- `dalia`: `es-MX-DaliaNeural` (Spanish Mexico female)
- `jorge`: `es-MX-JorgeNeural` (Spanish Mexico male)
- `en`: `en-US-JennyNeural` (English US female)

---

## Architecture & How It Works

1. **Daemon Listener:** When Herdr starts, the daemon listens on the Herdr IPC socket (`herdr event subscribe`) for pane state changes and agent completion notifications.
2. **Text Normalization:** Cleans ANSI escapes, spinners, token statistics, and formatting into clean spoken prose.
3. **Concurrency Control:** Acquires `/tmp/herdr-tts-playing.lock`. If another chat finishes, it yields or ignores depending on the active scope (`focused` vs `all`).
4. **Playback & Cancellation:** Audio streams via in-memory PCM decoding. Calling `--stop` or `--toggle-play` kills the player subprocess and frees the mutex lock in less than 200ms.

---

## License

[MIT](LICENSE) © 2026 ChipTime (Bruno Silva)
