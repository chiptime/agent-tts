# Deterministic browser audio fixtures

The committed `short.mp3`, `sample.mp3` and `long.mp3` files exercise native
Chromium decoding and playback without a TTS provider or physical speakers.

The `audio_capture_context` fixture in `tests/browser/conftest.py` generates the
canonical `speech.wav` in pytest's external temporary directory. It contains
48 kHz mono 16-bit PCM: a 440 Hz signal with reproducible silence windows.
Chromium consumes it through `--use-file-for-fake-audio-capture`, alongside the
fake-device and fake-permission flags. The signal proves capture and VAD, not
recognition accuracy or real-device acoustic echo cancellation.

Run from `hosts/herdr/brain`:

```sh
PYTHONPATH=src .venv/bin/python -m pytest --confcutdir=tests/browser tests/browser/test_audio_harness.py
```

The harness observes native `loadedmetadata`, `playing`, `timeupdate` and
`pause` events and recorded microphone bytes. All network routes are local
fixtures; unexpected traffic is rejected. Browser artifacts stay outside the
checkout under the system temporary directory.
