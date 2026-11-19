"""Model per pane (parity F7a S3).

brain never imports agent_tts: the model comes from the engine CLI
``agent-tts last-model`` run with the engine python (same seam as the STT
engine). Unknown harnesses, missing sessions and any CLI failure are an
honest ``None``; results are cached per (harness, session).
"""
from __future__ import annotations

import subprocess

import pytest

from herdr_brain.herdr import AgentInfo
from herdr_brain.pane_model import PaneModelResolver
from herdr_brain.tools import BrainTools

from tests.conftest import SETTINGS_KWARGS


class FakeRunner:
    def __init__(self, stdout="claude-opus-5-5\n", returncode=0, exc=None):
        self.calls = []
        self.stdout = stdout
        self.returncode = returncode
        self.exc = exc

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        if self.exc is not None:
            raise self.exc
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def resolver(runner, clock=None, enabled=True):
    return PaneModelResolver(
        python_resolver=lambda: "/venv/bin/python",
        runner=runner,
        clock=clock or Clock(),
        background=False,
        enabled=enabled,
    )


def test_runs_the_engine_last_model_cli_and_returns_its_first_line():
    runner = FakeRunner(stdout="claude-opus-5-5\nnoise\n")
    assert resolver(runner).model("claude", "abc-123") == "claude-opus-5-5"
    assert runner.calls == [[
        "/venv/bin/python", "-m", "agent_tts.cli", "last-model",
        "--agent", "claude", "--session-id", "abc-123",
    ]]


@pytest.mark.parametrize("alias,canonical", [("oa", "opencode"), ("agy", "antigravity")])
def test_harness_aliases_resolve_to_the_engine_name(alias, canonical):
    runner = FakeRunner(stdout="m\n")
    assert resolver(runner).model(alias, "s1") == "m"
    assert runner.calls[0][5] == canonical


@pytest.mark.parametrize("agent", ["pi", "grok", "kiro", "muse", "cursor", "hermes", "", None])
def test_harness_without_extractor_is_none_without_running_anything(agent):
    runner = FakeRunner()
    assert resolver(runner).model(agent, "s1") is None
    assert runner.calls == []


def test_missing_session_is_none_without_running_anything():
    runner = FakeRunner()
    assert resolver(runner).model("claude", "") is None
    assert runner.calls == []


@pytest.mark.parametrize("runner", [
    FakeRunner(returncode=1, stdout=""),
    FakeRunner(stdout="   \n"),
    FakeRunner(exc=subprocess.TimeoutExpired(["x"], 5)),
    FakeRunner(exc=OSError("no such file")),
])
def test_cli_failures_are_none(runner):
    assert resolver(runner).model("codex", "s1") is None


def test_unavailable_engine_python_is_none():
    runner = FakeRunner()
    res = PaneModelResolver(
        python_resolver=lambda: None, runner=runner, clock=Clock(), background=False,
        enabled=True,
    )
    assert res.model("claude", "s1") is None
    assert runner.calls == []


def test_results_are_cached_until_the_ttl_expires():
    runner = FakeRunner(stdout="m1\n")
    clock = Clock()
    res = resolver(runner, clock)
    assert res.model("claude", "s1") == "m1"
    runner.stdout = "m2\n"
    clock.now += 10
    assert res.model("claude", "s1") == "m1"
    assert len(runner.calls) == 1
    clock.now += res.ttl_s
    assert res.model("claude", "s1") == "m2"
    assert len(runner.calls) == 2


def test_disabled_resolver_never_runs():
    runner = FakeRunner()
    assert resolver(runner, enabled=False).model("claude", "s1") is None
    assert runner.calls == []


def test_off_env_disables_the_default_resolver(monkeypatch):
    monkeypatch.setenv("HERDR_BRAIN_PANE_MODEL", "off")
    assert PaneModelResolver().enabled is False


def test_background_mode_answers_cached_then_refreshes_without_blocking():
    runner = FakeRunner(stdout="m1\n")
    spawned = []
    res = PaneModelResolver(
        python_resolver=lambda: "/py", runner=runner, clock=Clock(),
        background=True, spawn=spawned.append, enabled=True,
    )
    assert res.model("claude", "s1") is None  # nothing known yet: never blocks
    assert len(spawned) == 1
    assert res.model("claude", "s1") is None  # one refresh in flight, not two
    assert len(spawned) == 1
    spawned[0]()
    assert res.model("claude", "s1") == "m1"


def test_background_spawn_failure_answers_none_and_does_not_wedge_the_key():
    runner = FakeRunner(stdout="m1\n")
    attempts = []

    def flaky_spawn(job):
        attempts.append(job)
        if len(attempts) == 1:
            raise RuntimeError("can't start new thread")

    res = PaneModelResolver(
        python_resolver=lambda: "/py", runner=runner, clock=Clock(),
        background=True, spawn=flaky_spawn, enabled=True,
    )
    assert res.model("claude", "s1") is None  # spawn raised: honest None, no raise
    assert res.model("claude", "s1") is None  # key not stuck inflight: retries spawn
    assert len(attempts) == 2
    attempts[1]()
    assert res.model("claude", "s1") == "m1"


def test_raising_clock_keeps_the_never_raise_promise_in_background_mode():
    runner = FakeRunner(stdout="m1\n")
    spawned = []

    def broken_clock():
        raise RuntimeError("clock unavailable")

    res = PaneModelResolver(
        python_resolver=lambda: "/py", runner=runner, clock=broken_clock,
        background=True, spawn=spawned.append, enabled=True,
    )
    assert res.model("claude", "s1") is None
    spawned[0]()  # _refresh survives the raising clock and clears inflight
    assert res.model("claude", "s1") == "m1"  # cached answer, still no raise


def test_raising_clock_keeps_the_never_raise_promise_in_foreground_mode():
    runner = FakeRunner(stdout="m1\n")

    def broken_clock():
        raise RuntimeError("clock unavailable")

    res = PaneModelResolver(
        python_resolver=lambda: "/py", runner=runner, clock=broken_clock,
        background=False, enabled=True,
    )
    assert res.model("claude", "s1") == "m1"  # lookup ran; broken clock is not a raise


# ── wiring: /view status and /herd entries carry the model ──────────────────


class FixedModels:
    def __init__(self, models):
        self.models = models

    def model(self, agent, session_id):
        return self.models.get((agent, session_id))


CLAUDE = AgentInfo(
    pane_id="w1:p1", agent="claude", status="idle", session_kind="id",
    session_value="abc", cwd="/repo", title="Claude", focused=True,
)
PI = AgentInfo(
    pane_id="w1:p2", agent="pi", status="idle", session_kind="id",
    session_value="def", cwd="/repo", title="Pi", focused=False,
)


def make_tools(make_stub, settings, models):
    stub = make_stub(agents=[CLAUDE, PI])
    return BrainTools(settings, herdr=stub, pane_model=FixedModels(models))


def test_view_status_carries_the_pane_model(make_stub, settings):
    tools = make_tools(make_stub, settings, {("claude", "abc"): "claude-opus-5-5"})
    assert tools.agent_view("w1:p1")["status"]["model"] == "claude-opus-5-5"
    assert tools.agent_view("w1:p2")["status"]["model"] is None


def test_state_carries_the_active_pane_model(make_stub, settings):
    tools = make_tools(make_stub, settings, {("claude", "abc"): "claude-opus-5-5"})
    assert tools.status_payload()["model"] == "claude-opus-5-5"


def test_herd_entries_carry_the_model(make_stub, settings):
    tools = make_tools(make_stub, settings, {("claude", "abc"): "claude-opus-5-5"})
    by_pane = {entry["pane_id"]: entry for entry in tools.herd()}
    assert by_pane["w1:p1"]["model"] == "claude-opus-5-5"
    assert by_pane["w1:p2"]["model"] is None


def test_inactive_status_has_a_null_model(make_stub, settings):
    stub = make_stub(agents=[])
    tools = BrainTools(settings, herdr=stub, pane_model=FixedModels({}))
    assert tools.status_payload()["model"] is None


# ── parity-f7d S1: the context window ─────────────────────────────────
#
# The limit comes from the harness's OWN local catalog (opencode's
# models.json, pi's models-store.json, omp's models.db — no network,
# no bundled table); the used tokens come from the engine's read-only
# ``cache-report`` probe of the same session. No catalog ⇒ no context:
# a window is never guessed.


import json as _json
import sqlite3

from herdr_brain import pane_model as pane_model_module


class ContextRunner:
    """Answers the two engine CLI probes: ``last-model`` prints the
    model, ``cache-report --json`` prints one measured turn."""

    def __init__(self, model="glm-5.3", turn=None):
        self.calls = []
        self.model = model
        self.turn = turn if turn is not None else {
            "timestamp": "2026-11-19T00:00:00Z",
            "input_tokens": 100,
            "cached_tokens": 1000,
            "cache_write_tokens": 10,
            "hit": True,
        }

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        if "last-model" in argv:
            return subprocess.CompletedProcess(argv, 0, self.model + "\n", "")
        body = {
            "agent": "opencode", "session_id": "s", "measurable": True,
            "turns": [self.turn], "summary": {},
        }
        return subprocess.CompletedProcess(
            argv, 0, _json.dumps(body), ""
        )


class BrokenReportRunner(ContextRunner):
    def __call__(self, argv, timeout):
        if "last-model" not in argv:
            return subprocess.CompletedProcess(argv, 1, "", "boom")
        return super().__call__(argv, timeout)


def context_resolver(runner, catalog=None, clock=None):
    return pane_model_module.PaneModelResolver(
        python_resolver=lambda: "/venv/bin/python",
        runner=runner,
        clock=clock or Clock(),
        background=False,
        enabled=True,
        opencode_catalog=catalog,
    )


def write_opencode_catalog(path, providers):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(providers), encoding="utf-8")


def write_pi_store(path, providers):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(providers), encoding="utf-8")


def write_omp_db(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("create table model_cache (provider_id text, models text)")
    conn.executemany(
        "insert into model_cache values (?, ?)",
        [(pid, _json.dumps(models)) for pid, models in rows],
    )
    conn.commit()
    conn.close()


class TestCatalogReaders:
    def test_opencode_catalog_names_the_limit(self, tmp_path):
        catalog = tmp_path / "models.json"
        write_opencode_catalog(catalog, {
            "zai": {"models": {"glm-5.3": {"limit": {"context": 200000}}}},
            "anthropic": {"models": {"claude-fable-5": {"limit": {"context": 1000000}}}},
        })

        assert pane_model_module.opencode_models_window(
            str(catalog), "glm-5.3"
        ) == 200000
        # A slashed id names its provider; the bare id is the key.
        assert pane_model_module.opencode_models_window(
            str(catalog), "anthropic/claude-fable-5"
        ) == 1000000

    def test_a_model_the_catalog_does_not_name_is_none(self, tmp_path):
        catalog = tmp_path / "models.json"
        write_opencode_catalog(catalog, {"zai": {"models": {}}})

        assert pane_model_module.opencode_models_window(str(catalog), "x") is None

    def test_providers_that_disagree_leave_the_window_unknown(self, tmp_path):
        catalog = tmp_path / "models.json"
        write_opencode_catalog(catalog, {
            "zai": {"models": {"glm-5.3": {"limit": {"context": 200000}}}},
            "openrouter": {"models": {"glm-5.3": {"limit": {"context": 400000}}}},
        })

        assert pane_model_module.opencode_models_window(
            str(catalog), "glm-5.3"
        ) is None

    def test_a_missing_or_oversized_catalog_is_none(self, tmp_path, monkeypatch):
        missing = str(tmp_path / "nope.json")
        assert pane_model_module.opencode_models_window(missing, "x") is None

        big = tmp_path / "big.json"
        big.write_text("x" * 32, encoding="utf-8")
        monkeypatch.setattr(pane_model_module, "CATALOG_MAX_BYTES", 16)
        assert pane_model_module.opencode_models_window(str(big), "x") is None

    def test_a_window_must_be_a_sane_positive_integer(self, tmp_path):
        catalog = tmp_path / "models.json"
        write_opencode_catalog(catalog, {
            "zai": {"models": {
                "zero": {"limit": {"context": 0}},
                "neg": {"limit": {"context": -5}},
                "huge": {"limit": {"context": 10 ** 12}},
            }},
        })

        for model in ("zero", "neg", "huge"):
            assert pane_model_module.opencode_models_window(
                str(catalog), model
            ) is None

    def test_pi_models_store_names_the_limit(self, tmp_path):
        store = tmp_path / "models-store.json"
        write_pi_store(store, {
            "anthropic": {"models": [
                {"id": "claude-fable-5", "contextWindow": 1000000},
            ]},
        })

        assert pane_model_module.pi_models_store_window(
            str(store), "claude-fable-5"
        ) == 1000000
        assert pane_model_module.pi_models_store_window(
            str(store), "anthropic/claude-fable-5"
        ) == 1000000

    def test_omp_models_db_reads_the_provider_rows(self, tmp_path):
        db = tmp_path / "models.db"
        write_omp_db(db, [
            ("openrouter", [{"id": "glm-5.3", "contextWindow": 200000}]),
            ("openrouter:pseudo-api", [
                {"id": "openrouter/glm-5.3", "contextWindow": 128000},
            ]),
        ])

        assert pane_model_module.omp_models_db_window(
            str(db), "glm-5.3"
        ) == 200000
        assert pane_model_module.omp_models_db_window(
            str(db), "openrouter/glm-5.3"
        ) == 128000


class TestResolverContext:
    def test_context_reads_used_from_the_probe_and_limit_from_the_catalog(
        self, tmp_path
    ):
        catalog = tmp_path / "models.json"
        write_opencode_catalog(catalog, {
            "zai": {"models": {"glm-5.3": {"limit": {"context": 200000}}}},
        })
        runner = ContextRunner(model="glm-5.3")

        context = context_resolver(runner, catalog=str(catalog)).context(
            "opencode", "ses_1"
        )

        assert context == {"used": 1110, "limit": 200000}
        # One last-model and one read-only cache-report call.
        assert ["last-model" in call for call in runner.calls] == [True, False]

    def test_without_a_catalog_there_is_no_context(self, tmp_path):
        runner = ContextRunner(model="glm-5.3")

        context = context_resolver(
            runner, catalog=str(tmp_path / "missing.json")
        ).context("opencode", "ses_1")

        assert context is None

    def test_a_harness_without_a_catalog_is_null_even_with_tokens(self):
        runner = ContextRunner(model="claude-opus-5-5")

        assert context_resolver(runner).context("claude", "abc") is None

    def test_a_failed_report_keeps_the_limit_and_nulls_the_used(self, tmp_path):
        catalog = tmp_path / "models.json"
        write_opencode_catalog(catalog, {
            "zai": {"models": {"glm-5.3": {"limit": {"context": 200000}}}},
        })

        context = context_resolver(
            BrokenReportRunner(model="glm-5.3"), catalog=str(catalog)
        ).context("opencode", "ses_1")

        assert context == {"used": None, "limit": 200000}

    def test_without_a_model_no_report_runs(self):
        runner = ContextRunner(model="")

        assert context_resolver(runner).context("opencode", "ses_1") is None
        assert len(runner.calls) == 1

    def test_a_switch_invalidates_the_cache_for_the_next_probe(self, tmp_path):
        catalog = tmp_path / "models.json"
        write_opencode_catalog(catalog, {
            "zai": {"models": {"glm-5.3": {"limit": {"context": 200000}}}},
        })
        runner = ContextRunner(model="glm-5.3")
        res = context_resolver(runner, catalog=str(catalog))
        assert res.context("opencode", "ses_1") is not None
        runner.model = "glm-5.4"
        assert res.context("opencode", "ses_1") is not None  # cached
        assert len(runner.calls) == 2

        runner.model = "glm-5.3"
        res.note_switched("opencode", "ses_1")

        assert res.context("opencode", "ses_1") is not None
        assert len(runner.calls) == 4  # both probes ran again

    def test_unknown_harness_or_session_is_none_without_running(self):
        runner = ContextRunner()

        assert context_resolver(runner).context("pi", "s") is None
        assert context_resolver(runner).context("opencode", "") is None
        assert runner.calls == []


class FixedContext:
    def __init__(self, models, contexts):
        self.models = models
        self.contexts = contexts

    def model(self, agent, session_id):
        return self.models.get((agent, session_id))

    def context(self, agent, session_id):
        return self.contexts.get((agent, session_id))


class TestContextWiring:
    def _tools(self, make_stub, settings, contexts):
        stub = make_stub(agents=[CLAUDE, PI])
        return BrainTools(
            settings, herdr=stub,
            pane_model=FixedContext({("claude", "abc"): "claude-opus-5-5"}, contexts),
        )

    def test_view_status_carries_the_context(self, make_stub, settings):
        tools = self._tools(make_stub, settings, {
            ("claude", "abc"): {"used": 1000, "limit": 200000},
        })
        assert tools.agent_view("w1:p1")["status"]["context"] == {
            "used": 1000, "limit": 200000,
        }
        # No catalog named the window: the key is ABSENT (additive on
        # purpose — the pre-F7d payload keeps its exact shape).
        assert "context" not in tools.agent_view("w1:p2")["status"]

    def test_state_and_herd_carry_the_context(self, make_stub, settings):
        tools = self._tools(make_stub, settings, {
            ("claude", "abc"): {"used": 1000, "limit": 200000},
        })
        assert tools.status_payload()["context"] == {
            "used": 1000, "limit": 200000,
        }
        by_pane = {entry["pane_id"]: entry for entry in tools.herd()}
        assert by_pane["w1:p1"]["context"] == {"used": 1000, "limit": 200000}
        assert "context" not in by_pane["w1:p2"]

    def test_a_double_without_the_seam_stays_null(self, make_stub, settings):
        stub = make_stub(agents=[CLAUDE, PI])
        tools = BrainTools(settings, herdr=stub, pane_model=FixedModels({}))
        assert "context" not in tools.status_payload()

    def test_inactive_status_has_a_null_context(self, make_stub, settings):
        stub = make_stub(agents=[])
        tools = BrainTools(
            settings, herdr=stub,
            pane_model=FixedContext({}, {}),
        )
        assert "context" not in tools.status_payload()


# ── the full connected-model catalog (opencode) ───────────────────────
#
# The on-screen picker paints only a window of its list (recents,
# favourites, a few more), so a screen-derived card can never offer
# every model. ``opencode models`` lists ALL the models of the
# operator's connected providers (``provider/model-id`` per line);
# opencode's local models.json cache names them for people (the picker
# Search filters by that display name). Read-only, cached, and a
# failure is an honest empty list with a reason — never a guess.

from herdr_brain.pane_model import OpencodeModelCatalog

CLI_LINES = (
    "opencode-go/glm-5.3\n"
    "opencode-go/qwen3.8-max\n"
    "anthropic/claude-opus-5-5\n"
    "openrouter/anthropic/claude-opus-5-5\n"
    "mystery/unnamed-model\n"
    "anthropic/claude-opus-5-5\n"  # duplicate line: one entry
)

DISPLAY_CATALOG = {
    "opencode-go": {
        "name": "OpenCode Go",
        "models": {
            "glm-5.3": {"name": "GLM-5.3"},
            "qwen3.8-max": {"name": "Qwen3.8 Max"},
        },
    },
    "anthropic": {
        "name": "Anthropic",
        "models": {"claude-opus-5-5": {"name": "Claude Opus 5.5"}},
    },
    "openrouter": {
        "name": "OpenRouter",
        "models": {"anthropic/claude-opus-5-5": {"name": "Claude Opus 5.5"}},
    },
}


def model_catalog(runner, tmp_path, clock=None, catalog=DISPLAY_CATALOG, **kwargs):
    path = tmp_path / "models.json"
    if catalog is not None:
        write_opencode_catalog(path, catalog)
    return OpencodeModelCatalog(
        runner=runner, clock=clock or Clock(), catalog_path=str(path),
        binary="/bin/opencode", **kwargs,
    )


class TestOpencodeModelCatalog:
    def test_lists_every_cli_model_joined_with_display_names(self, tmp_path):
        runner = FakeRunner(stdout=CLI_LINES)
        result = model_catalog(runner, tmp_path).list_models()

        assert runner.calls == [["/bin/opencode", "models"]]
        assert result["reason"] == ""
        by_id = {entry["id"]: entry for entry in result["models"]}
        assert len(result["models"]) == 5  # the duplicate line collapsed
        assert by_id["opencode-go/glm-5.3"] == {
            "id": "opencode-go/glm-5.3",
            "name": "GLM-5.3",
            "provider": "opencode-go",
            "provider_name": "OpenCode Go",
        }
        # An id with a slash keeps the whole tail as the model id.
        assert by_id["openrouter/anthropic/claude-opus-5-5"]["provider"] == "openrouter"
        assert by_id["openrouter/anthropic/claude-opus-5-5"]["name"] == "Claude Opus 5.5"

    def test_unknown_names_fall_back_to_the_ids(self, tmp_path):
        result = model_catalog(FakeRunner(stdout=CLI_LINES), tmp_path).list_models()
        mystery = next(e for e in result["models"] if e["provider"] == "mystery")
        assert mystery["name"] == "unnamed-model"
        assert mystery["provider_name"] == "mystery"

    def test_a_missing_models_json_still_lists_by_id(self, tmp_path):
        result = model_catalog(
            FakeRunner(stdout=CLI_LINES), tmp_path, catalog=None
        ).list_models()
        assert len(result["models"]) == 5
        glm = next(e for e in result["models"] if e["id"] == "opencode-go/glm-5.3")
        assert glm["name"] == "glm-5.3" and glm["provider_name"] == "opencode-go"

    def test_noise_lines_are_not_models(self, tmp_path):
        runner = FakeRunner(stdout="warning: update available\n\nanthropic/claude-opus-5-5\n")
        result = model_catalog(runner, tmp_path).list_models()
        assert [e["id"] for e in result["models"]] == ["anthropic/claude-opus-5-5"]

    def test_the_result_is_cached_until_the_ttl_expires(self, tmp_path):
        runner = FakeRunner(stdout=CLI_LINES)
        clock = Clock()
        catalog = model_catalog(runner, tmp_path, clock=clock, ttl_s=300)
        catalog.list_models()
        clock.now += 299
        catalog.list_models()
        assert len(runner.calls) == 1
        clock.now += 2
        catalog.list_models()
        assert len(runner.calls) == 2

    @pytest.mark.parametrize(
        "runner",
        [
            FakeRunner(returncode=1, stdout=""),
            FakeRunner(exc=subprocess.TimeoutExpired("opencode", 8)),
            FakeRunner(exc=OSError("no such file")),
            FakeRunner(stdout="\n\n"),
        ],
    )
    def test_a_failure_is_an_empty_list_with_a_reason(self, runner, tmp_path):
        result = model_catalog(runner, tmp_path).list_models()
        assert result["models"] == []
        assert result["reason"]

    def test_a_failure_is_retried_soon_not_cached_like_a_success(self, tmp_path):
        clock = Clock()
        runner = FakeRunner(returncode=1, stdout="")
        catalog = model_catalog(runner, tmp_path, clock=clock)
        catalog.list_models()
        catalog.list_models()
        assert len(runner.calls) == 1  # a short negative cache, no hammering
        clock.now += 60
        runner.returncode, runner.stdout = 0, CLI_LINES
        assert len(catalog.list_models()["models"]) == 5

    def test_a_missing_binary_is_an_empty_list_without_running(self, tmp_path, monkeypatch):
        runner = FakeRunner(stdout=CLI_LINES)
        monkeypatch.setattr("herdr_brain.pane_model.shutil.which", lambda _n: None)
        monkeypatch.setenv("HOME", str(tmp_path))
        catalog = OpencodeModelCatalog(
            runner=runner, clock=Clock(), catalog_path=str(tmp_path / "models.json")
        )
        result = catalog.list_models()
        assert result == {"models": [], "reason": "opencode-not-found"}
        assert runner.calls == []


class TestPaneModelsWiring:
    OPENCODE = AgentInfo(
        pane_id="w1:p9", agent="opencode", status="idle", session_kind="id",
        session_value="sid-9", cwd="/repo", title="OC", focused=False,
    )

    def tools(self, make_stub, settings, tmp_path, runner, models=None):
        stub = make_stub(agents=[CLAUDE, self.OPENCODE])
        return BrainTools(
            settings, herdr=stub,
            pane_model=FixedModels(models or {}),
            model_catalog=model_catalog(runner, tmp_path),
        )

    def test_the_current_model_is_marked_from_the_pane_model(
        self, make_stub, settings, tmp_path
    ):
        tools = self.tools(
            make_stub, settings, tmp_path, FakeRunner(stdout=CLI_LINES),
            models={("opencode", "sid-9"): "opencode-go/glm-5.3"},
        )
        result = tools.pane_models("w1:p9")
        current = [e["id"] for e in result["models"] if e["current"]]
        assert current == ["opencode-go/glm-5.3"]
        assert all("current" in e for e in result["models"])

    def test_no_current_when_the_pane_model_is_unknown(
        self, make_stub, settings, tmp_path
    ):
        tools = self.tools(make_stub, settings, tmp_path, FakeRunner(stdout=CLI_LINES))
        assert not any(e["current"] for e in tools.pane_models("w1:p9")["models"])

    def test_an_unknown_pane_is_none_and_another_harness_is_unsupported(
        self, make_stub, settings, tmp_path
    ):
        runner = FakeRunner(stdout=CLI_LINES)
        tools = self.tools(make_stub, settings, tmp_path, runner)
        assert tools.pane_models("w9:p9") is None
        assert tools.pane_models("w1:p1") == {"models": [], "reason": "unsupported"}
        assert runner.calls == []
