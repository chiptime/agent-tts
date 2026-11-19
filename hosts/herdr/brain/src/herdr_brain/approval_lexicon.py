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
from typing import Optional

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


# -- spoken multi-selections and free text (parity-f7b S5) ------------------
#
# Two more spoken dialog forms, same pure module and the same fail-closed
# discipline: a MULTI-selection ("marca la uno y la tres") names a SET of
# options, and a free-text answer ("responde: usa la rama main") carries
# literal words to type. resolve_dialog_choice itself is untouched — the
# single-choice mapping stays byte-identical.

#: Multi-select sentences carry a list, so they get more room than the
#: 48-char single-choice bound (still bounded: a set name is short).
DIALOG_MULTI_MAX_UTTERANCE_CHARS = 96

#: Number WORDS a spoken ordinal can take (STT spells digits out more
#: often than it digits them). 1–10 covers every pinned dialog.
_DIALOG_NUMBER_WORDS: dict[str, int] = {
    "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

#: Collectives join items ("la uno Y la tres"); a competing "o" (or) is
#: deliberately absent — an alternative is ambiguity, not a set.
_DIALOG_MULTI_JOINERS = frozenset({"y", "e", "and", ","})
_DIALOG_MULTI_ARTICLES = frozenset({"el", "la", "los", "las", "lo", "the"})
_DIALOG_MULTI_CONTEXT = frozenset(
    set(_DIALOG_NUMBER_CONTEXT) | {"opciones", "options", "numbers"}
)
_DIALOG_MULTI_VERBS = frozenset(_DIALOG_SELECTION_VERBS)


def resolve_dialog_choices(text: str):
    """Maps a spoken multi-selection to the exact option set, or None.

    Every item carries the SAME discipline as a single choice — an
    explicit selection verb or option context, no negation, 1-based,
    unambiguous — and the sentence may hold nothing else: one negation,
    a competing "o", a repeated mention or any stray word makes the
    WHOLE answer fall through so the caller asks the user instead of
    pressing anything.

    Examples:
        "marca la uno y la tres" -> (1, 3)
        "selecciona las opciones 1 y 3" -> (1, 3)
        "marca la uno o la tres" / "opción 1 o 2" / "no marques la 1" -> None
    """
    utterance = _normalize(text)
    if not utterance or len(utterance) > DIALOG_MULTI_MAX_UTTERANCE_CHARS:
        return None
    if _DIALOG_YES_NO_RE.fullmatch(utterance):
        return None  # a boolean answer is the single-choice form, not a set
    tokens = utterance.replace(",", " , ").split()
    numbers: list[int] = []
    has_verb_or_context = False
    for token in tokens:
        if token in _DIALOG_NEGATIONS:
            return None  # a negated or corrected command selects nothing
        if token in _DIALOG_MULTI_JOINERS or token in _DIALOG_MULTI_ARTICLES:
            continue
        if token in _DIALOG_MULTI_VERBS or token in _DIALOG_MULTI_CONTEXT:
            has_verb_or_context = True
            continue
        if token.isdigit():
            value = int(token)
            if value < 1:
                return None  # options are 1-based
            numbers.append(value)
            continue
        if token in _DIALOG_NUMBER_WORDS:
            numbers.append(_DIALOG_NUMBER_WORDS[token])
            continue
        return None  # any other word makes it not a pure selection list
    if not numbers or not has_verb_or_context:
        return None
    if len(set(numbers)) != len(numbers):
        return None  # a repeated mention is ambiguity, not emphasis
    return tuple(sorted(numbers))


#: A free-text answer must NAME itself ("responde: …"): the same lead-in
#: family the yes/no form uses, applied to the RAW utterance so the
#: extracted text keeps the speaker's casing and punctuation.
_DIALOG_FREE_TEXT_RE = re.compile(
    r"^(?:contesta|responde|dile|di|escribe|answer|say|write"
    r"|tell\s+(?:him|it))(?:[:,]|\s)+\s*(?:que\s+)?(\S.*)$",
    re.IGNORECASE,
)

#: A spoken answer is one breath, not a paragraph.
DIALOG_FREE_TEXT_MAX_CHARS = 280


def resolve_dialog_free_text(text: str):
    """Extracts the literal answer a free-text utterance carries, or None.

    The lead-in ("responde:", "dile ", "answer: …") names the words as
    the answer; the remainder after an optional "que" is the text,
    VERBATIM (casing preserved). No lead-in, nothing after it, or an
    answer beyond the spoken bound is None — the caller asks rather
    than types a guess.

    Examples:
        "responde: usa la rama main" -> "usa la rama main"
        "responde que usa la rama main" -> "usa la rama main"
        "usa la rama main" / "responde:" -> None
    """
    raw = (text or "").strip()
    if not raw or len(raw) > DIALOG_FREE_TEXT_MAX_CHARS + 24:
        return None
    match = _DIALOG_FREE_TEXT_RE.match(raw)
    if match is None:
        return None
    answer = match.group(1).strip()
    if not answer or len(answer) > DIALOG_FREE_TEXT_MAX_CHARS:
        return None
    return answer


# -- destructive text guard (server-side twin of the PWA analyzer) ---------
#
# The PWA types free-text answers only after destructive.js demands a
# second tap; voice has no second tap, so the SAME analyzer — same
# segments, same words, same rules, same Spanish reasons — runs here to
# REFUSE a matching spoken answer outright. A faithful port, not a new
# policy: false positives cost the keypad, false negatives cost a
# terminal.

_SEGMENT_SEPARATORS = (";", "&", "|", "\n", "\r")
_REDIRECT_OP_RE = re.compile(r"^(?:\d*&?)?>{1,2}\|?$|^:>$|^&>{1,2}$")
_GLUED_REDIRECT_RE = re.compile(r"(?:^|[^>])(>{1,2})\|?(/.*)$")
_BARE_LEADING_RE = re.compile(r"^[$({`'\"]+")
_BARE_TRAILING_RE = re.compile(r"[)`}'\"]+$")
_RECURSIVE_FLAG_RE = re.compile(r"^-[a-z]*r[a-z]*$")
_MKFS_RE = re.compile(r"^mkfs($|[^a-z0-9_])", re.IGNORECASE)


def _split_segments(text: str) -> list[str]:
    """One candidate command per segment; quotes guard their content."""
    segments: list[str] = []
    current = ""
    quote: Optional[str] = None
    for ch in text:
        if quote:
            current += ch
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            current += ch
        elif ch in _SEGMENT_SEPARATORS:
            segments.append(current)
            current = ""
        else:
            current += ch
    segments.append(current)
    return segments


def _bare(word: str) -> str:
    """A word as the shell would name a command: wrapper punctuation
    peeled off both edges, the middle untouched."""
    return _BARE_TRAILING_RE.sub("", _BARE_LEADING_RE.sub("", word))


def _is_program(word: str, name: str) -> bool:
    return _bare(word).lower() == name


def _is_recursive_flag(word: str) -> bool:
    flag = _bare(word).lower()
    return flag == "--recursive" or _RECURSIVE_FLAG_RE.match(flag) is not None


def _is_long_force_flag(word: str) -> bool:
    flag = _bare(word).lower()
    return flag == "--force" or flag.startswith("--force=")


def _is_push_force_flag(word: str) -> bool:
    flag = _bare(word).lower()
    return (
        flag == "-f"
        or _is_long_force_flag(word)
        or flag.startswith("--force-")
    )


def _is_system_target(path: str) -> bool:
    return path.startswith("/") and path != "/dev/null"


#: Ordered most-specific first (mirrors destructive.js); the first rule
#: to fire on any segment is the one whose reason is spoken.
_DESTRUCTIVE_RULES: tuple[tuple[str, str, object], ...] = (
    (
        "rm-recursive",
        "rm -r (borrado recursivo)",
        lambda words: any(
            _is_program(word, "rm")
            and any(_is_recursive_flag(later) for later in words[pos + 1:])
            for pos, word in enumerate(words)
        ),
    ),
    (
        "git-force-push",
        "git push --force (reescribe el historial)",
        lambda words: any(
            _is_program(words[pos], "git")
            and _is_program(words[pos + 1], "push")
            and any(
                _is_push_force_flag(later) for later in words[pos + 2:]
            )
            for pos in range(len(words) - 1)
        ),
    ),
    (
        "sudo",
        "sudo (se ejecuta como root)",
        lambda words: any(_is_program(word, "sudo") for word in words),
    ),
    (
        "force-flag",
        "flag --force",
        lambda words: any(_is_long_force_flag(word) for word in words),
    ),
    (
        "dd-raw-write",
        "dd if= (escritura cruda en disco)",
        lambda words: any(
            _is_program(word, "dd")
            and any(_bare(later).lower().startswith("if=") for later in words[pos + 1:])
            for pos, word in enumerate(words)
        ),
    ),
    (
        "mkfs",
        "mkfs (formatea un sistema de ficheros)",
        lambda words: any(
            _MKFS_RE.match(_bare(word)) is not None for word in words
        ),
    ),
    (
        "redirect-system-path",
        "redirect a una ruta del sistema",
        lambda words: any(
            (
                _REDIRECT_OP_RE.match(_bare(word)) is not None
                and pos + 1 < len(words)
                and _is_system_target(_bare(words[pos + 1]))
            )
            or (
                (glued := _GLUED_REDIRECT_RE.search(_bare(word))) is not None
                and _is_system_target(glued.group(2))
            )
            for pos, word in enumerate(words)
        ),
    ),
)


def destructive_text_reason(text: str):
    """The first destructive rule's reason for this text, or None.

    The server-side twin of the PWA's ``destructive.js``
    (``DestructiveGuard.isDestructiveInput``): segments split on shell
    separators outside quotes, words split on whitespace (quotes do NOT
    hide words), every rule sees every segment, rule priority over
    segment order. Non-strings and empty text are never destructive.
    """
    if not isinstance(text, str) or not text:
        return None
    segments = [
        [word for word in segment.split() if word]
        for segment in _split_segments(text)
    ]
    for _name, reason, test in _DESTRUCTIVE_RULES:
        for words in segments:
            if words and test(words):
                return reason
    return None
