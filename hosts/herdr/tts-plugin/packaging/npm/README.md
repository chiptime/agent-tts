# herdr-tts (npm wrapper)

Neural TTS voice notifications for [Herdr](https://github.com/chiptime/herdr-tts) agents.

**This package is an installer alias, not the plugin.** It contains no code of
its own: running it delegates to the tag-pinned one-line installer
(`scripts/install.sh`) served from the released tag on GitHub, so npm users and
`curl | sh` users run byte-identical logic from a single code path.

## Usage

```bash
# run without installing anything globally
npx herdr-tts

# or install globally, then run
npm install -g herdr-tts
herdr-tts
```

The installer requires `git`, `jq`, the `herdr` CLI, and either `uv` or
`python3` on PATH (plus `curl` for this wrapper). It verifies all of them
before touching anything.

## Options

| What | How |
|---|---|
| Pin / move the ref | `HERDR_TTS_REF=v0.16.0 npx herdr-tts` (default: `v0.16.0`; `main` works but is mutable — use deliberately) |
| Skip keymap adoption | `npx herdr-tts --no-keymap` (passed through to the installer) |

Re-running performs a guarded in-place upgrade. The installer prints the
manual uninstall steps at the end of every run; they are also documented in
the [project README](https://github.com/chiptime/herdr-tts#-installation).

## Why so thin?

The plugin is a Herdr host plugin: its CLI lives in the cloned checkout under
`~/.local/share/herdr-tts/plugin` and its daemon is managed by the Herdr
session. An npm package that shipped a copy of the code would create a second
source of truth; this wrapper deliberately keeps GitHub at the pinned tag as
the only distribution artifact.
