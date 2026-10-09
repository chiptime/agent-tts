"""Completion health gate (AT-11 task 3.5, design slice 19).

The wizard may write its completion marker only when BOTH checks pass
(spec "Completion health and safe retryability"):

1. the brain ``/health`` endpoint answers ``200`` with ``"tts": "ok"``;
2. ``herdr plugin list`` exits ``0`` and emits no manifest warning
   (audit A2: a stale registration prints ``warning: manifest
   unavailable: …``).

Deliberately NOT checked: the ``stt`` field.  ``/health`` ``stt`` is
``loading | ready | unavailable`` (design Decision 6) and ``unavailable``
is the correct, expected state after an STT refusal, so a refusal must
never fail the gate.  ``tts: degraded`` / ``missing`` do fail it (the
daemon is retried a few times first: a freshly started brain needs a
moment before it reports ``ok``).

Standard library only, like the rest of the wizard.  Both external
boundaries are injectable (``fetch`` and ``runner``) so the contract is
testable without a server or a ``herdr`` binary; production code never
passes them.  Every message goes through ``RunContext.diagnostic`` (the
single redaction boundary); the gate itself never touches a secret.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, List, Mapping, Tuple

from herdr_onboarding import resolve as _resolve

if TYPE_CHECKING:  # pragma: no cover — import-cycle-free typing only
    from herdr_onboarding.wizard import RunContext

HEALTH_HOST = "127.0.0.1"
HEALTH_TIMEOUT_S = 3.0
HEALTH_ATTEMPTS = 5
HEALTH_RETRY_DELAY_S = 1.0
PLUGIN_LIST_TIMEOUT_S = 30
TTS_OK = "ok"

# ``warning: manifest unavailable: …`` (audit A2) and any other warning
# line the herdr CLI prints; matched per line, case-insensitively.
_WARNING_LINE = re.compile(r"^\s*warn(?:ing)?\b", re.IGNORECASE)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_MANIFEST_PROBLEM = re.compile(
    r"manifest\s+(?:unavailable|invalid|error)", re.IGNORECASE
)

Fetch = Callable[[str, float], Tuple[int, str]]
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one gate check: ``ok`` plus an English explanation."""

    ok: bool
    detail: str


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Never let a local health probe become a request to another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # urllib reports the original 3xx as HTTPError


def default_fetch(url: str, timeout: float) -> Tuple[int, str]:
    """GET ``url`` and return ``(status, body)``.

    A direct opener (no environment proxy): the probe targets the local
    brain and must never be routed through an HTTP proxy or a redirect.
    HTTP error statuses (including rejected redirects) are returned;
    connection-level failures raise ``OSError``.
    """
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _RejectRedirects()
    )
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise OSError(str(exc.reason)) from None


def health_url(env: Mapping[str, str]) -> str:
    """``http://127.0.0.1:<port>/health`` with the portable port order
    (``HERDR_BRAIN_PORT`` → persisted config → ``8741``)."""
    port = _resolve.resolve_port(env=dict(env))
    return f"http://{HEALTH_HOST}:{port}/health"


def check_brain_health(
    env: Mapping[str, str],
    *,
    fetch: Fetch = default_fetch,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = HEALTH_ATTEMPTS,
    delay: float = HEALTH_RETRY_DELAY_S,
) -> CheckResult:
    """The brain ``/health`` must report ``tts: ok`` (``stt`` is ignored)."""
    try:
        url = health_url(env)
    except _resolve.ResolutionError as exc:
        return CheckResult(False, f"cannot determine the brain port: {exc}")
    last = "no attempt was made"
    for attempt in range(max(1, attempts)):
        if attempt:
            sleep(delay)
        try:
            status, body = fetch(url, HEALTH_TIMEOUT_S)
        except OSError as exc:
            last = (
                f"brain /health at {url} is unreachable ({type(exc).__name__}: "
                f"{exc}) — start it with: herdr-brain restart"
            )
            continue
        if status != 200:
            last = f"brain /health at {url} answered HTTP {status}"
            continue
        try:
            payload = json.loads(body)
        except ValueError:
            last = f"brain /health at {url} did not return JSON"
            continue
        tts = payload.get("tts") if isinstance(payload, dict) else None
        if tts == TTS_OK:
            return CheckResult(True, f"brain /health reports tts: {TTS_OK}")
        last = (
            f"brain /health at {url} reports tts: {tts!r} (expected "
            f"{TTS_OK!r}) — check: herdr-tts --contract-version and herdr-tts status"
        )
    return CheckResult(False, last)


def manifest_warnings(output: str) -> List[str]:
    """Lines of ``herdr plugin list`` output that are warnings."""
    return [
        line.strip()
        for line in _ANSI.sub("", output).splitlines()
        if _WARNING_LINE.match(line) or _MANIFEST_PROBLEM.search(line)
    ]


def check_plugin_list(
    env: Mapping[str, str],
    *,
    runner: Runner = subprocess.run,
) -> CheckResult:
    """``herdr plugin list`` must exit 0 and emit no manifest warning."""
    herdr = _resolve.resolve_herdr_bin(env=dict(env))
    try:
        completed = runner(
            [herdr, "plugin", "list"],
            capture_output=True,
            text=True,
            env=dict(env),
            timeout=PLUGIN_LIST_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(
            False,
            f"cannot run '{herdr} plugin list' ({type(exc).__name__}: {exc}) — "
            "set HERDR_BIN to the herdr binary",
        )
    output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    if completed.returncode != 0:
        return CheckResult(
            False,
            f"'{herdr} plugin list' exited {completed.returncode} — run it "
            "yourself to see why",
        )
    warnings = manifest_warnings(output)
    if warnings:
        shown = "; ".join(warnings[:3])
        return CheckResult(
            False,
            f"herdr plugin list emitted {len(warnings)} warning line(s): "
            f"{shown} — repair with: herdr plugin unlink <id> and reinstall",
        )
    return CheckResult(True, "herdr plugin list emitted no warnings")


class HealthGate:
    """Callable gate for ``Wizard(health_gate=...)``: ``True`` only when
    the brain reports ``tts: ok`` AND ``herdr plugin list`` is clean."""

    def __init__(
        self,
        *,
        fetch: Fetch = default_fetch,
        runner: Runner = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
        attempts: int = HEALTH_ATTEMPTS,
        delay: float = HEALTH_RETRY_DELAY_S,
    ):
        self._fetch = fetch
        self._runner = runner
        self._sleep = sleep
        self._attempts = attempts
        self._delay = delay

    def __call__(self, ctx: "RunContext") -> bool:
        results: Dict[str, CheckResult] = {
            "brain /health": check_brain_health(
                ctx.env,
                fetch=self._fetch,
                sleep=self._sleep,
                attempts=self._attempts,
                delay=self._delay,
            ),
            "plugin list": check_plugin_list(ctx.env, runner=self._runner),
        }
        for name, result in results.items():
            ctx.diagnostic(
                f"health gate — {name}: {'ok' if result.ok else 'FAILED'}: {result.detail}"
            )
        return all(result.ok for result in results.values())


def make_health_gate(**kwargs) -> HealthGate:
    """The production gate (keyword arguments are test seams)."""
    return HealthGate(**kwargs)


__all__ = [
    "CheckResult",
    "HealthGate",
    "check_brain_health",
    "check_plugin_list",
    "default_fetch",
    "health_url",
    "make_health_gate",
    "manifest_warnings",
]
