"""One harness source of truth for the PWA session routes (W3-U3).

``GET /session/roots`` PUBLISHES the closed harness list and
``POST /session/new`` (and ``/session/resume``) VALIDATE against it.
Both must read the same seam (``session_launcher``): whatever the
sheet lists is launchable, and nothing else is — aliases included.
"""

from __future__ import annotations

import pytest

from herdr_brain import session_launcher
from herdr_brain.transcript_readers.registry import (
    HARNESS_ALIASES,
    default_registry,
)

from tests.test_session_routes import make_client


class TestSessionLauncherSeam:
    def test_closed_list_is_the_registry_canonical_names(self):
        assert list(session_launcher.launchable_harnesses()) == (
            default_registry().harnesses()
        )

    def test_opencode_is_listed(self):
        assert "opencode" in session_launcher.launchable_harnesses()

    def test_resolve_normalizes_case_and_whitespace(self):
        assert session_launcher.resolve_launch_harness("  OpenCode ") == "opencode"

    @pytest.mark.parametrize("raw", ["oa", "omp", "agy", "gemini", "", "  ", None])
    def test_resolve_refuses_aliases_and_unknowns(self, raw):
        assert session_launcher.resolve_launch_harness(raw) is None


class TestRoutesAgreeOnTheClosedList:
    def test_every_listed_harness_is_accepted_by_post(self, tmp_path, monkeypatch):
        client, tools = make_client(tmp_path, monkeypatch)
        listed = client.get("/session/roots").json()["harnesses"]
        assert "opencode" in listed
        root = str(tmp_path / "work")
        for name in listed:
            resp = client.post("/session/new", json={"harness": name, "cwd": root})
            assert resp.status_code == 200, (name, resp.text)
        assert [call["agent_kind"] for call in tools.calls] == listed

    @pytest.mark.parametrize("alias", sorted(HARNESS_ALIASES) + ["gemini", "bash"])
    def test_unlisted_names_are_refused_by_post(self, tmp_path, monkeypatch, alias):
        client, tools = make_client(tmp_path, monkeypatch)
        assert alias not in client.get("/session/roots").json()["harnesses"]
        resp = client.post(
            "/session/new", json={"harness": alias, "cwd": str(tmp_path / "work")}
        )
        assert resp.status_code == 422
        assert resp.json() == {"detail": "unknown_harness"}
        assert tools.calls == []

    def test_unlisted_names_are_refused_by_resume(self, tmp_path, monkeypatch):
        client, tools = make_client(tmp_path, monkeypatch)
        resp = client.post(
            "/session/resume", json={"harness": "oa", "session_id": "abc"}
        )
        assert resp.status_code == 422
        assert resp.json() == {"detail": "unknown_harness"}
        assert tools.resume_calls == []

    def test_both_routes_read_the_one_seam(self, tmp_path, monkeypatch):
        # A single patch of the seam moves BOTH routes: no second list.
        monkeypatch.setattr(
            session_launcher, "launchable_harnesses", lambda: ("claude", "zzz")
        )
        client, tools = make_client(tmp_path, monkeypatch)
        root = str(tmp_path / "work")
        assert client.get("/session/roots").json()["harnesses"] == ["claude", "zzz"]
        assert client.post(
            "/session/new", json={"harness": "zzz", "cwd": root}
        ).status_code == 200
        refused = client.post("/session/new", json={"harness": "opencode", "cwd": root})
        assert refused.status_code == 422
        assert refused.json() == {"detail": "unknown_harness"}
        assert [call["agent_kind"] for call in tools.calls] == ["zzz"]
