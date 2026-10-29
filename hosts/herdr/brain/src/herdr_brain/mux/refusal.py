"""The single refusal shape of the multiplexer port.

Every adapter reports "this call is not available HERE" as
:class:`MuxRefusal` instead of an unexplained error, so a caller can tell
apart a multiplexer that lacks a capability from one that is down, one
whose pane vanished, and one that understood and said no.

The class derives from :class:`~herdr_brain.herdr.HerdrError` on purpose:
today's callers degrade through ``except HerdrError`` and must keep doing
so unchanged while the port lands.
"""

from __future__ import annotations

from ..herdr import HerdrError

#: Closed vocabulary of refusal reasons.
#:
#: - ``unsupported`` — this multiplexer does not have the capability and
#:   never will; explain it, do not report it as an error.
#: - ``gone`` — the pane existed and no longer does.
#: - ``refused`` — the multiplexer understood the request and said no
#:   (including the brain's own pre-spawn guards rejecting an input).
#: - ``unreachable`` — no answer; the only retry-worthy reason.
REFUSAL_REASONS = frozenset({"unsupported", "gone", "refused", "unreachable"})


class MuxRefusal(HerdrError):
    """A typed, explainable "no" from one multiplexer adapter."""

    def __init__(self, reason: str, capability: str, detail: str = "") -> None:
        if reason not in REFUSAL_REASONS:
            raise ValueError(
                f"unknown refusal reason {reason!r}: "
                f"expected one of {', '.join(sorted(REFUSAL_REASONS))}"
            )
        self.reason = reason
        self.capability = capability
        self.detail = detail
        message = f"mux {reason} ({capability})"
        super().__init__(f"{message}: {detail}" if detail else message)
