"""Unit tests for the reader cache (content-addressed rendered turns).

Covers the length-prefixed SHA-256 cache key, the sidecar validation
predicate, snapshot turn identity, cache mechanics (atomic publish,
corruption-as-miss, LRU/disk bounds, namespace invalidation), execution
bounds (semaphore, per-request budget, failure memo) and the Settings
knobs — per design Decisions 3-7, 9, 11.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

import pytest

import herdr_brain.reader as reader_module
from herdr_brain.config import Settings, load_settings
from herdr_brain.reader import (
    CONTRACT_ID,
    ReaderCache,
    cache_dir,
    cache_key,
    turn_id,
    validate_sidecar,
)
from herdr_brain.tts import ReaderError
from tests.conftest import SETTINGS_KWARGS


def sidecar(**overrides) -> dict:
    """A valid reader-pipeline/anchors@1 sidecar, overridable per case."""
    base = {
        "version": 1,
        "contract": "reader-pipeline/anchors@1",
        "alignment": "exact",
        "total_sents": 1,
        "total_paras": 1,
        "engine": {"lang": "es", "max_chars": 0, "summarize": False,
                   "lexicon_fp": "stub"},
        "sentences": [],
    }
    base.update(overrides)
    return base


def reader_settings(tmp_path: Path, **overrides) -> Settings:
    kwargs = {**SETTINGS_KWARGS, "audio_dir": str(tmp_path / "state" / "audio")}
    kwargs.update(overrides)
    return Settings(**kwargs)


def fake_renderer(html="<p>hola</p>", sidecar_dict=None, fail=False):
    """render_html-shaped double counting invocations."""
    calls: list[str] = []

    def render(settings, text, workdir):
        calls.append(text)
        if fail:
            raise ReaderError("stub boom")
        return html, dict(sidecar_dict if sidecar_dict is not None else sidecar())

    render.calls = calls
    return render


class TestCacheKey:
    """Decision 4: length-prefixed serialization over the profile tuple."""

    def test_key_is_sha256_hex(self):
        assert re.fullmatch(r"[0-9a-f]{64}", cache_key("hola"))

    def test_length_prefix_prevents_forged_key(self):
        """Text engineered to impersonate another tuple's joined material
        under a separator-style encoding still gets its own key."""
        victim = cache_key("hola")
        forged_text = "\x00".join(("1", CONTRACT_ID, "es", "0", "false", "hola"))
        assert cache_key(forged_text) != victim
        # Still injective: the forged text deterministically maps to itself.
        assert cache_key(forged_text) == cache_key(forged_text)

    def test_changed_text_changes_key(self):
        assert cache_key("uno") != cache_key("dos")

    def test_profile_version_changes_key(self, monkeypatch):
        before = cache_key("hola")
        monkeypatch.setattr(reader_module, "READER_PROFILE_VERSION", "2")
        assert cache_key("hola") != before


class TestValidateSidecar:
    """Data Contracts predicate: contract, version, alignment, engine."""

    def test_wrong_contract_rejected(self):
        assert validate_sidecar(sidecar(contract="reader-pipeline/anchors@9")) is False
        assert validate_sidecar(sidecar(version=2)) is False

    def test_missing_engine_rejected(self):
        broken = sidecar()
        del broken["engine"]
        assert validate_sidecar(broken) is False
        assert validate_sidecar(None) is False
        assert validate_sidecar("not-a-dict") is False

    def test_summarize_true_rejected(self):
        summarized = sidecar(engine={"lang": "es", "max_chars": 0,
                                     "summarize": True, "lexicon_fp": "stub"})
        assert validate_sidecar(summarized) is False

    def test_valid_sidecar_accepted(self):
        for alignment in ("exact", "coverage"):
            assert validate_sidecar(sidecar(alignment=alignment)) is True
        # Engine metadata participates: this is not the es/0/false profile.
        assert validate_sidecar(sidecar(engine={"lang": "en", "max_chars": 0,
                                                "summarize": False,
                                                "lexicon_fp": "stub"})) is False
        assert validate_sidecar(sidecar(engine={"lang": "es", "max_chars": 500,
                                                "summarize": False,
                                                "lexicon_fp": "stub"})) is False
        assert validate_sidecar(sidecar(alignment="other")) is False


class TestTurnIdentity:
    """Decision 4 framing over (session, index, role, text), 16 hex."""

    def test_duplicate_text_distinct_ids(self):
        same = "respuesta idéntica"
        first = turn_id("ses_a", 0, "assistant", same)
        second = turn_id("ses_a", 1, "assistant", same)
        assert first != second

    def test_turn_id_deterministic(self):
        args = ("ses_a", 3, "user", "misma pregunta")
        assert turn_id(*args) == turn_id(*args)
        for value in (turn_id(*args), turn_id("ses_a", 0, "assistant", "x")):
            assert re.fullmatch(r"[0-9a-f]{16}", value)


class TestCacheMechanics:
    """Decisions 5, 6, 11: envelope, atomic publish, bounds, revalidation."""

    def _cache(self, tmp_path, renderer=None) -> tuple[ReaderCache, Path]:
        cfg = reader_settings(tmp_path)
        return ReaderCache(cfg, renderer=renderer), cache_dir(cfg)

    def test_publish_is_atomic_single_file(self, tmp_path):
        cache, cdir = self._cache(tmp_path)
        key = cache_key("hola")
        cache.publish(key, "<p>hola</p>", sidecar())
        files = sorted(p.name for p in cdir.iterdir())
        assert files == [f"{key}.json"]  # one envelope, no tmp sibling
        envelope = json.loads((cdir / f"{key}.json").read_text(encoding="utf-8"))
        assert envelope["v"] == 1
        assert envelope["key"] == key
        assert envelope["contract"] == CONTRACT_ID
        assert envelope["html"] == "<p>hola</p>"
        assert envelope["map"] == sidecar()
        assert envelope["created_at"] > 0

    def test_corrupt_artifact_is_miss(self, tmp_path):
        cache, cdir = self._cache(tmp_path)
        key = cache_key("hola")
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / f"{key}.json").write_text("TRUNCATED{")
        assert cache.get(key) is None

    def test_missing_artifact_is_miss(self, tmp_path):
        cache, _ = self._cache(tmp_path)
        assert cache.get(cache_key("nada")) is None

    def test_unwritable_dir_degrades_failsoft(self, tmp_path, monkeypatch):
        def boom(*args, **kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(Path, "mkdir", boom)
        cache = ReaderCache(reader_settings(tmp_path), renderer=fake_renderer())
        pair = cache.render_turn("hola")  # must not raise
        assert pair == ("<p>hola</p>", sidecar())  # memory-only, still served

    def test_retention_evicts_oldest(self, tmp_path, monkeypatch):
        cache, cdir = self._cache(tmp_path)
        keys = [cache_key(f"turn-{i}") for i in range(6)]
        monkeypatch.setattr(reader_module, "READER_DISK_MAX", 5)
        for i, key in enumerate(keys[:5]):
            cache.publish(key, f"<p>{i}</p>", sidecar())
            # Deterministic ordering: mtime drives the oldest-first sweep.
            # Past, distinct stamps keep the freshly published file (mtime
            # ~now) unambiguously the newest.
            stamp = time.time() - (100 - i)
            os.utime(cdir / f"{key}.json", (stamp, stamp))
        # Drop the ceiling: the next publish must evict the three oldest.
        monkeypatch.setattr(reader_module, "READER_DISK_MAX", 3)
        cache.publish(keys[5], "<p>5</p>", sidecar())
        names = {p.name for p in cdir.iterdir()}
        assert names == {f"{k}.json" for k in keys[3:]}  # 3 newest survive

    def test_cache_filename_is_hex_only(self, tmp_path):
        cache, cdir = self._cache(tmp_path)
        cache.publish(cache_key("hola"), "<p>hola</p>", sidecar())
        for p in cdir.iterdir():
            assert re.fullmatch(r"[0-9a-f]{64}\.json", p.name), p.name

    def test_lru_evicts_beyond_128(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "mkdir", lambda self, *a, **k: None)
        renderer = fake_renderer()
        cache = ReaderCache(reader_settings(tmp_path), renderer=renderer)
        for i in range(129):  # one past the memory bound
            cache.render_turn(f"texto {i}")
        assert cache.render_turn("texto 0") is not None  # re-rendered
        assert cache.render_calls == 130  # 129 cold + 1 post-eviction

    def test_namespace_bump_invalidates(self, tmp_path, monkeypatch):
        cfg = reader_settings(tmp_path)
        cache_one = ReaderCache(cfg, renderer=fake_renderer())
        assert cache_one.render_turn("hola") is not None
        monkeypatch.setattr(reader_module, "READER_PROFILE_VERSION", "2")
        second = fake_renderer()
        cache_two = ReaderCache(cfg, renderer=second)
        assert cache_two.render_turn("hola") is not None
        assert len(second.calls) == 1  # old artifact invisible after bump


class TestBounds:
    """Decision 7: semaphore, failure memo, per-request render budget."""

    def test_concurrency_semaphore_bound(self, tmp_path):
        lock = threading.Lock()
        state = {"current": 0, "max": 0, "done": 0}

        def slow_renderer(settings, text, workdir):
            with lock:
                state["current"] += 1
                state["max"] = max(state["max"], state["current"])
            time.sleep(0.05)
            with lock:
                state["current"] -= 1
                state["done"] += 1
            return "<p>x</p>", sidecar()

        cache = ReaderCache(reader_settings(tmp_path), renderer=slow_renderer)
        threads = [
            threading.Thread(target=cache.render_turn, args=(f"turn {i}",))
            for i in range(6)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert state["max"] <= 2      # semaphore held: never above the bound
        assert state["done"] == 6     # short waits, every render completed

    def test_failure_memo_suppresses_respawn(self, tmp_path):
        renderer = fake_renderer(fail=True)
        cache = ReaderCache(reader_settings(tmp_path), renderer=renderer)
        for _ in range(4):  # repeated failing polls
            assert cache.render_turn("turn que falla") is None
        assert cache.render_calls == 1  # exactly one spawn, then memoized

    def test_render_budget_per_request(self, tmp_path):
        renderer = fake_renderer()
        cache = ReaderCache(reader_settings(tmp_path), renderer=renderer)
        turns = [{"role": "user", "text": f"turno {i}"} for i in range(20)]
        out = cache.render_snapshot(turns)
        assert cache.render_calls <= reader_module.READER_MAX_RENDERS_PER_REQUEST
        assert len(out) == 20  # every turn answered, source order intact
        rendered = [t for t in out if t["html"] is not None]
        assert len(rendered) == 16  # budget exactly, progressive warming later
        for turn in out:
            assert (turn["html"] is None) == (turn["map"] is None)  # pair nulls

    def test_budget_allows_sixteen_cold_renders(self, tmp_path):
        """Spec delta reader-always-formatted: the budget constant is 16;
        a 20-turn cold conversation renders exactly 16 per request, the
        over-budget 4 stay null-pair (fail-soft unchanged), and the next
        request fills them progressively — all 20 rendered by then."""
        assert reader_module.READER_MAX_RENDERS_PER_REQUEST == 16
        renderer = fake_renderer()
        cache = ReaderCache(reader_settings(tmp_path), renderer=renderer)
        turns = [{"role": "user", "text": f"turno {i}"} for i in range(20)]
        out = cache.render_snapshot(turns)
        assert cache.render_calls == 16          # budget exactly, never more
        assert len(out) == 20                    # every turn answered
        rendered = [t for t in out if t["html"] is not None]
        assert len(rendered) == 16
        for turn in out:
            assert (turn["html"] is None) == (turn["map"] is None)  # null-pair
        second = cache.render_snapshot(turns)    # next 5s poll: fill the rest
        assert cache.render_calls == 20          # only the 4 cold ones spawned
        assert all(t["html"] is not None for t in second)


class TestReaderSettings:
    """Decision 9: knobs with defaults so every existing construction holds."""

    def test_reader_settings_defaults(self):
        cfg = Settings(**SETTINGS_KWARGS)  # unchanged conftest construction
        assert cfg.reader_timeout_s == 30
        assert cfg.reader_concurrency == 2

    def test_reader_settings_env_override(self):
        cfg = load_settings(env={
            "HERDR_BRAIN_READER_TIMEOUT_S": "7",
            "HERDR_BRAIN_READER_CONCURRENCY": "4",
        })
        assert cfg.reader_timeout_s == 7
        assert cfg.reader_concurrency == 4
