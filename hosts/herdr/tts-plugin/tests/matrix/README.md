# Bash decision matrix (voice-stack D9)

`bash-decisions.json` enumerates the decision alternatives of MODIFIED Bash
code (per the baseline↔candidate snapshot diff) and maps each alternative to
the named test cases of `../host_cli_cases.sh` that execute it.

Rules:

- Every alternative of every modified decision needs at least one case.
- Referenced cases must have been EXECUTED with outcome OK in the harness run.
- Missing, failed, or not-executed cases fail the gate; unsupported syntax in
  modified code is a typed blocked result (fail-closed).
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
