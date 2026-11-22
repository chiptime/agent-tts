# mux fixture notes

Fixtures for recorded-runner tests; `test_mux_*_live.py` instead uses disposable
local sessions and skips when the required binary is missing.

- `tmux/list-panes.txt` — `tmux list-panes -a -F` output, tab-separated in
  `TmuxAdapter.LIST_FORMAT` field order.
- `tmux/capture-visible.txt` / `.ansi` — `tmux capture-pane -p` with and
  without `-e`.
- `tmux/capture-recent.txt` — history capture (`-S -<n>`), 12 recorded lines.
- `tmux/new-window.txt` — `tmux new-window -P -F '#{window_id} #{pane_id}'`.
- `zellij/list-panes.json` — synthetic session entries in the array shape
  captured with `zellij action list-panes --all --json` on zellij 0.45.1
  (2026-11-22). Terminals carry `pane_cwd`; plugin and terminal IDs can overlap,
  and plugins can report focus. Only terminal entries are exposed.
- `zellij/dump-screen.ansi` / `.txt`, `dump-empty.txt` and the prompt dumps
  use the compact output of `action dump-screen [--ansi]` from the isolated
  `cat` pane. The tall dumps are synthetic content in the same format for
  line-budget tests.

T4 live coverage: tmux 3.8 types via the private stdin-backed paste buffer
without entering or switching focus. Zellij 0.45.1 types via `action
write-chars` without `write 10`; logical keys use `action send-keys` (for
example `Ctrl u`). Neither typing call claims delivery. Zellij can return zero
for a missing pane; a prompt whose screen never changes remains `unknown`,
not `sent` or `effective`. Zellij scrollback and `pane_run` remain unsupported.
