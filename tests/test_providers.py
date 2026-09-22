import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent_tts.providers.piper as piper_module
from agent_tts.providers.base import parse_rate_to_multiplier
from agent_tts.providers import (
    EdgeTTSProvider,
    ElevenLabsTTSProvider,
    OpenAITTSProvider,
    PiperTTSProvider,
    get_provider,
    provider_names,
    provider_voices,
)


class TestProviders(unittest.TestCase):
    def test_parse_rate_to_multiplier(self):
        self.assertEqual(parse_rate_to_multiplier("+20%"), 1.2)
        self.assertEqual(parse_rate_to_multiplier("+10%"), 1.1)
        self.assertEqual(parse_rate_to_multiplier("+0%"), 1.0)
        self.assertEqual(parse_rate_to_multiplier("-10%"), 0.9)
        self.assertEqual(parse_rate_to_multiplier("1.5"), 1.5)
        self.assertEqual(parse_rate_to_multiplier(""), 1.0)

    def test_edge_voice_resolution(self):
        prov = EdgeTTSProvider()
        self.assertEqual(prov.resolve_voice("elvira"), "es-ES-ElviraNeural")
        self.assertEqual(prov.resolve_voice("alvaro"), "es-ES-AlvaroNeural")
        self.assertEqual(prov.resolve_voice("custom-voice"), "custom-voice")

    def test_openai_voice_resolution(self):
        prov = OpenAITTSProvider(api_key="test")
        self.assertEqual(prov.resolve_voice("elvira"), "nova")
        self.assertEqual(prov.resolve_voice("alvaro"), "onyx")
        self.assertEqual(prov.resolve_voice("alloy"), "alloy")

    def test_elevenlabs_voice_resolution(self):
        prov = ElevenLabsTTSProvider(api_key="test")
        self.assertEqual(prov.resolve_voice_id("rachel"), "21m00Tcm4TlvDq8ikWAM")
        self.assertEqual(prov.resolve_voice_id("custom_id_1234567890"), "custom_id_1234567890")

    def test_piper_provider(self):
        prov = PiperTTSProvider(model_path="/tmp/test_model.onnx")
        self.assertEqual(prov.model_path, "/tmp/test_model.onnx")
        prov_no_bin = PiperTTSProvider(binary_path="/nonexistent/path/piper")
        self.assertFalse(prov_no_bin.is_available())

    def test_get_provider(self):
        self.assertIsInstance(get_provider("edge"), EdgeTTSProvider)
        self.assertIsInstance(get_provider("openai"), OpenAITTSProvider)
        self.assertIsInstance(get_provider("elevenlabs"), ElevenLabsTTSProvider)
        self.assertIsInstance(get_provider("piper"), PiperTTSProvider)
        self.assertIsInstance(get_provider("local"), PiperTTSProvider)
        self.assertIsInstance(get_provider("unknown"), EdgeTTSProvider)


class TestPiperBinaryResolution(unittest.TestCase):
    """Resolution order: AGENT_TTS_PIPER_BIN (existing file) > shutil.which."""

    def setUp(self):
        # Explicit binary_path avoids resolution during __init__; the method
        # under test is then called directly.
        self.provider = PiperTTSProvider(binary_path="/nonexistent/path/piper")

    def test_env_var_wins_when_file_exists(self):
        with tempfile.NamedTemporaryFile(prefix="piper-bin-") as bin_file:
            with mock.patch.dict(os.environ, {"AGENT_TTS_PIPER_BIN": bin_file.name}):
                self.assertEqual(self.provider._find_piper_binary(), bin_file.name)

    def test_env_var_set_but_missing_falls_back_to_path(self):
        missing = os.path.join(tempfile.gettempdir(), "definitely-not-installed-piper")
        with mock.patch.dict(os.environ, {"AGENT_TTS_PIPER_BIN": missing}):
            with mock.patch.object(piper_module.shutil, "which", return_value="/usr/bin/piper"):
                self.assertEqual(self.provider._find_piper_binary(), "/usr/bin/piper")

    def test_path_lookup_used_without_env(self):
        with mock.patch.dict(os.environ, {"AGENT_TTS_PIPER_BIN": ""}):
            with mock.patch.object(piper_module.shutil, "which", return_value="/usr/bin/piper"):
                self.assertEqual(self.provider._find_piper_binary(), "/usr/bin/piper")

    def test_no_env_and_not_on_path_returns_none(self):
        with mock.patch.dict(os.environ, {"AGENT_TTS_PIPER_BIN": ""}):
            with mock.patch.object(piper_module.shutil, "which", return_value=None):
                self.assertIsNone(self.provider._find_piper_binary())

    def test_no_host_specific_candidate_paths_remain(self):
        # Audit guard: the provider must stay host-agnostic.
        source = Path(piper_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn("herdr", source)


class TestVoiceCatalog(unittest.TestCase):
    def test_provider_names_stable_order(self):
        self.assertEqual(
            provider_names(), ["edge", "openai", "elevenlabs", "piper", "kokoro"]
        )

    def test_map_backed_providers_derive_from_existing_voice_data(self):
        edge = provider_voices("edge")
        self.assertIn("es-ES-ElviraNeural", edge)
        self.assertIn("en-US-JennyNeural", edge)
        self.assertEqual(edge, sorted(set(edge)))
        self.assertIn("nova", provider_voices("openai"))
        self.assertIn("rachel", provider_voices("elevenlabs"))

    def test_provider_aliases_resolve(self):
        self.assertEqual(provider_voices("eleven"), provider_voices("elevenlabs"))
        self.assertEqual(provider_voices("local"), provider_voices("piper"))

    def test_model_backed_providers_derive_from_the_voice_store(self):
        self.assertIn("ef_dora", provider_voices("kokoro"))
        with tempfile.TemporaryDirectory() as empty_store:
            with mock.patch.dict(os.environ, {"AGENT_TTS_VOICES_DIR": empty_store}):
                self.assertEqual(provider_voices("piper"), [])

    def test_unknown_provider_raises_actionable_error(self):
        with self.assertRaises(ValueError) as ctx:
            provider_voices("skyNET")
        message = str(ctx.exception)
        self.assertIn("skyNET", message)
        self.assertIn("edge", message)  # known providers are listed in the error


if __name__ == "__main__":
    unittest.main()
