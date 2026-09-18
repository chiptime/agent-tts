import unittest

from agent_tts.cleaner import clean_agent_text, extract_last_turn


# Trimmed but representative subset of a REAL OpenCode pane capture
# (/tmp/opencode/opencode_footer_sample.txt): right-sidebar columns glued
# to content, deep-indented sidebar wraps, 'cat -A' border artifacts,
# footer status lines and model/plan banners.
FIXTURE_SAMPLE = (
    "  ┃    M-bM-^TM-^C  head -5                                                                                 141,958 tokens\n"
    "  ┃                     Context                              M-bM-^VM-^H$                                   14% used\n"
    "  ┃    M-bM-^TM-^C                                                                                          $0.00 spent\n"
    "  ┃  Click to expand                                                                                                                             █\n"
    "      + Thought: 16ms                                                                                        ▼ Subagents 1.3.0                    █\n"
    "                                                                                                             • codegraph Connected\n"
    "  ┃                                                                                                           Connection closed\n"
    "                                                                                                               stream\"\n"
    "      Esta sesión soy w4:p3. Vuelco mi propio scrollback para ver el footer exactamente como lo recibe        [✓] Documentation-only task a…      █\n"
    "      El dump revela el problema con claridad.\n"
    "      — líneas pegadas al contenido tras el borde │, y sus continuaciones\n"
    "   ┃  Gentle-Orchestrator · GLM-5.3-Flash Z.AI Coding Plan · max                                             ~/.dotfiles:master\n"
    "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
    "   ■■■■⬝⬝⬝⬝  esc interrupt                                                 142.0K (14%)  ctrl+p commands    • OpenCode 1.18.31\n"
    "  ⠋⠋⠋⠋⠋⠋⠋  esc interrupt    140.9K (14%)   ctrl+p commands    • OpenCode 1.18.31\n"
)

CHROME_MARKERS = (
    "Context", "tokens", "spent", "Connected", "MCP", "LSP",
    "ctrl+", "esc", "OpenCode", "Click to expand", "Thought",
    "interrupt", "codegraph", "Gentle-Orchestrator", "·",
)

BORDER_GLYPHS = ("┃", "█", "▀", "╹", "■", "⬝", "⠋", "━")


class TestFooterNoise(unittest.TestCase):
    def test_real_fixture_chrome_removed_and_content_preserved(self):
        cleaned = clean_agent_text(FIXTURE_SAMPLE)
        for marker in CHROME_MARKERS:
            self.assertNotIn(marker, cleaned, f"chrome marker survived: {marker!r}")
        for glyph in BORDER_GLYPHS:
            self.assertNotIn(glyph, cleaned, f"border glyph survived: {glyph!r}")
        self.assertIn("Esta sesión soy w4:p3", cleaned)
        self.assertIn("El dump revela el problema con claridad.", cleaned)
        self.assertIn("líneas pegadas al contenido", cleaned)

    def test_deep_indented_sidebar_and_wraps_dropped(self):
        text = (
            "Respuesta normal del agente.\n"
            + " " * 45 + "• codegraph Connected\n"
            + "  ┃" + " " * 50 + "Connection closed\n"
            + " " * 45 + "stream\"\n"
            + " " * 45 + "140,880 tokens\n"
        )
        result = extract_last_turn(text)
        self.assertNotIn("Connected", result)
        self.assertNotIn("Connection closed", result)
        self.assertNotIn("stream", result)
        self.assertNotIn("140,880", result)
        self.assertIn("Respuesta normal del agente.", result)

    def test_glued_sidebar_cut_preserves_left_content(self):
        text = (
            '  ┃  {"id":"cli:agent:list","result":{"agents":[{"agent":"opencode"        ▼ MCP\n'
            '  ┃  "agent":"opencode",    14% used                             • notion Connected                   █\n'
            "  ┃  Gentle-Orchestrator · GLM-5.3-Flash Z.AI Coding Plan · max     ~/.dotfiles:master\n"
        )
        cleaned = clean_agent_text(text)
        self.assertIn('"agent":"opencode"', cleaned)
        self.assertNotIn("14% used", cleaned)
        self.assertNotIn("notion", cleaned)
        self.assertNotIn("MCP", cleaned)
        self.assertNotIn("Gentle-Orchestrator", cleaned)
        self.assertNotIn("master", cleaned)

    def test_model_plan_and_keybind_lines_dropped(self):
        text = (
            "❯ resume\n"
            "Gentle-Orchestrator · GLM-5.3-Flash\n"
            "ctrl+p commands\n"
            "El resumen completo de la respuesta del agente aqui.\n"
        )
        result = extract_last_turn(text)
        self.assertEqual(result, "El resumen completo de la respuesta del agente aqui.")

    def test_prose_mentioning_chrome_survives(self):
        text = (
            "$ por que lee el footer?\n"
            "Porque el mensaje Use Ctrl+C para cancelar y el MCP error viajan en la misma linea,\n"
            "y la tabla muestra la columna Status sin filtro previo.\n"
        )
        cleaned = clean_agent_text(text)
        self.assertIn("Use Ctrl+C para cancelar", cleaned)
        self.assertIn("MCP error", cleaned)
        self.assertIn("Status", cleaned)

    def test_plain_scrollback_regression(self):
        scrollback = (
            "$ pytest -q\n"
            "Ran 49 tests in 0.412s\n"
            "OK\n"
            "$ explique el bug del footer\n"
            "El daemon lee el scrollback completo sin filtrar el pie de pantalla.\n"
        )
        result = extract_last_turn(scrollback)
        self.assertNotIn("pytest", result)
        self.assertEqual(
            result,
            "El daemon lee el scrollback completo sin filtrar el pie de pantalla.",
        )


if __name__ == "__main__":
    unittest.main()
