import unittest
from agent_tts.summarizer import summarize


class TestSummarizer(unittest.TestCase):
    def test_summarize_git_diff(self):
        diff_text = (
            "diff --git a/src/audio.py b/src/audio.py\n"
            "--- a/src/audio.py\n"
            "+++ b/src/audio.py\n"
            "@@ -1,5 +1,6 @@\n"
            "+import time\n"
            "+import miniaudio\n"
            "-import os\n"
            "diff --git a/src/cli.py b/src/cli.py\n"
            "--- a/src/cli.py\n"
            "+++ b/src/cli.py\n"
            "@@ -10,3 +10,4 @@\n"
            "+def new_func(): pass\n"
        )
        res = summarize(diff_text)
        self.assertIn("Diff de Git:", res)
        self.assertIn("2 archivos con cambios", res)
        self.assertIn("src/audio.py y src/cli.py", res)
        self.assertIn("3 líneas añadidas", res)
        self.assertIn("1 eliminadas", res)

    def test_summarize_git_status(self):
        status_clean = "On branch main\nnothing to commit, working tree clean"
        res_clean = summarize(status_clean)
        self.assertIn("directorio de trabajo limpio", res_clean)

        status_dirty = (
            "On branch feature/voice\n"
            "Changes not staged for commit:\n"
            "\tmodified:   src/audio.py\n"
            "\tmodified:   src/cli.py\n"
            "\n"
            "Untracked files:\n"
            "\tsrc/summarizer.py\n"
        )
        res_dirty = summarize(status_dirty)
        self.assertIn("feature/voice", res_dirty)
        self.assertIn("2 modificados", res_dirty)
        self.assertIn("1 sin seguimiento", res_dirty)

    def test_summarize_test_results(self):
        # Unittest
        unittest_log = "Ran 16 tests in 0.404s\n\nOK"
        self.assertIn("16 pruebas pasadas en 0.404 segundos", summarize(unittest_log))

        # Pytest
        pytest_log = "======= 42 passed, 2 failed in 5.12s ======="
        self.assertIn("42 passed, 2 failed", summarize(pytest_log))

        # Jest
        jest_log = "Tests:       1 failed, 15 passed, 16 total\nSnapshots:   0 total"
        self.assertIn("1 failed, 15 passed, 16 total", summarize(jest_log))

        # Cargo
        cargo_log = "test result: ok. 8 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"
        self.assertIn("Cargo test (ok)", summarize(cargo_log))

    def test_summarize_traceback(self):
        tb = (
            'Traceback (most recent call last):\n'
            '  File "/home/user/app/main.py", line 42, in <module>\n'
            '    run_task()\n'
            '  File "/home/user/app/worker.py", line 105, in run_task\n'
            '    raise ValueError("Invalid configuration provided")\n'
            'ValueError: Invalid configuration provided\n'
        )
        res = summarize(tb)
        self.assertIn("Excepción detectada", res)
        self.assertIn("worker.py línea 105", res)
        self.assertIn("ValueError: Invalid configuration provided", res)

    def test_summarize_compiler_error(self):
        rust_err = (
            "error[E0308]: mismatched types\n"
            "  --> src/main.rs:14:5\n"
            "   |\n"
            "14 |     42\n"
            "   |     ^^ expected `&str`, found `i32`\n"
        )
        res = summarize(rust_err)
        self.assertIn("Error de compilación en main.rs línea 14", res)
        self.assertIn("mismatched types", res)

    def test_summarize_verbose_prose(self):
        prose = (
            "He terminado de implementar la nueva arquitectura modular para el reproductor de audio. "
            "Primero analizamos los requisitos y determinamos que la separación era necesaria. "
            "Luego se crearon las pruebas unitarias cubriendo casos extremos. "
            "También se documentaron todas las funciones públicas y privadas con ejemplos claros. "
            "El siguiente paso es desplegar y verificar el funcionamiento en el entorno de pruebas."
        )
        res = summarize(prose, max_sentences=2)
        self.assertIn("He terminado de implementar la nueva arquitectura modular para el reproductor de audio.", res)
        self.assertIn("El siguiente paso es desplegar y verificar el funcionamiento en el entorno de pruebas.", res)
        # Should omit middle sentences
        self.assertNotIn("Luego se crearon las pruebas unitarias", res)


if __name__ == "__main__":
    unittest.main()
