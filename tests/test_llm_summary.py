import contextlib
import io
import os
import subprocess
import unittest
from unittest import mock

from agent_tts.cleaner import clean_agent_text
from agent_tts.llm_summary import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_TIMEOUT_SEC,
    MAX_OUTPUT_CHARS,
    ProviderFailedError,
    summarize_with_llm,
)


DIFF_TEXT = (
    "diff --git a/src/audio.py b/src/audio.py\n"
    "--- a/src/audio.py\n"
    "+++ b/src/audio.py\n"
    "@@ -1,3 +1,4 @@\n"
    "+new line\n"
)


def _make_runner(stdout=None, error=None):
    """Builds an injectable fake runner (no subprocess, no network) that records calls."""
    calls = []

    def runner(cmd, prompt, timeout_sec):
        calls.append({"cmd": list(cmd), "prompt": prompt, "timeout": timeout_sec})
        if error is not None:
            raise error
        return stdout

    runner.calls = calls
    return runner


def _which_patch(mapping):
    """Patches shutil.which as seen from llm_summary: mapping[name] -> path or None."""
    return mock.patch(
        "agent_tts.llm_summary.shutil.which",
        side_effect=lambda name: mapping.get(name),
    )


class TestLLMSummary(unittest.TestCase):
    def test_claude_present_is_used_first(self):
        runner = _make_runner(stdout="Una frase ejecutiva concisa.")
        with _which_patch({"claude": "/usr/bin/claude"}):
            res = summarize_with_llm(DIFF_TEXT, runner=runner)
        self.assertEqual(res, "Una frase ejecutiva concisa.")
        self.assertEqual(len(runner.calls), 1)
        call = runner.calls[0]
        # Prompt travels via stdin (argv never carries the text: no injection surface).
        self.assertEqual(call["cmd"], ["claude", "-p"])
        self.assertIn(DIFF_TEXT.strip(), call["prompt"])
        self.assertLessEqual(call["timeout"], DEFAULT_TIMEOUT_SEC)

    def test_claude_absent_codex_used(self):
        runner = _make_runner(stdout="Codex resume en una frase.")
        with _which_patch({"codex": "/usr/bin/codex"}):
            res = summarize_with_llm(DIFF_TEXT, runner=runner)
        self.assertEqual(res, "Codex resume en una frase.")
        self.assertEqual(runner.calls[0]["cmd"], ["codex", "exec"])

    def test_only_ollama_default_model(self):
        runner = _make_runner(stdout="Resumen por ollama.")
        with _which_patch({"ollama": "/usr/bin/ollama"}):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AGENT_TTS_OLLAMA_MODEL", None)
                res = summarize_with_llm(DIFF_TEXT, runner=runner)
        self.assertEqual(res, "Resumen por ollama.")
        self.assertEqual(runner.calls[0]["cmd"], ["ollama", "run", DEFAULT_OLLAMA_MODEL])

    def test_ollama_model_env_override(self):
        runner = _make_runner(stdout="Resumen por ollama.")
        with _which_patch({"ollama": "/usr/bin/ollama"}):
            with mock.patch.dict(os.environ, {"AGENT_TTS_OLLAMA_MODEL": "llama3.2:1b"}):
                res = summarize_with_llm(DIFF_TEXT, runner=runner)
        self.assertEqual(res, "Resumen por ollama.")
        self.assertEqual(runner.calls[0]["cmd"], ["ollama", "run", "llama3.2:1b"])

    def test_ollama_model_explicit_param_wins_over_env(self):
        runner = _make_runner(stdout="Resumen por ollama.")
        with _which_patch({"ollama": "/usr/bin/ollama"}):
            with mock.patch.dict(os.environ, {"AGENT_TTS_OLLAMA_MODEL": "llama3.2:1b"}):
                summarize_with_llm(DIFF_TEXT, runner=runner, ollama_model="qwen3:4b")
        self.assertEqual(runner.calls[0]["cmd"], ["ollama", "run", "qwen3:4b"])

    def test_timeout_returns_none_and_logs(self):
        runner = _make_runner(error=subprocess.TimeoutExpired(cmd=["claude", "-p"], timeout=10))
        stderr = io.StringIO()
        with _which_patch({"claude": "/usr/bin/claude"}):
            with contextlib.redirect_stderr(stderr):
                res = summarize_with_llm(DIFF_TEXT, runner=runner)
        self.assertIsNone(res)
        self.assertIn("timed out", stderr.getvalue())

    def test_nonzero_exit_returns_none(self):
        runner = _make_runner(error=ProviderFailedError("exit code 1: boom"))
        with _which_patch({"claude": "/usr/bin/claude"}):
            res = summarize_with_llm(DIFF_TEXT, runner=runner)
        self.assertIsNone(res)

    def test_generic_runner_exception_returns_none(self):
        runner = _make_runner(error=FileNotFoundError("binary vanished"))
        with _which_patch({"claude": "/usr/bin/claude"}):
            res = summarize_with_llm(DIFF_TEXT, runner=runner)
        self.assertIsNone(res)

    def test_empty_output_returns_none(self):
        runner = _make_runner(stdout="   \n\t  ")
        stderr = io.StringIO()
        with _which_patch({"claude": "/usr/bin/claude"}):
            with contextlib.redirect_stderr(stderr):
                res = summarize_with_llm(DIFF_TEXT, runner=runner)
        self.assertIsNone(res)
        self.assertIn("empty output", stderr.getvalue())

    def test_oversized_output_returns_none(self):
        runner = _make_runner(stdout="palabra " * 200)  # > MAX_OUTPUT_CHARS
        stderr = io.StringIO()
        with _which_patch({"claude": "/usr/bin/claude"}):
            with contextlib.redirect_stderr(stderr):
                res = summarize_with_llm(DIFF_TEXT, runner=runner)
        self.assertIsNone(res)
        self.assertGreater(len("palabra " * 200), MAX_OUTPUT_CHARS)
        self.assertIn("output too long", stderr.getvalue())

    def test_no_providers_returns_none_and_logs_fallback(self):
        runner = _make_runner()
        stderr = io.StringIO()
        with _which_patch({}):
            with contextlib.redirect_stderr(stderr):
                res = summarize_with_llm(DIFF_TEXT, runner=runner)
        self.assertIsNone(res)
        self.assertEqual(runner.calls, [])
        self.assertIn("falling back to offline --tldr", stderr.getvalue())

    def test_failure_falls_through_to_next_provider(self):
        calls = []

        def runner(cmd, prompt, timeout_sec):
            calls.append(list(cmd))
            if cmd[0] == "claude":
                raise ProviderFailedError("exit code 1")
            return "Codex salva el resumen."

        stderr = io.StringIO()
        with _which_patch({"claude": "/usr/bin/claude", "codex": "/usr/bin/codex"}):
            with contextlib.redirect_stderr(stderr):
                res = summarize_with_llm(DIFF_TEXT, runner=runner)
        self.assertEqual(res, "Codex salva el resumen.")
        # First available wins, failures fall through in priority order.
        self.assertEqual(calls[0], ["claude", "-p"])
        self.assertEqual(calls[1], ["codex", "exec"])
        self.assertIn("claude failed", stderr.getvalue())

    def test_empty_input_returns_none_without_invoking_runner(self):
        runner = _make_runner(stdout="nunca se llama")
        with _which_patch({"claude": "/usr/bin/claude"}):
            res = summarize_with_llm("   \n\t", runner=runner)
        self.assertIsNone(res)
        self.assertEqual(runner.calls, [])


class TestLLMSummaryCleanerIntegration(unittest.TestCase):
    def test_unresolvable_cli_falls_back_to_tldr_result(self):
        stderr = io.StringIO()
        with _which_patch({}):
            with contextlib.redirect_stderr(stderr):
                res = clean_agent_text(DIFF_TEXT, summarize=True, llm_summary=True)
        expected = clean_agent_text(DIFF_TEXT, summarize=True)
        self.assertEqual(res, expected)
        self.assertIn("Diff de Git: 1 archivo con cambios", res)
        self.assertIn("falling back to offline --tldr", stderr.getvalue())

    def test_llm_result_wins_over_heuristic(self):
        runner = _make_runner(stdout="Se añadió una línea nueva al módulo de audio.")
        with _which_patch({"claude": "/usr/bin/claude"}):
            with mock.patch("agent_tts.llm_summary._subprocess_runner", runner):
                res = clean_agent_text(DIFF_TEXT, summarize=True, llm_summary=True)
        self.assertEqual(res, "Se añadió una línea nueva al módulo de audio.")
        self.assertNotIn("Diff de Git:", res)

    def test_tldr_only_does_not_reach_llm(self):
        runner = _make_runner()
        with _which_patch({"claude": "/usr/bin/claude"}):
            with mock.patch("agent_tts.llm_summary._subprocess_runner", runner):
                res = clean_agent_text(DIFF_TEXT, summarize=True)
        self.assertIn("Diff de Git:", res)
        self.assertEqual(runner.calls, [])

    def test_both_flags_llm_wins_via_cli_semantics(self):
        # Mirrors cli.py: summarize=args.summarize or args.llm_summary, llm_summary=args.llm_summary.
        runner = _make_runner(stdout="Frase ejecutiva del LLM.")
        with _which_patch({"claude": "/usr/bin/claude"}):
            with mock.patch("agent_tts.llm_summary._subprocess_runner", runner):
                res = clean_agent_text(
                    DIFF_TEXT,
                    summarize=True,  # --tldr also given
                    llm_summary=True,  # --llm-summary wins
                )
        self.assertEqual(res, "Frase ejecutiva del LLM.")


if __name__ == "__main__":
    unittest.main()
