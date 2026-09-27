"""Voice resolve lexicon for approval gates (pure, es + en).

Maps a confirming-state STT utterance to one of four outcomes — approve,
reject, replace_intent, unknown — so the approval endpoints can act on it
(PRD-action-approval-gate §5). Pure module: no I/O, no state, no clock;
deterministic on the utterance text alone.

Matching rule (PRD §5): the trimmed, casefolded, accent-stripped
utterance equals a trigger or starts with one while staying short
(<= 24 chars). The length guard is what keeps a bare "no" from firing
inside longer sentences: a long sentence merely containing "no" never
resolves to reject. "No sé" resolves to reject (safe direction) via the
"no" prefix. Anything unmatched, empty, or too long resolves to unknown,
which T4 maps onto the reprompt budget.
"""

from __future__ import annotations

import unicodedata

# Outcomes. T4 maps these onto ApprovalGateStore decisions: approve ->
# approve, reject -> reject, replace_intent -> PATCH + reprompt
# (listen_replace flow), unknown -> reprompt (budget then rejects).
OUTCOME_APPROVE = "approve"
OUTCOME_REJECT = "reject"
OUTCOME_REPLACE_INTENT = "replace_intent"
OUTCOME_UNKNOWN = "unknown"

# A set match applies only while the normalized utterance stays at or
# below this length (PRD §5 short-utterance rule).
MAX_UTTERANCE_CHARS = 24

# Trigger words verbatim from PRD §5. They are normalized at import, so
# the PRD spelling stays traceable here while matching is accent- and
# case-insensitive ("sí" ~ "si", "envíalo" ~ "envialo").
APPROVE_WORDS: tuple[str, ...] = (
    "sí", "si", "confirmo", "vale", "dale", "adelante", "hazlo",
    "envíalo", "mándalo", "yes", "confirm", "go ahead",
)
REJECT_WORDS: tuple[str, ...] = (
    "no", "cancela", "cancelar", "para", "stop", "espera", "no lo envíes",
)
REPLACE_WORDS: tuple[str, ...] = (
    "cambia", "cambia el texto", "re-dicta", "redicta", "otro texto",
    "mejor dile",
)


def _normalize(text: str | None) -> str:
    """Trims, casefolds and strips diacritics ("Envíalo" -> "envialo").

    STT output routinely drops Spanish accents ("si" for "sí"), so both
    triggers and utterances pass through this same function.
    """
    folded = (text or "").strip().casefold()
    decomposed = unicodedata.normalize("NFD", folded)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_APPROVE_TRIGGERS = frozenset(_normalize(word) for word in APPROVE_WORDS)
_REJECT_TRIGGERS = frozenset(_normalize(word) for word in REJECT_WORDS)
_REPLACE_TRIGGERS = frozenset(_normalize(word) for word in REPLACE_WORDS)

# Categories checked in this order so any future cross-category trigger
# overlap resolves toward the safe direction first. The current sets do
# not overlap across categories.
_RESOLUTION_ORDER = (
    (OUTCOME_REJECT, _REJECT_TRIGGERS),
    (OUTCOME_REPLACE_INTENT, _REPLACE_TRIGGERS),
    (OUTCOME_APPROVE, _APPROVE_TRIGGERS),
)


def resolve_utterance(text: str) -> str:
    """Resolves a confirming-state utterance to an approval outcome.

    The utterance is normalized (trim + casefold + accent-strip), then a
    trigger matches only when the whole utterance is short (<= 24 chars)
    and equals the trigger or starts with it. Note the startswith match
    is literal, with no word boundary: per the PRD rule, short words
    that merely begin with a trigger ("sigue" ~ "si") do resolve — the
    <= 24-char guard is the only protection, by design.

    Examples:
        "sí" / "dale, envíalo ya" -> approve
        "no" / "no sé" / "no lo envíes" -> reject
        "cambia el texto porfa" -> replace_intent
        "" / "hola qué tal" / long sentences containing "no" -> unknown
    """
    utterance = _normalize(text)
    if not utterance or len(utterance) > MAX_UTTERANCE_CHARS:
        return OUTCOME_UNKNOWN
    for outcome, triggers in _RESOLUTION_ORDER:
        if any(
            utterance == trigger or utterance.startswith(trigger)
            for trigger in triggers
        ):
            return outcome
    return OUTCOME_UNKNOWN
