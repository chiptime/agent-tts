# Contracts — Monorepo Architecture Interfaces

This directory is the **normative source of truth** for versioned contracts across components in the monorepo.

## Principles

1. **Dependency Boundary**:
   - `hosts/*` depends on `engine` exclusively via `contracts/ipc-v2.md` and public artifacts.
   - `engine` has **zero** knowledge or dependencies on `hosts/*` (enforced by boundary tests).
   - `hosts/herdr/brain` interacts with `hosts/herdr/tts-plugin` exclusively via `contracts/tts-brain-v1.md`.

2. **Normative vs Descriptive**:
   - Files in this directory describe what the executable tests actually enforce (the wire and CLI formats), not historical promises.
   - Every contract requirement links to its *verified by* test suite.

## Active Contracts

- **[`ipc-v2.md`](ipc-v2.md)**: Control channel framing v2 (length-prefixed framing, socket lifecycle, daemon dispatch and priority queue management).
- **[`tts-brain-v1.md`](tts-brain-v1.md)**: Speech rendering (`--render-text`), HTML reader pipeline (`--render-html`), and daemon liveness probe interface between `herdr-tts` and `herdr-brain`.
