"""Server-side spoken i18n (S4, doc 09 — the server lexicon half).

Spanish is the DEFAULT and must stay byte-identical to the historical
strings (the watcher templates and the gate closers are pinned by the
pre-existing suite); English is the one added locale (L3). The locale
is chosen once per call through ``POST /call/context`` and every
spoken surface reads the same value.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from herdr_brain import spoken_lexicon
from herdr_brain.config import Settings
from herdr_brain.server import APPROVAL_CLOSER, REPROMPT_LINE, create_app

from tests.conftest import SETTINGS_KWARGS


# ── the lexicon itself ───────────────────────────────────────────────────────


class TestLexicon:
    def test_es_templates_are_the_historical_strings_verbatim(self):
        # Byte-identical Spanish: these exact strings are pinned by the
        # pre-existing watcher/server suites and by installed phones.
        assert spoken_lexicon.blocked_announcement("es", "opencode repo", "¿Seguimos?") == (
            "opencode repo necesita tu atención: ¿Seguimos?"
        )
        assert spoken_lexicon.done_announcement("es", "opencode repo", "Todo verde.") == (
            "opencode repo terminó: Todo verde."
        )
        assert spoken_lexicon.fallback_detail("es") == "sin detalle disponible"
        assert spoken_lexicon.approval_closer("es") == APPROVAL_CLOSER == "¿Se envía?"
        assert spoken_lexicon.reprompt_line("es") == REPROMPT_LINE == "¿Sí o no?"

    def test_en_templates_exist_for_every_es_string(self):
        assert spoken_lexicon.blocked_announcement("en", "opencode repo", "Proceed?") == (
            "opencode repo needs your attention: Proceed?"
        )
        assert spoken_lexicon.done_announcement("en", "opencode repo", "All green.") == (
            "opencode repo finished: All green."
        )
        assert spoken_lexicon.fallback_detail("en") == "no detail available"
        assert spoken_lexicon.approval_closer("en") == "Send it?"
        assert spoken_lexicon.reprompt_line("en") == "Yes or no?"

    def test_every_locale_covers_the_same_keys(self):
        for locale in spoken_lexicon.LOCALES:
            bundle = spoken_lexicon.LEXICON[locale]
            assert set(bundle) == set(spoken_lexicon.LEXICON["es"])

    def test_unknown_locale_falls_back_to_spanish(self):
        # Anything the lexicon does not know speaks Spanish — the
        # default is a product property, never an exception.
        assert (
            spoken_lexicon.approval_closer("fr")
            == spoken_lexicon.approval_closer("es")
        )

    def test_normalize_locale_accepts_only_known_locales(self):
        assert spoken_lexicon.normalize_locale("en") == "en"
        assert spoken_lexicon.normalize_locale(" ES ") == "es"
        assert spoken_lexicon.normalize_locale("de") is None
        assert spoken_lexicon.normalize_locale(None) is None
        assert spoken_lexicon.normalize_locale(7) is None


# ── the watcher announces in the call's locale ───────────────────────────────


def _watcher_announcement(locale_provider, tmp_path):
    from herdr_brain.herdr import AgentInfo
    from herdr_brain.watcher import AgentWatcher

    class FixedHerdr:
        def __init__(self, agents):
            self._agents = agents

        def list_agents(self):
            return list(self._agents)

        def read_screen(self, pane_id, n_lines=None, source="visible", as_ansi=False):
            return "¿Continuar con la migración?"

    settings = Settings(**{**SETTINGS_KWARGS, "state_dir": str(tmp_path)})
    watcher = AgentWatcher(
        settings,
        herdr=FixedHerdr(
            [
                AgentInfo(
                    pane_id="w1:p1", agent="opencode", status="working",
                    session_kind="id", session_value=None, cwd="/repo",
                    title="OpenCode", focused=True,
                )
            ]
        ),
        tts_renderer=lambda cfg, text, out: out.write_bytes(b"x") or out,
        locale_provider=locale_provider,
        debounce_s=0,
    )
    watcher.poll_once()  # baseline
    agent = AgentInfo(
        pane_id="w1:p1", agent="opencode", status="blocked",
        session_kind="id", session_value=None, cwd="/repo",
        title="OpenCode", focused=True,
    )
    watcher._herdr._agents = [agent]
    produced = watcher.poll_once()
    assert produced, "the blocked transition must announce"
    return produced[0]


class TestWatcherLocale:
    def test_default_locale_is_spanish_byte_identical(self, tmp_path):
        ann = _watcher_announcement(None, tmp_path)
        assert ann["text"] == (
            "opencode repo necesita tu atención: ¿Continuar con la migración?"
        )

    def test_es_provider_keeps_the_historical_bytes(self, tmp_path):
        ann = _watcher_announcement(lambda: "es", tmp_path)
        assert ann["text"] == (
            "opencode repo necesita tu atención: ¿Continuar con la migración?"
        )

    def test_en_provider_announces_in_english(self, tmp_path):
        ann = _watcher_announcement(lambda: "en", tmp_path)
        assert ann["text"] == (
            "opencode repo needs your attention: ¿Continuar con la migración?"
        )


# ── the call context carries the locale ─────────────────────────────────────


class FakeTTS:
    def __call__(self, settings, text, out_path):
        out_path.write_bytes(b"ID3-fake")
        return out_path


def _client(tmp_path, monkeypatch, llm):
    import herdr_brain.server as server_module
    from tests.test_call_context import FakeReplayTools

    monkeypatch.setattr(server_module, "BrainTools", FakeReplayTools)
    app = create_app(
        settings=Settings(**{**SETTINGS_KWARGS, "state_dir": str(tmp_path / "state")}),
        llm_factory=lambda _cfg, _tools: llm,
        tts_renderer=FakeTTS(),
    )
    return TestClient(app)


class TestCallContextLocale:
    def test_default_snapshot_carries_the_spanish_locale(self, tmp_path, monkeypatch):
        from tests.test_call_context import FakeGatedLLM

        client = _client(tmp_path, monkeypatch, FakeGatedLLM())
        assert client.get("/call/context").json()["locale"] == "es"

    def test_locale_is_persisted_and_read_back(self, tmp_path, monkeypatch):
        from tests.test_call_context import FakeGatedLLM

        client = _client(tmp_path, monkeypatch, FakeGatedLLM())
        body = client.post("/call/context", json={"locale": "en"}).json()
        assert body["locale"] == "en"
        assert client.get("/call/context").json()["locale"] == "en"

    def test_unknown_locale_is_ignored_not_stored(self, tmp_path, monkeypatch):
        from tests.test_call_context import FakeGatedLLM

        client = _client(tmp_path, monkeypatch, FakeGatedLLM())
        client.post("/call/context", json={"locale": "en"})
        # A bad locale changes nothing: the previous one survives.
        body = client.post("/call/context", json={"locale": "de"}).json()
        assert body["locale"] == "en"
        assert client.get("/call/context").json()["locale"] == "en"
        # A non-string is a validation problem the edge refuses.
        assert client.post("/call/context", json={"locale": 7}).status_code == 422

    def test_omitting_the_locale_keeps_the_previous_one(self, tmp_path, monkeypatch):
        from tests.test_call_context import FakeGatedLLM

        client = _client(tmp_path, monkeypatch, FakeGatedLLM())
        client.post("/call/context", json={"locale": "en"})
        body = client.post("/call/context", json={"pane_id": "w1:p9"}).json()
        assert body["locale"] == "en" and body["pane_id"] == "w1:p9"


# ── the gate closers follow the locale ──────────────────────────────────────


class TestGateClosers:
    def _gated(self, tmp_path, monkeypatch):
        from tests.test_call_context import FakeGatedLLM

        llm = FakeGatedLLM()
        return _client(tmp_path, monkeypatch, llm), llm

    def test_ask_closer_is_spanish_by_default(self, tmp_path, monkeypatch):
        client, _ = self._gated(tmp_path, monkeypatch)
        body = client.post("/ask", json={"text": "gated question"}).json()
        assert body["answer"].endswith(APPROVAL_CLOSER)

    def test_ask_closer_is_english_when_the_call_selected_en(
        self, tmp_path, monkeypatch
    ):
        client, _ = self._gated(tmp_path, monkeypatch)
        client.post("/call/context", json={"locale": "en"})
        body = client.post("/ask", json={"text": "gated question"}).json()
        assert body["answer"].endswith("Send it?")
        assert not body["answer"].endswith(APPROVAL_CLOSER)

    def test_reprompt_line_follows_the_locale(self, tmp_path, monkeypatch):
        client, _ = self._gated(tmp_path, monkeypatch)
        client.post("/call/context", json={"locale": "en"})
        assert client.post("/ask", json={"text": "gated question"}).status_code == 200
        gate_id = client.get("/approval/current").json()["approval"]["gate_id"]
        body = client.post(
            f"/approval/{gate_id}/resolve", json={"utterance": "øøø"}
        ).json()
        assert body["decision"] == "reprompt"
        assert body["answer"] == "Yes or no?"
