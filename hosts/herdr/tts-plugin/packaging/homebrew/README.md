# Homebrew packaging (tap-ready formula)

`herdr-tts.rb` is a working-shaped formula for the released tag. It is **not
in any tap yet** — creating and pushing the tap is a deliberate maintainer
step, documented below.

## What the formula actually does (and does not)

1. Homebrew **clones the repo at the pinned tag** (`url ... .git` with
   `tag:` + `revision:` — both must be bumped on every release).
2. It runs the plugin's **own `scripts/bootstrap.sh`** with
   `XDG_DATA_HOME=#{prefix}`, so the Python venv (and the immutable-pinned
   `agent-tts` engine) are built **inside the keg**. No vendored engine, no
   second pin to maintain.
3. It shims `bin/herdr-tts` with `HERDR_PLUGIN_ROOT=#{prefix}` (and
   `XDG_DATA_HOME=#{prefix}`) so the launcher finds the venv it was built with.

**Why the shim also pins `XDG_DATA_HOME`:** the launcher resolves its venv
from that variable, and there is no separate override for the venv location.
The honest consequence: runtime state under the data dir (audio history,
podcast feed) lives in the keg and is wiped by `brew upgrade` / `brew cleanup`.
Machines that care about that state should use the one-line installer
(`scripts/install.sh`), which keeps everything under `$HOME/.local/share`.

**Why not `homebrew-core`:** the plugin is host-coupled (its daemon, watcher
and keymap integration assume the Herdr ADE), releases are git tags rather
than tarballs, and the build runs a project-owned bootstrap script — all
reasonable for a personal tap, all friction for core. Revisit if the project
grows standalone tarball releases.

## Publishing a tap (maintainer steps)

1. Create a public GitHub repo named `homebrew-tap` under `chiptime`
   (the `homebrew-` prefix is required by Homebrew).
2. Copy the formula into it:

   ```bash
   git clone git@github.com:chiptime/homebrew-tap.git /tmp/opencode/homebrew-tap
   mkdir -p /tmp/opencode/homebrew-tap/Formula
   cp packaging/homebrew/herdr-tts.rb /tmp/opencode/homebrew-tap/Formula/
   ```

3. Audit and test before pushing:

   ```bash
   cd /tmp/opencode/homebrew-tap
   brew audit --strict --new Formula/herdr-tts.rb
   brew install --build-from-source ./Formula/herdr-tts.rb
   brew test herdr-tts
   brew uninstall herdr-tts
   ```

   The `test do` block runs `herdr-tts --contract-version` (surface contract
   v1): it prints `1` with no audio, no daemon and no network.
4. Commit and push. Users then install with:

   ```bash
   brew tap chiptime/tap
   brew install chiptime/tap/herdr-tts
   ```

## Release maintenance

On every release tag, update **both** `tag:` and `revision:` in the formula
(`git rev-parse vX.Y.Z` gives the revision) and re-run the audit. The formula
deliberately pins the exact commit so taps stay reproducible even if a tag is
ever re-pointed.

## Scope note

The formula installs the CLI (`--speak`, `--render-text`, `--status`,
`keymap ...`, `skill ...`). The plugin experience (daemon, watchers, dashboard,
keybindings) requires the Herdr host — `herdr plugin install chiptime/herdr-tts`
remains the recommended install path; the formula is for machines that manage
the CLI through Homebrew.
