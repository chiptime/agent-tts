# herdr-tts (npm wrapper)

Neural TTS voice notifications for [Herdr](https://github.com/chiptime/agent-tts)
agents. This wrapper is packaging source inside the
[agent-tts monorepo](https://github.com/chiptime/agent-tts/tree/main/hosts/herdr/tts-plugin/packaging/npm),
not a standalone project.

**This package is an installer alias, not the plugin.** It contains no code of
its own: running it delegates to the tag-pinned one-line installer
(`hosts/herdr/tts-plugin/scripts/install.sh`) served from the released
**monorepo** tag on GitHub, so npm users and `curl | sh` users run
byte-identical logic from a single code path.

## Publication status (honest)

This wrapper is **not published to the npm registry**. Publishing it is a
deliberate maintainer step that has not been performed; until it is, `npx
herdr-tts` and `npm install -g herdr-tts` will not find a package under this
name. Use the documented installation routes in the
[project README](https://github.com/chiptime/agent-tts/tree/main/hosts/herdr/tts-plugin#-installation)
instead. The usage below describes what the wrapper does **once published**.

## Usage (once published)

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
| Pin / move the ref | `HERDR_TTS_REF=v0.16.0 npx herdr-tts` (default: `v0.16.0`, an agent-tts monorepo tag; `main` works but is mutable — use deliberately) |
| Skip keymap adoption | `npx herdr-tts --no-keymap` (passed through to the installer) |

Re-running performs a guarded in-place upgrade. The installer prints the
manual uninstall steps at the end of every run; they are also documented in
the [project README](https://github.com/chiptime/agent-tts/tree/main/hosts/herdr/tts-plugin#-installation).

## Why so thin?

The plugin is a Herdr host plugin: its CLI lives in the cloned monorepo
checkout under `~/.local/share/herdr-tts/plugin` (at `hosts/herdr/tts-plugin`)
and its daemon is managed by the Herdr session. An npm package that shipped a
copy of the code would create a second source of truth; this wrapper
deliberately keeps the monorepo tag on GitHub as the only distribution
artifact.

## Support scope

This wrapper inherits the plugin's support targets (Linux, WSL2, macOS) and
makes no claims beyond them: native Windows is not supported, and the
automated smoke suite runs on Linux only — there is no validated
cross-platform coverage to claim. The wrapper itself adds no behavior; any
limitation of the installer it delegates to applies unchanged.
