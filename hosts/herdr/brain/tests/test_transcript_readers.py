"""Registry + connector tests against the synthesized fixture stores.

Every store lives under ``tests/fixtures/transcripts`` and is pointed at
through the same env vars production would set, so each test proves both
the on-disk format reading and the env-overridable roots (doc 05 AC4).
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"

OPENCODE_SESSION = "ses_fixture0001"
CLAUDE_SESSION = "00000000-1111-2222-3333-444444444444"
ANTIGRAVITY_SESSION = "11111111-2222-3333-4444-555555555555"
CODEX_SESSION = "aaaaaaaa-1111-2222-3333-444444444444"
GROK_SESSION = "bbbbbbbb-1111-2222-3333-444444444444"
PI_SESSION = "cccccccc-1111-2222-3333-444444444444"
OMP_SESSION = "dddddddd-1111-2222-3333-444444444444"
HERMES_SESSION = "20260102_100000_demo"
HERMES_PROFILE_SESSION = "20260102_110000_wk"


@pytest.fixture
def registry_env(monkeypatch):
    """Env pointing every harness root at the fixture stores."""
    monkeypatch.setenv("OPENCODE_DB", str(FIXTURES / "opencode" / "opencode.db"))
    monkeypatch.setenv("CLAUDE_PROJECTS_ROOT", str(FIXTURES / "claude" / "projects"))
    monkeypatch.setenv("ANTIGRAVITY_ROOT", str(FIXTURES / "antigravity"))
    monkeypatch.setenv("CODEX_ROOT", str(FIXTURES / "codex" / "sessions"))
    monkeypatch.setenv("GROK_ROOT", str(FIXTURES / "grok" / "sessions"))
    monkeypatch.setenv(
        "PI_ROOTS",
        str(FIXTURES / "pi" / "agent" / "sessions"),
    )
    monkeypatch.setenv("HERMES_ROOT", str(FIXTURES / "hermes"))
    from herdr_brain.transcript_readers.registry import default_registry

    return default_registry()


class TestRegistryResolution:
    """Doc 05 AC2: exact-name resolution, declared aliases, no-connector."""

    def test_every_supported_harness_resolves_by_exact_name(self, registry_env):
        expected = {"antigravity", "claude", "codex", "grok", "hermes", "opencode", "pi"}
        assert set(registry_env.harnesses()) == expected
        for name in expected:
            assert registry_env.resolve(name) is not None, name

    def test_declared_aliases_resolve_to_their_canonical_reader(self, registry_env):
        assert registry_env.resolve("omp").name == "pi"
        assert registry_env.resolve("oa").name == "opencode"
        assert registry_env.resolve("agy").name == "antigravity"

    def test_an_alias_is_not_an_extra_harness(self, registry_env):
        assert "omp" not in registry_env.harnesses()

    def test_agent_without_connector_resolves_to_none(self, registry_env):
        assert registry_env.resolve("vim") is None
        assert registry_env.resolve("") is None
        assert registry_env.resolve(None) is None


class TestFixtureStores:
    """Doc 05 AC4: each connector reads its synthesized fixture store."""

    def test_opencode_reads_turns_and_title(self, registry_env):
        reader = registry_env.resolve("opencode")
        turns = reader.read(OPENCODE_SESSION, 10)
        assert [t.role for t in turns] == ["user", "assistant", "user", "assistant"]
        # Multi-part assistant message joins its parts in order.
        assert "Ayer cerramos el parser" in turns[1].text
        assert "dos pruebas pendientes" in turns[1].text
        assert reader.read_title(OPENCODE_SESSION) == "Cierre del parser"

    def test_claude_reads_turns(self, registry_env):
        turns = registry_env.resolve("claude").read(CLAUDE_SESSION, 10)
        assert [t.role for t in turns] == ["user", "assistant", "user", "assistant"]
        assert "18 comprobaciones" in turns[-1].text
        assert turns[0].created_at is not None

    def test_antigravity_reads_turns(self, registry_env):
        turns = registry_env.resolve("antigravity").read(ANTIGRAVITY_SESSION, 10)
        assert [t.role for t in turns] == ["user", "assistant"]
        assert turns[0].text == "explica el fallo del build"
        assert "lockfile" in turns[1].text

    def test_codex_reads_response_items_only(self, registry_env):
        """The codex log double-books every turn (response_item AND
        event_msg); the reader must take response_item once and drop the
        event stream, the developer role and the reasoning rows."""
        turns = registry_env.resolve("codex").read(CODEX_SESSION, 10)
        assert [t.role for t in turns] == [
            "user", "assistant", "user", "assistant",
        ]
        assert turns[0].text == "resume el estado del repositorio"
        assert "12 archivos modificados" in turns[1].text

    def test_grok_extracts_user_query_and_drops_synthetic_rows(self, registry_env):
        turns = registry_env.resolve("grok").read(GROK_SESSION, 10)
        assert [t.role for t in turns] == ["user", "assistant", "user", "assistant"]
        assert turns[0].text == "arregla el test roto del login"
        # The skills banner (synthetic_reason) and system row never render.
        assert all("Skills available" not in t.text for t in turns)
        assert turns[-1].text == "Suite completa en verde: 34 pruebas pasando."

    def test_pi_reads_text_blocks_and_skips_tool_results(self, registry_env):
        turns = registry_env.resolve("pi").read(PI_SESSION, 10)
        assert [t.role for t in turns] == ["user", "assistant", "user", "assistant"]
        assert turns[0].text == "abre el panel de metricas"
        assert turns[1].text == "Panel abierto en el puerto 4096."

    def test_omp_reads_pis_format_from_its_own_root(self, monkeypatch):
        monkeypatch.setenv(
            "PI_ROOTS", str(FIXTURES / "pi-omp" / "agent" / "sessions")
        )
        from herdr_brain.transcript_readers.registry import default_registry

        turns = default_registry().resolve("omp").read(OMP_SESSION, 10)
        assert [t.role for t in turns] == ["user", "assistant"]
        assert "4173" in turns[1].text

    def test_hermes_reads_active_rows_from_the_base_db(self, registry_env):
        turns = registry_env.resolve("hermes").read(HERMES_SESSION, 10)
        assert [t.role for t in turns] == ["user", "assistant", "user", "assistant"]
        # The inactive row (active=0, compacted=0) and the hidden one stay out.
        assert all("descartada" not in t.text for t in turns)
        assert all("diagnostico" not in t.text for t in turns)
        assert turns[-1].text == "Migracion aplicada; el esquema esta al dia."

    def test_hermes_falls_back_to_profile_databases(self, registry_env):
        turns = registry_env.resolve("hermes").read(HERMES_PROFILE_SESSION, 10)
        assert [t.role for t in turns] == ["user", "assistant"]
        assert turns[-1].text == "Parte abierto con las horas de ayer."


class TestEnvRoots:
    """Roots are env-driven: a wrong root reads nothing, never errors."""

    def test_codex_root_from_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CODEX_ROOT", str(tmp_path))
        from herdr_brain.transcript_readers.registry import default_registry

        assert default_registry().resolve("codex").read(CODEX_SESSION, 5) == []

    def test_grok_root_from_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GROK_ROOT", str(tmp_path))
        from herdr_brain.transcript_readers.registry import default_registry

        assert default_registry().resolve("grok").read(GROK_SESSION, 5) == []

    def test_hermes_root_from_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_ROOT", str(tmp_path))
        from herdr_brain.transcript_readers.registry import default_registry

        assert default_registry().resolve("hermes").read(HERMES_SESSION, 5) == []

    def test_pi_accepts_multiple_roots(self, monkeypatch):
        monkeypatch.setenv(
            "PI_ROOTS",
            str(FIXTURES / "missing") + ":" + str(FIXTURES / "pi" / "agent" / "sessions"),
        )
        from herdr_brain.transcript_readers.registry import default_registry

        turns = default_registry().resolve("pi").read(PI_SESSION, 5)
        assert turns and turns[0].text == "abre el panel de metricas"


class TestRoutingIntegration:
    """transcripts.read_turns routes the new harnesses through the registry."""

    @pytest.mark.parametrize(
        "agent,session",
        [
            ("codex", CODEX_SESSION),
            ("grok", GROK_SESSION),
            ("pi", PI_SESSION),
            ("omp", OMP_SESSION),
        ],
    )
    def test_read_turns_routes_new_harnesses(self, monkeypatch, agent, session):
        monkeypatch.setenv("CODEX_ROOT", str(FIXTURES / "codex" / "sessions"))
        monkeypatch.setenv("GROK_ROOT", str(FIXTURES / "grok" / "sessions"))
        monkeypatch.setenv(
            "PI_ROOTS",
            str(FIXTURES / "pi" / "agent" / "sessions")
            + ":"
            + str(FIXTURES / "pi-omp" / "agent" / "sessions"),
        )
        from herdr_brain.transcripts import read_turns

        turns = read_turns(agent, session, 10)
        assert turns and turns[-1].role == "assistant"

    def test_read_turns_unknown_agent_stays_none(self, registry_env):
        from herdr_brain.transcripts import read_turns

        assert read_turns("vim", "whatever", 10) is None
