# Multiplexer live-probe records (parity-f7f S3)

First-hand probe records for every capability cell the brain declares
for a multiplexer, per the acceptance rule "every supported capability
cell has a live probe recorded with date and multiplexer version".
Re-probe after every multiplexer upgrade and append a new dated section;
a cell that stops matching its record must lose its declaration.

## tmux — 2026-11-20, tmux 3.8

Method (authorized by the operator, D3): probes ran against a dedicated
scratch server started as `tmux -L <ephemeral> -f /dev/null new-session`,
addressed only through a wrapper binary so no adapter call could ever
reach a real session; `kill-server` ran at the end and `list-sessions`
confirmed the server was gone. Two methodology facts learned the hard
way: the scratch server MUST start with `-f /dev/null` (tmux loads
`~/.tmux.conf` by default, which changes base-index and pane behavior
and would invalidate the shapes), and classification reads must cover
the whole grid, not a tail slice (fixed in `51d50f7`).

| Cell | Live result (tmux 3.8) |
|---|---|
| `pane_grid` | `list-panes -a -F` answers; pane `%0` listed |
| `ansi_screen` | `capture-pane -p [-e]` answers for visible reads |
| `scrollback` | `capture-pane -S` returns history (29–39 lines probed) |
| `prompt_submit` | reply classified `sent` ("la pantalla del agente avanzó"), echoed text and its output both visible on screen |
| `keys` | `pane_run` + `send-keys` answers; output visible on screen |
| `tabs` | `new-window -P -F` returned pane `%1` / window `@1` |
| `send_text` (refusal) | refused `unsupported` as designed (no typing-without-Enter surface) |
| `probe()` | reachable=True with "tmux server answered list-sessions (tmux 3.8)"; dead socket → reachable=False, version still reported |

## zellij — NOT VERIFIED LIVE

zellij is not installed on this host and the operator authorized no
install (D2). Every zellij cell (`prompt_submit` write path, probe's
session-state touch) is unit-tested against CLI-shape-derived fixtures
and is marked `UNVERIFIED live` in the adapter code. Two known shape
risks recorded for the eventual live re-pin: the write path targets
panes by `--pane-id` with `write-chars` + `write 10`, and the
`list-panes --all --json` fixture field names are flagged as derived,
not measured (Engram observation 13515). Until zellij exists on a host
and this section gains a dated record, zellij cells count as
unverified for closure purposes — honestly, not silently.
