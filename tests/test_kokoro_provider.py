import asyncio
import os
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

from agent_tts.providers import KokoroTTSProvider, get_provider

try:
    import numpy  # noqa: F401  (required by the real inference input builder)

    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False


SAMPLES = 32  # fake float32 samples produced by the fake session


def _fake_audio_f32() -> bytes:
    """Deterministic float32 PCM bytes (small sine-ish ramp)."""
    return struct.pack(f"<{SAMPLES}f", *([0.1 * (i % 10) - 0.4 for i in range(SAMPLES)]))


class FakeSession:
    """Stand-in for onnxruntime.InferenceSession recording the run inputs."""

    def __init__(self):
        self.calls = []

    def run(self, output_names, input_feed):
        self.calls.append(dict(input_feed))
        assert set(input_feed) == {"tokens", "style", "speed"}
        tokens = input_feed["tokens"]
        assert tokens.dtype.name == "int64" and tokens.shape[0] == 1
        style = input_feed["style"]
        assert style.dtype.name == "float32" and tuple(style.shape) == (1, 256)
        return [_fake_audio_f32()]


def _make_store(tmp: str, vocab: dict = None) -> str:
    """Creates a minimal kokoro bundle layout: model.onnx + config + one voice."""
    bundle = os.path.join(tmp, "kokoro")
    os.makedirs(os.path.join(bundle, "voices"))
    with open(os.path.join(bundle, "model.onnx"), "wb") as f:
        f.write(b"fake-kokoro-onnx")
    vocab = vocab or {"$": 0, "h": 50, "o": 57, "l": 54, "a": 43, "ɐ": 32}
    with open(os.path.join(bundle, "config.json"), "w", encoding="utf-8") as f:
        import json

        json.dump({"vocab": vocab}, f)
    with open(os.path.join(bundle, "voices", "ef_dora.bin"), "wb") as f:
        f.write(b"\x00\x00\x80?\x00\x00\x00\x00" * 128)  # 256 float32 values
    return bundle


class TestKokoroVoiceResolution(unittest.TestCase):
    def test_unknown_and_edge_defaults_map_to_es_default(self):
        prov = KokoroTTSProvider()
        self.assertEqual(prov.resolve_voice(""), "ef_dora")
        self.assertEqual(prov.resolve_voice("es-ES-ElviraNeural"), "ef_dora")
        self.assertEqual(prov.resolve_voice("nova"), "ef_dora")

    def test_kokoro_shaped_names_are_kept(self):
        prov = KokoroTTSProvider()
        self.assertEqual(prov.resolve_voice("af_heart"), "af_heart")
        self.assertEqual(prov.resolve_voice("em_alex"), "em_alex")

    def test_env_override_selects_voice(self):
        with mock.patch.dict(os.environ, {"AGENT_TTS_KOKORO_VOICE": "em_alex"}):
            self.assertEqual(KokoroTTSProvider().resolve_voice(""), "em_alex")

    def test_env_model_override(self):
        with mock.patch.dict(os.environ, {"AGENT_TTS_KOKORO_MODEL": "/custom/model.onnx"}):
            self.assertEqual(KokoroTTSProvider().model_path, "/custom/model.onnx")

    def test_get_provider_returns_kokoro(self):
        self.assertIsInstance(get_provider("kokoro"), KokoroTTSProvider)

    def test_streaming_not_supported(self):
        self.assertFalse(KokoroTTSProvider.supports_stream)
        with self.assertRaises(NotImplementedError):
            KokoroTTSProvider().synthesize_stream("hola", "ef_dora")


class TestKokoroLazyDependencies(unittest.TestCase):
    def test_missing_model_raises_install_hint(self):
        prov = KokoroTTSProvider(model_path="/nonexistent/kokoro.onnx")
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(prov.synthesize("hola", "ef_dora", "+0%"))
        self.assertIn("agent-tts voice install kokoro", str(ctx.exception))

    def test_missing_onnxruntime_raises_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _make_store(tmp)
            # No session_factory injected: the real lazy import path runs.
            prov = KokoroTTSProvider(store_root=tmp)
            self.assertTrue(prov.is_available())
            self.assertEqual(prov.model_path, os.path.join(bundle, "model.onnx"))
            # sys.modules[name] = None makes `import onnxruntime` raise ImportError.
            with mock.patch.dict(sys.modules, {"onnxruntime": None}):
                with self.assertRaises(RuntimeError) as ctx:
                    asyncio.run(prov.synthesize("hola", "ef_dora", "+0%"))
            self.assertIn("pip install onnxruntime", str(ctx.exception))

    def test_missing_espeak_and_phonemizer_raises_actionable_error(self):
        prov = KokoroTTSProvider()
        with mock.patch.dict(sys.modules, {"phonemizer": None}), mock.patch(
            "agent_tts.providers.kokoro.shutil.which", return_value=None
        ):
            with self.assertRaises(RuntimeError) as ctx:
                prov._default_phonemize("hola", "ef_dora")
        self.assertIn("espeak-ng", str(ctx.exception))

    def test_espeak_binary_phonemizes_via_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "fake-espeak-ng")
            with open(script, "w") as f:
                f.write("#!/bin/sh\ncat > /dev/null\necho 'holɐ'\n")
            os.chmod(script, 0o755)
            prov = KokoroTTSProvider()
            with mock.patch.dict(sys.modules, {"phonemizer": None}), mock.patch(
                "agent_tts.providers.kokoro.shutil.which", return_value=script
            ):
                self.assertEqual(prov._default_phonemize("hola mundo", "ef_dora"), "holɐ")

    def test_espeak_voice_mapping(self):
        prov = KokoroTTSProvider()
        self.assertEqual(prov._espeak_voice("ef_dora"), "es")
        self.assertEqual(prov._espeak_voice("em_alex"), "es")
        self.assertEqual(prov._espeak_voice("af_heart"), "en-us")
        self.assertEqual(prov._espeak_voice("x_mystery"), "en-us")

    def test_missing_voice_bin_raises_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_store(tmp)
            prov = KokoroTTSProvider(
                store_root=tmp,
                session_factory=lambda p: FakeSession(),
                phonemize_fn=lambda text, voice: "hola",
            )
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(prov.synthesize("hola", "af_heart", "+0%"))
            self.assertIn("voice install kokoro", str(ctx.exception))


@unittest.skipUnless(NUMPY_AVAILABLE, "numpy required for inference input building")
class TestKokoroSynthesis(unittest.TestCase):
    def test_happy_path_produces_wav_with_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_store(tmp)
            session = FakeSession()
            prov = KokoroTTSProvider(
                store_root=tmp,
                session_factory=lambda p: session,
                phonemize_fn=lambda text, voice: "hola",
            )
            self.assertTrue(prov.is_available())
            result = asyncio.run(prov.synthesize("Hola mundo", "ef_dora", "+0%"))

            # WAV container: RIFF header, 16-bit mono 24 kHz.
            self.assertTrue(bytes(result).startswith(b"RIFF"))
            self.assertEqual(struct.unpack("<I", result[24:28])[0], 24000)
            self.assertGreater(len(result), 44)
            self.assertEqual(result.boundaries.sentences[0].text, "Hola mundo")

            # Inference inputs: padded token ids, 1x256 style, speed scalar.
            self.assertEqual(len(session.calls), 1)
            feed = session.calls[0]
            tokens = feed["tokens"].tolist()[0]
            self.assertEqual(tokens, [0, 50, 57, 54, 43, 0])  # pad + hola + pad
            self.assertEqual(float(feed["speed"]), 1.0)

    def test_rate_maps_to_speed_multiplier(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_store(tmp)
            session = FakeSession()
            prov = KokoroTTSProvider(
                store_root=tmp,
                session_factory=lambda p: session,
                phonemize_fn=lambda text, voice: "hola",
            )
            asyncio.run(prov.synthesize("Hola", "ef_dora", "+20%"))
            self.assertAlmostEqual(float(session.calls[0]["speed"]), 1.2)

    def test_stop_checker_discards_synthesis(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_store(tmp)
            prov = KokoroTTSProvider(
                store_root=tmp,
                session_factory=lambda p: FakeSession(),
                phonemize_fn=lambda text, voice: "hola",
            )
            result = asyncio.run(prov.synthesize("Hola", "ef_dora", "+0%", stop_checker=lambda: True))
            self.assertEqual(bytes(result), b"")

    def test_empty_text_short_circuits(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_store(tmp)
            prov = KokoroTTSProvider(
                store_root=tmp,
                session_factory=lambda p: FakeSession(),
                phonemize_fn=lambda text, voice: "hola",
            )
            result = asyncio.run(prov.synthesize("   ", "ef_dora", "+0%"))
            self.assertEqual(bytes(result), b"")


class TestKokoroStoreIntegration(unittest.TestCase):
    def test_installed_store_voice_is_available(self):
        from agent_tts.voices import install_voice

        tmp = tempfile.mkdtemp(prefix="agent-tts-kokoro-itest-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        sources = os.path.join(tmp, "sources")

        def write(rel: str, payload: bytes):
            path = os.path.join(sources, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(payload)

        write("onnx/model.onnx", b"kokoro-weights")
        write("config.json", b'{"vocab": {"$": 0}}')
        for voice in ("ef_dora", "em_alex", "em_santa", "af_heart"):
            write(f"voices/{voice}.bin", b"\x00" * 1024)

        def fetch(url: str, dest: str) -> None:
            rel = url.split("/resolve/", 1)[1].split("/", 1)[1]
            shutil.copyfile(os.path.join(sources, rel), dest)

        install_voice("kokoro", store_root=os.path.join(tmp, "store"), fetch=fetch, progress=False)
        prov = KokoroTTSProvider(store_root=os.path.join(tmp, "store"))
        self.assertTrue(prov.is_available())
        self.assertTrue(os.path.isfile(os.path.join(prov.voices_dir, "ef_dora.bin")))


if __name__ == "__main__":
    unittest.main()
