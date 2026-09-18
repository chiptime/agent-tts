import unittest
from agent_tts.cleaner import clean_agent_text, extract_last_turn, strip_ansi


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


if __name__ == "__main__":
    unittest.main()
