"""Operational doctor checks (S8, doc 11 fase 2): the brain-side rows.

Distinct from the onboarding doctor (``tools/herdr_onboarding/doctor.py``,
which probes the INSTALL): these rows are the running brain's own
surfaces —

* ``port-brain`` / ``port-crew`` — someone already listening on the
  brain's (or the crew listener's) port;
* ``root-443`` — who owns the tailnet's HTTPS root, read from a
  RECORDED ``tailscale serve status --json`` the operator supplies;
  the brain never runs tailscale (L4), so the raw text comes in and the
  answer is generic: another service's target, this brain's target, or
  no mapping;
* ``vapid`` / ``push-store`` — Web Push keys present (value never
  printed) and the subscription store loadable;
* ``crew`` — the roster loads (solo is healthy; corrupt fails closed);
* ``operator-config`` — the four TOML surfaces load with no skipped
  rows.

Every check is read-only, no secret value ever reaches a detail string,
and each failure names its repair. ``report`` prints the table and
returns the process exit code (0 all ok, 1 any failed row).
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass(frozen=True)
class CheckResult:
    """One doctor row: a verdict, what was observed, and the repair."""

    name: str
    ok: bool
    detail: str
    hint: str = ""


# ── port conflicts ──────────────────────────────────────────────────────────


def _default_port_probe(port: int) -> bool:
    """Whether something already ACCEPTS on this loopback port.

    One loopback connect with a tiny timeout: a listener answers
    (True), a free port refuses (False). Never binds, never holds a
    socket open, touches loopback only."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def check_ports(
    probe: Callable[[int], bool], brain_port: int, crew_port: int
) -> list[CheckResult]:
    """The two ports this daemon wants: the PWA port always, the crew
    port whenever a crew exists (probing it is harmless either way —
    free simply means available)."""
    rows = []
    if probe(brain_port):
        rows.append(CheckResult(
            "port-brain", False,
            f"something is already listening on 127.0.0.1:{brain_port}",
            f"stop it, or set HERDR_BRAIN_PORT (persisted in config.env) "
            f"to a free port instead of {brain_port}",
        ))
    else:
        rows.append(CheckResult("port-brain", True, f"port {brain_port} is free"))
    if probe(crew_port):
        rows.append(CheckResult(
            "port-crew", False,
            f"something is already listening on 127.0.0.1:{crew_port} "
            "(the crew TLS listener wants it)",
            f"free {crew_port} or set HERDR_BRAIN_CREW_PORT",
        ))
    else:
        rows.append(CheckResult("port-crew", True, f"port {crew_port} is free"))
    return rows


# ── root 443 ownership, from a RECORDED status ──────────────────────────────


def _walk_for_443(node) -> tuple[Optional[object], bool]:
    """Recursively find a ``"443"`` (or 443) key in the recorded status
    and return (its value, found). The tailscale status shape has moved
    across versions; scanning for the port key keeps the check generic
    instead of pinning one schema."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "443" or key == 443:
                return value, True
            inner, found = _walk_for_443(value)
            if found:
                return inner, True
    elif isinstance(node, list):
        for item in node:
            inner, found = _walk_for_443(item)
            if found:
                return inner, True
    return None, False


def _targets_text(root_value) -> str:
    """A short generic rendering of what the 443 mapping points at —
    target URLs only, never interpreted."""
    texts: list[str] = []

    def _collect(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("Proxy", "proxy", "URL", "url") and isinstance(value, str):
                    texts.append(value)
                else:
                    _collect(value)
        elif isinstance(node, list):
            for item in node:
                _collect(item)

    _collect(root_value)
    return ", ".join(dict.fromkeys(texts)) or "an unnamed target"


def check_root_443(status_text: str, brain_port: int) -> CheckResult:
    """Who owns the tailnet's HTTPS root, from recorded status JSON.

    Informational by design (ok=True): the row's job is the WARNING the
    serve verb also prints — taking 443 while another service holds it
    is the operator's cutover decision, not a health failure."""
    hint = (
        "the cutover is manual: stop the other service and unserve its "
        "mapping before `herdr-brain serve --root`"
    )
    try:
        parsed = json.loads(status_text)
    except (ValueError, TypeError):
        return CheckResult(
            "root-443", True,
            "could not read the recorded `tailscale serve status --json`",
            "re-run `tailscale serve status --json` yourself and pass the file",
        )
    root_value, found = _walk_for_443(parsed)
    if not found:
        return CheckResult(
            "root-443", True, "no mapping on 443 in the recorded status",
            "the root is free; `herdr-brain serve --root` prints the command",
        )
    targets = _targets_text(root_value)
    if f":{brain_port}" in targets or f":{brain_port}/" in targets:
        return CheckResult(
            "root-443", True,
            f"the root 443 mapping already points at this brain ({targets})",
            "",
        )
    return CheckResult(
        "root-443", True,
        f"another service owns the HTTPS root port (the mapping points at "
        f"{targets})",
        hint,
    )


# ── Web Push: VAPID keys + the subscription store ───────────────────────────


def check_push(state_dir) -> list[CheckResult]:
    state_dir = Path(state_dir)
    rows: list[CheckResult] = []

    from .webpush.store import StoreError, SubscriptionStore
    from .webpush.vapid import VapidError, load_keys

    try:
        keys = load_keys(state_dir)
        if keys is None:
            # The documented soft-off state: nothing broken, nothing
            # armed — a repair hint, not an alarm.
            rows.append(CheckResult(
                "vapid", False, "no VAPID keys configured — push is soft-off",
                "import a pair with `herdr-brain push import <dotenv>` (prints "
                "only the public key)",
            ))
        else:
            rows.append(CheckResult(
                "vapid", True, "VAPID keys present (Web Push armed)",
            ))
            # The public key names the sender; still, the detail stays
            # keyless — nothing about the pair belongs in a report.
    except VapidError as exc:
        rows.append(CheckResult(
            "vapid", False, f"VAPID keys are configured but broken: {exc}",
            "repair or remove vapid.json, then `herdr-brain push import` a "
            "valid pair",
        ))

    try:
        store = SubscriptionStore(state_dir)
        count = len(store.list())
        rows.append(CheckResult(
            "push-store", True, f"push subscription store ok ({count} active)",
        ))
    except StoreError as exc:
        rows.append(CheckResult(
            "push-store", False,
            f"the push subscription store is corrupt: {exc}",
            "repair or remove push-subscriptions.json by hand — the brain "
            "refuses to reset it",
        ))
    return rows


# ── crew roster ─────────────────────────────────────────────────────────────


def check_crew(state_dir) -> CheckResult:
    from .crew.roster import CrewRoster, CrewRosterCorrupt

    state_dir = Path(state_dir)
    try:
        roster = CrewRoster.load(state_dir)
    except CrewRosterCorrupt as exc:
        return CheckResult(
            "crew", False, f"the crew roster is corrupt: {exc}",
            "inspect crew.json by hand — the brain refuses to reset it (a "
            "reset would un-pin every peer)",
        )
    if not roster.has_crew:
        return CheckResult("crew", True, "solo (no crew declared)")
    names = ", ".join(sorted(roster.peers)) or "no peers yet"
    return CheckResult(
        "crew", True,
        f"role {roster.role} as {roster.self_name or '?'}; peers: {names}",
    )


# ── operator config ────────────────────────────────────────────────────────


def check_operator_config(config_dir=None) -> CheckResult:
    from .operator_config import OperatorConfig

    catalog = OperatorConfig(config_dir=config_dir) if config_dir else OperatorConfig()
    reports = catalog.check()
    total_rows = sum(report.row_count for report in reports)
    issues = [issue for report in reports for issue in report.issues]
    if not issues:
        present = sum(1 for report in reports if report.present)
        return CheckResult(
            "operator-config", True,
            f"{present}/4 operator files present, {total_rows} healthy rows",
        )
    first = issues[0].describe()
    return CheckResult(
        "operator-config", False,
        f"{len(issues)} skipped row(s); first: {first}",
        "run `herdr-brain config check` for the per-file report",
    )


# ── the report ──────────────────────────────────────────────────────────────


def report(rows, out=None) -> int:
    """Prints the table (ok/FAIL, detail, repair) and returns the exit
    code: 0 every row ok, 1 any failed row."""
    import sys

    stream = out if out is not None else sys.stdout
    failed = False
    for row in rows:
        marker = "ok" if row.ok else "FAIL"
        if not row.ok:
            failed = True
        line = f"{marker} {row.name}: {row.detail}"
        if row.hint:
            line += f"\n     fix: {row.hint}"
        print(line, file=stream)
    return 1 if failed else 0


def run_all(
    state_dir,
    brain_port: int,
    crew_port: int,
    serve_status_text: Optional[str] = None,
    port_probe: Optional[Callable[[int], bool]] = None,
) -> list[CheckResult]:
    """Every brain-side row in report order."""
    probe = port_probe or _default_port_probe
    rows = list(check_ports(probe, brain_port, crew_port))
    if serve_status_text is not None:
        rows.append(check_root_443(serve_status_text, brain_port))
    rows.extend(check_push(state_dir))
    rows.append(check_crew(state_dir))
    rows.append(check_operator_config())
    return rows
