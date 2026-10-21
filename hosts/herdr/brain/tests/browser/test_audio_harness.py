"""Native Chromium audio input/output proof with deterministic fake hardware."""

from pathlib import Path


FIXTURES = Path(__file__).resolve().parents[1] / "e2e/fixtures"
ORIGIN = "http://127.0.0.1:41731"


def test_fake_capture_and_native_playback(audio_capture_context):
    context = audio_capture_context
    blocked = []

    def route(request):
        if request.request.url == ORIGIN + "/":
            return request.fulfill(content_type="text/html", body="""
                <button id="start">Start audio</button><audio id="player" src="/sample.mp3"></audio>
                <script>
                window.__realMedia = {events: [], inputPeak: 0, bytes: 0};
                const player = document.getElementById('player');
                for (const type of ['loadedmetadata', 'playing', 'timeupdate', 'pause', 'ended']) {
                    player.addEventListener(type, () => __realMedia.events.push(type));
                }
                document.getElementById('start').onclick = async () => {
                    const stream = await navigator.mediaDevices.getUserMedia({audio: {
                        echoCancellation: true, noiseSuppression: true, autoGainControl: true
                    }});
                    window.captureStream = stream;
                    __realMedia.settings = stream.getAudioTracks()[0].getSettings();
                    const audio = window.captureAudio = new AudioContext();
                    await audio.resume();
                    const analyser = audio.createAnalyser();
                    audio.createMediaStreamSource(stream).connect(analyser);
                    const frame = new Float32Array(analyser.fftSize);
                    window.inputTimer = setInterval(() => {
                        analyser.getFloatTimeDomainData(frame);
                        const rms = Math.sqrt(frame.reduce((sum, v) => sum + v * v, 0) / frame.length);
                        __realMedia.inputPeak = Math.max(__realMedia.inputPeak, rms);
                    }, 20);
                    const recorder = window.captureRecorder = new MediaRecorder(stream);
                    recorder.ondataavailable = event => { __realMedia.bytes += event.data.size; };
                    recorder.start(100);
                    await player.play();
                };
                </script>
            """)
        if request.request.url == ORIGIN + "/sample.mp3":
            return request.fulfill(content_type="audio/mpeg", body=(FIXTURES / "sample.mp3").read_bytes())
        blocked.append(request.request.url)
        request.abort("blockedbyclient")

    context.route("**/*", route)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(ORIGIN + "/")
    page.locator("#start").click()
    page.wait_for_function("() => __realMedia.inputPeak > 0.01 && __realMedia.bytes > 0")
    page.wait_for_function("() => __realMedia.events.includes('timeupdate')")
    snapshot = page.locator("#player").evaluate("""player => ({
        duration: player.duration, paused: player.paused, probe: __realMedia
    })""")
    assert snapshot["duration"] > 0
    assert not snapshot["paused"]
    assert "loadedmetadata" in snapshot["probe"]["events"]
    assert "playing" in snapshot["probe"]["events"]
    assert snapshot["probe"]["settings"]["echoCancellation"] is True
    page.evaluate("""() => {
        document.getElementById('player').pause();
        captureRecorder.stop();
        captureStream.getTracks().forEach(track => track.stop());
        clearInterval(inputTimer);
        captureAudio.close();
    }""")
    assert page.evaluate("() => __realMedia.events.includes('pause')")
    assert not blocked
    assert not errors
