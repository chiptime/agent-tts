# conversation-reader Delta: reader-always-formatted

## MODIFIED Requirements

### Requirement: On-Demand Loading and Snapshot Discipline

The client MUST request rendered content for the viewed conversation's turns when the reading surface loads and on each subsequent refresh of that surface, so that formatted rendering is the default presentation. The client MUST NOT require an expansion gesture ("ver más") to trigger rendering; that gesture MUST remain purely a visual truncation control for very long turns. The client MUST display existing text immediately for any turn whose rendered content has not yet arrived, and MUST replace it with the formatted HTML once the response arrives (progressive cold fill across polls). The client MUST replace the reader snapshot as a unit and MUST discard stale rendered responses after a pane or session change rather than joining independent responses by array position.

#### Scenario: All turns formatted without expansion

- GIVEN a conversation whose turns are viewable in the reading surface
- WHEN the reading surface loads
- THEN every turn is mounted for formatted rendering without any expansion gesture
- AND turns whose rendered content is cached display formatted immediately

#### Scenario: Text first, upgrade later

- GIVEN a turn whose rendered content has not yet arrived (cold, over budget, or renderer failed)
- WHEN the turn is displayed
- THEN the existing text is shown immediately
- AND the formatted HTML replaces it only once the rendered response arrives

#### Scenario: Expansion no longer gates rendering

- GIVEN a turn that is visually truncated by the "ver más" control
- WHEN the reading surface loads or refreshes
- THEN the rendered content for that turn is requested and mounted regardless of the truncation state

#### Scenario: Progressive cold fill across polls

- GIVEN more cold turns than the per-request render budget
- WHEN the surface refreshes on its existing poll cycle
- THEN each poll renders up to the budget of still-cold turns
- AND every turn eventually displays formatted content without any expansion gesture

#### Scenario: Stale response discarded on surface change

- GIVEN a rendered-endpoint response in flight for pane A
- WHEN the user switches the viewing surface to pane B before the response arrives
- THEN the response for pane A is discarded and never merged into pane B's reader snapshot

## Unchanged Requirements

All other requirements of `conversation-reader` remain in force verbatim: the endpoint contract, turn identity, inline anchors@1 map and staleness posture, content-addressed cache mechanics (cache hits spawn no subprocesses), bounded renderer invocation, fail-soft null-pair invariant, reader mounting and scoped insertion, strict plain-text fallback, turn-scoped sentence selection, hardened links, CSP, non-executable metadata, and the dual test-suite obligations.

## Sanctioned constant change

`READER_MAX_RENDERS_PER_REQUEST` changes 8 -> 16 (module docstring of `src/herdr_brain/reader.py` required a spec delta for any value change; this document is that delta). Rationale: measured cold render p95 ≈ 0.28 s; 16 amortized cold renders across the existing 5 s poll cycle remain bounded, and the content cache makes the spend one-time per turn.
