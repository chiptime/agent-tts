"""Secrets and credentials redactor for agent speech.

Agent transcripts (SQLite/JSONL connectors, terminal scrollback) can carry
API keys, tokens, passwords and long hashes inside diffs, logs and
authorization headers. Every text synthesized, notified (ntfy.sh) or
published to the podcast RSS feed passes through the shared cleaning stage,
so redaction is applied exactly once there (``cleaner.clean_agent_text``)
and every downstream consumer inherits sanitized text.

Stdlib ``re`` only: all patterns are pre-compiled at import time, matching
is pure and offline. Spoken placeholders are short Spanish phrases so the
synthesized audio stays terse.
"""

import re
from typing import Callable, List, Tuple, Union

# Spoken placeholders (Spanish: they are read aloud, never displayed).
_TOKEN_REDACTED = "[token redactado]"
_API_KEY_REDACTED = "[clave de API omitida]"
_ACCESS_KEY_REDACTED = "[clave de acceso omitida]"
_PRIVATE_KEY_REDACTED = "[clave privada omitida]"
_HASH_REDACTED = "[hash redactado]"
_VALUE_REDACTED = "[clave omitida]"

# --- Pre-compiled token patterns -------------------------------------------
#
# Word-boundary discipline keeps normal words safe: "sketch" can never
# trigger the sk- rule (no hyphen) and "tokenize" can never trigger the
# token= rule (\b requires a non-word char right after "token").

# PEM private key blocks (RSA/EC/DSA/OPENSSH/PKCS-8 variants). Matched first
# so the base64 body is never half-eaten by the other families.
_PEM_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*-----",
    re.DOTALL,
)

# Authorization header value, including the "Bearer <token>" form in the
# same match so it collapses into a single spoken placeholder.
_AUTHORIZATION_RE = re.compile(r"(?i)\bAuthorization\s*:\s*(?:Bearer\s+)?[^\s,;]+")

# Standalone "Bearer <token>". The value must be 8+ token-ish chars and
# contain at least one digit: prose like "Bearer authentication" can never
# match, while real bearer tokens are digit-bearing in practice.
_BEARER_RE = re.compile(
    r"(?i)\bBearer\s+"
    r"(?=[A-Za-z0-9._~+/=-]{8,}\b)"
    r"[A-Za-z0-9._~+/=-]*[0-9][A-Za-z0-9._~+/=-]*"
)

# JWT: three dot-separated base64url segments; the JOSE header always
# base64-starts with eyJ (the encoded '{' of '{"'). Matched before the
# prefix families so no prefix rule can split a token mid-structure.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")

# OpenAI keys: sk-proj- with hyphen/underscore-bearing bodies, and legacy
# sk- keys whose body is pure base62. Hyphens are excluded from the legacy
# body so hyphenated library names (sk-learn-gradient-...) can never match.
_OPENAI_KEY_RE = re.compile(r"\bsk-(?:proj-[A-Za-z0-9_-]{20,}|[A-Za-z0-9]{20,})")

# GitHub tokens: classic ghp_/gho_/ghs_/ghu_ and fine-grained github_pat_.
_GITHUB_TOKEN_RE = re.compile(
    r"\b(?:gh[posu]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{22,})"
)

# GitLab personal access tokens.
_GITLAB_TOKEN_RE = re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")

# AWS access key IDs: AKIA + exactly 16 uppercase alphanumerics.
_AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")

# Slack tokens: bot/app/user/refresh/legacy families.
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")

# Generic credential assignments: api_key=, apikey=, secret=, password=,
# passwd=, token= (case-insensitive, '=' or ':'). The value must span 4+
# chars and contain at least one digit or symbol, so normal prose
# ("the secret ingredient is love") can never match; real credentials are
# digit/symbol-bearing in practice. Quotes around the value are consumed.
_ASSIGNMENT_VALUE_CLASS = r"[A-Za-z0-9_.@#$%^&*!?~/\\+=|<>(){}\[\]-]"
_GENERIC_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|secret|passwd|password|token)"
    r"(\s*[=:]\s*)"
    r"[\"']?"
    r"(?=" + _ASSIGNMENT_VALUE_CLASS + r"{4,}[\"']?(?:\s|$))"
    + _ASSIGNMENT_VALUE_CLASS + r"*"
    r"[0-9@#$%^&*!?~/\\+=|<>(){}\[\]-]"
    + _ASSIGNMENT_VALUE_CLASS + r"*"
    r"[\"']?"
)

# Long hex digests (SHA-256-like). Lookarounds require EXACTLY 64 hex chars,
# so git short SHAs (7-12) and full SHA-1 (40) never match, and no 64-char
# window can be cut out of an even longer hex blob.
_HEX64_HASH_RE = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])")


def _assignment_sub(match: "re.Match") -> str:
    """Rebuilds '<key><separator>' and appends the spoken placeholder."""
    return match.group(1) + match.group(2) + _VALUE_REDACTED


# Application order matters: structural or high-context patterns first
# (PEM, header/bearer values, JWT) so the looser prefix families can never
# split them; generic assignments and the hash rule run last.
_REDACTION_RULES: List[Tuple[re.Pattern, Union[str, Callable[["re.Match"], str]]]] = [
    (_PEM_PRIVATE_KEY_RE, _PRIVATE_KEY_REDACTED),
    (_AUTHORIZATION_RE, _TOKEN_REDACTED),
    (_BEARER_RE, _TOKEN_REDACTED),
    (_JWT_RE, _TOKEN_REDACTED),
    (_OPENAI_KEY_RE, _API_KEY_REDACTED),
    (_GITHUB_TOKEN_RE, _TOKEN_REDACTED),
    (_GITLAB_TOKEN_RE, _TOKEN_REDACTED),
    (_AWS_KEY_RE, _ACCESS_KEY_REDACTED),
    (_SLACK_TOKEN_RE, _TOKEN_REDACTED),
    (_GENERIC_ASSIGNMENT_RE, _assignment_sub),
    (_HEX64_HASH_RE, _HASH_REDACTED),
]


def redact_secrets(text: str) -> str:
    """Redacts API keys, tokens, passwords, JWTs, private keys and long hashes.

    One pass per pre-compiled family. Intended to run once inside the
    shared cleaning stage so speech synthesis, ntfy.sh notifications and
    the podcast RSS feed can never carry raw credentials downstream.
    """
    if not text:
        return text
    for pattern, replacement in _REDACTION_RULES:
        text = pattern.sub(replacement, text)
    return text
