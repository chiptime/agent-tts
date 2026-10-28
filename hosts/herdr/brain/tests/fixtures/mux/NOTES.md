# mux fixture notes

Recorded-format fixtures for the mux adapter tests (`tests/test_mux_*.py`).
No test ever invokes a real multiplexer: these files pin the exact output
shapes the adapters parse, shaped after the CLI surfaces described in
`docs/paridad/07-multiplexores-tmux-zellij.md`.

- `tmux/list-panes.txt` — `tmux list-panes -a -F` output, tab-separated in
  `TmuxAdapter.LIST_FORMAT` field order.
- `tmux/capture-visible.txt` / `.ansi` — `tmux capture-pane -p` with and
  without `-e`.
- `tmux/capture-recent.txt` — history capture (`-S -<n>`), 12 recorded lines.
- `tmux/new-window.txt` — `tmux new-window -P -F '#{window_id} #{pane_id}'`.
- `zellij/list-panes.json` — `zellij list-panes --all --json` array; `cwd`
  may be `null` and optional fields may be absent (parsing is lenient).
- `zellij/dump-screen.ansi` / `.txt` — `zellij action dump-screen` with and
  without `--ansi`.
