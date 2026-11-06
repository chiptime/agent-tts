"""Live-engine STT settings (S4): env-driven knobs with never-download
defaults. The live model default is large-v3 BY NAME — the llm-hub cache
path is a deploy-time env value, never a code default."""

from __future__ import annotations

from herdr_brain.config import (
    DEFAULT_LIVE_STT_COMPUTE,
    DEFAULT_LIVE_STT_DEVICE,
    DEFAULT_LIVE_STT_FALLBACK_MODEL,
    DEFAULT_LIVE_STT_MODEL,
    load_settings,
)

_BASE_ENV = {"HERDR_TTS_HOME": "/tmp/herdr-tts-surface"}


class TestLiveSttSettings:
    def test_defaults(self):
        s = load_settings(dict(_BASE_ENV))
        assert s.live_stt_model == "large-v3"
        assert s.live_stt_device == "cuda"
        assert s.live_stt_compute == "int8_float16"
        assert s.live_stt_fallback_model == "small"

    def test_env_overrides(self):
        s = load_settings({
            **_BASE_ENV,
            "HERDR_BRAIN_LIVE_STT_MODEL": "/cache/hub/models--Systran--faster-whisper-large-v3",
            "HERDR_BRAIN_LIVE_STT_DEVICE": "cpu",
            "HERDR_BRAIN_LIVE_STT_COMPUTE": "int8",
            "HERDR_BRAIN_LIVE_STT_FALLBACK_MODEL": "base",
        })
        assert s.live_stt_model == "/cache/hub/models--Systran--faster-whisper-large-v3"
        assert s.live_stt_device == "cpu"
        assert s.live_stt_compute == "int8"
        assert s.live_stt_fallback_model == "base"

    def test_default_model_is_a_name_not_a_machine_path(self):
        """Pins the deploy contract: no absolute path (llm-hub's or any
        other machine's) may ship as the code default."""
        assert DEFAULT_LIVE_STT_MODEL == "large-v3"
        assert "/" not in DEFAULT_LIVE_STT_MODEL
        assert "llm-hub" not in DEFAULT_LIVE_STT_MODEL
