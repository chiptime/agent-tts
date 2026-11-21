"""Composite multiplexer: routing, listing, down-child isolation, probe/caps.

Covers f7f S1: ``HERDR_BRAIN_MUX=herdr,tmux,zellij`` lists panes of all
three children (herdr ids untouched, tmux/zellij ids origin-prefixed),
and a child that stops answering never sinks the aggregate — its
last-known panes are served flagged ``unreachable``, zero panes when no
last-known listing exists.

Safety: children are in-process fakes and the registry-integration cases
run real adapters over canned runners — no test ever spawns a real
tmux/zellij/herdr binary.
"""

from __future__ import annotations

import threading

import pytest

from herdr_brain.herdr import AgentInfo, HerdrError, WorkspaceInfo
from herdr_brain.mux import (
    CAPABILITY_NAMES,
    Capabilities,
    CompositeMux,
    MuxAdapter,
    MuxProbe,
    MuxRefusal,
)


def agent(pid: str, *, focused: bool = False, status: str = "idle",
          name: str = "opencode") -> AgentInfo:
    return AgentInfo(
        pane_id=pid,
        agent=name,
        status=status,
        session_kind="",
        session_value="",
        cwd="/repo",
        title=pid,
        focused=focused,
    )


def shell(pid: str, *, focused: bool = False) -> AgentInfo:
    # tmux/zellij panes are reported as plain shells with status unknown.
    return agent(pid, focused=focused, status="unknown", name="shell")


def caps_of(cells, latency: str = "push", spaces: str = "many") -> Capabilities:
    return Capabilities(
        supported=frozenset(cells),
        evidence={cell: f"evidence {cell}" for cell in cells},
        facts={"topology_latency": latency, "spaces": spaces},
    )


class FakeMux:
    """In-process child double: canned panes, recorded routed calls."""

    def __init__(self, agents=(), probe: MuxProbe = None, caps: Capabilities = None):
        self.agents = list(agents)
        self.probe_result = probe or MuxProbe(True, "fake answered")
        self.caps_result = caps or caps_of(["pane_grid"])
        self.raise_in: dict = {}
        self.calls: list = []
        self.screen_result = "screen body"
        self.start_result = "w9:p1"
        self.tab_result = {"tab_id": "t1", "pane_id": "p1"}

    def _hit(self, name, args, result=None):
        self.calls.append((name, args))
        failure = self.raise_in.get(name)
        if failure is not None:
            raise failure
        return result

    def capabilities(self):
        return self.caps_result

    def probe(self):
        return self.probe_result

    def list_agents(self):
        return self._hit("list_agents", (), list(self.agents))

    def active_agent(self):
        return self._hit("active_agent", ())

    def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
        return self._hit(
            "read_screen", (pane_id, n_lines, source, as_ansi), self.screen_result
        )

    def send_keys(self, pane_id, keys):
        return self._hit("send_keys", (pane_id, tuple(keys)), {"ok": True, "error": ""})

    def pane_run(self, pane_id, command):
        return self._hit("pane_run", (pane_id, command))

    def send_prompt(self, pane_id, text, timeout_ms=None):
        return self._hit(
            "send_prompt", (pane_id, text, timeout_ms),
            {"ok": True, "status": "done", "output": ""},
        )

    def send_text(self, pane_id, text):
        return self._hit("send_text", (pane_id, text))

    def create_tab(self, label, cwd=None, workspace_id=None):
        return self._hit("create_tab", (label, cwd, workspace_id), self.tab_result)

    def start_agent(self, name, kind, pane_id, timeout_ms=None):
        return self._hit("start_agent", (name, kind, pane_id, timeout_ms),
                         self.start_result)

    def agent_wait(self, pane_id, until="idle", timeout_ms=15_000):
        return self._hit("agent_wait", (pane_id, until, timeout_ms))

    def list_workspaces(self):
        return self._hit(
            "list_workspaces", (),
            [WorkspaceInfo(workspace_id="w1", label="Main", number=1, focused=True)],
        )

    def focus_workspace(self, workspace_id):
        return self._hit("focus_workspace", (workspace_id,), "")


def three_children() -> dict:
    return {
        "herdr": FakeMux(agents=[agent("w1:p1"), agent("w1:p2", focused=True)]),
        "tmux": FakeMux(agents=[shell("%3"), shell("%5")]),
        "zellij": FakeMux(agents=[shell("17")]),
    }


class TestListing:
    def test_lists_children_in_config_order_with_prefixed_ids(self):
        mux = CompositeMux(three_children())
        assert [a.pane_id for a in mux.list_agents()] == [
            "w1:p1", "w1:p2", "tmux:%3", "tmux:%5", "zellij:17",
        ]

    def test_herdr_agents_pass_through_untouched(self):
        # S1(2): herdr pane ids do not change — the very objects the
        # child produced, no copy, no rewrite.
        children = three_children()
        listed = CompositeMux(children).list_agents()
        assert listed[0] is children["herdr"].agents[0]
        assert listed[1] is children["herdr"].agents[1]
        assert listed[0].pane_id == "w1:p1"

    def test_prefixed_panes_keep_the_childs_other_fields(self):
        mux = CompositeMux(three_children())
        listed = mux.list_agents()
        tmux_pane = next(a for a in listed if a.pane_id == "tmux:%3")
        assert tmux_pane.agent == "shell"
        assert tmux_pane.cwd == "/repo"

    def test_active_agent_picks_the_focused_pane_across_children(self):
        # A focused tmux pane beats herdr's unfocused panes: "focused"
        # is the strongest signal, wherever it lives.
        children = {
            "herdr": FakeMux(agents=[agent("w1:p1")]),
            "tmux": FakeMux(agents=[shell("%3", focused=True)]),
        }
        active = CompositeMux(children).active_agent()
        assert active is not None and active.pane_id == "tmux:%3"

    def test_active_agent_skips_a_down_child(self):
        # A pane of a down child is never "active" fiction.
        children = three_children()
        children["tmux"].raise_in["list_agents"] = HerdrError("tmux down")
        active = CompositeMux(children).active_agent()
        assert active is not None and active.pane_id == "w1:p2"


class TestRouting:
    def test_bare_id_routes_to_herdr_unchanged(self):
        herdr, tmux = FakeMux(), FakeMux()
        mux = CompositeMux({"herdr": herdr, "tmux": tmux})
        mux.read_screen("w1:p9", 30, "recent", True)
        assert herdr.calls == [("read_screen", ("w1:p9", 30, "recent", True))]
        assert tmux.calls == []

    def test_tmux_prefix_routes_and_strips(self):
        herdr, tmux = FakeMux(), FakeMux()
        mux = CompositeMux({"herdr": herdr, "tmux": tmux})
        mux.read_screen("tmux:%3")
        assert tmux.calls == [("read_screen", ("%3", None, "visible", False))]
        assert herdr.calls == []

    def test_zellij_prefix_routes_and_strips(self):
        zellij = FakeMux()
        mux = CompositeMux({"herdr": FakeMux(), "zellij": zellij})
        mux.agent_wait("zellij:17", until="done", timeout_ms=5000)
        assert zellij.calls == [("agent_wait", ("17", "done", 5000))]

    def test_a_colon_form_that_is_no_alternate_prefix_is_a_herdr_bare_id(self):
        # PIN, not a fix: herdr pane ids legitimately contain colons
        # (workspace:pane forms like w11:p2), so ONLY the known alternate
        # prefixes (tmux:, zellij:) and the explicit herdr: scheme are
        # special — any other colon-form IS a herdr bare id by contract,
        # routed verbatim and unchanged. Refusing them as "unknown
        # prefix" would break every herdr reply. (Verifier finding,
        # adjudicated: the code is correct; the mission doc's
        # "unknown-prefix refuses" line read too literally.)
        herdr, tmux = FakeMux(), FakeMux()
        mux = CompositeMux({"herdr": herdr, "tmux": tmux})
        mux.send_prompt("other:abc", "x")
        mux.send_prompt("14:p2", "y")
        assert herdr.calls == [
            ("send_prompt", ("other:abc", "x", None)),
            ("send_prompt", ("14:p2", "y", None)),
        ]
        assert tmux.calls == []

    @pytest.mark.parametrize(
        "call",
        [
            lambda mux: mux.read_screen("tmux:%3"),
            lambda mux: mux.send_keys("tmux:%3", ["enter"]),
            lambda mux: mux.pane_run("tmux:%3", "make test"),
            lambda mux: mux.send_prompt("tmux:%3", "hi", 1000),
            lambda mux: mux.send_text("tmux:%3", "hi"),
            lambda mux: mux.agent_wait("tmux:%3"),
        ],
        ids=["read_screen", "send_keys", "pane_run", "send_prompt",
             "send_text", "agent_wait"],
    )
    def test_every_pane_scoped_surface_routes_by_prefix(self, call):
        # The composite is a router: exactly one child sees the call,
        # with the prefix stripped, and the caller sees the child's
        # result verbatim.
        herdr, tmux = FakeMux(), FakeMux()
        call(CompositeMux({"herdr": herdr, "tmux": tmux}))
        assert len(tmux.calls) == 1
        assert tmux.calls[0][1][0] == "%3"
        assert herdr.calls == []

    def test_child_results_pass_through_verbatim(self):
        sentinel = object()
        herdr = FakeMux()
        herdr.screen_result = sentinel
        assert CompositeMux({"herdr": herdr}).read_screen("w1:p1") is sentinel

    def test_child_refusal_propagates_unchanged(self):
        # REUSE the children's refusals: the same exception instance the
        # child raised reaches the caller, never a composite re-write.
        refusal = MuxRefusal("unsupported", "agent_start", "tmux cannot start agents")
        tmux = FakeMux()
        tmux.raise_in["start_agent"] = refusal
        mux = CompositeMux({"herdr": FakeMux(), "tmux": tmux})
        with pytest.raises(MuxRefusal) as excinfo:
            mux.start_agent("build", "opencode", "tmux:%9")
        assert excinfo.value is refusal


class TestUnroutableIds:
    def test_prefix_for_an_absent_multiplexer_refuses_explainably(self):
        mux = CompositeMux({"herdr": FakeMux(), "zellij": FakeMux()})
        with pytest.raises(MuxRefusal) as excinfo:
            mux.read_screen("tmux:%3")
        assert excinfo.value.reason == "refused"
        assert "tmux" in excinfo.value.detail
        assert "herdr" in excinfo.value.detail
        assert "zellij" in excinfo.value.detail

    def test_bare_id_without_a_herdr_child_refuses(self):
        mux = CompositeMux({"tmux": FakeMux(), "zellij": FakeMux()})
        with pytest.raises(MuxRefusal) as excinfo:
            mux.send_prompt("%3", "hi")
        assert excinfo.value.reason == "refused"
        assert "herdr" in excinfo.value.detail

    def test_herdr_scheme_prefix_is_refused(self):
        # herdr ids are never prefixed; the composite must not silently
        # rewrite one shape into the other.
        mux = CompositeMux({"herdr": FakeMux()})
        with pytest.raises(MuxRefusal, match="never prefixed"):
            mux.read_screen("herdr:w1:p2")


class TestDownChildIsolation:
    def test_down_child_serves_last_known_panes_as_unreachable(self):
        # S1(3): tmux does not answer -> herdr and zellij keep working,
        # tmux panes show unreachable.
        children = three_children()
        mux = CompositeMux(children)
        mux.list_agents()  # prime the last-known cache
        children["tmux"].raise_in["list_agents"] = HerdrError("tmux did not answer")
        served = mux.list_agents()
        assert [a.pane_id for a in served] == [
            "w1:p1", "w1:p2", "tmux:%3", "tmux:%5", "zellij:17",
        ]
        tmux_panes = [a for a in served if a.pane_id.startswith("tmux:")]
        assert all(a.status == "unreachable" for a in tmux_panes)
        # live children pass through verbatim, untouched by the failure
        assert served[0] is children["herdr"].agents[0]
        assert served[-1].pane_id == "zellij:17"
        assert served[-1].status == "unknown"

    def test_first_failure_with_no_cache_lists_zero_panes_for_that_child(self):
        # Never fake ids: a child with no last-known listing lists zero.
        children = three_children()
        children["tmux"].raise_in["list_agents"] = HerdrError("tmux did not answer")
        mux = CompositeMux(children)
        assert [a.pane_id for a in mux.list_agents()] == [
            "w1:p1", "w1:p2", "zellij:17",
        ]

    def test_cache_tracks_the_latest_successful_listing(self):
        children = {"tmux": FakeMux(agents=[shell("%1")])}
        mux = CompositeMux(children)
        mux.list_agents()
        children["tmux"].agents = [shell("%2")]
        mux.list_agents()
        children["tmux"].raise_in["list_agents"] = HerdrError("down")
        served = mux.list_agents()
        assert [a.pane_id for a in served] == ["tmux:%2"]
        assert served[0].status == "unreachable"

    def test_concurrent_listing_never_corrupts_the_cache(self):
        # watcher.py and HTTP threads share one instance: the cache is
        # lock-guarded and no listing may raise or tear.
        children = {"herdr": FakeMux(agents=[agent("w1:p1")]),
                    "tmux": FakeMux(agents=[shell("%3")])}
        mux = CompositeMux(children)
        errors: list = []

        def churn():
            try:
                for index in range(200):
                    children["tmux"].raise_in["list_agents"] = (
                        HerdrError("down") if index % 2 else None
                    )
                    listed = mux.list_agents()
                    assert listed, "the herdr child always lists"
            except Exception as exc:  # noqa: BLE001 — recorded as failure
                errors.append(exc)

        threads = [threading.Thread(target=churn) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []


class TestProbe:
    def test_reachable_when_any_child_answers(self):
        children = {
            "herdr": FakeMux(probe=MuxProbe(True, "herdr CLI answered --help")),
            "tmux": FakeMux(probe=MuxProbe(False, "tmux did not answer: boom")),
        }
        probe = CompositeMux(children).probe()
        assert probe.reachable is True
        for name in ("herdr", "tmux"):
            assert name in probe.detail

    def test_all_children_down_is_not_reachable(self):
        children = {
            "tmux": FakeMux(probe=MuxProbe(False, "tmux did not answer: boom")),
            "zellij": FakeMux(probe=MuxProbe(False, "zellij did not answer: boom")),
        }
        probe = CompositeMux(children).probe()
        assert probe.reachable is False
        assert "tmux" in probe.detail and "zellij" in probe.detail

    def test_version_names_each_child_honestly(self):
        children = {
            "herdr": FakeMux(probe=MuxProbe(True, "herdr CLI answered --help")),
            "tmux": FakeMux(probe=MuxProbe(True, "tmux CLI answered -V", "3.6b")),
        }
        probe = CompositeMux(children).probe()
        assert probe.version == "tmux=3.6b"

    def test_probe_never_enlarges_capability_cells(self):
        mux = CompositeMux(three_children())
        before = mux.capabilities()
        probe = mux.probe()
        assert mux.capabilities() == before  # a probe adds facts, never cells
        assert not hasattr(probe, "supported")


class TestCapabilitiesMerge:
    def test_union_of_cells_carries_merged_named_evidence(self):
        children = {
            "herdr": FakeMux(caps=caps_of(CAPABILITY_NAMES)),
            "tmux": FakeMux(caps=caps_of(["pane_grid", "ansi_screen", "tabs"])),
        }
        caps = CompositeMux(children).capabilities()
        assert caps.supported == frozenset(CAPABILITY_NAMES)
        assert caps.evidence["pane_grid"] == (
            "herdr: evidence pane_grid; tmux: evidence pane_grid"
        )
        assert caps.evidence["agent_start"] == "herdr: evidence agent_start"

    def test_partial_origins_are_named_in_facts(self):
        # A surface the composite can only route to SOME origins says so.
        children = {
            "herdr": FakeMux(caps=caps_of(CAPABILITY_NAMES)),
            "tmux": FakeMux(caps=caps_of(["pane_grid"])),
        }
        caps = CompositeMux(children).capabilities()
        assert caps.fact("origins:agent_start") == "herdr"
        assert caps.fact("origins:pane_grid") == "herdr, tmux"
        assert caps.fact("children") == "herdr, tmux"

    def test_topology_latency_is_push_only_when_every_child_pushes(self):
        mixed = CompositeMux({
            "herdr": FakeMux(caps=caps_of(["pane_grid"], latency="push")),
            "tmux": FakeMux(caps=caps_of(["pane_grid"], latency="bounded:1000")),
            "zellij": FakeMux(caps=caps_of(["pane_grid"], latency="bounded:12000")),
        }).capabilities()
        assert mixed.fact("topology_latency") == "bounded:12000"
        all_push = CompositeMux({
            "herdr": FakeMux(caps=caps_of(["pane_grid"], latency="push")),
        }).capabilities()
        assert all_push.fact("topology_latency") == "push"

    def test_topology_latency_is_unknown_when_a_child_declares_none(self):
        # Never fabricate: a child without a topology fact makes the
        # aggregate unknown, not "push".
        caps = CompositeMux({
            "herdr": FakeMux(caps=caps_of(["pane_grid"], latency="push")),
            "tmux": FakeMux(caps=caps_of(["pane_grid"], latency="")),
        }).capabilities()
        assert caps.fact("topology_latency") == ""

    def test_topology_latency_treats_unparseable_bounds_as_unknown(self):
        caps = CompositeMux({
            "tmux": FakeMux(caps=caps_of(["pane_grid"], latency="bounded:soon")),
            "zellij": FakeMux(caps=caps_of(["pane_grid"], latency="bounded:12000")),
        }).capabilities()
        assert caps.fact("topology_latency") == ""

    def test_spaces_is_many_when_any_child_has_many(self):
        caps = CompositeMux({
            "zellij": FakeMux(caps=caps_of(["pane_grid"], spaces="one")),
        }).capabilities()
        assert caps.fact("spaces") == "one"
        caps = CompositeMux({
            "herdr": FakeMux(caps=caps_of(["pane_grid"], spaces="many")),
            "zellij": FakeMux(caps=caps_of(["pane_grid"], spaces="one")),
        }).capabilities()
        assert caps.fact("spaces") == "many"


class TestSpawnSurfaces:
    def test_create_tab_routes_to_the_first_tabs_supporter(self):
        herdr = FakeMux(caps=caps_of(["pane_grid", "tabs"]))
        tmux = FakeMux(caps=caps_of(["pane_grid", "tabs"]))
        mux = CompositeMux({"herdr": herdr, "tmux": tmux})
        result = mux.create_tab("Build", "/repo")
        assert herdr.calls == [("create_tab", ("Build", "/repo", None))]
        assert tmux.calls == []
        assert result == {"tab_id": "t1", "pane_id": "p1"}

    def test_create_tab_prefers_herdr_even_when_tmux_comes_first(self):
        # Agents live in herdr (tmux/zellij panes are shells): every
        # spawn prefers the herdr child whatever the config order — a
        # tmux-first composite must NOT receive agent tab spawns.
        tmux = FakeMux(caps=caps_of(["pane_grid", "tabs"]))
        herdr = FakeMux(caps=caps_of(["pane_grid", "tabs"]))
        mux = CompositeMux({"tmux": tmux, "herdr": herdr})
        result = mux.create_tab("Build", "/repo")
        assert herdr.calls == [("create_tab", ("Build", "/repo", None))]
        assert tmux.calls == []
        assert result == herdr.tab_result

    def test_create_tab_workspace_targeting_is_herdr_only(self):
        tmux = FakeMux(caps=caps_of(["pane_grid", "tabs"]))
        mux = CompositeMux({"tmux": tmux})
        with pytest.raises(MuxRefusal) as excinfo:
            mux.create_tab("Build", "/repo", workspace_id="w2")
        assert excinfo.value.reason == "unsupported"
        assert "herdr" in excinfo.value.detail
        assert tmux.calls == []

    def test_create_tab_workspace_id_rides_to_the_herdr_child(self):
        herdr = FakeMux(caps=caps_of(["pane_grid", "tabs"]))
        mux = CompositeMux({"herdr": herdr})
        mux.create_tab("Build", "/repo", workspace_id="w2")
        assert herdr.calls == [("create_tab", ("Build", "/repo", "w2"))]

    def test_create_tab_without_herdr_prefixes_the_returned_pane_id(self):
        # A spawned tmux pane must come back routable: tmux:%9, not a
        # bare %9 that would route to herdr.
        tmux = FakeMux(caps=caps_of(["pane_grid", "tabs"]))
        tmux.tab_result = {"tab_id": "@1", "pane_id": "%9"}
        mux = CompositeMux({"tmux": tmux})
        assert mux.create_tab("Build") == {"tab_id": "@1", "pane_id": "tmux:%9"}

    def test_create_tab_without_any_supporter_reuses_the_first_childs_refusal(self):
        # No child claims tabs: the composite delegates to the first
        # child and surfaces ITS refusal instance unchanged (a real
        # zellij child refuses exactly this way).
        refusal = MuxRefusal("unsupported", "tabs", "outside the verified surface")
        zellij = FakeMux(caps=caps_of(["pane_grid"]))
        zellij.raise_in["create_tab"] = refusal
        mux = CompositeMux({"zellij": zellij})
        with pytest.raises(MuxRefusal) as excinfo:
            mux.create_tab("Build")
        assert excinfo.value is refusal
        assert zellij.calls == [("create_tab", ("Build", None, None))]

    def test_start_agent_routes_by_pane_id(self):
        herdr = FakeMux()
        mux = CompositeMux({"herdr": herdr, "tmux": FakeMux()})
        result = mux.start_agent("build", "opencode", "w2:p1", 5000)
        assert herdr.calls == [("start_agent", ("build", "opencode", "w2:p1", 5000))]
        assert result == herdr.start_result


class TestContract:
    def test_composite_satisfies_the_port_protocol(self):
        assert isinstance(CompositeMux(three_children()), MuxAdapter)

    def test_list_workspaces_delegates_to_the_herdr_child(self):
        herdr = FakeMux()
        mux = CompositeMux({"herdr": herdr})
        result = mux.list_workspaces()
        assert herdr.calls == [("list_workspaces", ())]
        assert result == [
            WorkspaceInfo(workspace_id="w1", label="Main", number=1, focused=True)
        ]

    def test_list_workspaces_refuses_without_a_herdr_child(self):
        mux = CompositeMux({"tmux": FakeMux()})
        with pytest.raises(MuxRefusal) as excinfo:
            mux.list_workspaces()
        assert excinfo.value.reason == "unsupported"

    def test_unknown_child_name_fails_loudly(self):
        with pytest.raises(ValueError, match="unknown multiplexer"):
            CompositeMux({"screen": FakeMux()})


class TestFocusWorkspace:
    """f7f S4: the herdr-only workspace focus, delegated like
    list_workspaces — never on the port protocol, refused with the
    same vocabulary when no herdr child exists."""

    def test_focus_delegates_to_the_herdr_child_surface(self):
        herdr = FakeMux()
        mux = CompositeMux({"herdr": herdr, "tmux": FakeMux()})
        mux.focus_workspace("w2")
        assert herdr.calls == [("focus_workspace", ("w2",))]

    def test_focus_reaches_the_client_behind_the_herdr_child(self):
        # Production shape: the herdr child is a HerdrMuxAdapter whose
        # adapter class carries no focus delegation yet — the call must
        # reach the CLIENT behind it (the server's pane-list seam
        # precedent: an injected client double IS the client).
        from herdr_brain.mux import HerdrMuxAdapter

        class FocusClient:
            def __init__(self):
                self.calls = []

            def focus_workspace(self, workspace_id):
                self.calls.append(workspace_id)
                return ""

        client = FocusClient()
        mux = CompositeMux({"herdr": HerdrMuxAdapter(client), "tmux": FakeMux()})
        mux.focus_workspace("w3")
        assert client.calls == ["w3"]

    def test_focus_refuses_without_a_herdr_child(self):
        mux = CompositeMux({"tmux": FakeMux(), "zellij": FakeMux()})
        with pytest.raises(MuxRefusal) as excinfo:
            mux.focus_workspace("w1")
        assert excinfo.value.reason == "unsupported"
        assert excinfo.value.capability == "focus_workspace"
        assert "herdr-only" in excinfo.value.detail

    def test_focus_refuses_when_the_herdr_child_has_no_surface(self):
        # A herdr child with neither a focus surface nor a client
        # behind it refuses unsupported instead of AttributeError.
        class BareChild:
            pass

        mux = CompositeMux({"herdr": BareChild()})
        with pytest.raises(MuxRefusal) as excinfo:
            mux.focus_workspace("w1")
        assert excinfo.value.reason == "unsupported"
        assert "herdr child" in excinfo.value.detail
