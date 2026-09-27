"""Quantized kokoro variant: env-driven install and provider model resolution.

Covers AGENT_TTS_KOKORO_VARIANT ("fp32" default | "quantized") on both sides:
- voices.install_voice: downloads onnx/model_quantized.onnx (~92 MB) as
  <bundle>/model_quantized.onnx instead of model.onnx and records the variant
  in voice.json.
- KokoroTTSProvider: prefers the quantized export when requested and present,
  falls back to fp32 when absent, and lets an explicit AGENT_TTS_KOKORO_MODEL
  win.
"""

import contextlib
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from agent_tts.providers.kokoro import KokoroTTSProvider
from agent_tts.voices import (
    ENV_KOKORO_VARIANT,
    KOKORO_VARIANT_FP32,
    KOKORO_VARIANT_QUANTIZED,
    VoiceStoreError,
    install_voice,
    list_voices,
)


@contextlib.contextmanager
def _variant_env(value):
    """Pins AGENT_TTS_KOKORO_VARIANT; None removes it (restored on exit)."""
    with mock.patch.dict(os.environ):
        if value is None:
            os.environ.pop(ENV_KOKORO_VARIANT, None)
        else:
            os.environ[ENV_KOKORO_VARIANT] = value
        yield


def _write_source_file(path: str, payload: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(payload)
    return path


def _fake_fetch_from(source_dir: str):
    """Builds a fetch(url, dest) that resolves HF URLs to files under source_dir."""

    def fetch(url: str, dest: str) -> None:
        rel = url.split("/resolve/", 1)[1].split("/", 1)[1]
        source = os.path.join(source_dir, rel)
        if not os.path.isfile(source):
            raise FileNotFoundError(f"fake fetch: missing source for {url} ({source})")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(source, dest)

    return fetch


def _store_voice_names(store_root: str):
    return [entry["name"] for entry in list_voices(store_root=store_root)]


class KokoroVariantTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-tts-kokoro-variant-")
        self.store = os.path.join(self.tmp, "store")
        self.sources = os.path.join(self.tmp, "sources")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Source tree covering BOTH variants (config + voice bins are shared).
        _write_source_file(os.path.join(self.sources, "onnx/model.onnx"), b"kokoro-fp32-weights")
        _write_source_file(
            os.path.join(self.sources, "onnx/model_quantized.onnx"), b"kokoro-quantized-weights"
        )
        _write_source_file(
            os.path.join(self.sources, "config.json"),
            json.dumps({"vocab": {"$": 0, "a": 43}}).encode(),
        )
        for voice in ("ef_dora", "em_alex", "em_santa", "af_heart"):
            _write_source_file(os.path.join(self.sources, f"voices/{voice}.bin"), b"\x00" * 1024)

    def _install(self) -> str:
        return install_voice(
            "kokoro", store_root=self.store, fetch=_fake_fetch_from(self.sources), progress=False
        )

    def _read_manifest(self) -> dict:
        with open(os.path.join(self.store, "kokoro", "voice.json"), encoding="utf-8") as f:
            return json.load(f)


class TestVariantInstall(KokoroVariantTestBase):
    def test_quantized_install_writes_model_quantized_and_manifest_variant(self):
        with _variant_env(KOKORO_VARIANT_QUANTIZED):
            self._install()
        bundle = os.path.join(self.store, "kokoro")
        with open(os.path.join(bundle, "model_quantized.onnx"), "rb") as f:
            self.assertEqual(f.read(), b"kokoro-quantized-weights")
        # The quantized export replaces model.onnx; the rest of the bundle is identical.
        self.assertFalse(os.path.exists(os.path.join(bundle, "model.onnx")))
        self.assertTrue(os.path.isfile(os.path.join(bundle, "config.json")))
        self.assertTrue(os.path.isfile(os.path.join(bundle, "voices", "ef_dora.bin")))
        manifest = self._read_manifest()
        self.assertEqual(manifest["variant"], "quantized")
        self.assertEqual(manifest["provider"], "kokoro")
        self.assertIn("model_quantized.onnx", manifest["files"])

    def test_fp32_default_install_unchanged(self):
        with _variant_env(None):
            self._install()
        bundle = os.path.join(self.store, "kokoro")
        self.assertTrue(os.path.isfile(os.path.join(bundle, "model.onnx")))
        self.assertFalse(os.path.exists(os.path.join(bundle, "model_quantized.onnx")))
        manifest = self._read_manifest()
        self.assertEqual(manifest["variant"], "fp32")
        self.assertIn("model.onnx", manifest["files"])

    def test_invalid_variant_value_raises_actionable_error(self):
        with _variant_env("int8"):
            with self.assertRaises(VoiceStoreError) as ctx:
                self._install()
        self.assertIn(ENV_KOKORO_VARIANT, str(ctx.exception))
        # Nothing was installed.
        self.assertEqual(_store_voice_names(self.store), [])


class TestProviderVariantResolution(KokoroVariantTestBase):
    def _make_bundle(self, *files: str) -> str:
        bundle = os.path.join(self.store, "kokoro")
        os.makedirs(os.path.join(bundle, "voices"), exist_ok=True)
        for name in files:
            _write_source_file(os.path.join(bundle, name), b"weights")
        _write_source_file(os.path.join(bundle, "config.json"), b'{"vocab": {}}')
        return bundle

    def test_prefers_quantized_when_env_set_and_file_exists(self):
        bundle = self._make_bundle("model.onnx", "model_quantized.onnx")
        with _variant_env(KOKORO_VARIANT_QUANTIZED):
            provider = KokoroTTSProvider(store_root=self.store)
        self.assertEqual(provider.model_path, os.path.join(bundle, "model_quantized.onnx"))
        self.assertTrue(provider.is_available())

    def test_falls_back_to_fp32_silently_when_quantized_missing(self):
        bundle = self._make_bundle("model.onnx")
        with _variant_env(KOKORO_VARIANT_QUANTIZED):
            provider = KokoroTTSProvider(store_root=self.store)
        self.assertEqual(provider.model_path, os.path.join(bundle, "model.onnx"))
        self.assertTrue(provider.is_available())

    def test_explicit_model_beats_variant(self):
        self._make_bundle("model.onnx", "model_quantized.onnx")
        explicit = os.path.join(self.tmp, "custom.onnx")
        _write_source_file(explicit, b"custom-weights")
        with _variant_env(KOKORO_VARIANT_QUANTIZED):
            with mock.patch.dict(os.environ, {"AGENT_TTS_KOKORO_MODEL": explicit}):
                provider = KokoroTTSProvider(store_root=self.store)
        self.assertEqual(provider.model_path, explicit)

    def test_no_env_uses_fp32_even_when_quantized_present(self):
        bundle = self._make_bundle("model.onnx", "model_quantized.onnx")
        with _variant_env(None):
            provider = KokoroTTSProvider(store_root=self.store)
        self.assertEqual(provider.model_path, os.path.join(bundle, "model.onnx"))
        self.assertTrue(provider.is_available())


if __name__ == "__main__":
    unittest.main()
