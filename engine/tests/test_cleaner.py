import json
import os
import tempfile
import unittest
from unittest import mock

from agent_tts.cleaner import clean_agent_text, extract_last_turn, load_user_lexicon, strip_ansi


class TestCleaner(unittest.TestCase):
    def test_strip_ansi(self):
        text = "\x1b[31mError:\x1b[0m Failed to connect"
        self.assertEqual(strip_ansi(text), "Error: Failed to connect")

    def test_clean_agent_text_tokens_and_code(self):
        raw = (
            "```python\n"
            "def foo():\n"
            "    return 42\n"
            "```\n"
            "Tokens: 14.2k in | 520 out\n"
            "Cost: $0.02\n"
            "Elapsed: 1.2s\n"
        )
        cleaned = clean_agent_text(raw)
        self.assertNotIn("Tokens:", cleaned)
        self.assertNotIn("Cost:", cleaned)
        self.assertNotIn("Elapsed:", cleaned)
        self.assertIn("[bloque de código omitido]", cleaned)

    def test_clean_agent_text_table_conversion(self):
        table = (
            "┌──────────┬────────┐\n"
            "│ Feature  │ Status │\n"
            "├──────────┼────────┤\n"
            "│ Seek     │ Active │\n"
            "└──────────┴────────┘\n"
        )
        cleaned = clean_agent_text(table)
        self.assertNotIn("┌", cleaned)
        self.assertNotIn("└", cleaned)
        self.assertIn("Feature — Status", cleaned)
        self.assertIn("Seek — Active", cleaned)

    def test_extract_last_turn(self):
        scrollback = (
            "❯ git status\n"
            "On branch main\n"
            "nothing to commit\n"
            "❯ Explain the architecture\n"
            "The architecture consists of three modular layers.\n"
        )
        last_turn = extract_last_turn(scrollback)
        self.assertNotIn("git status", last_turn)
        self.assertIn("The architecture consists of three modular layers.", last_turn)

    def test_technical_lexicon_and_units(self):
        raw = "El endpoint de la API con JWT y K8s tardó 150ms usando 2.5MB en v1.2.3."
        cleaned = clean_agent_text(raw)
        self.assertIn("A P I", cleaned)
        self.assertIn("J W T", cleaned)
        self.assertIn("Kubernetes", cleaned)
        self.assertIn("150 milisegundos", cleaned)
        self.assertIn("2.5 megabytes", cleaned)
        self.assertIn("versión 1.2.3", cleaned)

    def test_clean_agent_text_with_summarize_flag(self):
        diff = (
            "diff --git a/src/audio.py b/src/audio.py\n"
            "--- a/src/audio.py\n"
            "+++ b/src/audio.py\n"
            "@@ -1,3 +1,4 @@\n"
            "+new line\n"
        )
        res = clean_agent_text(diff, summarize=True)
        self.assertIn("Diff de Git: 1 archivo con cambios", res)

    def test_currencies_and_abbreviations(self):
        raw = "El precio es $45.20 (o 15€ aprox., p. ej. en la pág. 12). El Dr. pagó 1€ y $1."
        cleaned = clean_agent_text(raw)
        self.assertIn("45 dólares con 20 centavos", cleaned)
        self.assertIn("15 euros", cleaned)
        self.assertIn("aproximadamente", cleaned)
        self.assertIn("por ejemplo", cleaned)
        self.assertIn("página 12", cleaned)
        self.assertIn("doctor", cleaned)
        self.assertIn("1 euro", cleaned)
        self.assertIn("1 dólar", cleaned)

    def test_currencies_and_abbreviations_english(self):
        raw = "Total cost is $45.20 (approx. 10€, e.g. see p. 5). Dr. Smith paid 1€ and $1."
        cleaned = clean_agent_text(raw, lang="en")
        self.assertIn("45 dollars and 20 cents", cleaned)
        self.assertIn("10 euros", cleaned)
        self.assertIn("approximately", cleaned)
        self.assertIn("for example", cleaned)
        self.assertIn("page 5", cleaned)
        self.assertIn("Doctor", cleaned)
        self.assertIn("1 euro", cleaned)
        self.assertIn("1 dollar", cleaned)

    def test_pre_extracted_skips_turn_extraction(self):
        # Contract: with pre_extracted=True the input is already the exact
        # message to speak, so scrollback turn extraction is skipped and even
        # terminal-chrome-looking lines survive, while message cleaning still
        # runs. Without the flag, the banner is chrome and gets filtered.
        chrome_banner = "OpenCode · GLM Flash · Coding Plan\nLa compilación terminó correctamente."

        pre = clean_agent_text(chrome_banner, pre_extracted=True)
        self.assertIn("OpenCode · GLM Flash · Coding Plan", pre)
        self.assertIn("La compilación terminó correctamente.", pre)

        extracted = clean_agent_text(chrome_banner)
        self.assertNotIn("GLM Flash", extracted)
        self.assertIn("La compilación terminó correctamente.", extracted)

    def test_redaction_runs_before_summarizer(self):
        # Contract: secrets are redacted BEFORE the TL;DR branch echoes input
        # sentences, so a spoken summary can never leak raw credentials.
        secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        prose = (
            f"Configuré el cliente con el token {secret} en el entorno de producción. "
            "Tras revisar los logs el despliegue terminó correctamente y sin errores visibles."
        )
        res = clean_agent_text(prose, summarize=True)
        self.assertNotIn(secret, res)
        self.assertNotIn("ghp_", res)
        # The summary is still produced (both sentences fit the heuristic budget).
        self.assertIn("Configuré el cliente", res)


class TestUserLexicon(unittest.TestCase):
    """Chain: AGENT_TTS_LEXICON env > ~/.config/agent-tts/lexicon.json > defaults."""

    def test_env_var_lexicon_wins(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"k8s": "kates"}, f)
            path = f.name
        self.addCleanup(os.remove, path)
        with mock.patch.dict(os.environ, {"AGENT_TTS_LEXICON": path}):
            self.assertEqual(load_user_lexicon(), {"k8s": "kates"})

    def test_chain_never_probes_host_specific_paths(self):
        probed = []

        def fake_isfile(path):
            probed.append(path)
            return False

        with mock.patch.dict(os.environ, {"AGENT_TTS_LEXICON": ""}):
            with mock.patch.object(os.path, "isfile", fake_isfile):
                self.assertEqual(load_user_lexicon(), {})
        self.assertTrue(probed)  # the fallback chain was actually walked
        for path in probed:
            self.assertNotIn("herdr", path)

    def test_no_user_lexicon_falls_back_to_builtin_defaults(self):
        with mock.patch.dict(os.environ, {"AGENT_TTS_LEXICON": ""}):
            with mock.patch.object(os.path, "isfile", return_value=False):
                self.assertEqual(load_user_lexicon(), {})


if __name__ == "__main__":
    unittest.main()
