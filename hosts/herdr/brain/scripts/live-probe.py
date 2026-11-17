#!/usr/bin/env python
"""Local E2E probe of the live streaming call — no phone needed.

Boots the real brain app on a local port with temp state (hub STT
backend reaching llm-hub's Whisper, Silero framing, Smart Turn when
HERDR_BRAIN_SMART_TURN_ENABLED is exported) and feeds a PCM16 16 kHz
mono file into /call/stream exactly like the phone client: 100 ms
frames, stream start/stop, turn-end transcribe. Prints vad_status,
partials, finals and the dispatched transcript per scenario.

Usage (from hosts/herdr/brain):
  herdr-tts --render-text /tmp/frase.mp3 "tu frase de prueba"
  ffmpeg -y -i /tmp/frase.mp3 -ac 1 -ar 16000 -f s16le /tmp/frase.pcm
  HERDR_BRAIN_SMART_TURN_ENABLED=1 .venv/bin/python scripts/live-probe.py /tmp/frase.pcm

The TTS-contract warning at startup is expected (stubbed tts_home):
this probe never synthesizes. The synthetic-audio caveat from L58
applies to SMART TURN verdict tuning (TTS always sounds "complete"),
not to transcript-fidelity scenarios like these.
"""
import array
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import uvicorn
import websockets

from herdr_brain.pairing import PairingStore
from herdr_brain.server import create_app

PORT = 8791
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = None


def scaled_pcm(raw: bytes, target_peak: int) -> bytes:
    a = array.array("h")
    a.frombytes(raw)
    peak = max(1, max(abs(x) for x in a))
    factor = min(1.0, target_peak / peak)
    out = array.array("h", (max(-32768, min(32767, int(x * factor))) for x in a))
    return out.tobytes()


async def drive(name: str, pcm: bytes, speech_ms: int):
    frames = [pcm[i:i + 3200] for i in range(0, len(pcm) - 3200 + 1, 3200)]
    events = []
    async with websockets.connect(f"ws://127.0.0.1:{PORT}/call/stream") as ws:
        await ws.send(json.dumps(
            {"type": "auth", "token": TOKEN, "locale": "es", "vad": True}))

        async def reader():
            try:
                async for m in ws:
                    try:
                        events.append(json.loads(m))
                    except Exception:
                        pass
            except Exception:
                pass

        r = asyncio.ensure_future(reader())
        await asyncio.sleep(0.5)  # auth_ok + vad_status arrive
        await ws.send(json.dumps({"type": "stream", "action": "start"}))
        for f in frames:
            await ws.send(f)
            await asyncio.sleep(0.1)
        # server_turns: the framer needs SILENCE FRAMES to emit
        # speech_end (like the phone's continuous uplink) — the server
        # then closes the turn itself after its silence window.
        for _ in range(25):
            await ws.send(b"\x00" * 3200)
            await asyncio.sleep(0.1)
        await ws.send(json.dumps({"type": "stream", "action": "stop"}))
        await asyncio.sleep(0.3)
        await ws.send(json.dumps({
            "type": "transcribe", "duration_ms": speech_ms, "pre_roll_ms": 500}))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if [e for e in events if e.get("type") in ("transcript", "error")]:
                break
            await asyncio.sleep(0.2)
        r.cancel()
    vad = next((e for e in events if e.get("type") == "vad_status"), {})
    partials = [e.get("text", "") for e in events if e.get("type") == "partial"]
    finals = [e.get("text", "") for e in events if e.get("type") == "final"]
    transcript = next(
        (e for e in events if e.get("type") == "transcript"), None)
    print(f"\n=== {name} ===")
    print(f"  vad_status: engine={vad.get('engine')} "
          f"smart_turn={vad.get('smart_turn')}")
    print(f"  partials ({len(partials)}): {partials[:4]}")
    print(f"  finals   ({len(finals)}): {finals[:4]}")
    print(f"  TRANSCRIPT: {transcript!r}")
    return transcript


async def main(pcm_path: str):
    raw = open(pcm_path, "rb").read()
    a = array.array("h")
    a.frombytes(raw)
    full_ms = int(len(a) / 16)

    quiet = scaled_pcm(raw, 1600)   # the phone's measured mic level
    cut_frames = int(len(raw) * 0.68 // 3200)  # ~68%: a mid-phrase cut
    cut = raw[: cut_frames * 3200]
    cut_quiet = scaled_pcm(cut, 1600)

    await drive("A: full phrase, normal level", raw, full_ms)
    await drive("B: full phrase, phone-quiet (peak 1600)", quiet, full_ms)
    await drive(
        "C: truncated ~68%, phone-quiet (the mid-phrase cut)",
        cut_quiet, int(len(cut) / 32) * 2)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    tmp = tempfile.mkdtemp(prefix="liveprobe-")
    tts_stub = os.path.join(tmp, "tts")
    os.makedirs(tts_stub, exist_ok=True)
    cfg_kwargs = dict(
        herdr_bin="herdr-fake",
        glm_api_key="probe-key",
        glm_base_url="https://example.invalid/",
        glm_model="glm-5",
        tts_home=tts_stub,
        tts_bin="/bin/true",
        tts_voice="elvira",
        tts_rate="+0%",
        tts_timeout_s=10,
        audio_dir=os.path.join(tmp, "audio"),
        prompt_timeout_ms=5_000,
        max_tool_rounds=4,
        approval_timeout_s=60,
        screen_lines=40,
        stt_model="small",
        stt_device="auto",
        stt_compute="auto",
        stt_warmup=False,
    )
    from herdr_brain.config import Settings
    cfg = Settings(state_dir=tmp, smart_turn_enabled=True, **cfg_kwargs)
    os.makedirs(cfg.audio_dir, exist_ok=True)
    app = create_app(settings=cfg, version="probe")
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=PORT, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(2.0)

    store = PairingStore(tmp)
    minted = store.mint()
    req = urllib.request.Request(
        BASE + "/pair",
        data=json.dumps({"code": minted["code"], "label": "probe"}).encode(),
        headers={"content-type": "application/json"}, method="POST",
    )
    TOKEN = json.loads(urllib.request.urlopen(req, timeout=5).read())["token"]
    print("server up, token minted")
    asyncio.run(main(sys.argv[1]))
