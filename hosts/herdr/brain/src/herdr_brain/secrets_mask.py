"""Secret masking for text on its way out of the host (S14).

Every surface that carries agent text beyond this machine — the pane
mirror, history pages, the reading views, SSE announcements, the LLM
live context and the spoken renders — passes through one masking pass,
so a credential that wandered onto a screen or into a transcript never
leaves with it. Masking is an OUTPUT concern only: transcripts, stores
and caches keep their bytes exactly as written; nothing on disk is
rewritten by this module.

Detectors are deliberately conservative heuristics. A false negative
only means one more secret stays home; a false positive mangles
ordinary text the user asked to read. So every pattern demands a known
shape (a provider prefix, a three-part JWT, PEM fences, URL userinfo)
or a secret-sounding key name paired with a non-trivial value. Ordinary
code, paths, git SHAs, UUIDs and short values stay untouched.

Placeholders keep a hint of what was hidden (``sk-ant-…[masked]``,
``[masked:jwt]``) and are stable: the same secret always yields the
same placeholder. The spoken variant replaces every placeholder with
one natural phrase so text-to-speech never spells a mask out.

The switch is the ``HERDR_BRAIN_SECRET_MASKING`` environment variable:
masking is ON unless the value is one of the explicit off words
(``off``, ``0``, ``false``, ``no``). The switch is read at call time so
tests and operators can flip it without rebuilding anything.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

#: Environment variable that disables masking when set to an off word.
ENV_VAR = "HERDR_BRAIN_SECRET_MASKING"
_OFF_VALUES = frozenset({"off", "0", "false", "no"})

#: One natural spoken placeholder for every masked secret.
SPOKEN_PLACEHOLDER = "clave oculta"

Span = Tuple[int, int, str]


def masking_enabled(environ: Optional[Dict[str, str]] = None) -> bool:
    """True unless the switch carries an explicit off value."""
    env = os.environ if environ is None else environ
    value = str(env.get(ENV_VAR, "")).strip().lower()
    return value not in _OFF_VALUES


# -- named detectors ---------------------------------------------------------

#: Provider API keys: (regex, hint) where the regex captures the known
#: literal prefix in group 1. The left look-behind stops mid-word
#: matches ("task-management" is not an OpenAI key).
_PROVIDER_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"(?<![A-Za-z0-9])(sk-ant-)[A-Za-z0-9_-]{16,}"), "{prefix}…[masked]"),
    (re.compile(r"(?<![A-Za-z0-9])(sk-proj-)[A-Za-z0-9_-]{20,}"), "{prefix}…[masked]"),
    (re.compile(r"(?<![A-Za-z0-9])(sk-)(?!ant-|proj-)[A-Za-z0-9]{20,}"), "{prefix}…[masked]"),
    (re.compile(r"(?<![A-Za-z0-9])(github_pat_)[A-Za-z0-9_]{22,}"), "{prefix}…[masked]"),
    (re.compile(r"(?<![A-Za-z0-9])(gh[pos]_)[A-Za-z0-9]{30,}"), "{prefix}…[masked]"),
    (re.compile(r"(?<![A-Za-z0-9])(xox[abp]-)[A-Za-z0-9-]{10,}"), "{prefix}…[masked]"),
    (re.compile(r"(?<![A-Za-z0-9])(AKIA)[A-Z0-9]{16}(?![A-Za-z0-9])"), "{prefix}…[masked]"),
    (re.compile(r"(?<![A-Za-z0-9])(AIza)[A-Za-z0-9_-]{30,}"), "{prefix}…[masked]"),
    (re.compile(r"(?<![A-Za-z0-9])(sk_live_)[A-Za-z0-9]{16,}"), "{prefix}…[masked]"),
    (re.compile(r"(?<![A-Za-z0-9])(npm_)[A-Za-z0-9]{30,}"), "{prefix}…[masked]"),
)

_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"(?![A-Za-z0-9_-])"
)

_PEM_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY( BLOCK)?-----"
    r"[\s\S]*?"
    r"-----END [A-Z0-9 ]*PRIVATE KEY( BLOCK)?-----"
)

_BEARER_RE = re.compile(
    r"(?i)\b(authorization[ \t]*:[ \t]*bearer[ \t]+)[A-Za-z0-9._~+/=-]{8,}"
)

_URL_CREDS_RE = re.compile(
    r"(?<![A-Za-z0-9])((?:[a-z][a-z0-9+.\-]{0,15}://))[^\s/@:]{1,64}:[^\s/@]{1,128}@"
)

#: KEY=value / "key": "value" with a secret-sounding name. Matched in
#: two steps — key+separator first, then the value — so an ordinary
#: key whose value happens to CONTAIN a secret never swallows the
#: region and hides the inner credential.
_KEY_SEP_RE = re.compile(
    r"(?P<key>\"?[A-Za-z_][A-Za-z0-9_.\\-]*\"?)(?P<sep>[ \t]*[:=][ \t]*)"
)
_VALUE_RE = re.compile(
    "(?P<value>\"[^\"\\n]{6,}\"|'[^'\\n]{6,}'|[^\\s\"'(){}\\[\\];,<>&|]{6,})"
)

#: Whole key segments that mark an assignment as secret-bearing.
_SECRET_KEY_SEGMENTS = frozenset({"password", "passwd", "secret", "token", "apikey"})
#: Multi-word key compounds (compared on a _-normalized key).
_SECRET_KEY_COMPOUNDS = ("api_key", "private_key", "access_key", "client_secret")
#: Key shapes strong enough to mask even NUMERIC values (pins): every
#: secret-sounding name except the bare ``token`` segment, which LLM
#: APIs use for purely numeric counters (next_token).
_STRONG_KEY_SEGMENTS = frozenset({"password", "passwd", "secret", "apikey"})

#: Values that are never worth masking (booleans, nulls, placeholders).
_TRIVIAL_VALUES = frozenset(
    {"true", "false", "null", "none", "nil", "undefined", "yes", "no"}
)


def find_provider_keys(text: str, hinted: bool = True) -> List[Span]:
    """Known provider API keys, replaced by their prefix as a hint.

    ``hinted=False`` swaps the prefix hint for a bare ``[masked]`` —
    for derived hint text (the pending excerpt) where even the raw
    provider prefix must not appear.
    """
    template = "{prefix}…[masked]" if hinted else "[masked]"
    spans: List[Span] = []
    for pattern, _hint in _PROVIDER_PATTERNS:
        for match in pattern.finditer(text):
            spans.append((match.start(), match.end(), template.format(prefix=match.group(1))))
    return spans


def find_jwts(text: str) -> List[Span]:
    """Three-segment signed JWTs."""
    return [(m.start(), m.end(), "[masked:jwt]") for m in _JWT_RE.finditer(text)]


def find_pem_blocks(text: str) -> List[Span]:
    """Fenced PRIVATE KEY blocks (RSA/EC/OpenSSH/encrypted...)."""
    return [
        (m.start(), m.end(), "[masked:private-key]") for m in _PEM_RE.finditer(text)
    ]


def find_bearer_tokens(text: str) -> List[Span]:
    """``Authorization: Bearer`` headers keep their scheme, lose the token."""
    return [
        (m.start(), m.end(), m.group(1) + "[masked:bearer]")
        for m in _BEARER_RE.finditer(text)
    ]


def find_url_credentials(text: str) -> List[Span]:
    """``scheme://user:pass@`` userinfo, scheme and host preserved."""
    return [
        (m.start(), m.end(), m.group(1) + "[masked:url-credentials]@")
        for m in _URL_CREDS_RE.finditer(text)
    ]


def _key_secret_strength(raw_key: str) -> Optional[str]:
    """``"strong"``, ``"weak"`` or None for an assignment key name.

    Strong names (password/passwd/secret/apikey and the key compounds)
    mask even numeric values — pins are digits too. The bare ``token``
    segment is weak: LLM APIs expose purely numeric token counters
    (``next_token``) that must stay readable.
    """
    key = raw_key.strip("\"'")
    norm = re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_").lower()
    if not norm:
        return None
    segments = set(norm.split("_"))
    if segments & _STRONG_KEY_SEGMENTS:
        return "strong"
    if any(re.search(rf"(?:^|_){compound}(?:_|$)", norm)
           for compound in _SECRET_KEY_COMPOUNDS):
        return "strong"
    if segments & _SECRET_KEY_SEGMENTS:
        return "weak"
    return None


def _value_maskable(raw_value: str, quoted: bool, strong: bool) -> bool:
    """True when the value is substantial enough to be a credential."""
    value = raw_value[1:-1] if quoted else raw_value
    if len(value) < 6 or value.lower() in _TRIVIAL_VALUES:
        return False
    if value[0] in "$<{" or value[-1] in "}>":
        return False  # shell/json placeholders, never the secret itself
    if re.search(r"[(){}\[\]]", value):
        return False  # code calls, not literals
    if re.fullmatch(r"[*x.·\-]+", value):
        return False  # already-redacted dots/stars
    if re.fullmatch(r"[\d.\-]+", value):
        # Numbers: plausible ONLY under a strong secret name (a pin);
        # counters, versions and ids under ordinary names stay exact.
        return strong
    if not quoted:
        # Bare values must look credential-grade: a digit or underscore
        # inside, or real length. Prose ("the secret: nothing here")
        # fails this and stays readable.
        if not (re.search(r"[\d_]", value) or len(value) >= 12):
            return False
    return True


def find_secret_assignments(text: str) -> List[Span]:
    """KEY=value / "key": "value" with a secret-sounding name.

    The key and separator stay visible; only the value is replaced.
    """
    spans: List[Span] = []
    for match in _KEY_SEP_RE.finditer(text):
        strength = _key_secret_strength(match.group("key"))
        if strength is None:
            continue
        value_match = _VALUE_RE.match(text, match.end())
        if value_match is None:
            continue
        raw_value = value_match.group("value")
        quoted = (
            len(raw_value) >= 2
            and raw_value[0] in "\"'"
            and raw_value[-1] == raw_value[0]
        )
        if not _value_maskable(raw_value, quoted, strength == "strong"):
            continue
        replacement = (
            match.group("key")
            + match.group("sep")
            + (raw_value[0] + "…[masked]" + raw_value[-1] if quoted else "…[masked]")
        )
        spans.append((match.start(), value_match.end(), replacement))
    return spans


_ALL_FINDERS = (
    find_jwts,
    find_pem_blocks,
    find_bearer_tokens,
    find_url_credentials,
    find_secret_assignments,
)


def _resolve(spans: List[Span]) -> List[Span]:
    """Leftmost-longest, non-overlapping: a secret masked once stays once."""
    accepted: List[Span] = []
    covered = -1
    for start, end, replacement in sorted(spans, key=lambda s: (s[0], -s[1])):
        if start >= covered:
            accepted.append((start, end, replacement))
            covered = end
    return accepted


def mask_spans(text: str, spoken: bool = False, hinted: bool = True) -> List[Span]:
    """Resolved mask spans of ``text`` (empty when masking is off)."""
    if not masking_enabled() or not text:
        return []
    spans: List[Span] = list(find_provider_keys(text, hinted=hinted))
    for finder in _ALL_FINDERS:
        spans.extend(finder(text))
    if spoken:
        spans = [(s, e, SPOKEN_PLACEHOLDER) for s, e, _r in spans]
    return _resolve(spans)


def _replace_spans(text: str, spans: List[Span]) -> str:
    if not spans:
        return text
    out: List[str] = []
    cursor = 0
    for start, end, replacement in spans:
        out.append(text[cursor:start])
        out.append(replacement)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def mask_text(text: str, spoken: bool = False, hinted: bool = True) -> str:
    """Masks every detected secret in ``text`` (identity when off).

    ``spoken`` swaps every hint placeholder for :data:`SPOKEN_PLACEHOLDER`
    so the result reads naturally through text-to-speech. ``hinted=False``
    drops the raw provider-prefix hint for derived hint text (the pending
    excerpt), where even a public prefix fragment must not appear.
    """
    if not text:
        return text
    return _replace_spans(text, mask_spans(text, spoken=spoken, hinted=hinted))


#: A display placeholder already present in text (replay path): the
#: prefix token glued to it belongs to the mask and goes too.
_PLACEHOLDER_RE = re.compile(r"\S{0,40}\[masked[^\]]*\]")


def mask_for_speech(text: str) -> str:
    """Speech-boundary mask: secrets AND stale display placeholders read
    as the one natural spoken phrase."""
    if not masking_enabled() or not text:
        return text
    spans = [
        (m.start(), m.end(), SPOKEN_PLACEHOLDER) for m in _PLACEHOLDER_RE.finditer(text)
    ]
    spans.extend(mask_spans(text, spoken=True))
    return _replace_spans(text, _resolve(spans))


# -- styled runs --------------------------------------------------------------


def _style_of(run: dict) -> dict:
    return {key: value for key, value in run.items() if key != "t"}


def mask_runs(runs: List[List[dict]]) -> List[List[dict]]:
    """Masks an ANSI-runs payload (list of lines of ``{t, ...}`` runs).

    Detection runs on the JOINED text of the whole payload, so a
    credential split across styled runs — or across LINES, like a PEM
    block — is still caught. When no span crosses a line boundary each
    line is rebuilt on its own and lines without matches are returned
    as the same object, untouched; a crossing span (multiline secret)
    collapses its covered lines into the placeholder, preserving every
    surviving character's style.
    """
    if not masking_enabled() or not runs:
        return runs
    texts = ["".join(run.get("t", "") for run in line) for line in runs]
    spans = mask_spans("\n".join(texts))
    if not spans:
        return runs
    starts: List[int] = []
    position = 0
    for text in texts:
        starts.append(position)
        position += len(text) + 1
    crossing = any(
        any(s < start < e for start in starts[1:]) for s, e, _r in spans
    )
    if not crossing:
        out: List[List[dict]] = []
        for index, line in enumerate(runs):
            base = starts[index]
            limit = base + len(texts[index])
            line_spans = [
                (s - base, e - base, replacement)
                for s, e, replacement in spans
                if base <= s and e <= limit
            ]
            out.append(_rebuild_line(line, line_spans) if line_spans else line)
        return out
    return _rebuild_flat(runs, spans)


def _rebuild_flat(runs: List[List[dict]], spans: List[Span]) -> List[List[dict]]:
    """Rebuilds a payload whose spans cross line boundaries.

    The payload is flattened into (text, style) pieces with explicit
    newline markers between lines, spans are applied over the flat
    stream (dropping the newlines they cover), and the result is split
    back into lines.
    """
    pieces: List[Tuple[str, dict]] = []  # (text, style); "\n" markers
    for index, line in enumerate(runs):
        for run in line:
            text = run.get("t", "")
            if text:
                pieces.append((text, _style_of(run)))
        if index < len(runs) - 1:
            pieces.append(("\n", {}))
    entries: List[Tuple[int, int, str, dict]] = []
    position = 0
    for text, style in pieces:
        entries.append((position, position + len(text), text, style))
        position += len(text)

    def style_at(index: int) -> dict:
        for start, end, _text, style in entries:
            if start <= index < end:
                return style
        return entries[-1][3] if entries else {}

    out: List[Tuple[str, dict]] = []

    def emit(text: str, style: dict) -> None:
        if not text:
            return
        if out and out[-1][1] == style and text != "\n" and out[-1][0] != "\n":
            out[-1] = (out[-1][0] + text, style)
        else:
            out.append((text, style))

    cursor = 0
    for start, end, replacement in spans:
        for pstart, pend, text, style in entries:
            low, high = max(cursor, pstart), min(start, pend)
            if low < high:
                emit(text[low - pstart : high - pstart], style)
        emit(replacement, style_at(start))
        cursor = max(cursor, end)
    for pstart, pend, text, style in entries:
        low = max(cursor, pstart)
        if low < pend:
            emit(text[low - pstart : pend - pstart], style)

    lines: List[List[Tuple[str, dict]]] = [[]]
    for text, style in out:
        if text == "\n":
            lines.append([])
            continue
        line = lines[-1]
        if line and line[-1][1] == style:
            line[-1] = (line[-1][0] + text, style)
        else:
            line.append((text, style))
    # A span ending on the last newline can leave one trailing empty
    # line the original payload never had.
    while len(lines) > 1 and not lines[-1]:
        lines.pop()
    return [[dict(style, t=text) for text, style in line] for line in lines]


def _rebuild_line(line: List[dict], spans: List[Span]) -> List[dict]:
    pieces: List[Tuple[str, dict]] = []  # (text, style), adjacent-equal merged

    def emit(text: str, style: dict) -> None:
        if not text:
            return
        if pieces and pieces[-1][1] == style:
            pieces[-1] = (pieces[-1][0] + text, style)
        else:
            pieces.append((text, style))

    entries = [(run.get("t", ""), _style_of(run)) for run in line]
    offsets: List[Tuple[int, int]] = []
    position = 0
    for text, _style in entries:
        offsets.append((position, position + len(text)))
        position += len(text)

    def style_at(index: int) -> dict:
        for (start, end), (_text, style) in zip(offsets, entries):
            if start <= index < end:
                return style
        return entries[-1][1] if entries else {}

    def emit_plain(upto_from: int, upto_to: int) -> None:
        for (start, end), (text, style) in zip(offsets, entries):
            low, high = max(upto_from, start), min(upto_to, end)
            if low < high:
                emit(text[low - start : high - start], style)

    cursor = 0
    for start, end, replacement in spans:
        emit_plain(cursor, start)
        emit(replacement, style_at(start))
        cursor = max(cursor, end)
    emit_plain(cursor, position)

    return [dict(style, t=text) for text, style in pieces if text]
