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

## tmux — 2026-11-20, tmux 3.8 (rev 2, parity T7 `tmuxfix`)

Supersedes the `prompt_submit` and `tabs` rows above by re-probing on the
same-day scratch methodology (unique `-L` socket, `-f /dev/null`,
HOME/TMPDIR/TMUX_TMPDIR in scratch, `cat` target pane so nothing executes,
`kill-server` teardown asserted gone). New evidence channel:
`tests/test_mux_tmux_live.py` — the adapter runs behind a wrapper binary that
logs its argv, so the probes additionally prove the sent text never rides
argv and no `herdr-paste-*` buffer survives. Recorded first as failures
against `6d33868` (RED: trailing `;` vanished on screen with rc 0 and a
`sent` receipt; active window moved from `@0` to the new window), then as
passes after the fix.

| Cell | Live result (tmux 3.8) |
|---|---|
| `prompt_submit` | exact bytes delivered for `echo hi;`, shell-metachar payloads (`'$(whoami)' "q" \`calc\` && true;`), accented/symbol text (`ñ é ç ✓ «» ¿¡ —;`) and wide Unicode (`日本語 😀;`) — text rides stdin into a private `herdr-paste-<uuid>` buffer (`load-buffer`), then `paste-buffer -d -S` pastes byte-exact by pane id and the Enter submit rides the SAME tmux invocation across tmux's own `;` command separator (one round-trip narrows the window another client's command could exploit between paste and submit); `-d` deletes the buffer with the paste plus a best-effort `delete-buffer` on every path. BYTE-LEVEL proof: the panes print the exact received bytes as hex; every battery payload's UTF-8 byte sequence (trailing `;` included) matches what was sent (tests/test_mux_tmux_live.py) |
| `tabs` | `new-window -d -P -F` returned ids and left the session's current window untouched (`window_active` stayed on the original window, the new window inactive) |
| `failures` | a failed `load-buffer`/`paste-buffer` propagates the typed refusal (`refused`/`gone`, mapped by `/pane/reply` to 502 with a recorded `refused` receipt) and still deletes its own buffer; the failed invocation is the only write attempted |

The `send_text` refusal, `pane_grid`, `ansi_screen`, `scrollback`, `keys` and
`probe()` rows stand as recorded above (untouched by T7).

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
