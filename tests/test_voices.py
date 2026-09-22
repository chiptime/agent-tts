import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

from agent_tts.voices import (
    KOKORO_ALIASES,
    VoiceStoreError,
    download_file,
    get_store_root,
    handle_voice_command,
    install_voice,
    kokoro_model_dir,
    list_voices,
    piper_voice_urls,
    remove_voice,
)


def _write_source_file(path: str, payload: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(payload)
    return path


def _fake_fetch_from(source_dir: str):
    """Builds a fetch(url, dest) that resolves HF URLs to files under source_dir."""

    def fetch(url: str, dest: str) -> None:
        # Map the remote URL shape (repo/resolve/main/<rel>) to a local file.
        rel = url.split("/resolve/", 1)[1].split("/", 1)[1]
        source = os.path.join(source_dir, rel)
        if not os.path.isfile(source):
            raise FileNotFoundError(f"fake fetch: missing source for {url} ({source})")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(source, dest)

    return fetch


class TestVoiceNameValidation(unittest.TestCase):
    def test_rejects_path_separators(self):
        for bad in ("../evil", "a/b", "a\\b", "sub/dir/voice", "", "   ", ".hidden", "voice name"):
            with self.assertRaises(VoiceStoreError):
                remove_voice(bad, store_root=tempfile.gettempdir())

    def test_rejects_dotdot_component(self):
        with self.assertRaises(VoiceStoreError):
            remove_voice("..", store_root=tempfile.gettempdir())


class TestPiperVoiceUrls(unittest.TestCase):
    def test_full_name_resolves_huggingface_pattern(self):
        onnx, js = piper_voice_urls("es_ES-davefx-medium")
        self.assertEqual(
            onnx,
            "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx",
        )
        self.assertTrue(js.endswith("es_ES-davefx-medium.onnx.json"))

    def test_short_name_defaults_to_medium_quality(self):
        onnx, _ = piper_voice_urls("es_ES-davefx")
        self.assertIn("/davefx/medium/es_ES-davefx-medium.onnx", onnx)

    def test_unknown_name_raises_actionable_error(self):
        with self.assertRaises(VoiceStoreError) as ctx:
            piper_voice_urls("not-a-piper-name")
        self.assertIn("kokoro", str(ctx.exception))


class TestInstallListRemove(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-tts-voices-test-")
        self.store = os.path.join(self.tmp, "store")
        self.sources = os.path.join(self.tmp, "sources")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_piper_install_list_remove_round_trip(self):
        payload = b"fake-onnx-model-bytes"
        _write_source_file(
            os.path.join(self.sources, "es/es_ES/davefx/medium/es_ES-davefx-medium.onnx"),
            payload,
        )
        _write_source_file(
            os.path.join(self.sources, "es/es_ES/davefx/medium/es_ES-davefx-medium.onnx.json"),
            b'{"phoneme_type": "espeak"}',
        )
        fetch = _fake_fetch_from(self.sources)

        path = install_voice("es_ES-davefx-medium", store_root=self.store, fetch=fetch, progress=False)
        self.assertEqual(path, os.path.join(self.store, "es_ES-davefx-medium"))
        with open(os.path.join(path, "es_ES-davefx-medium.onnx"), "rb") as f:
            self.assertEqual(f.read(), payload)

        entries = list_voices(store_root=self.store)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "es_ES-davefx-medium")
        self.assertEqual(entries[0]["provider"], "piper")

        removed = remove_voice("es_ES-davefx-medium", store_root=self.store)
        self.assertTrue(os.path.isdir(os.path.dirname(removed)))
        self.assertFalse(os.path.exists(removed))
        self.assertEqual(list_voices(store_root=self.store), [])

    def test_kokoro_alias_installs_model_bundle(self):
        onnx_payload = b"kokoro-onnx-weights"
        _write_source_file(os.path.join(self.sources, "onnx/model.onnx"), onnx_payload)
        _write_source_file(
            os.path.join(self.sources, "config.json"),
            json.dumps({"vocab": {"$": 0, "a": 43}}).encode(),
        )
        for voice in ("ef_dora", "em_alex", "em_santa", "af_heart"):
            _write_source_file(os.path.join(self.sources, f"voices/{voice}.bin"), b"\x00" * 1024)
        fetch = _fake_fetch_from(self.sources)

        install_voice("kokoro", store_root=self.store, fetch=fetch, progress=False)

        entries = {e["name"]: e for e in list_voices(store_root=self.store)}
        self.assertIn("kokoro", entries)
        self.assertEqual(entries["kokoro"]["provider"], "kokoro")
        self.assertTrue(entries["kokoro"]["default"])
        self.assertEqual(kokoro_model_dir(self.store), os.path.join(self.store, "kokoro"))
        self.assertTrue(os.path.isfile(os.path.join(self.store, "kokoro", "model.onnx")))
        self.assertTrue(os.path.isfile(os.path.join(self.store, "kokoro", "voices", "ef_dora.bin")))

    def test_install_is_atomic_on_fetch_failure(self):
        def failing_fetch(url: str, dest: str) -> None:
            raise VoiceStoreError(f"Network error downloading {url}")

        with self.assertRaises(VoiceStoreError):
            install_voice("es_ES-davefx-medium", store_root=self.store, fetch=failing_fetch, progress=False)
        # No partial voice directory or temp leftovers remain.
        self.assertEqual(os.listdir(self.store) if os.path.isdir(self.store) else [], [])
        self.assertEqual(list_voices(store_root=self.store), [])

    def test_install_rejects_existing_voice(self):
        os.makedirs(os.path.join(self.store, "kokoro"))
        with self.assertRaises(VoiceStoreError) as ctx:
            install_voice("kokoro", store_root=self.store, fetch=lambda u, d: None, progress=False)
        self.assertIn("already installed", str(ctx.exception))

    def test_install_rejects_unknown_name(self):
        with self.assertRaises(VoiceStoreError):
            install_voice("definitely-not-a-voice", store_root=self.store, fetch=lambda u, d: None, progress=False)

    def test_remove_rejects_escape_outside_store(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        # A symlink inside the store pointing outside must be rejected.
        os.makedirs(self.store)
        os.symlink(outside, os.path.join(self.store, "sneaky"))
        with self.assertRaises(VoiceStoreError):
            remove_voice("sneaky", store_root=self.store)
        self.assertTrue(os.path.isdir(outside))

    def test_remove_missing_voice_errors(self):
        with self.assertRaises(VoiceStoreError) as ctx:
            remove_voice("ghost", store_root=self.store)
        self.assertIn("not installed", str(ctx.exception))

    def test_list_marks_piper_default_from_env(self):
        voice_dir = os.path.join(self.store, "es_ES-davefx-medium")
        os.makedirs(voice_dir)
        with open(os.path.join(voice_dir, "voice.json"), "w") as f:
            json.dump({"name": "es_ES-davefx-medium", "provider": "piper", "files": {}}, f)
        with mock.patch.dict(os.environ, {"PIPER_MODEL": os.path.join(self.store, "es_ES-davefx-medium.onnx")}):
            entries = list_voices(store_root=self.store)
        self.assertTrue(entries[0]["default"])


class TestDownloadFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-tts-dl-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _file_url(self, name: str, payload: bytes) -> str:
        return "file://" + _write_source_file(os.path.join(self.tmp, name), payload)

    def test_local_file_url_download_is_atomic(self):
        dest = os.path.join(self.tmp, "dest", "model.bin")
        payload = b"x" * 5000
        written = download_file(self._file_url("src.bin", payload), dest, progress=False)
        self.assertEqual(written, len(payload))
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), payload)
        leftovers = os.listdir(os.path.dirname(dest))
        self.assertEqual(leftovers, ["model.bin"])

    def test_missing_source_raises_clean_error_without_partial_file(self):
        dest = os.path.join(self.tmp, "dest2", "model.bin")
        with self.assertRaises(VoiceStoreError) as ctx:
            download_file("file:///nonexistent/path/model.bin", dest, progress=False)
        self.assertIn("Network error", str(ctx.exception))
        self.assertFalse(os.path.exists(dest))

    def test_sha256_mismatch_rejects_download(self):
        dest = os.path.join(self.tmp, "dest3", "model.bin")
        good_sha = "a" * 64
        with self.assertRaises(VoiceStoreError) as ctx:
            download_file(self._file_url("src2.bin", b"payload"), dest, sha256=good_sha, progress=False)
        self.assertIn("Checksum mismatch", str(ctx.exception))
        self.assertFalse(os.path.exists(dest))

    def test_sha256_match_accepts_download(self):
        import hashlib

        payload = b"payload"
        digest = hashlib.sha256(payload).hexdigest()
        dest = os.path.join(self.tmp, "dest4", "model.bin")
        download_file(self._file_url("src3.bin", payload), dest, sha256=digest, progress=False)
        self.assertTrue(os.path.isfile(dest))


class TestStoreRootEnv(unittest.TestCase):
    def test_env_override_read_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"AGENT_TTS_VOICES_DIR": tmp}):
                self.assertEqual(get_store_root(), tmp)
                self.assertEqual(kokoro_model_dir(), os.path.join(tmp, "kokoro"))


class TestVoiceCommandHandler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-tts-cmd-test-")
        self.store = os.path.join(self.tmp, "store")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_list_empty_reports_hint(self):
        out = tempfile.TemporaryFile("w+")
        code = handle_voice_command(["list"], store_root=self.store, out=out)
        out.seek(0)
        self.assertEqual(code, 0)
        self.assertIn("No voices installed", out.read())

    def test_install_unknown_name_exits_non_zero(self):
        code = handle_voice_command(
            ["install", "not-a-voice"], store_root=self.store, out=tempfile.TemporaryFile("w+")
        )
        self.assertEqual(code, 1)

    def test_remove_missing_exits_non_zero(self):
        code = handle_voice_command(["remove", "ghost"], store_root=self.store, out=tempfile.TemporaryFile("w+"))
        self.assertEqual(code, 1)

    def test_kokoro_alias_names(self):
        self.assertIn("kokoro", KOKORO_ALIASES)


class TestVoiceListCatalog(unittest.TestCase):
    """`voice list [--json] [provider]` prints the built-in voice catalog."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-tts-catalog-test-")
        self.store = os.path.join(self.tmp, "store")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _run(self, argv):
        out = tempfile.TemporaryFile("w+")
        self.addCleanup(out.close)
        code = handle_voice_command(argv, store_root=self.store, out=out)
        out.seek(0)
        return code, out.read()

    def test_json_catalog_shape(self):
        code, raw = self._run(["list", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(raw)
        self.assertEqual(
            payload["providers"], ["edge", "openai", "elevenlabs", "piper", "kokoro"]
        )
        self.assertEqual(set(payload["voices"]), set(payload["providers"]))
        self.assertIn("es-ES-ElviraNeural", payload["voices"]["edge"])
        self.assertIn("nova", payload["voices"]["openai"])
        self.assertIn("rachel", payload["voices"]["elevenlabs"])

    def test_json_single_provider_is_a_list(self):
        code, raw = self._run(["list", "edge", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(raw)
        self.assertIsInstance(payload, list)
        self.assertTrue(payload)
        self.assertTrue(all(isinstance(v, str) for v in payload))
        self.assertIn("es-ES-ElviraNeural", payload)

    def test_plain_single_provider_prints_one_voice_per_line(self):
        code, raw = self._run(["list", "openai"])
        self.assertEqual(code, 0)
        lines = [line for line in raw.splitlines() if line.strip()]
        self.assertTrue(lines)
        self.assertIn("nova", lines)

    def test_unknown_provider_exits_non_zero(self):
        code, _ = self._run(["list", "no-such-provider"])
        self.assertEqual(code, 1)

    def test_without_json_or_provider_keeps_installed_listing(self):
        code, raw = self._run(["list"])
        self.assertEqual(code, 0)
        self.assertIn("No voices installed", raw)


if __name__ == "__main__":
    unittest.main()
