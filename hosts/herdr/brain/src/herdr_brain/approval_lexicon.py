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

import re
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


# -- terminal dialog choices (S9/FR-09) -----------------------------------
#
# A DIFFERENT question than the gate lexicon: an utterance names WHICH
# option of a spoken terminal dialog to press ("dile que sí",
# "selecciona la opción 1"), and the deterministic mapping happens in
# tools.answer_dialog against the CURRENT dialog. Pure module, same
# normalize; returns None for anything that is not an explicit choice —
# vague sentences must fall through to conversation, never guess.

# Dialog commands are short sentences, not bare words: allow a little
# more room than the gate lexicon's 24 chars.
DIALOG_MAX_UTTERANCE_CHARS = 48

CHOICE_OPTION = "option"
CHOICE_BOOLEAN = "boolean"
CHOICE_YES = "yes"
CHOICE_NO = "no"

# A digit resolves as a choice only when the utterance names it AS an
# option: an option/number word or a selection verb accompanies the
# digit. A bare digit inside an unrelated sentence is not a choice.
_DIALOG_NUMBER_RE = re.compile(r"\b(\d{1,2})\b")
_DIALOG_NUMBER_CONTEXT = ("opcion", "option", "numero", "number")
_DIALOG_SELECTION_VERBS = (
    "selecciona", "elige", "escoge", "marca", "pulsa", "dile",
    "select", "choose", "pick", "press",
)
# A negated or corrected command never selects: "no selecciones la
# opción 1", "selecciona 2, no, mejor 1". The bare yes/no words are
# matched FIRST (whole utterance), so plain "no" still resolves.
_DIALOG_NEGATIONS = frozenset(
    {"no", "nunca", "jamas", "tampoco", "ninguna", "ninguno",
     "not", "never", "dont", "quits", "cancela"}
)
# Optional lead-in ("dile", "contesta que", "answer", "tell him") plus an
# optional "que": the remainder must BE the yes/no word, whole.
_DIALOG_YES_NO_RE = re.compile(
    r"^(?:(?:dile|di|contesta|responde|answer|say|tell\s+(?:him|it))\s+)?"
    r"(?:que\s+)?(si|yes|no|nope)$"
)


def resolve_dialog_choice(text: str):
    """Maps a spoken dialog answer to a deterministic choice, or None.

    Returns ``("option", n)`` for an explicitly numbered selection,
    ``("boolean", "yes"|"no")`` for "dile que sí"/"que no"-style answers,
    and None for anything that does not name exactly one unambiguous,
    un-negated choice — vague sentences, negations ("no elijas la opción
    2"), multiple numbers ("opción 1 o 2") and self-corrections
    ("selecciona 2, no, mejor 1") must all fall through so the caller
    asks the user instead of pressing anything.

    Examples:
        "dile que sí" -> ("boolean", "yes")
        "selecciona la opción 1" -> ("option", 1)
        "no selecciones la opción 1" / "opción 1 o 2" / "no sé" -> None
    """
    utterance = _normalize(text)
    if not utterance or len(utterance) > DIALOG_MAX_UTTERANCE_CHARS:
        return None
    yes_no = _DIALOG_YES_NO_RE.fullmatch(utterance)
    if yes_no is not None:
        word = yes_no.group(1)
        return (CHOICE_BOOLEAN, CHOICE_NO if word in ("no", "nope") else CHOICE_YES)
    numbers = _DIALOG_NUMBER_RE.findall(utterance)
    if len(numbers) != 1:
        return None  # zero numbers, or several competing ones
    if int(numbers[0]) == 0:
        return None  # options are 1-based
    words = set(_DIALOG_NUMBER_RE.sub(" ", utterance).split())
    if words & _DIALOG_NEGATIONS:
        return None  # a negated or corrected command selects nothing
    has_context = any(word in utterance for word in _DIALOG_NUMBER_CONTEXT)
    has_verb = any(
        utterance.startswith(verb) or f" {verb}" in f" {utterance}"
        for verb in _DIALOG_SELECTION_VERBS
    )
    if has_context or has_verb:
        return (CHOICE_OPTION, int(numbers[0]))
    return None
