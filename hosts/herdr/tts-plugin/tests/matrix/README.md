# Bash decision matrix (voice-stack D9)

`bash-decisions.json` enumerates the decision alternatives of MODIFIED Bash
code (per the baseline↔candidate snapshot diff) and maps each alternative to
the named test cases of `../host_cli_cases.sh` that execute it.

Rules:

- Every alternative of every modified decision needs at least one case.
- Referenced cases must have been EXECUTED with outcome OK in the harness run.
- Missing, failed, or not-executed cases fail the gate; unsupported syntax in
  modified code is a typed blocked result (fail-closed).
- The table is regenerated per milestone pairing. For the M1 closure pairing
  (genesis B0 → final M1 candidate) the modified PRODUCT Bash delta in
  `bin/herdr-tts` carries no decision constructs (the identified-playback and
  targeted-cancel wiring plus the `AGENT_TTS_*` fallback assignments are
  plain assignments/calls), so the product table is EMPTY — a measured fact
  printed by the gate as `decisions=0`, not a not_applicable shortcut.
- The case-runner harness (`host_cli_cases.sh`) is the INSTRUMENT, never the
  measured surface: its own assertion guards (`x || return 1`) fail exactly
  when a case fails, so their failure arms are unmeasurable by construction.
  Its modified lines are measured by the G-BASH-LINES gate instead.
- The matrix is evidence of alternatives covered. It is **not** a numeric
  branch-coverage percentage and is never presented as one.

Schema:

```json
[
  {
    "id": "<file>:<line>:<construct>:<index>",
    "file": "hosts/herdr/tts-plugin/bin/herdr-tts",
    "line": 3197,
    "construct": "if",
    "alternatives": [
      {"label": "cond-true", "cases": ["busy_local_admits_pending"]},
      {"label": "cond-false", "cases": ["idle_local_plays"]}
    ]
  }
]
```

The seed table is `[]`: no Bash decisions are modified yet.
