"""Server-side spoken i18n (S4, doc 09): one lexicon, two locales.

Everything the brain SPEAKS is product copy assembled from templates —
the watcher's transition digests and the deterministic gate closers.
This module owns those templates per locale so the spoken surface can
follow the call's language while the DEFAULT stays byte-identical
Spanish (the exact historical strings; installed phones and the
pre-existing suite pin them).

The locale is chosen by the operator's clients through the call
context (``POST /call/context`` with ``locale``) and read wherever a
spoken string is built. ``es`` is the default and the fallback: a
locale the lexicon does not know speaks Spanish, never an exception.
"""

from __future__ import annotations

from typing import Callable, Optional

#: The locales v1 ships (L3: es + en, framework ready for more).
LOCALES = ("es", "en")

#: The product default AND the fallback for anything unknown.
DEFAULT_LOCALE = "es"

#: One bundle per locale with the SAME keys (pinned by tests). Keys:
#: ``blocked``/``done`` take ``{label}`` + the detail slot, ``fallback``
#: replaces an unreadable detail, ``closer``/``reprompt`` are the
#: deterministic approval closers.
LEXICON: dict[str, dict[str, str]] = {
    "es": {
        "blocked": "{label} necesita tu atención: {question}",
        "done": "{label} terminó: {detail}",
        "fallback": "sin detalle disponible",
        "closer": "¿Se envía?",
        "reprompt": "¿Sí o no?",
    },
    "en": {
        "blocked": "{label} needs your attention: {question}",
        "done": "{label} finished: {detail}",
        "fallback": "no detail available",
        "closer": "Send it?",
        "reprompt": "Yes or no?",
    },
}


def normalize_locale(value: object) -> Optional[str]:
    """A locale this lexicon speaks, or None.

    Case-insensitive and whitespace-tolerant (clients send BCP-47-ish
    tags); anything unknown — including non-strings — is None so the
    caller can IGNORE it (the POST contract) instead of guessing.
    """
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    return cleaned if cleaned in LOCALES else None


def _bundle(locale: Optional[str]) -> dict:
    return LEXICON.get(locale or "", LEXICON[DEFAULT_LOCALE])


def blocked_announcement(locale: Optional[str], label: str, question: str) -> str:
    """The spoken digest for a pane that needs the operator."""
    return _bundle(locale)["blocked"].format(label=label, question=question)


def done_announcement(locale: Optional[str], label: str, detail: str) -> str:
    """The spoken digest for a pane that finished its turn."""
    return _bundle(locale)["done"].format(label=label, detail=detail)


def fallback_detail(locale: Optional[str]) -> str:
    """What the digest says when no detail could be read."""
    return _bundle(locale)["fallback"]


def approval_closer(locale: Optional[str]) -> str:
    """The deterministic closer appended when a gate opens."""
    return _bundle(locale)["closer"]


def reprompt_line(locale: Optional[str]) -> str:
    """The one spoken re-prompt after an ambiguous confirmation."""
    return _bundle(locale)["reprompt"]


def locale_provider_from(store) -> Callable[[], str]:
    """A ``() -> locale`` closure over a call-context store.

    The store needs a ``locale()`` accessor (CallContextStore has one);
    any failure — no store, no accessor — resolves to the default so a
    spoken string can never crash on its locale lookup.
    """
    def _provider() -> str:
        try:
            value = store.locale() if store is not None else None
        except Exception:  # noqa: BLE001 — speech never breaks on locale
            return DEFAULT_LOCALE
        return value if value in LOCALES else DEFAULT_LOCALE

    return _provider
