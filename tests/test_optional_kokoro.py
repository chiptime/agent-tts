"""Optional-dependency contract: kokoro/voices must never burden a default install.

Verifies that:
- importing ``agent_tts`` never eagerly pulls the kokoro module or its heavy
  extras (onnxruntime / numpy / phonemizer),
- provider listing, stream gating, and the default (edge) path keep working
  when every optional dependency raises ImportError,
- ``--provider kokoro`` fails with a clean English error that names the
  ``agent-tts[kokoro]`` extra (exit code non-zero),
- the voice store (download/list) stays stdlib-only.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from agent_tts.providers import get_provider
from agent_tts.providers.base import TTSProvider

OPTIONAL_MODULES = ("onnxruntime", "numpy", "phonemizer")


def _shim_env_modules():
    """sys.modules patch that makes every optional import raise ImportError."""
    return mock.patch.dict(sys.modules, {name: None for name in OPTIONAL_MODULES})


def _make_shim_dir() -> str:
    """Creates a PYTHONPATH dir of fake optional modules that raise on import."""
    tmp = tempfile.mkdtemp(prefix="agent-tts-noextras-")
    for name in OPTIONAL_MODULES:
        with open(os.path.join(tmp, f"{name}.py"), "w", encoding="utf-8") as f:
            f.write(f"raise ImportError('simulated missing {name}')\n")
    return tmp


def _isolated_env(extra: dict) -> dict:
    """Process env for CLI subprocess tests: shims + transient files in tmp."""
    shim = _make_shim_dir()
    env = dict(os.environ)
    env["PYTHONPATH"] = shim + os.pathsep + env.get("PYTHONPATH", "")
    env["AGENT_TTS_LOCK_FILE"] = os.path.join(shim, "test.lock")
    env["AGENT_TTS_PID_FILE"] = os.path.join(shim, "test.pid")
    env["AGENT_TTS_SOCKET"] = os.path.join(shim, "test.sock")
    env.update(extra)
    return env


class TestImportPurity(unittest.TestCase):
    def test_importing_package_never_pulls_kokoro_or_onnx(self):
        # Even with the heavy extras INSTALLED (this venv has them), the
        # package import graph must not eagerly import any of them.
        code = (
            "import sys, agent_tts, agent_tts.cli;"
            "bad = sorted(m for m in sys.modules if 'onnx' in m or m.endswith('.kokoro'));"
            "print(bad);"
            "assert not bad, f'eager optional imports: {bad}'"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "[]")

    def test_provider_listing_and_gating_work_without_optional_deps(self):
        from agent_tts.cli import use_pipelined_stream

        with _shim_env_modules():
            self.assertIsInstance(get_provider("edge"), TTSProvider)
            self.assertIsInstance(get_provider("piper"), TTSProvider)
            self.assertTrue(use_pipelined_stream("edge", "auto", False, None, False, 400))
            # Registration itself is stdlib-only: instantiating the class must
            # not require onnxruntime (that is a synthesis-time check).
            self.assertIsInstance(get_provider("kokoro"), TTSProvider)


class TestLazyRegistry(unittest.TestCase):
    def test_kokoro_class_resolves_lazily_from_both_packages(self):
        import agent_tts
        import agent_tts.providers.kokoro as kokoro_mod
        import agent_tts.providers as providers

        self.assertIs(providers.KokoroTTSProvider, kokoro_mod.KokoroTTSProvider)
        self.assertIs(agent_tts.KokoroTTSProvider, kokoro_mod.KokoroTTSProvider)

    def test_unknown_lazy_attribute_raises_attribute_error(self):
        import agent_tts
        import agent_tts.providers as providers

        with self.assertRaises(AttributeError):
            providers.DefinitelyNotAThing
        with self.assertRaises(AttributeError):
            agent_tts.DefinitelyNotAThing

    def test_unimportable_kokoro_module_raises_clean_registry_error(self):
        with mock.patch.dict(sys.modules, {"agent_tts.providers.kokoro": None}):
            with self.assertRaises(RuntimeError) as ctx:
                get_provider("kokoro")
        self.assertIn("agent-tts[kokoro]", str(ctx.exception))


class TestKokoroSynthesisWithoutExtras(unittest.TestCase):
    def test_missing_onnxruntime_error_names_the_extra(self):
        from agent_tts.providers import KokoroTTSProvider

        tmp = tempfile.mkdtemp(prefix="agent-tts-kokoro-extra-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        model = os.path.join(tmp, "model.onnx")
        with open(model, "wb") as f:
            f.write(b"fake-kokoro-onnx")
        prov = KokoroTTSProvider(model_path=model, voices_dir=tmp, config_path=os.path.join(tmp, "cfg.json"))
        self.assertTrue(prov.is_available())

        with _shim_env_modules():
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(prov.synthesize("hola", "ef_dora", "+0%"))
        message = str(ctx.exception)
        self.assertIn("agent-tts[kokoro]", message)
        # The concrete dependency is still named for pip-level debugging.
        self.assertIn("pip install onnxruntime", message)


class TestVoiceStoreNeedsNoExtras(unittest.TestCase):
    def test_voice_store_is_stdlib_only(self):
        from agent_tts import voices

        tmp = tempfile.mkdtemp(prefix="agent-tts-voices-pure-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with _shim_env_modules():
            self.assertEqual(voices.list_voices(store_root=tmp), [])
            self.assertTrue(callable(voices.install_voice))
            self.assertTrue(callable(voices.handle_voice_command))


class TestCliWithoutExtras(unittest.TestCase):
    def test_default_cli_entrypoint_works_without_optional_deps(self):
        # Empty input exits cleanly (0) after touching the whole import graph
        # and argument pipeline — no network, no audio device.
        env = _isolated_env({})
        proc = subprocess.run(
            [sys.executable, "-m", "agent_tts.cli"],
            input="",
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Only runpy's pre-existing "-m + package imports cli" RuntimeWarning
        # may appear; no optional-dependency or import error is allowed.
        self.assertNotIn("Error", proc.stderr)

    def test_voice_list_works_without_optional_deps(self):
        store = tempfile.mkdtemp(prefix="agent-tts-voices-cli-")
        self.addCleanup(shutil.rmtree, store, ignore_errors=True)
        env = _isolated_env({"AGENT_TTS_VOICES_DIR": store})
        proc = subprocess.run(
            [sys.executable, "-m", "agent_tts.cli", "voice", "list"],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("No voices installed", proc.stdout)

    def test_cli_kokoro_fails_cleanly_naming_the_extra(self):
        env = _isolated_env({})
        model = os.path.join(os.path.dirname(env["AGENT_TTS_PID_FILE"]), "model.onnx")
        with open(model, "wb") as f:
            f.write(b"fake-kokoro-onnx")
        env["AGENT_TTS_KOKORO_MODEL"] = model
        env["AGENT_TTS_KOKORO_VOICES_DIR"] = os.path.dirname(model)
        env["AGENT_TTS_KOKORO_CONFIG"] = os.path.join(os.path.dirname(model), "config.json")
        proc = subprocess.run(
            [sys.executable, "-m", "agent_tts.cli", "--no-play", "--provider", "kokoro", "hola mundo"],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("agent-tts[kokoro]", proc.stderr)


if __name__ == "__main__":
    unittest.main()
