import unittest
from agent_tts.providers.base import parse_rate_to_multiplier
from agent_tts.providers import (
    EdgeTTSProvider,
    ElevenLabsTTSProvider,
    OpenAITTSProvider,
    PiperTTSProvider,
    get_provider,
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


if __name__ == "__main__":
    unittest.main()
