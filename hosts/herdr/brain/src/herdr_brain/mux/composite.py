"""Composite multiplexer: several adapters behind one port, ids routed by origin.

``HERDR_BRAIN_MUX`` may name a comma-separated list (e.g.
``herdr,tmux,zellij``); :class:`CompositeMux` is the router that makes
those adapters look like ONE :class:`~herdr_brain.mux.MuxAdapter`. It
never re-implements a child: every pane-scoped call is routed to the
child that produced the pane id, verbatim, and the children's own
refusals propagate unchanged.

Id contract (f7f S1(2)): herdr pane ids pass through EXACTLY as the
herdr adapter produced them — no prefix, no copy. They are the keys of
deep links, crew, audit and gates; any code path that would turn a
herdr id into a different string (or vice versa) is a defect. Panes from
the other adapters carry an origin prefix so routing is unambiguous:
tmux panes become ``tmux:%N``, zellij panes ``zellij:<id>``. A bare id
routes to the herdr child (back-compat); an id prefixed for a
multiplexer this composite does not include, a bare id when no herdr
child exists, or an explicit ``herdr:`` scheme is a :class:`MuxRefusal`
with an explainable reason — never a silent fallthrough. Spawned ids
come back the same way: a child that returns one of its own pane ids
(``create_tab``) gets its prefix added so the id stays routable; herdr
results pass through untouched.

Unreachable rule (f7f S1(3)): a child whose CLI fails at listing time
never sinks the aggregate — the other children still list, and the
failed child serves its last-known panes with
``status="unreachable"`` (the watcher already reports pane state through
``AgentInfo.status`` and no consumer serializes AgentInfo wholesale, so
flagging status is additive). With no last-known listing the child lists
zero panes; the composite never fakes ids. The cache is guarded by a
lock because the watcher thread and HTTP request threads share one
instance (the children themselves already tolerate concurrent calls).

Capabilities merge rule (pinned by tests, per the evidence discipline of
:mod:`herdr_brain.mux.capabilities`): a cell is declared only when at
least one child declares it — the composite invents no evidence — and
the cell's evidence is the merge of every supporting child's evidence,
each named (``"tmux: ..."``). Every supported cell names its origins in
``facts`` under ``origins:<cell>``, so a surface the composite can only
route to SOME origins says so. ``topology_latency`` is ``push`` only
when every child's fact is exactly ``push``, ``bounded:<max ms>`` when
at least one child declares a parseable bound and none declares an
unknown fact, and ``""`` (unknown) otherwise — the composite never
fabricates a latency nobody declared. ``spaces`` is ``many`` when
any child has many. ``probe()`` reports each child's outcome honestly:
reachable means at least one child answered, and the detail names every
child.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Dict, List, Mapping, Optional, Tuple

from ..herdr import AgentInfo, HerdrError, WorkspaceInfo, pick_active
from .capabilities import Capabilities, MuxProbe
from .protocol import MuxAdapter
from .refusal import MuxRefusal

#: Pane-id origin prefixes, keyed by multiplexer name. herdr is
#: deliberately absent: herdr pane ids are bare (they are the keys the
#: rest of the brain already uses), so an explicit ``herdr:`` scheme is
#: refused instead of silently rewritten.
ORIGIN_PREFIXES = {"tmux": "tmux:", "zellij": "zellij:"}

#: Status served for the panes of a child that no longer answers: its
#: last-known listing, honestly flagged stale.
UNREACHABLE_STATUS = "unreachable"


class CompositeMux:
    """Several multiplexer adapters behind one pane surface."""

    def __init__(self, children: Mapping[str, MuxAdapter]) -> None:
        unknown = set(children) - set(ORIGIN_PREFIXES) - {"herdr"}
        if unknown:
            raise ValueError(
                f"unknown multiplexer {', '.join(sorted(unknown))}: "
                f"expected one of herdr, tmux, zellij"
            )
        self._children: Dict[str, MuxAdapter] = dict(children)
        self._last_known: Dict[str, List[AgentInfo]] = {}
        self._cache_lock = threading.Lock()

    @property
    def names(self) -> Tuple[str, ...]:
        """The configured child multiplexers, in config order."""
        return tuple(self._children)

    # -- routing -------------------------------------------------------------

    def _route(self, pane_id: str, capability: str) -> Tuple[str, MuxAdapter, str]:
        """Resolves a composite pane id to (child name, child, local id)."""
        if pane_id.startswith("herdr:"):
            raise MuxRefusal(
                "refused", capability,
                f"herdr pane ids are never prefixed: use "
                f"{pane_id[len('herdr:'):]!r} directly (the composite never "
                "rewrites a herdr id)",
            )
        for name, prefix in ORIGIN_PREFIXES.items():
            if pane_id.startswith(prefix):
                child = self._children.get(name)
                if child is None:
                    raise MuxRefusal(
                        "refused", capability,
                        f"pane id {pane_id!r} names multiplexer {name!r}, "
                        "which is not part of this composite "
                        f"({', '.join(self._children)})",
                    )
                return name, child, pane_id[len(prefix):]
        child = self._children.get("herdr")
        if child is None:
            raise MuxRefusal(
                "refused", capability,
                f"bare pane id {pane_id!r} addresses herdr, which is not "
                f"part of this composite ({', '.join(self._children)})",
            )
        return "herdr", child, pane_id

    @staticmethod
    def _prefixed(agent: AgentInfo, prefix: str) -> AgentInfo:
        # An empty prefix returns the AgentInfo untouched: herdr ids and
        # objects pass through verbatim.
        if not prefix:
            return agent
        return replace(agent, pane_id=prefix + agent.pane_id)

    def _last_known_panes(self, name: str) -> List[AgentInfo]:
        prefix = ORIGIN_PREFIXES.get(name, "")
        with self._cache_lock:
            known = list(self._last_known.get(name, ()))
        if not known:
            return []
        return [
            replace(agent, pane_id=prefix + agent.pane_id,
                    status=UNREACHABLE_STATUS)
            for agent in known
        ]

    def _gather(self) -> List[Tuple[List[AgentInfo], bool]]:
        """One listing pass: (panes in composite form, child failed)."""
        gathered: List[Tuple[List[AgentInfo], bool]] = []
        for name, child in self._children.items():
            prefix = ORIGIN_PREFIXES.get(name, "")
            try:
                listed = child.list_agents()
            except HerdrError:
                # A down child never sinks the aggregate: serve its
                # last-known panes flagged unreachable (or none).
                gathered.append((self._last_known_panes(name), True))
                continue
            with self._cache_lock:
                self._last_known[name] = list(listed)
            gathered.append(([self._prefixed(a, prefix) for a in listed], False))
        return gathered

    # -- listing ---------------------------------------------------------------

    def list_agents(self) -> List[AgentInfo]:
        return [
            agent for panes, _failed in self._gather() for agent in panes
        ]

    def active_agent(self) -> Optional[AgentInfo]:
        # Only live listings compete: a pane of a down child is never
        # "active" fiction. pick_active is the shared selector.
        live = [agent for panes, failed in self._gather() if not failed
                for agent in panes]
        return pick_active(live)

    def list_workspaces(self) -> List[WorkspaceInfo]:
        # herdr-only surface (parity-f7c S1b), deliberately NOT on the
        # port protocol: delegate to the herdr child when this composite
        # has one; refuse exactly like the alternates otherwise.
        child = self._children.get("herdr")
        if child is None:
            raise MuxRefusal(
                "unsupported", "list_workspaces",
                "workspaces are herdr-only; this composite has no herdr "
                f"child ({', '.join(self._children)})",
            )
        return child.list_workspaces()

    # -- pane-scoped surfaces (pure routing) -------------------------------------

    def read_screen(
        self,
        pane_id: str,
        n_lines: Optional[int] = None,
        source: str = "visible",
        as_ansi: bool = False,
    ) -> str:
        capability = "scrollback" if source != "visible" else "ansi_screen"
        _name, child, local_id = self._route(pane_id, capability)
        return child.read_screen(
            local_id, n_lines=n_lines, source=source, as_ansi=as_ansi
        )

    def send_keys(self, pane_id: str, keys: List[str]) -> dict:
        _name, child, local_id = self._route(pane_id, "keys")
        return child.send_keys(local_id, keys)

    def pane_run(self, pane_id: str, command: str) -> None:
        _name, child, local_id = self._route(pane_id, "keys")
        return child.pane_run(local_id, command)

    def send_prompt(self, pane_id: str, text: str, timeout_ms: Optional[int] = None) -> dict:
        _name, child, local_id = self._route(pane_id, "prompt_submit")
        return child.send_prompt(local_id, text, timeout_ms=timeout_ms)

    def send_text(self, pane_id: str, text: str) -> None:
        _name, child, local_id = self._route(pane_id, "send_text")
        return child.send_text(local_id, text)

    def agent_wait(self, pane_id: str, until: str = "idle", timeout_ms: int = 15_000) -> None:
        _name, child, local_id = self._route(pane_id, "agent_detection")
        return child.agent_wait(local_id, until=until, timeout_ms=timeout_ms)

    # -- spawn surfaces -----------------------------------------------------------

    def create_tab(
        self,
        label: str,
        cwd: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> dict:
        herdr = self._children.get("herdr")
        if workspace_id:
            # Workspace targeting is herdr-only (parity-f7c S1b): it
            # rides to the herdr child or refuses before any spawn.
            if herdr is None:
                raise MuxRefusal(
                    "unsupported", "tabs",
                    "workspace targeting is herdr-only and this composite "
                    f"has no herdr child ({', '.join(self._children)})",
                )
            return herdr.create_tab(label, cwd=cwd, workspace_id=workspace_id)
        # Agents live in herdr (tmux/zellij panes are shells and agent
        # detection stays out of the alternates): every spawn prefers
        # the herdr child when the composite has one, whatever the
        # config order. Only a herdr-less composite falls back to the
        # first child that claims tabs.
        if herdr is not None:
            return herdr.create_tab(label, cwd=cwd)
        for name, child in self._children.items():
            if child.capabilities().supports("tabs"):
                result = child.create_tab(label, cwd=cwd)
                prefix = ORIGIN_PREFIXES.get(name, "")
                if prefix and isinstance(result, dict) and result.get("pane_id"):
                    # The spawned pane id must come back routable.
                    result = {**result, "pane_id": prefix + str(result["pane_id"])}
                return result
        # No child claims tabs: let the first child refuse with its own
        # refusal — the composite is a router, not a reimplementation.
        first = next(iter(self._children.values()))
        return first.create_tab(label, cwd=cwd)

    def start_agent(
        self,
        name: str,
        kind: str,
        pane_id: str,
        timeout_ms: Optional[int] = None,
    ) -> str:
        child_name, child, local_id = self._route(pane_id, "agent_start")
        result = child.start_agent(name, kind, local_id, timeout_ms=timeout_ms)
        prefix = ORIGIN_PREFIXES.get(child_name, "")
        # In practice only herdr answers here (the alternates refuse
        # agent_start); a child that ever returns its own id gets it
        # prefixed so it stays routable.
        return prefix + result if prefix else result

    # -- probe and capabilities ------------------------------------------------------

    def probe(self) -> MuxProbe:
        outcomes = [(name, child.probe()) for name, child in self._children.items()]
        reachable = any(outcome.reachable for _name, outcome in outcomes)
        detail = "; ".join(
            f"{name}: {'yes' if outcome.reachable else 'no'} ({outcome.detail})"
            for name, outcome in outcomes
        )
        versions = [
            f"{name}={outcome.version}"
            for name, outcome in outcomes
            if outcome.version
        ]
        return MuxProbe(reachable, detail, ", ".join(versions))

    def capabilities(self) -> Capabilities:
        per_child = [
            (name, child.capabilities()) for name, child in self._children.items()
        ]
        origins: Dict[str, List[str]] = {}
        for name, caps in per_child:
            for cell in caps.supported:
                origins.setdefault(cell, []).append(name)
        evidence = {
            cell: "; ".join(
                f"{name}: {caps.evidence[cell]}"
                for name, caps in per_child
                if cell in caps.supported
            )
            for cell in origins
        }
        facts = {
            "children": ", ".join(self._children),
            # The composite spans at least as many spaces as its widest
            # child; it never narrows a "many" back to "one".
            "spaces": (
                "many"
                if any(caps.fact("spaces") == "many" for _n, caps in per_child)
                else "one"
            ),
            "topology_latency": self._merged_topology_latency(per_child),
        }
        for cell, names in origins.items():
            facts[f"origins:{cell}"] = ", ".join(names)
        return Capabilities(
            supported=frozenset(origins), evidence=evidence, facts=facts
        )

    @staticmethod
    def _merged_topology_latency(per_child) -> str:
        # Honest merge, never a fabricated value: "push" only when every
        # child's fact is exactly "push"; "bounded:<max>" when at least
        # one bound parsed and every child's fact is push or a parseable
        # bound; any other (or absent) fact makes the aggregate unknown.
        if not per_child:
            return ""
        bounds = []
        for _name, caps in per_child:
            value = caps.fact("topology_latency")
            if value == "push":
                continue  # a pushing child never bounds the aggregate
            if value.startswith("bounded:"):
                try:
                    bounds.append(int(value[len("bounded:"):]))
                    continue
                except ValueError:
                    pass
            return ""  # unknown fact: never guess a latency
        return f"bounded:{max(bounds)}" if bounds else "push"
