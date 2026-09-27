# conversation-reader Delta: toast-formatted-avisos

## ADDED Requirements

### Requirement: Formatted Agent-Status Toast

The client toast that announces agent status or responses MUST render the announced text as reader HTML when a rendered snapshot for that exact text is available (content-keyed lookup, cache hit, no additional renderer invocation), and MUST fall back to plain `textContent` when it is not (watcher template avisos, renderer unavailable, any fail-soft condition). Formatted toast content MUST be excerpted by CSS clipping only — the client MUST NOT truncate HTML text by hand. Toast insertion MUST follow the scoped-insertion rules (pipeline output only, hardened links) and MUST NOT weaken any fail-soft, security, or CSP requirement of this capability.

#### Scenario: Formatted toast when HTML is available

- GIVEN an agent response whose text has a rendered snapshot in the reader cache
- WHEN the toast announces it
- THEN the toast shows the formatted HTML (e.g. bold and inline code rendered)
- AND no new renderer subprocess is spawned for the announcement

#### Scenario: Plain fallback for template avisos and failures

- GIVEN a watcher template aviso (never rendered) or a renderer failure
- WHEN the toast announces it
- THEN the toast shows the plain text via textContent, exactly as before this delta

#### Scenario: CSS excerpt only

- GIVEN a formatted toast whose HTML exceeds the toast height
- WHEN the toast is displayed
- THEN the content is clipped by CSS (max-height/overflow)
- AND no HTML manipulation (string cutting, tag stripping by regex) is performed on the payload

#### Scenario: Security rules inherited

- GIVEN any formatted toast insertion
- WHEN the payload is mounted
- THEN links are hardened (target=_blank, rel=noopener noreferrer) and insertion remains scoped to the toast container
