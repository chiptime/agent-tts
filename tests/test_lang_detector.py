import unittest
from agent_tts.lang_detector import (
    detect_language,
    resolve_voice_for_language,
    segment_by_language,
)


class TestLangDetector(unittest.TestCase):
    def test_detect_language(self):
        # Spanish text
        es_text = "He analizado el repositorio y los cambios están listos para revisión."
        self.assertEqual(detect_language(es_text), "es")

        # Spanish text with characters
        es_chars = "La función está fallando en producción."
        self.assertEqual(detect_language(es_chars), "es")

        # English text
        en_text = "The quick brown fox jumps over the lazy dog and tests pass with flying colors."
        self.assertEqual(detect_language(en_text), "en")

        en_err = "fatal: remote origin already exists and cannot be created."
        self.assertEqual(detect_language(en_err), "en")

    def test_segment_by_language(self):
        mixed = (
            "He encontrado este problema en la base de datos. "
            "Fatal error: connection refused on port 5432. "
            "Debemos reiniciar el servicio de PostgreSQL inmediatamente."
        )
        segments = segment_by_language(mixed, default_lang="es")
        self.assertTrue(len(segments) >= 2)
        # First segment Spanish
        self.assertEqual(segments[0][0], "es")
        self.assertIn("He encontrado este problema", segments[0][1])

        # Second segment English
        self.assertEqual(segments[1][0], "en")
        self.assertIn("Fatal error", segments[1][1])

        # Third segment Spanish
        if len(segments) >= 3:
            self.assertEqual(segments[2][0], "es")
            self.assertIn("Debemos reiniciar", segments[2][1])

    def test_resolve_voice_for_language(self):
        # Female Spanish -> Female English
        self.assertEqual(resolve_voice_for_language("elvira", "en", "edge"), "en-US-JennyNeural")
        # Male Spanish -> Male English
        self.assertEqual(resolve_voice_for_language("alvaro", "en", "edge"), "en-US-GuyNeural")
        # Female English -> Female Spanish
        self.assertEqual(resolve_voice_for_language("en-US-JennyNeural", "es", "edge"), "es-ES-ElviraNeural")
        # Male English -> Male Spanish
        self.assertEqual(resolve_voice_for_language("en-US-GuyNeural", "es", "edge"), "es-ES-AlvaroNeural")

        # OpenAI
        self.assertEqual(resolve_voice_for_language("nova", "en", "openai"), "nova")
        self.assertEqual(resolve_voice_for_language("onyx", "es", "openai"), "onyx")


if __name__ == "__main__":
    unittest.main()
